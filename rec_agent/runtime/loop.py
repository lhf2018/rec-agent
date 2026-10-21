from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from rec_agent.config import Settings, get_settings
from rec_agent.models import (
    CandidateMerchant,
    RecommendedMerchant,
    RecommendedProduct,
    RouteMode,
    Scene,
    UserSnapshot,
)
from rec_agent.runtime.budget import DeadlineBudget
from rec_agent.runtime.guard import Evidence, Guard
from rec_agent.runtime.trace import TraceRecorder
from rec_agent.timeutil import utc_now
from rec_agent.tools import ToolExecutor


class AgentLoop:
    """MVP loop with deterministic mock LLM behavior (no external API required)."""

    def __init__(
        self,
        tools: ToolExecutor,
        guard: Guard | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.tools = tools
        self.guard = guard or Guard()
        self.settings = settings or get_settings()

    def run(
        self,
        mode: RouteMode,
        snapshot: UserSnapshot,
        scene: Scene,
        candidates: list[CandidateMerchant],
        trace: TraceRecorder | None = None,
        deadline_at: datetime | None = None,
        candidate_set_version: str = "",
    ) -> dict[str, Any]:
        self.tools.reset()
        recorder = trace or TraceRecorder()
        recorder.set_meta(route_mode=mode.value)
        if deadline_at is None:
            deadline_at = utc_now() + timedelta(milliseconds=self.settings.task_timeout_ms)
        budget = DeadlineBudget(deadline_at, self.settings)
        version = candidate_set_version
        allowed = self._allowed_ids(candidates)
        evidence = self.guard.build_evidence_index(candidates, candidate_set_version=version)
        by_id = {c.merchant_id: c for c in candidates}

        if mode == RouteMode.direct:
            if not budget.allow_model():
                return self._deadline_exhausted(recorder, candidates, evidence)
            recorder.bump_model()
            draft = self._mock_select(snapshot, candidates, prefer_short_term=False)
            recorder.add_step(
                step_index=1,
                decision_reason_code="direct_submit",
                tool_name="submit_recommendation",
                actions=[{"tool": "submit_recommendation"}],
                evidence_ids=[eid for m in draft for eid in m.evidence_ids],
            )
            gr = self.guard.validate_submission(
                draft,
                allowed,
                by_id,
                evidence,
                candidate_set_version=version,
                now=utc_now(),
            )
            recorder.add_guard({"ok": gr.ok, "errors": gr.errors, "soft_dropped": gr.soft_dropped})
            return self._pack(gr, recorder, candidates, evidence)

        max_steps = self.settings.react_max_steps
        for step in range(1, max_steps + 1):
            if not budget.allow_model():
                return self._deadline_exhausted(recorder, candidates, evidence)
            recorder.bump_model()
            if step == 1 and snapshot.short_term:
                short = max(snapshot.short_term, key=lambda x: x.confidence).topic
                left = candidates[0].merchant_id if candidates else None
                right_cand = next((c for c in candidates if c.category == short), None)
                if left and right_cand and right_cand.merchant_id != left:
                    if not budget.allow_tool(self.settings.tool_timeout_ms):
                        return self._deadline_exhausted(recorder, candidates, evidence)
                    obs = self.tools.execute(
                        "compare_candidates",
                        {
                            "left_id": left,
                            "right_id": right_cand.merchant_id,
                            "aspects": ["price", "distance", "category"],
                        },
                        allowed_ids=allowed,
                        snapshot=snapshot,
                        scene=scene,
                        candidates=candidates,
                    )
                    if obs.get("ok"):
                        evidence[obs["evidence_id"]] = Evidence(
                            evidence_id=obs["evidence_id"],
                            facts={"compare": True, "data": obs.get("data", {})},
                            source="tool",
                            subject_type="tool",
                            subject_id=obs["evidence_id"],
                            candidate_set_version=version,
                        )
                    recorder.add_step(
                        step_index=step,
                        decision_reason_code="need_compare_short_term_intent",
                        tool_name="compare_candidates",
                        result_status="ok" if obs.get("ok") else "error",
                        evidence_ids=[obs.get("evidence_id")] if obs.get("evidence_id") else [],
                        actions=[{"tool": "compare_candidates"}],
                        observations=[obs],
                        extra={"missing_evidence": ["compare"]},
                    )
                    continue

            if len(candidates) < 1:
                if not budget.allow_tool(self.settings.supplement_timeout_ms):
                    return self._deadline_exhausted(recorder, candidates, evidence)
                obs = self.tools.execute(
                    "supplement_recall",
                    {"strategy": "widen_radius"},
                    allowed_ids=allowed,
                    snapshot=snapshot,
                    scene=scene,
                    candidates=candidates,
                )
                recorder.add_step(
                    step_index=step,
                    decision_reason_code="insufficient_candidates",
                    tool_name="supplement_recall",
                    result_status="ok" if obs.get("ok") else "error",
                    actions=[{"tool": "supplement_recall"}],
                    observations=[obs],
                )
                if obs.get("ok"):
                    new_raw = obs["data"]["new_candidates"]
                    new_cands = [CandidateMerchant(**c) for c in new_raw]
                    version = obs["data"].get("candidate_set_version") or version
                    candidates = candidates + new_cands
                    allowed = self._allowed_ids(candidates)
                    by_id = {c.merchant_id: c for c in candidates}
                    # drop stale primary evidence that does not match new version
                    evidence = {
                        k: v
                        for k, v in evidence.items()
                        if v.subject_type in {"snapshot", "tool"}
                        or not v.candidate_set_version
                        or v.candidate_set_version == version
                    }
                    evidence.update(
                        self.guard.build_evidence_index(
                            new_cands, candidate_set_version=version
                        )
                    )
                continue

            draft = self._mock_select(snapshot, candidates, prefer_short_term=True)
            recorder.add_step(
                step_index=step,
                decision_reason_code="react_submit",
                tool_name="submit_recommendation",
                actions=[{"tool": "submit_recommendation"}],
                evidence_ids=[eid for m in draft for eid in m.evidence_ids],
            )
            gr = self.guard.validate_submission(
                draft,
                allowed,
                by_id,
                evidence,
                candidate_set_version=version,
                now=utc_now(),
            )
            recorder.add_guard({"ok": gr.ok, "errors": gr.errors, "soft_dropped": gr.soft_dropped})
            return self._pack(gr, recorder, candidates, evidence)

        recorder.set_meta(fallback_reason="max_steps")
        return {
            "ok": False,
            "merchants": [],
            "errors": ["max_steps"],
            "steps": recorder.steps,
            "model_calls": recorder.model_calls,
            "candidates": candidates,
            "evidence_index": evidence,
            "fallback_reason": "max_steps",
            "trace": recorder.summary(),
        }

    def _deadline_exhausted(
        self, recorder: TraceRecorder, candidates, evidence
    ) -> dict[str, Any]:
        recorder.set_meta(fallback_reason="deadline_exhausted")
        recorder.add_step(
            step_index=len(recorder.steps) + 1,
            decision_reason_code="deadline_exhausted",
            result_status="budget_exhausted",
        )
        return {
            "ok": False,
            "merchants": [],
            "errors": ["deadline_exhausted"],
            "steps": recorder.steps,
            "model_calls": recorder.model_calls,
            "candidates": candidates,
            "evidence_index": evidence,
            "fallback_reason": "deadline_exhausted",
            "trace": recorder.summary(),
        }

    def _pack(self, gr, recorder: TraceRecorder, candidates, evidence) -> dict[str, Any]:
        return {
            "ok": gr.ok,
            "merchants": gr.merchants,
            "errors": gr.errors,
            "steps": recorder.steps,
            "model_calls": recorder.model_calls,
            "candidates": candidates,
            "evidence_index": evidence,
            "trace": recorder.summary(),
        }

    def _allowed_ids(self, candidates: list[CandidateMerchant]) -> set[str]:
        ids: set[str] = set()
        for c in candidates:
            ids.add(c.merchant_id)
            for p in c.products:
                ids.add(p.product_id)
        return ids

    def _mock_select(
        self,
        snapshot: UserSnapshot,
        candidates: list[CandidateMerchant],
        prefer_short_term: bool,
    ) -> list[RecommendedMerchant]:
        ordered = list(candidates)
        if prefer_short_term and snapshot.short_term:
            topic = max(snapshot.short_term, key=lambda x: x.confidence).topic
            ordered = sorted(
                candidates,
                key=lambda c: (0 if c.category == topic else 1, -c.score),
            )
        out: list[RecommendedMerchant] = []
        for c in ordered[:3]:
            if not c.products:
                continue
            p = c.products[0]
            out.append(
                RecommendedMerchant(
                    merchant_id=c.merchant_id,
                    name=c.name,
                    reason=(
                        f"近 30 天偏好相关，当前营业，距离约 {int(c.distance_m)} 米在配送范围内"
                        if c.merchant_id in snapshot.frequent_merchants
                        else f"与当前场景匹配，距离约 {int(c.distance_m)} 米，人均约 {int(c.avg_price)} 元"
                    ),
                    evidence_ids=[c.evidence_id or f"cand_{c.merchant_id}", "snap_history"],
                    products=[
                        RecommendedProduct(
                            product_id=p.product_id,
                            name=p.name,
                            reason=f"有库存，价格 {int(p.price)} 元落在可接受区间",
                            evidence_ids=[f"prod_{p.product_id}", "snap_price_band"],
                        )
                    ],
                )
            )
        return out
