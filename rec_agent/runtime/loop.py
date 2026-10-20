from __future__ import annotations

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
from rec_agent.runtime.guard import Evidence, Guard
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
    ) -> dict[str, Any]:
        self.tools.reset()
        steps: list[dict[str, Any]] = []
        allowed = self._allowed_ids(candidates)
        evidence = self.guard.build_evidence_index(candidates)
        by_id = {c.merchant_id: c for c in candidates}
        model_calls = 0

        if mode == RouteMode.direct:
            model_calls += 1
            draft = self._mock_select(snapshot, candidates, prefer_short_term=False)
            steps.append(
                {
                    "step_index": 1,
                    "decision_reason_code": "direct_submit",
                    "actions": [{"tool": "submit_recommendation"}],
                    "observations": [],
                }
            )
            gr = self.guard.validate_submission(draft, allowed, by_id, evidence)
            return {
                "ok": gr.ok,
                "merchants": gr.merchants,
                "errors": gr.errors,
                "steps": steps,
                "model_calls": model_calls,
                "candidates": candidates,
                "evidence_index": evidence,
            }

        # REACT path
        max_steps = self.settings.react_max_steps
        draft: list[RecommendedMerchant] | None = None
        for step in range(1, max_steps + 1):
            model_calls += 1
            if step == 1 and snapshot.short_term:
                # compare top engine vs short-term match
                short = max(snapshot.short_term, key=lambda x: x.confidence).topic
                left = candidates[0].merchant_id if candidates else None
                right_cand = next((c for c in candidates if c.category == short), None)
                if left and right_cand and right_cand.merchant_id != left:
                    obs = self.tools.execute(
                        "compare_candidates",
                        {"left_id": left, "right_id": right_cand.merchant_id, "aspects": ["price", "distance", "category"]},
                        allowed_ids=allowed,
                        snapshot=snapshot,
                        scene=scene,
                        candidates=candidates,
                    )
                    if obs.get("ok"):
                        evidence[obs["evidence_id"]] = Evidence(
                            evidence_id=obs["evidence_id"],
                            facts={"compare": True, **obs.get("data", {})},
                            source="tool",
                        )
                    steps.append(
                        {
                            "step_index": step,
                            "decision_reason_code": "need_compare_short_term_intent",
                            "missing_evidence": ["compare"],
                            "actions": [{"tool": "compare_candidates"}],
                            "observations": [obs],
                        }
                    )
                    continue

            if len(candidates) < 1:
                obs = self.tools.execute(
                    "supplement_recall",
                    {"strategy": "widen_radius"},
                    allowed_ids=allowed,
                    snapshot=snapshot,
                    scene=scene,
                    candidates=candidates,
                )
                steps.append(
                    {
                        "step_index": step,
                        "decision_reason_code": "insufficient_candidates",
                        "actions": [{"tool": "supplement_recall"}],
                        "observations": [obs],
                    }
                )
                if obs.get("ok"):
                    new_raw = obs["data"]["new_candidates"]
                    new_cands = [CandidateMerchant(**c) for c in new_raw]
                    candidates = candidates + new_cands
                    allowed = self._allowed_ids(candidates)
                    by_id = {c.merchant_id: c for c in candidates}
                    evidence.update(self.guard.build_evidence_index(new_cands))
                continue

            # submit
            draft = self._mock_select(snapshot, candidates, prefer_short_term=True)
            steps.append(
                {
                    "step_index": step,
                    "decision_reason_code": "react_submit",
                    "actions": [{"tool": "submit_recommendation"}],
                    "observations": [],
                }
            )
            gr = self.guard.validate_submission(draft, allowed, by_id, evidence)
            return {
                "ok": gr.ok,
                "merchants": gr.merchants,
                "errors": gr.errors,
                "steps": steps,
                "model_calls": model_calls,
                "candidates": candidates,
                "evidence_index": evidence,
            }

        # exhausted
        return {
            "ok": False,
            "merchants": [],
            "errors": ["max_steps"],
            "steps": steps,
            "model_calls": model_calls,
            "candidates": candidates,
            "evidence_index": evidence,
            "fallback_reason": "max_steps",
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
