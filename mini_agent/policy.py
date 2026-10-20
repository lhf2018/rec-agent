from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mini_agent.config import Settings, get_settings
from mini_agent.models import (
    EngineOutput,
    OpportunityResult,
    RouteMode,
    Scene,
    UserSnapshot,
)


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


def _parse_hour(occurred_at: str) -> int:
    # supports ISO with timezone
    try:
        dt = datetime.fromisoformat(occurred_at)
        return dt.hour
    except ValueError:
        return 12


class Policy:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def check_eligibility(self, snapshot: UserSnapshot, scene: Scene) -> EligibilityResult:
        if not snapshot.allow_personalized:
            return EligibilityResult(False, "not_authorized")
        hour = _parse_hour(scene.occurred_at)
        start, end = self.settings.dnd_start_hour, self.settings.dnd_end_hour
        in_dnd = hour >= start or hour < end if start > end else start <= hour < end
        if in_dnd:
            return EligibilityResult(False, "dnd_window")
        if scene.location is None:
            return EligibilityResult(False, "invalid_scene")
        if snapshot.category_opt_out and set(snapshot.category_opt_out) & set(snapshot.long_term_categories):
            # simplified: if opted out of all interest cats
            pass
        # freq / cooldown checked via recent_impressions length as MVP proxy
        if len(snapshot.recent_impressions) >= self.settings.freq_cap_per_day:
            return EligibilityResult(False, "freq_cap")
        return EligibilityResult(True, None)

    def check_opportunity(self, snapshot: UserSnapshot, scene: Scene) -> OpportunityDecision:
        # similar suppress: recent impressions overlap frequent / short-term topics heavily
        if snapshot.recent_conversions:
            return OpportunityDecision(OpportunityResult.suppress, "recent_conversion_suppress")
        # short-interval duplicate: if same scene_id pattern already impressed (MVP: impressions contain scene)
        if scene.scene_id in snapshot.recent_impressions:
            return OpportunityDecision(OpportunityResult.suppress, "duplicate_scene_suppress")
        # if user just marked not interested on many nearby options — still allow engine, hard filter handles
        return OpportunityDecision(OpportunityResult.recommend_now, "opportunity_ok")

    def route(
        self,
        engine: EngineOutput,
        budget_ok: bool = True,
        enable_llm: bool = True,
        enable_react: bool = True,
    ) -> RouteDecision:
        if not engine.candidates:
            return RouteDecision(RouteMode.skip, "no_candidates")
        if not budget_ok:
            return RouteDecision(RouteMode.fallback, "budget_exhausted")
        if not enable_llm:
            return RouteDecision(RouteMode.rule_engine, "llm_disabled")

        need_react = (
            engine.intent_conflict or engine.need_freshness or len(engine.candidates) < 2
        )
        if need_react and enable_react:
            return RouteDecision(RouteMode.react, "react_gap")
        if engine.facts_complete and not engine.intent_conflict and not engine.need_freshness:
            # prefer rule engine when fully clear
            return RouteDecision(RouteMode.rule_engine, "facts_complete_no_conflict")
        return RouteDecision(RouteMode.direct, "need_natural_reason")

    def check_delivery(
        self,
        snapshot: UserSnapshot,
        merchant_ids: list[str],
    ) -> DeliveryResult:
        # re-check authorization
        if not snapshot.allow_personalized:
            return DeliveryResult(False, "delivery_blocked_not_authorized")
        for mid in merchant_ids:
            if mid in snapshot.hard.not_interested_merchants:
                return DeliveryResult(False, "delivery_blocked_not_interested")
            if mid in snapshot.hard.blocked_merchants:
                return DeliveryResult(False, "delivery_blocked_blocked")
        return DeliveryResult(True, None)


def template_reason(c) -> str:
    return (
        f"与当前场景匹配，距离约 {int(c.distance_m)} 米，"
        f"人均约 {int(c.avg_price)} 元，排序靠前"
    )
