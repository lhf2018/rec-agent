from __future__ import annotations

from datetime import datetime, timezone

from rec_agent.catalog import Catalog, get_catalog
from rec_agent.config import Settings, get_settings
from rec_agent.engine import RecommendationEngine
from rec_agent.models import (
    CreateTaskRequest,
    FeedbackRequest,
    OpportunityResult,
    RecommendationResult,
    RecommendedMerchant,
    RouteMode,
    TaskStatus,
)
from rec_agent.policy import Policy, template_reason
from rec_agent.runtime.guard import Guard
from rec_agent.runtime.loop import AgentLoop
from rec_agent.store import Store
from rec_agent.tools import ToolExecutor


class RecommendationService:
    def __init__(
        self,
        catalog: Catalog | None = None,
        store: Store | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.catalog = catalog or get_catalog()
        self.store = store or Store()
        self.policy = Policy(self.settings)
        self.engine = RecommendationEngine(self.catalog, self.settings)
        self.guard = Guard(self.settings)
        self.tools = ToolExecutor(self.catalog, self.engine)
        self.loop = AgentLoop(self.tools, self.guard, self.settings)

    def create_task(self, req: CreateTaskRequest) -> RecommendationResult:
        key = f"{req.tenant_id}:{req.user_id}:{req.scene.trigger.value}:{req.scene.scene_id}"
        existing = self.store.get_by_idempotency(key)
        if existing:
            return RecommendationResult(**existing)

        task_id = self.store.new_task_id()
        snapshot = self.catalog.get_user(req.user_id)
        if not snapshot or snapshot.tenant_id != req.tenant_id:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.failed,
                decision_reason_code="user_not_found",
            )
            self._persist(task_id, req, key, result, [])
            return result

        # 1) eligibility
        elig = self.policy.check_eligibility(snapshot, req.scene)
        if not elig.ok:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.skipped,
                decision_reason_code=elig.reason_code,
                opportunity_result=None,
            )
            self._persist(task_id, req, key, result, [])
            return result

        # 2) opportunity
        opp = self.policy.check_opportunity(snapshot, req.scene)
        if opp.result == OpportunityResult.suppress:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.suppressed,
                opportunity_result=opp.result,
                decision_reason_code=opp.reason_code,
            )
            self._persist(task_id, req, key, result, [])
            return result
        if opp.result == OpportunityResult.skip:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.skipped,
                opportunity_result=opp.result,
                decision_reason_code=opp.reason_code,
            )
            self._persist(task_id, req, key, result, [])
            return result

        # 3) engine
        engine_out = self.engine.run(snapshot, req.scene)
        if not engine_out.candidates:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.empty,
                opportunity_result=opp.result,
                decision_reason_code="no_candidates",
                route_mode=RouteMode.skip,
                metrics={"filter_stats": engine_out.filter_stats},
            )
            self._persist(task_id, req, key, result, [])
            return result

        # 4) route
        route = self.policy.route(
            engine_out,
            budget_ok=True,
            enable_llm=self.settings.enable_llm,
            enable_react=self.settings.enable_react,
        )

        steps: list[dict] = []
        merchants: list[RecommendedMerchant] = []
        status = TaskStatus.succeeded
        source = "rule_engine"
        fallback_reason = None
        model_calls = 0

        if route.mode == RouteMode.skip:
            status = TaskStatus.empty
        elif route.mode == RouteMode.fallback:
            merchants = self.guard.baseline_to_recommendations(engine_out.baseline)
            status = TaskStatus.degraded
            source = "ranker_fallback"
            fallback_reason = route.reason_code
        elif route.mode == RouteMode.rule_engine:
            merchants = self._rule_engine_result(engine_out.baseline)
            source = "rule_engine"
        elif route.mode in (RouteMode.direct, RouteMode.react):
            agent_out = self.loop.run(route.mode, snapshot, req.scene, engine_out.candidates)
            steps = agent_out.get("steps") or []
            model_calls = agent_out.get("model_calls") or 0
            if agent_out.get("ok") and agent_out.get("merchants"):
                merchants = agent_out["merchants"]
                source = "agent"
            else:
                merchants = self.guard.baseline_to_recommendations(engine_out.baseline)
                status = TaskStatus.degraded
                source = "ranker_fallback"
                fallback_reason = agent_out.get("fallback_reason") or "agent_failed"
        else:
            merchants = self._rule_engine_result(engine_out.baseline)

        # diversity / evidence index for baseline paths
        if merchants and source != "agent":
            evidence = self.guard.build_evidence_index(engine_out.candidates)
            by_id = {c.merchant_id: c for c in engine_out.candidates}
            allowed = set(by_id) | {p.product_id for c in engine_out.candidates for p in c.products}
            gr = self.guard.validate_submission(merchants, allowed, by_id, evidence)
            merchants = gr.merchants
            if not merchants:
                status = TaskStatus.empty
                fallback_reason = fallback_reason or "guard_empty"

        # delivery
        if merchants:
            delivery = self.policy.check_delivery(snapshot, [m.merchant_id for m in merchants])
            if not delivery.ok:
                result = RecommendationResult(
                    task_id=task_id,
                    status=TaskStatus.skipped,
                    route_mode=route.mode,
                    opportunity_result=opp.result,
                    decision_reason_code=delivery.reason_code,
                    merchants=[],
                    metrics={"model_calls": model_calls, "filter_stats": engine_out.filter_stats},
                )
                self._persist(task_id, req, key, result, steps)
                return result

        # record impression ids lightly on in-memory snapshot for demo suppress
        if merchants and status in (TaskStatus.succeeded, TaskStatus.degraded):
            snapshot.recent_impressions.append(req.scene.scene_id)

        result = RecommendationResult(
            task_id=task_id,
            status=status,
            route_mode=route.mode,
            opportunity_result=opp.result,
            decision_reason_code=route.reason_code,
            fallback_reason=fallback_reason,
            source=source,
            merchants=merchants,
            metrics={
                "model_calls": model_calls,
                "filter_stats": engine_out.filter_stats,
                "candidate_set_version": engine_out.candidate_set_version,
                "intent_conflict": engine_out.intent_conflict,
            },
        )
        self._persist(task_id, req, key, result, steps)
        return result

    def _rule_engine_result(self, baseline) -> list[RecommendedMerchant]:
        from rec_agent.models import RecommendedProduct

        out: list[RecommendedMerchant] = []
        for c in baseline[: self.settings.max_merchants]:
            if not c.products:
                continue
            p = c.products[0]
            out.append(
                RecommendedMerchant(
                    merchant_id=c.merchant_id,
                    name=c.name,
                    reason=template_reason(c),
                    evidence_ids=[c.evidence_id or f"cand_{c.merchant_id}"],
                    products=[
                        RecommendedProduct(
                            product_id=p.product_id,
                            name=p.name,
                            reason=f"有库存，价格 {int(p.price)} 元",
                            evidence_ids=[f"prod_{p.product_id}"],
                        )
                    ],
                )
            )
        return out

    def _persist(self, task_id, req, key, result: RecommendationResult, steps: list) -> None:
        payload = result.model_dump()
        payload["request"] = req.model_dump()
        self.store.save_task(task_id, req.tenant_id, req.user_id, key, result.status.value, payload)
        if steps:
            self.store.save_steps(task_id, steps)

    def get_task(self, task_id: str) -> dict | None:
        return self.store.get_task(task_id)

    def get_trace(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        steps = self.store.get_steps(task_id)
        return {"task": task, "steps": steps}

    def feedback(self, task_id: str, body: FeedbackRequest) -> dict:
        task = self.store.get_task(task_id)
        if not task:
            return {"ok": False, "error": "task_not_found"}
        user_id = task.get("request", {}).get("user_id")
        if body.event_type == "not_interested":
            if not body.merchant_id:
                return {"ok": False, "error": "merchant_id_required"}
            returned = {m["merchant_id"] for m in task.get("merchants", [])}
            if body.merchant_id not in returned:
                return {"ok": False, "error": "merchant_not_in_task"}
            self.catalog.apply_not_interested(user_id, body.merchant_id)
        event_time = body.event_time or datetime.now(timezone.utc).isoformat()
        self.store.add_feedback(task_id, user_id, body.merchant_id, body.event_type, event_time)
        return {"ok": True}
