from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

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
    score: float = 0.0
    features: dict[str, Any] = field(default_factory=dict)


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

    def opportunity_features(self, snapshot: UserSnapshot, scene: Scene) -> dict[str, Any]:
        """Feature vector for future EV model; always logged with opportunity_events."""
        short_conf = (
            max((s.confidence for s in snapshot.short_term), default=0.0)
            if snapshot.short_term
            else 0.0
        )
        last = to_utc(snapshot.runtime.last_success_at)
        now = to_utc(scene.occurred_at) or utc_now()
        minutes_since_delivery = (
            (now - last).total_seconds() / 60.0 if last is not None else None
        )
        return {
            "has_recent_conversion": bool(snapshot.recent_conversions),
            "scene_seen_recently": bool(snapshot.runtime.scene_seen_recently),
            "delivery_count_in_window": int(snapshot.runtime.delivery_count_in_window),
            "not_interested_count": len(snapshot.hard.not_interested_merchants),
            "has_short_term": bool(snapshot.short_term),
            "short_term_confidence": round(float(short_conf), 4),
            "frequent_merchant_count": len(snapshot.frequent_merchants),
            "recent_shown_category_count": len(snapshot.runtime.recent_shown_categories),
            "exposure_count": len(snapshot.runtime.exposure_merchant_ids),
            "minutes_since_delivery": (
                round(minutes_since_delivery, 2) if minutes_since_delivery is not None else None
            ),
            "meal_period": scene.meal_period,
            "trigger": scene.trigger.value,
        }

    def score_opportunity(self, features: dict[str, Any]) -> float:
        """Heuristic opportunity score in [0, 1]. Hard rules still own final suppress/skip."""
        score = 0.62
        if features.get("has_short_term"):
            score += 0.12 * min(1.0, float(features.get("short_term_confidence") or 0.0))
        if int(features.get("frequent_merchant_count") or 0) > 0:
            score += 0.08
        if features.get("has_recent_conversion"):
            score -= 0.35
        if features.get("scene_seen_recently"):
            score -= 0.28
        ni = int(features.get("not_interested_count") or 0)
        if ni >= 5:
            score -= 0.2
        elif ni >= 2:
            score -= 0.08
        deliveries = int(features.get("delivery_count_in_window") or 0)
        if deliveries >= max(1, self.settings.freq_cap_per_day // 2):
            score -= 0.1
        mins = features.get("minutes_since_delivery")
        if mins is not None and mins < 30:
            score -= 0.12
        return round(max(0.0, min(1.0, score)), 4)

    def check_opportunity(self, snapshot: UserSnapshot, scene: Scene) -> OpportunityDecision:
        features = self.opportunity_features(snapshot, scene)
        score = self.score_opportunity(features)

        def decide(result: OpportunityResult, reason: str) -> OpportunityDecision:
            return OpportunityDecision(result, reason, score=score, features=features)

        if snapshot.recent_conversions:
            return decide(OpportunityResult.suppress, "recent_conversion_suppress")

        if snapshot.runtime.scene_seen_recently:
            return decide(OpportunityResult.suppress, "duplicate_scene_suppress")

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
                    return decide(OpportunityResult.suppress, "similar_category_suppress")

        if (
            len(snapshot.hard.not_interested_merchants) >= 5
            and not snapshot.short_term
            and not snapshot.frequent_merchants
        ):
            return decide(OpportunityResult.suppress, "sparse_after_negative")

        # Soft gate: optional score floor (off by default)
        thr = self.settings.opportunity_min_score
        if thr is not None and score < thr:
            return decide(OpportunityResult.suppress, "low_opportunity_score")

        return decide(OpportunityResult.recommend_now, "opportunity_ok")

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
