from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from rec_agent.config import Settings, get_settings
from rec_agent.models import (
    EngineOutput,
    OpportunityResult,
    RouteMode,
    Scene,
    UserSnapshot,
)
from rec_agent.timeutil import parse_hour_in_zone, to_utc, utc_now


@dataclass
class EligibilityResult:
    ok: bool
    reason_code: str | None = None


@dataclass
class OpportunityDecision:
    result: OpportunityResult
    reason_code: str


@dataclass
class RouteDecision:
    mode: RouteMode
    reason_code: str


@dataclass
class DeliveryResult:
    ok: bool
    reason_code: str | None = None


class Policy:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def check_eligibility(self, snapshot: UserSnapshot, scene: Scene) -> EligibilityResult:
        if not snapshot.allow_personalized:
            return EligibilityResult(False, "not_authorized")
        if "*" in snapshot.category_opt_out or "active_recommend" in snapshot.category_opt_out:
            return EligibilityResult(False, "category_opt_out")

        hour = parse_hour_in_zone(scene.occurred_at)
        start, end = self.settings.dnd_start_hour, self.settings.dnd_end_hour
        in_dnd = hour >= start or hour < end if start > end else start <= hour < end
        if in_dnd:
            return EligibilityResult(False, "dnd_window")

        if scene.location is None:
            return EligibilityResult(False, "invalid_scene")

        if snapshot.runtime.delivery_count_in_window >= self.settings.freq_cap_per_day:
            return EligibilityResult(False, "freq_cap")

        last = to_utc(snapshot.runtime.last_success_at)
        if last is not None and self.settings.cooldown_minutes > 0:
            now = to_utc(scene.occurred_at) or utc_now()
            if now - last < timedelta(minutes=self.settings.cooldown_minutes):
                return EligibilityResult(False, "cooldown")

        return EligibilityResult(True, None)

    def check_opportunity(self, snapshot: UserSnapshot, scene: Scene) -> OpportunityDecision:
        if snapshot.recent_conversions:
            return OpportunityDecision(OpportunityResult.suppress, "recent_conversion_suppress")

        if snapshot.runtime.scene_seen_recently:
            return OpportunityDecision(OpportunityResult.suppress, "duplicate_scene_suppress")

        last = to_utc(snapshot.runtime.last_success_at)
        if (
            last is not None
            and snapshot.runtime.recent_shown_categories
            and snapshot.short_term
        ):
            now = to_utc(scene.occurred_at) or utc_now()
            if now - last < timedelta(minutes=self.settings.similar_suppress_minutes):
                short_topics = {i.topic for i in snapshot.short_term}
                if short_topics & set(snapshot.runtime.recent_shown_categories):
                    return OpportunityDecision(
                        OpportunityResult.suppress, "similar_category_suppress"
                    )

        if (
            len(snapshot.hard.not_interested_merchants) >= 5
            and not snapshot.short_term
            and not snapshot.frequent_merchants
        ):
            return OpportunityDecision(OpportunityResult.suppress, "sparse_after_negative")

        return OpportunityDecision(OpportunityResult.recommend_now, "opportunity_ok")

    def route(
        self,
        engine: EngineOutput,
        budget_ok: bool = True,
        enable_llm: bool = True,
        enable_react: bool = True,
        force_mode: RouteMode | None = None,
    ) -> RouteDecision:
        if force_mode is not None:
            return RouteDecision(force_mode, f"force_{force_mode.value.lower()}")

        if not engine.candidates:
            return RouteDecision(RouteMode.skip, "no_candidates")
        if not budget_ok:
            return RouteDecision(RouteMode.fallback, "budget_exhausted")
        if not enable_llm:
            return RouteDecision(RouteMode.rule_engine, "llm_disabled")

        # High complexity / evidence gaps → REACT (only when tools can help)
        need_react = (
            engine.intent_conflict
            or engine.need_freshness
            or engine.need_compare
            or engine.supplement_needed
        )
        if need_react and enable_react:
            reason = "react_gap"
            if engine.supplement_needed and not (
                engine.intent_conflict or engine.need_compare or engine.need_freshness
            ):
                reason = "supplement_needed"
            elif engine.need_compare:
                reason = "need_compare"
            elif engine.intent_conflict:
                reason = "intent_conflict"
            return RouteDecision(RouteMode.react, reason)

        # Medium: soft rerank / natural reason, no tool gaps → DIRECT
        if (
            engine.facts_complete
            and engine.soft_rerank
            and not engine.intent_conflict
            and not engine.need_freshness
            and not engine.need_compare
            and not engine.supplement_needed
        ):
            return RouteDecision(RouteMode.direct, "soft_rerank")

        # Low: complete facts, no gaps
        if engine.facts_complete and not engine.intent_conflict and not engine.need_freshness:
            return RouteDecision(RouteMode.rule_engine, "facts_complete_no_conflict")

        return RouteDecision(RouteMode.direct, "need_natural_reason")

    def check_delivery(
        self,
        snapshot: UserSnapshot,
        merchant_ids: list[str],
    ) -> DeliveryResult:
        if not snapshot.allow_personalized:
            return DeliveryResult(False, "delivery_blocked_not_authorized")
        for mid in merchant_ids:
            if mid in snapshot.hard.not_interested_merchants:
                return DeliveryResult(False, "delivery_blocked_not_interested")
            if mid in snapshot.hard.blocked_merchants:
                return DeliveryResult(False, "delivery_blocked_blocked")
        return DeliveryResult(True, None)

    def budget_ok(self, deadline_at: datetime, now: datetime | None = None) -> bool:
        from rec_agent.runtime.budget import DeadlineBudget

        return DeadlineBudget(deadline_at, self.settings).allow_model(now)


def template_reason(c) -> str:
    return (
        f"与当前场景匹配，距离约 {int(c.distance_m)} 米，"
        f"人均约 {int(c.avg_price)} 元，排序靠前"
    )


def rule_rerank(
    candidates: list,
    snapshot: UserSnapshot,
    limit: int = 3,
) -> list:
    """Deterministic preference/short-term rerank without LLM (eval baseline)."""
    short = None
    if snapshot.short_term:
        short = max(snapshot.short_term, key=lambda x: x.confidence).topic

    def key(c):
        score = 0
        if short and c.category == short:
            score += 100
        if any(t in c.tags or c.category == t for t in snapshot.preferred_tags):
            score += 50
        if c.merchant_id in snapshot.frequent_merchants:
            score += 20
        return (score, c.score)

    return sorted(candidates, key=key, reverse=True)[:limit]
