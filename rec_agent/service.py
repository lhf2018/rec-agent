from __future__ import annotations

from datetime import timedelta

from rec_agent.catalog import Catalog, get_catalog
from rec_agent.config import Settings, get_settings
from rec_agent.engine import RecommendationEngine
from rec_agent.feedback import FeedbackService
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
from rec_agent.runtime.trace import TraceRecorder
from rec_agent.snapshot import SnapshotBuilder
from rec_agent.store import Store
from rec_agent.timeutil import to_utc_iso, utc_now
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
        self.tools = ToolExecutor(self.catalog, self.engine, store=self.store, settings=self.settings)
        self.loop = AgentLoop(self.tools, self.guard, self.settings)
        self.snapshots = SnapshotBuilder(self.catalog, self.store, self.settings)
        self.feedback_svc = FeedbackService(self.catalog, self.store, self.settings)

    def create_task(self, req: CreateTaskRequest) -> RecommendationResult:
        key = f"{req.tenant_id}:{req.user_id}:{req.scene.trigger.value}:{req.scene.scene_id}"
        existing = self.store.get_by_idempotency(key)
        if existing:
            fields = set(RecommendationResult.model_fields)
            return RecommendationResult(**{k: v for k, v in existing.items() if k in fields})

        task_id = self.store.new_task_id()
        started = utc_now()
        deadline_at = started + timedelta(milliseconds=self.settings.task_timeout_ms)
        trace = TraceRecorder(run_id=task_id)
        scene_key = f"{req.scene.trigger.value}:{req.scene.scene_id}"

        snapshot = self.snapshots.build(req.user_id, now=started, scene_key=scene_key)
        if not snapshot or snapshot.tenant_id != req.tenant_id:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.failed,
                decision_reason_code="user_not_found",
            )
            self._persist(task_id, req, key, result, trace)
            return result

        elig = self.policy.check_eligibility(snapshot, req.scene)
        if not elig.ok:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.skipped,
                decision_reason_code=elig.reason_code,
                opportunity_result=None,
            )
            self._persist(task_id, req, key, result, trace)
            return result

        opp = self.policy.check_opportunity(snapshot, req.scene)
        trace.set_meta(opportunity_result=opp.result.value)
        if opp.result == OpportunityResult.suppress:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.suppressed,
                opportunity_result=opp.result,
                decision_reason_code=opp.reason_code,
            )
            self._persist(task_id, req, key, result, trace)
            return result
        if opp.result == OpportunityResult.skip:
            result = RecommendationResult(
                task_id=task_id,
                status=TaskStatus.skipped,
                opportunity_result=opp.result,
                decision_reason_code=opp.reason_code,
            )
            self._persist(task_id, req, key, result, trace)
            return result

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
            self._persist(task_id, req, key, result, trace)
            return result

        budget_ok = self.policy.budget_ok(deadline_at)
        route = self.policy.route(
            engine_out,
            budget_ok=budget_ok,
            enable_llm=self.settings.enable_llm,
            enable_react=self.settings.enable_react,
        )
        trace.set_meta(route_mode=route.mode.value, decision_reason_code=route.reason_code)

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
            trace.set_meta(fallback_reason=fallback_reason)
        elif route.mode == RouteMode.rule_engine:
            merchants = self._rule_engine_result(engine_out.baseline)
            source = "rule_engine"
            trace.add_step(
                step_index=1,
                decision_reason_code="rule_engine_template",
                tool_name=None,
                result_status="ok",
                evidence_ids=[m.evidence_ids[0] for m in merchants if m.evidence_ids],
            )
        elif route.mode in (RouteMode.direct, RouteMode.react):
            agent_out = self.loop.run(
                route.mode, snapshot, req.scene, engine_out.candidates, trace=trace
            )
            model_calls = agent_out.get("model_calls") or 0
            if agent_out.get("ok") and agent_out.get("merchants"):
                merchants = agent_out["merchants"]
                source = "agent"
            else:
                merchants = self.guard.baseline_to_recommendations(engine_out.baseline)
                status = TaskStatus.degraded
                source = "ranker_fallback"
                fallback_reason = agent_out.get("fallback_reason") or "agent_failed"
                trace.set_meta(fallback_reason=fallback_reason)
        else:
            merchants = self._rule_engine_result(engine_out.baseline)

        if merchants and source != "agent":
            evidence = self.guard.build_evidence_index(engine_out.candidates)
            by_id = {c.merchant_id: c for c in engine_out.candidates}
            allowed = set(by_id) | {p.product_id for c in engine_out.candidates for p in c.products}
            gr = self.guard.validate_submission(merchants, allowed, by_id, evidence)
            merchants = gr.merchants
            trace.add_guard({"ok": gr.ok, "errors": gr.errors, "soft_dropped": gr.soft_dropped})
            if not merchants:
                status = TaskStatus.empty
                fallback_reason = fallback_reason or "guard_empty"

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
                    metrics={
                        "model_calls": model_calls,
                        "filter_stats": engine_out.filter_stats,
                        "latency_ms": trace.summary()["latency_ms"],
                    },
                )
                self._persist(task_id, req, key, result, trace)
                return result

        if merchants and status in (TaskStatus.succeeded, TaskStatus.degraded):
            by_id = {c.merchant_id: c for c in engine_out.candidates}
            cats = [
                by_id[m.merchant_id].category
                for m in merchants
                if m.merchant_id in by_id
            ]
            self.store.record_delivery(
                req.user_id,
                task_id=task_id,
                merchant_ids=[m.merchant_id for m in merchants],
                categories=cats,
                delivered_at=to_utc_iso(started),
            )
            self.store.record_scene_seen(req.user_id, scene_key, seen_at=to_utc_iso(started))

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
                "latency_ms": trace.summary()["latency_ms"],
                "run_id": trace.run_id,
                "deadline_at": to_utc_iso(deadline_at),
            },
        )
        self._persist(task_id, req, key, result, trace)
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

    def _persist(
        self,
        task_id: str,
        req: CreateTaskRequest,
        key: str,
        result: RecommendationResult,
        trace: TraceRecorder,
    ) -> None:
        payload = result.model_dump()
        payload["request"] = req.model_dump()
        payload["trace_summary"] = {
            "run_id": trace.run_id,
            "route_mode": trace.route_mode,
            "opportunity_result": trace.opportunity_result,
            "decision_reason_code": trace.decision_reason_code or result.decision_reason_code,
            "fallback_reason": trace.fallback_reason or result.fallback_reason,
            "model_calls": trace.model_calls or result.metrics.get("model_calls", 0),
            "latency_ms": trace.summary()["latency_ms"],
            "guard_results": trace.guard_results,
        }
        self.store.save_task(task_id, req.tenant_id, req.user_id, key, result.status.value, payload)
        if trace.steps:
            self.store.save_steps(task_id, trace.steps)

    def get_task(self, task_id: str) -> dict | None:
        return self.store.get_task(task_id)

    def get_trace(self, task_id: str) -> dict:
        task = self.store.get_task(task_id)
        steps = self.store.get_steps(task_id)
        return {"task": task, "steps": steps, "trace_summary": (task or {}).get("trace_summary")}

    def feedback(self, task_id: str, body: FeedbackRequest) -> dict:
        return self.feedback_svc.apply(task_id, body)
