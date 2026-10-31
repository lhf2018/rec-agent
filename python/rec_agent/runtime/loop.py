from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from rec_agent.config import Settings, get_settings
from rec_agent.llm import (
    LLMError,
    OpenAICompatibleClient,
    build_system_prompt,
    build_user_payload,
)
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
from rec_agent.runtime.registry import ToolRegistry
from rec_agent.runtime.trace import TraceRecorder
from rec_agent.timeutil import to_utc_iso, utc_now
from rec_agent.tools import ToolExecutor


@dataclass
class _LoopState:
    snapshot: UserSnapshot
    scene: Scene
    candidates: list[CandidateMerchant]
    version: str
    allowed: set[str]
    by_id: dict[str, CandidateMerchant]
    evidence: dict[str, Evidence]
    tool_evidence_ids: list[str] = field(default_factory=list)
    compare_pick: str | None = None
    demote_ids: set[str] = field(default_factory=set)
    submit_failures: int = 0
    self_score: float | None = None
    compared: bool = False
    supplemented: bool = False
    refreshed: bool = False
    exposure_fetched: bool = False
    products_fetched: bool = False


class AgentLoop:
    """DIRECT / REACT loop with mock planner or real OpenAI-compatible tool calling."""

    def __init__(
        self,
        tools: ToolExecutor,
        guard: Guard | None = None,
        settings: Settings | None = None,
        llm: OpenAICompatibleClient | None = None,
    ) -> None:
        self.tools = tools
        self.guard = guard or Guard()
        self.settings = settings or get_settings()
        self.llm = llm or OpenAICompatibleClient(self.settings)
        self.registry = ToolRegistry()

    def run(
        self,
        mode: RouteMode,
        snapshot: UserSnapshot,
        scene: Scene,
        candidates: list[CandidateMerchant],
        trace: TraceRecorder | None = None,
        deadline_at: datetime | None = None,
        candidate_set_version: str = "",
        *,
        need_compare: bool = False,
        need_freshness: bool = False,
        supplement_needed: bool = False,
        soft_rerank: bool = False,
    ) -> dict[str, Any]:
        self.tools.reset()
        recorder = trace or TraceRecorder()
        if deadline_at is None:
            deadline_at = utc_now() + timedelta(milliseconds=self.settings.task_timeout_ms)
        recorder.set_meta(route_mode=mode.value, deadline_at=to_utc_iso(deadline_at))
        budget = DeadlineBudget(deadline_at, self.settings)
        state = _LoopState(
            snapshot=snapshot,
            scene=scene,
            candidates=list(candidates),
            version=candidate_set_version,
            allowed=self._allowed_ids(candidates),
            by_id={c.merchant_id: c for c in candidates},
            evidence=self.guard.build_evidence_index(
                candidates, candidate_set_version=candidate_set_version
            ),
        )

        if mode == RouteMode.direct:
            return self._run_direct(state, recorder, budget, soft_rerank=soft_rerank)

        return self._run_react(
            state,
            recorder,
            budget,
            need_compare=need_compare,
            need_freshness=need_freshness,
            supplement_needed=supplement_needed,
        )

    # --- DIRECT ---

    def _run_direct(
        self,
        state: _LoopState,
        recorder: TraceRecorder,
        budget: DeadlineBudget,
        *,
        soft_rerank: bool,
    ) -> dict[str, Any]:
        if not budget.allow_model():
            return self._deadline_exhausted(recorder, state)
        recorder.bump_model()

        if self.settings.use_mock_llm:
            draft = self._mock_select(
                state.snapshot,
                state.candidates,
                prefer_short_term=False,
                prefer_soft_tags=soft_rerank,
            )
            return self._finalize_submit(
                state,
                recorder,
                draft,
                self_score=0.82 if soft_rerank else 0.75,
                step_index=1,
                reason_code="direct_submit",
            )

        return self._llm_loop(
            state,
            recorder,
            budget,
            mode=RouteMode.direct,
            tool_names=["submit_recommendation"],
            tool_choice={"type": "function", "function": {"name": "submit_recommendation"}},
            hints={"soft_rerank": soft_rerank, "tools_allowed": False},
        )

    # --- REACT ---

    def _run_react(
        self,
        state: _LoopState,
        recorder: TraceRecorder,
        budget: DeadlineBudget,
        *,
        need_compare: bool,
        need_freshness: bool,
        supplement_needed: bool,
    ) -> dict[str, Any]:
        if self.settings.use_mock_llm:
            return self._mock_react(
                state,
                recorder,
                budget,
                need_compare=need_compare,
                need_freshness=need_freshness,
                supplement_needed=supplement_needed,
            )
        return self._llm_loop(
            state,
            recorder,
            budget,
            mode=RouteMode.react,
            tool_names=self.registry.names(),
            tool_choice="auto",
            hints={
                "need_compare": need_compare,
                "need_freshness": need_freshness,
                "supplement_needed": supplement_needed,
            },
        )

    def _mock_react(
        self,
        state: _LoopState,
        recorder: TraceRecorder,
        budget: DeadlineBudget,
        *,
        need_compare: bool,
        need_freshness: bool,
        supplement_needed: bool,
    ) -> dict[str, Any]:
        max_steps = self.settings.react_max_steps
        for step in range(1, max_steps + 1):
            if not budget.allow_model():
                return self._deadline_exhausted(recorder, state)
            recorder.bump_model()

            # 1) exposure history → demote recently shown
            if not state.exposure_fetched:
                obs = self._exec_tool(
                    state, "get_exposure_history", {"limit": 10}, budget, recorder, step,
                    reason="observe_exposure_history",
                )
                if obs is None:
                    return self._deadline_exhausted(recorder, state)
                state.exposure_fetched = True
                if obs.get("ok"):
                    items = (obs.get("data") or {}).get("items") or []
                    state.demote_ids |= {
                        str(it["merchant_id"])
                        for it in items
                        if it.get("merchant_id") and it.get("event_type") in {"impression", "not_interested"}
                    }
                    if obs.get("evidence_id"):
                        state.tool_evidence_ids.append(obs["evidence_id"])
                        state.evidence[obs["evidence_id"]] = Evidence(
                            evidence_id=obs["evidence_id"],
                            facts={"exposure": True, "items": items},
                            source="tool",
                            subject_type="tool",
                            subject_id=obs["evidence_id"],
                            candidate_set_version=state.version,
                        )
                continue

            if need_freshness and not state.refreshed and state.candidates:
                mid = state.candidates[0].merchant_id
                obs = self._exec_tool(
                    state,
                    "get_merchant_detail",
                    {"merchant_id": mid},
                    budget,
                    recorder,
                    step,
                    reason="need_freshness_refresh",
                )
                if obs is None:
                    return self._deadline_exhausted(recorder, state)
                state.refreshed = True
                if obs.get("ok"):
                    self._ingest_merchant_detail(state, mid, obs)
                continue

            if need_compare and not state.compared and state.snapshot.short_term and len(state.candidates) >= 2:
                short = max(state.snapshot.short_term, key=lambda x: x.confidence).topic
                left = state.candidates[0].merchant_id
                right_cand = next((c for c in state.candidates if c.category == short), None)
                if right_cand and right_cand.merchant_id != left:
                    obs = self._exec_tool(
                        state,
                        "compare_candidates",
                        {
                            "left_id": left,
                            "right_id": right_cand.merchant_id,
                            "aspects": ["price", "distance", "category"],
                        },
                        budget,
                        recorder,
                        step,
                        reason="need_compare_short_term_intent",
                    )
                    if obs is None:
                        return self._deadline_exhausted(recorder, state)
                    state.compared = True
                    if obs.get("ok"):
                        eid = obs["evidence_id"]
                        state.evidence[eid] = Evidence(
                            evidence_id=eid,
                            facts={"compare": True, "data": obs.get("data", {})},
                            source="tool",
                            subject_type="tool",
                            subject_id=eid,
                            candidate_set_version=state.version,
                        )
                        state.tool_evidence_ids.append(eid)
                        state.compare_pick = right_cand.merchant_id
                    continue
                state.compared = True

            if (
                supplement_needed
                and not state.supplemented
                and len(state.candidates) < self.settings.supplement_min
            ):
                obs = self._exec_tool(
                    state,
                    "supplement_recall",
                    {"strategy": "widen_radius"},
                    budget,
                    recorder,
                    step,
                    reason="insufficient_candidates",
                    timeout_ms=self.settings.supplement_timeout_ms,
                )
                if obs is None:
                    return self._deadline_exhausted(recorder, state)
                state.supplemented = True
                if obs.get("ok"):
                    self._apply_supplement(state, obs)
                continue

            if not state.products_fetched and state.candidates:
                ordered = self._order_candidates(state)
                target = next((c for c in ordered if c.products), None)
                if target:
                    pid = target.products[0].product_id
                    obs = self._exec_tool(
                        state,
                        "get_product_detail",
                        {"product_id": pid},
                        budget,
                        recorder,
                        step,
                        reason="refresh_product_facts",
                    )
                    if obs is None:
                        return self._deadline_exhausted(recorder, state)
                    state.products_fetched = True
                    if obs.get("ok") and obs.get("evidence_id"):
                        eid = obs["evidence_id"]
                        state.tool_evidence_ids.append(eid)
                        state.evidence[eid] = Evidence(
                            evidence_id=eid,
                            facts=dict(obs.get("data") or {}),
                            source="tool",
                            subject_type="product",
                            subject_id=pid,
                            candidate_set_version=state.version,
                            expires_at=target.expires_at,
                        )
                    continue
                state.products_fetched = True

            draft = self._mock_select(
                state.snapshot,
                state.candidates,
                prefer_short_term=True,
                prefer_merchant_id=state.compare_pick,
                extra_evidence_ids=state.tool_evidence_ids,
                demote_ids=state.demote_ids,
            )
            packed = self._finalize_submit(
                state,
                recorder,
                draft,
                self_score=0.88,
                step_index=step,
                reason_code="react_submit",
            )
            if packed.get("ok") or packed.get("fallback_reason"):
                return packed
            # Guard/confidence reject → retry once with baseline-ish soft select
            if state.submit_failures >= self.settings.max_submit_failures:
                return packed
            # continue to allow another submit attempt with safer draft
            draft = self._mock_select(
                state.snapshot,
                state.candidates,
                prefer_short_term=False,
                extra_evidence_ids=state.tool_evidence_ids,
                demote_ids=state.demote_ids,
            )
            return self._finalize_submit(
                state,
                recorder,
                draft,
                self_score=0.7,
                step_index=step + 1,
                reason_code="react_submit_retry",
            )

        recorder.set_meta(fallback_reason="max_steps")
        return self._fail_pack(recorder, state, ["max_steps"], "max_steps")

    # --- Real LLM tool loop ---

    def _llm_loop(
        self,
        state: _LoopState,
        recorder: TraceRecorder,
        budget: DeadlineBudget,
        *,
        mode: RouteMode,
        tool_names: list[str],
        tool_choice: Any,
        hints: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.llm.configured():
            recorder.set_meta(fallback_reason="llm_not_configured")
            recorder.add_step(
                step_index=1,
                decision_reason_code="llm_not_configured",
                result_status="error",
            )
            return self._fail_pack(recorder, state, ["llm_not_configured"], "llm_not_configured")

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": build_system_prompt(
                    mode=mode.value, max_merchants=self.settings.max_merchants
                ),
            },
            {
                "role": "user",
                "content": build_user_payload(
                    snapshot_summary=self._snapshot_summary(state.snapshot),
                    scene=state.scene.model_dump(),
                    candidates=[self._cand_brief(c) for c in state.candidates],
                    hints=hints,
                ),
            },
        ]
        tools = self.llm.tools_for(tool_names)
        # DIRECT: allow one Guard-reject retry (submit → repair → submit).
        max_steps = 2 if mode == RouteMode.direct else self.settings.react_max_steps

        for step in range(1, max_steps + 1):
            if not budget.allow_model():
                return self._deadline_exhausted(recorder, state)
            recorder.bump_model()
            is_last = step >= max_steps
            if mode == RouteMode.direct:
                choice: Any = tool_choice
            elif is_last:
                choice = {
                    "type": "function",
                    "function": {"name": "submit_recommendation"},
                }
            elif step == 1:
                choice = tool_choice
            else:
                choice = "auto"
            try:
                resp = self.llm.complete(
                    messages,
                    tools=tools,
                    tool_choice=choice,
                    timeout_s=self.settings.llm_timeout_s,
                )
            except LLMError as e:
                err = str(e)
                reason = "llm_invalid_api_key" if "invalid_api_key" in err or "Incorrect API key" in err else "llm_error"
                recorder.set_meta(fallback_reason=reason)
                recorder.add_step(
                    step_index=step,
                    decision_reason_code=reason,
                    result_status="error",
                    extra={"error": err[:400]},
                )
                return self._fail_pack(recorder, state, [err], reason)

            if not resp.tool_calls:
                # Try parse free-form JSON submit as last resort
                draft = self._repair_draft(self._parse_content_submit(resp.content or "") or [], state)
                if draft:
                    packed = self._finalize_submit(
                        state,
                        recorder,
                        draft,
                        self_score=None,
                        step_index=step,
                        reason_code="llm_content_submit",
                    )
                    if packed.get("ok") or state.submit_failures >= self.settings.max_submit_failures:
                        return packed
                    messages.append({"role": "assistant", "content": resp.content or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": "Submission rejected. Call submit_recommendation with corrected merchants and evidence_ids.",
                        }
                    )
                    continue
                recorder.add_step(
                    step_index=step,
                    decision_reason_code="llm_no_tool_call",
                    result_status="error",
                )
                return self._fail_pack(recorder, state, ["llm_no_tool_call"], "llm_no_tool_call")

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": resp.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in resp.tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in resp.tool_calls:
                if tc.name == "submit_recommendation":
                    draft = self._repair_draft(
                        self._merchants_from_submit_args(tc.arguments, state), state
                    )
                    score = tc.arguments.get("self_score")
                    try:
                        score_f = float(score) if score is not None else None
                    except (TypeError, ValueError):
                        score_f = None
                    packed = self._finalize_submit(
                        state,
                        recorder,
                        draft,
                        self_score=score_f,
                        step_index=step,
                        reason_code="llm_submit",
                        decision=str(tc.arguments.get("decision") or "recommend"),
                    )
                    if packed.get("ok"):
                        return packed
                    if state.submit_failures >= self.settings.max_submit_failures:
                        return packed
                    # feed guard errors back with concrete allowed ids
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(
                                {
                                    "ok": False,
                                    "errors": packed.get("errors"),
                                    "hint": (
                                        "只使用 closed candidates 中的 merchant_id/product_id；"
                                        "evidence_ids 必须来自候选 evidence_id 或工具返回；"
                                        "理由须含证据中的距离米数与价格元。"
                                    ),
                                    "allowed_merchant_ids": sorted(
                                        {c.merchant_id for c in state.candidates}
                                    )[:20],
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue

                # Non-submit tools
                timeout = (
                    self.settings.supplement_timeout_ms
                    if tc.name == "supplement_recall"
                    else self.settings.tool_timeout_ms
                )
                if not budget.allow_tool(timeout):
                    return self._deadline_exhausted(recorder, state)
                obs = self.tools.execute(
                    tc.name,
                    tc.arguments,
                    allowed_ids=state.allowed,
                    snapshot=state.snapshot,
                    scene=state.scene,
                    candidates=state.candidates,
                )
                self._apply_tool_side_effects(state, tc.name, obs)
                recorder.add_step(
                    step_index=step,
                    decision_reason_code=f"llm_tool_{tc.name}",
                    tool_name=tc.name,
                    result_status="ok" if obs.get("ok") else "error",
                    evidence_ids=[obs["evidence_id"]] if obs.get("evidence_id") else [],
                    actions=[{"tool": tc.name, "arguments": tc.arguments}],
                    observations=[obs],
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(obs, ensure_ascii=False, default=str),
                    }
                )

        recorder.set_meta(fallback_reason="max_steps")
        return self._fail_pack(recorder, state, ["max_steps"], "max_steps")

    # --- shared helpers ---

    def _exec_tool(
        self,
        state: _LoopState,
        name: str,
        args: dict[str, Any],
        budget: DeadlineBudget,
        recorder: TraceRecorder,
        step: int,
        *,
        reason: str,
        timeout_ms: int | None = None,
    ) -> dict[str, Any] | None:
        to = timeout_ms if timeout_ms is not None else self.settings.tool_timeout_ms
        if not budget.allow_tool(to):
            return None
        obs = self.tools.execute(
            name,
            args,
            allowed_ids=state.allowed,
            snapshot=state.snapshot,
            scene=state.scene,
            candidates=state.candidates,
        )
        recorder.add_step(
            step_index=step,
            decision_reason_code=reason,
            tool_name=name,
            result_status="ok" if obs.get("ok") else "error",
            evidence_ids=[obs["evidence_id"]] if obs.get("evidence_id") else [],
            actions=[{"tool": name, "arguments": args}],
            observations=[obs],
        )
        return obs

    def _ingest_merchant_detail(self, state: _LoopState, mid: str, obs: dict[str, Any]) -> None:
        eid = obs["evidence_id"]
        cand = state.by_id.get(mid)
        state.evidence[eid] = Evidence(
            evidence_id=eid,
            facts=dict(obs.get("data") or {}),
            source="tool",
            subject_type="merchant",
            subject_id=mid,
            candidate_set_version=state.version,
            expires_at=cand.expires_at if cand else "",
        )
        state.tool_evidence_ids.append(eid)

    def _apply_supplement(self, state: _LoopState, obs: dict[str, Any]) -> None:
        new_raw = obs["data"]["new_candidates"]
        new_cands = [CandidateMerchant(**c) for c in new_raw]
        if not new_cands:
            return
        state.version = obs["data"].get("candidate_set_version") or state.version
        state.candidates = state.candidates + new_cands
        state.allowed = self._allowed_ids(state.candidates)
        state.by_id = {c.merchant_id: c for c in state.candidates}
        kept = {
            k: v
            for k, v in state.evidence.items()
            if v.subject_type in {"snapshot", "tool"}
        }
        state.evidence = kept
        state.evidence.update(
            self.guard.build_evidence_index(
                state.candidates, candidate_set_version=state.version
            )
        )
        if obs.get("evidence_id"):
            eid = obs["evidence_id"]
            state.evidence[eid] = Evidence(
                evidence_id=eid,
                facts={"supplement": True, "strategy": obs["data"].get("strategy")},
                source="tool",
                subject_type="tool",
                subject_id=eid,
                candidate_set_version=state.version,
            )
            state.tool_evidence_ids.append(eid)

    def _apply_tool_side_effects(
        self, state: _LoopState, name: str, obs: dict[str, Any]
    ) -> None:
        if not obs.get("ok"):
            return
        if name == "get_merchant_detail":
            mid = str((obs.get("data") or {}).get("merchant_id") or "")
            if mid:
                self._ingest_merchant_detail(state, mid, obs)
        elif name == "get_product_detail":
            eid = obs.get("evidence_id")
            if eid:
                state.tool_evidence_ids.append(eid)
                state.evidence[eid] = Evidence(
                    evidence_id=eid,
                    facts=dict(obs.get("data") or {}),
                    source="tool",
                    subject_type="product",
                    subject_id=str((obs.get("data") or {}).get("product_id") or ""),
                    candidate_set_version=state.version,
                )
        elif name == "compare_candidates":
            eid = obs.get("evidence_id")
            if eid:
                state.tool_evidence_ids.append(eid)
                state.evidence[eid] = Evidence(
                    evidence_id=eid,
                    facts={"compare": True, "data": obs.get("data", {})},
                    source="tool",
                    subject_type="tool",
                    subject_id=eid,
                    candidate_set_version=state.version,
                )
        elif name == "get_exposure_history":
            items = (obs.get("data") or {}).get("items") or []
            state.demote_ids |= {
                str(it["merchant_id"])
                for it in items
                if it.get("merchant_id") and it.get("event_type") in {"impression", "not_interested"}
            }
            eid = obs.get("evidence_id")
            if eid:
                state.tool_evidence_ids.append(eid)
                state.evidence[eid] = Evidence(
                    evidence_id=eid,
                    facts={"exposure": True, "items": items},
                    source="tool",
                    subject_type="tool",
                    subject_id=eid,
                    candidate_set_version=state.version,
                )
        elif name == "supplement_recall":
            self._apply_supplement(state, obs)

    def _finalize_submit(
        self,
        state: _LoopState,
        recorder: TraceRecorder,
        draft: list[RecommendedMerchant],
        *,
        self_score: float | None,
        step_index: int,
        reason_code: str,
        decision: str = "recommend",
    ) -> dict[str, Any]:
        # Protocol: always go through submit_recommendation tool
        payload = {
            "decision": decision,
            "merchants": [m.model_dump() for m in draft],
            "self_score": self_score,
        }
        obs = self.tools.execute(
            "submit_recommendation",
            payload,
            allowed_ids=state.allowed,
            snapshot=state.snapshot,
            scene=state.scene,
            candidates=state.candidates,
        )
        recorder.add_step(
            step_index=step_index,
            decision_reason_code=reason_code,
            tool_name="submit_recommendation",
            result_status="ok" if obs.get("ok") else "error",
            evidence_ids=[eid for m in draft for eid in m.evidence_ids],
            actions=[{"tool": "submit_recommendation"}],
            observations=[obs],
            extra={"self_score": self_score, "decision": decision},
        )

        if decision == "skip" or not draft:
            recorder.add_guard({"ok": True, "errors": [], "soft_dropped": [], "skipped": True})
            state.self_score = self_score
            return {
                "ok": True,
                "merchants": [],
                "errors": [],
                "steps": recorder.steps,
                "model_calls": recorder.model_calls,
                "candidates": state.candidates,
                "evidence_index": state.evidence,
                "self_score": self_score,
                "trace": recorder.summary(),
            }

        thr = self.settings.display_confidence_threshold
        if thr is not None and self_score is not None and self_score < thr:
            state.submit_failures += 1
            recorder.add_guard(
                {
                    "ok": False,
                    "errors": [f"confidence_below_threshold:{self_score}<{thr}"],
                    "soft_dropped": [],
                }
            )
            if state.submit_failures >= self.settings.max_submit_failures:
                recorder.set_meta(fallback_reason="submit_arbitration")
                return self._fail_pack(
                    recorder,
                    state,
                    [f"confidence_below_threshold:{self_score}<{thr}"],
                    "submit_arbitration",
                )
            return {
                "ok": False,
                "merchants": [],
                "errors": [f"confidence_below_threshold:{self_score}<{thr}"],
                "steps": recorder.steps,
                "model_calls": recorder.model_calls,
                "candidates": state.candidates,
                "evidence_index": state.evidence,
                "self_score": self_score,
                "trace": recorder.summary(),
            }

        gr = self.guard.validate_submission(
            draft,
            state.allowed,
            state.by_id,
            state.evidence,
            candidate_set_version=state.version,
            now=utc_now(),
        )
        recorder.add_guard({"ok": gr.ok, "errors": gr.errors, "soft_dropped": gr.soft_dropped})
        if gr.ok and gr.merchants:
            state.self_score = self_score
            return {
                "ok": True,
                "merchants": gr.merchants,
                "errors": gr.errors,
                "steps": recorder.steps,
                "model_calls": recorder.model_calls,
                "candidates": state.candidates,
                "evidence_index": state.evidence,
                "self_score": self_score,
                "trace": recorder.summary(),
            }

        state.submit_failures += 1
        if state.submit_failures >= self.settings.max_submit_failures:
            recorder.set_meta(fallback_reason="submit_arbitration")
            return self._fail_pack(
                recorder, state, gr.errors or ["submit_rejected"], "submit_arbitration"
            )
        return {
            "ok": False,
            "merchants": [],
            "errors": gr.errors,
            "steps": recorder.steps,
            "model_calls": recorder.model_calls,
            "candidates": state.candidates,
            "evidence_index": state.evidence,
            "self_score": self_score,
            "trace": recorder.summary(),
        }

    def _deadline_exhausted(self, recorder: TraceRecorder, state: _LoopState) -> dict[str, Any]:
        recorder.set_meta(fallback_reason="deadline_exhausted", budget_exhausted=True)
        recorder.add_step(
            step_index=len(recorder.steps) + 1,
            decision_reason_code="deadline_exhausted",
            result_status="budget_exhausted",
        )
        return self._fail_pack(recorder, state, ["deadline_exhausted"], "deadline_exhausted")

    def _fail_pack(
        self,
        recorder: TraceRecorder,
        state: _LoopState,
        errors: list[str],
        fallback_reason: str,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "merchants": [],
            "errors": errors,
            "steps": recorder.steps,
            "model_calls": recorder.model_calls,
            "candidates": state.candidates,
            "evidence_index": state.evidence,
            "fallback_reason": fallback_reason,
            "self_score": state.self_score,
            "trace": recorder.summary(),
        }

    def _allowed_ids(self, candidates: list[CandidateMerchant]) -> set[str]:
        ids: set[str] = set()
        for c in candidates:
            ids.add(c.merchant_id)
            for p in c.products:
                ids.add(p.product_id)
        return ids

    def _order_candidates(self, state: _LoopState) -> list[CandidateMerchant]:
        return self._sorted_pool(
            state.candidates,
            prefer_short_term=True,
            prefer_merchant_id=state.compare_pick,
            demote_ids=state.demote_ids,
            snapshot=state.snapshot,
        )

    def _sorted_pool(
        self,
        candidates: list[CandidateMerchant],
        *,
        prefer_short_term: bool,
        prefer_merchant_id: str | None,
        demote_ids: set[str],
        snapshot: UserSnapshot,
        prefer_soft_tags: bool = False,
    ) -> list[CandidateMerchant]:
        ordered = list(candidates)
        if prefer_merchant_id and snapshot.short_term:
            topic = max(snapshot.short_term, key=lambda x: x.confidence).topic
            preferred = [c for c in candidates if c.category == topic]
            pool = preferred if preferred else list(candidates)
            ordered = sorted(
                pool,
                key=lambda c: (
                    1 if c.merchant_id in demote_ids else 0,
                    0 if c.merchant_id == prefer_merchant_id else 1,
                    -c.score,
                ),
            )
        elif prefer_short_term and snapshot.short_term:
            topic = max(snapshot.short_term, key=lambda x: x.confidence).topic
            ordered = sorted(
                candidates,
                key=lambda c: (
                    1 if c.merchant_id in demote_ids else 0,
                    0 if c.category == topic else 1,
                    -c.score,
                ),
            )
        elif prefer_soft_tags and snapshot.preferred_tags:
            ordered = sorted(
                candidates,
                key=lambda c: (
                    1 if c.merchant_id in demote_ids else 0,
                    0
                    if any(t in c.tags or c.category == t for t in snapshot.preferred_tags)
                    else 1,
                    -c.score,
                ),
            )
        else:
            ordered = sorted(
                candidates,
                key=lambda c: (1 if c.merchant_id in demote_ids else 0, -c.score),
            )
        return ordered

    def _mock_select(
        self,
        snapshot: UserSnapshot,
        candidates: list[CandidateMerchant],
        prefer_short_term: bool,
        prefer_soft_tags: bool = False,
        prefer_merchant_id: str | None = None,
        extra_evidence_ids: list[str] | None = None,
        demote_ids: set[str] | None = None,
    ) -> list[RecommendedMerchant]:
        demote = demote_ids or set()
        ordered = self._sorted_pool(
            candidates,
            prefer_short_term=prefer_short_term,
            prefer_merchant_id=prefer_merchant_id,
            demote_ids=demote,
            snapshot=snapshot,
            prefer_soft_tags=prefer_soft_tags,
        )
        extra = list(extra_evidence_ids or [])
        out: list[RecommendedMerchant] = []
        for c in ordered[:3]:
            if not c.products:
                continue
            p = c.products[0]
            eids = [c.evidence_id or f"cand_{c.merchant_id}"]
            detail_eid = f"detail_{c.merchant_id}"
            prod_eid = f"prod_{p.product_id}"
            cmp_ids = [eid for eid in extra if eid.startswith("cmp_")]
            expo_ids = [eid for eid in extra if eid.startswith("expo_")]
            if prefer_merchant_id and c.merchant_id == prefer_merchant_id and cmp_ids:
                eids.extend(cmp_ids)
                reason = (
                    f"对比后更贴合近期兴趣，距离约 {int(c.distance_m)} 米，"
                    f"人均约 {int(c.avg_price)} 元"
                )
            elif detail_eid in extra:
                eids.append(detail_eid)
                reason = (
                    f"已刷新营业与距离证据，距离约 {int(c.distance_m)} 米，"
                    f"人均约 {int(c.avg_price)} 元"
                )
            elif "supp_batch" in extra and c.provenance == "supplement":
                eids.append("supp_batch")
                reason = (
                    f"补召回后入选，距离约 {int(c.distance_m)} 米，"
                    f"人均约 {int(c.avg_price)} 元"
                )
            elif c.merchant_id in demote and expo_ids:
                eids.extend(expo_ids[:1])
                eids.append("snap_history")
                reason = (
                    f"避开近期曝光后重排，距离约 {int(c.distance_m)} 米，"
                    f"人均约 {int(c.avg_price)} 元"
                )
            elif c.merchant_id in snapshot.frequent_merchants:
                eids.append("snap_history")
                reason = (
                    f"近 30 天偏好相关，当前营业，距离约 {int(c.distance_m)} 米在配送范围内"
                )
            else:
                eids.append("snap_history")
                reason = (
                    f"与当前场景匹配，距离约 {int(c.distance_m)} 米，"
                    f"人均约 {int(c.avg_price)} 元"
                )
            prod_eids = [prod_eid, "snap_price_band"]
            if prod_eid in extra:
                # cite refreshed product observation
                pass
            out.append(
                RecommendedMerchant(
                    merchant_id=c.merchant_id,
                    name=c.name,
                    reason=reason,
                    evidence_ids=eids,
                    products=[
                        RecommendedProduct(
                            product_id=p.product_id,
                            name=p.name,
                            reason=f"有库存，价格 {int(p.price)} 元落在可接受区间",
                            evidence_ids=prod_eids,
                        )
                    ],
                )
            )
        return out

    def _merchants_from_submit_args(
        self, args: dict[str, Any], state: _LoopState
    ) -> list[RecommendedMerchant]:
        raw = args.get("merchants") or []
        out: list[RecommendedMerchant] = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                m = RecommendedMerchant.model_validate(item)
            except Exception:  # noqa: BLE001
                mid = str(item.get("merchant_id") or "")
                cand = state.by_id.get(mid)
                if not cand or not cand.products:
                    continue
                p = cand.products[0]
                m = RecommendedMerchant(
                    merchant_id=mid,
                    name=cand.name,
                    reason=str(item.get("reason") or "模型推荐"),
                    evidence_ids=list(item.get("evidence_ids") or [cand.evidence_id or f"cand_{mid}"]),
                    products=[
                        RecommendedProduct(
                            product_id=p.product_id,
                            name=p.name,
                            reason=f"价格 {int(p.price)} 元",
                            evidence_ids=[f"prod_{p.product_id}"],
                        )
                    ],
                )
            out.append(m)
        return out

    def _evidence_ok_for_merchant(self, eid: str, state: _LoopState, merchant_id: str) -> bool:
        # Never allow product evidence on merchant reasons (Guard evidence_subject).
        if eid.startswith("prod_"):
            return False
        ev = state.evidence.get(eid)
        if ev is None:
            return eid in {
                f"cand_{merchant_id}",
                "snap_history",
            } or eid.startswith(("cmp_", "detail_", "expo_", "supp_"))
        if ev.subject_type == "merchant":
            return ev.subject_id == merchant_id
        return ev.subject_type in {"tool", "snapshot"}

    def _evidence_ok_for_product(
        self, eid: str, state: _LoopState, product_id: str, merchant_id: str
    ) -> bool:
        if eid.startswith(("cand_", "detail_", "cmp_", "expo_", "supp_")):
            return False
        ev = state.evidence.get(eid)
        if ev is None:
            return eid in {f"prod_{product_id}", "snap_price_band"}
        if ev.subject_type == "product":
            return ev.subject_id == product_id
        return ev.subject_type == "snapshot"

    def _repair_draft(
        self, draft: list[RecommendedMerchant], state: _LoopState
    ) -> list[RecommendedMerchant]:
        """Fill missing names / evidence / numeric reasons so Guard is less brittle for real LLMs."""
        repaired: list[RecommendedMerchant] = []
        for m in draft:
            cand = state.by_id.get(m.merchant_id)
            if not cand:
                continue
            base_eid = cand.evidence_id or f"cand_{cand.merchant_id}"
            eids = [
                eid
                for eid in (m.evidence_ids or [])
                if self._evidence_ok_for_merchant(eid, state, cand.merchant_id)
            ]
            if base_eid not in eids:
                eids.insert(0, base_eid)
            for eid in state.tool_evidence_ids:
                if eid.startswith(("cmp_", "detail_", "expo_", "supp_")) and eid not in eids:
                    if self._evidence_ok_for_merchant(eid, state, cand.merchant_id):
                        eids.append(eid)
            if not eids:
                eids = [base_eid]
            if "snap_history" not in eids:
                eids.append("snap_history")
            reason = (m.reason or "").strip()
            if "米" not in reason or "元" not in reason:
                reason = (
                    f"{reason + '；' if reason else ''}"
                    f"距离约 {int(cand.distance_m)} 米，人均约 {int(cand.avg_price)} 元"
                )
            products: list[RecommendedProduct] = []
            for p in m.products or []:
                pc = next((x for x in cand.products if x.product_id == p.product_id), None)
                if pc is None and cand.products:
                    pc = cand.products[0]
                    p = RecommendedProduct(
                        product_id=pc.product_id,
                        name=pc.name,
                        reason=p.reason or f"有库存，价格 {int(pc.price)} 元落在可接受区间",
                        evidence_ids=p.evidence_ids,
                    )
                if pc is None:
                    continue
                prod_eid = f"prod_{pc.product_id}"
                peids = [
                    eid
                    for eid in (p.evidence_ids or [])
                    if self._evidence_ok_for_product(eid, state, pc.product_id, cand.merchant_id)
                ]
                if prod_eid not in peids:
                    peids.insert(0, prod_eid)
                if "snap_price_band" not in peids:
                    peids.append("snap_price_band")
                if not peids:
                    peids = [prod_eid, "snap_price_band"]
                preason = (p.reason or "").strip()
                if "元" not in preason:
                    preason = f"{preason + '，' if preason else ''}价格 {int(pc.price)} 元"
                products.append(
                    RecommendedProduct(
                        product_id=pc.product_id,
                        name=p.name or pc.name,
                        reason=preason,
                        evidence_ids=peids,
                    )
                )
            if not products and cand.products:
                pc = cand.products[0]
                products = [
                    RecommendedProduct(
                        product_id=pc.product_id,
                        name=pc.name,
                        reason=f"有库存，价格 {int(pc.price)} 元落在可接受区间",
                        evidence_ids=[f"prod_{pc.product_id}", "snap_price_band"],
                    )
                ]
            if not products:
                continue
            repaired.append(
                RecommendedMerchant(
                    merchant_id=cand.merchant_id,
                    name=m.name or cand.name,
                    reason=reason,
                    evidence_ids=eids,
                    products=products[: self.settings.max_products_per_merchant],
                )
            )
            if len(repaired) >= self.settings.max_merchants:
                break
        return repaired

    def _parse_content_submit(self, content: str) -> list[RecommendedMerchant] | None:
        text = content.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        merchants = data.get("merchants") if isinstance(data, dict) else None
        if not isinstance(merchants, list):
            return None
        out: list[RecommendedMerchant] = []
        for item in merchants:
            if isinstance(item, dict):
                try:
                    out.append(RecommendedMerchant.model_validate(item))
                except Exception:  # noqa: BLE001
                    continue
        return out or None

    def _snapshot_summary(self, snapshot: UserSnapshot) -> dict[str, Any]:
        return {
            "user_id": snapshot.user_id,
            "long_term_categories": snapshot.long_term_categories,
            "preferred_tags": snapshot.preferred_tags,
            "frequent_merchants": snapshot.frequent_merchants,
            "short_term": [s.model_dump() for s in snapshot.short_term],
            "hard": snapshot.hard.model_dump(),
            "runtime": {
                "exposure_merchant_ids": snapshot.runtime.exposure_merchant_ids,
                "recent_shown_categories": snapshot.runtime.recent_shown_categories,
            },
        }

    def _cand_brief(self, c: CandidateMerchant) -> dict[str, Any]:
        return {
            "merchant_id": c.merchant_id,
            "name": c.name,
            "category": c.category,
            "avg_price": c.avg_price,
            "distance_m": c.distance_m,
            "is_open": c.is_open,
            "score": c.score,
            "tags": c.tags,
            "evidence_id": c.evidence_id,
            "products": [
                {
                    "product_id": p.product_id,
                    "name": p.name,
                    "price": p.price,
                    "in_stock": p.in_stock,
                }
                for p in c.products[:3]
            ],
        }
