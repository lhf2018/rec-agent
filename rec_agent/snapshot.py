from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

from rec_agent.catalog import Catalog
from rec_agent.config import Settings, get_settings
from rec_agent.models import HardConstraints, RuntimeSignals, ShortTermIntent, UserSnapshot
from rec_agent.store import Store
from rec_agent.timeutil import to_utc, to_utc_iso, utc_now


def _is_expired(expires_at: str, now: datetime) -> bool:
    exp = to_utc(expires_at)
    if exp is None:
        return False
    return exp <= now


class SnapshotBuilder:
    """Assemble a read-only UserSnapshot: Catalog static ⊕ Store dynamic."""

    def __init__(
        self,
        catalog: Catalog,
        store: Store | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.settings = settings or get_settings()

    def build(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        scene_key: str | None = None,
    ) -> UserSnapshot | None:
        raw = self.catalog.get_user(user_id)
        if not raw:
            return None
        snap = deepcopy(raw)
        clock = now or utc_now()
        if clock.tzinfo is None:
            from datetime import timezone

            clock = clock.replace(tzinfo=timezone.utc)

        active_short: list[ShortTermIntent] = []
        for intent in snap.short_term:
            if _is_expired(intent.expires_at, clock):
                continue
            active_short.append(intent)
        snap.short_term = active_short

        seed_ni = list(snap.hard.not_interested_merchants)
        dynamic_ni: list[str] = []
        runtime = RuntimeSignals()

        if self.store is not None:
            dynamic_ni = self.store.list_not_interested(user_id, now=clock)
            since_freq = clock - timedelta(hours=self.settings.freq_window_hours)
            runtime.delivery_count_in_window = self.store.count_deliveries_since(user_id, since_freq)
            summary = self.store.get_delivery_summary(user_id)
            runtime.last_success_at = summary.last_success_at or ""
            runtime.recent_shown_merchants = list(summary.merchant_ids)
            runtime.recent_shown_categories = list(summary.categories)
            if scene_key:
                runtime.scene_seen_recently = self.store.scene_seen_within(
                    user_id,
                    scene_key,
                    window=timedelta(minutes=self.settings.scene_dedupe_minutes),
                    now=clock,
                )
            since_expo = clock - timedelta(hours=self.settings.exposure_history_window_hours)
            items = self.store.list_exposure_merchants(user_id, since=since_expo, limit=50)
            # preserve order, unique merchant ids
            seen: set[str] = set()
            mids: list[str] = []
            for it in items:
                if it.merchant_id in seen:
                    continue
                seen.add(it.merchant_id)
                mids.append(it.merchant_id)
            runtime.exposure_merchant_ids = mids
            runtime.dynamic_not_interested = list(dynamic_ni)

        merged_ni = list(dict.fromkeys(seed_ni + dynamic_ni))
        snap.hard = HardConstraints(
            dietary_forbid=list(snap.hard.dietary_forbid),
            blocked_merchants=list(snap.hard.blocked_merchants),
            not_interested_merchants=merged_ni,
            budget_min=snap.hard.budget_min,
            budget_max=snap.hard.budget_max,
        )
        snap.runtime = runtime
        snap.built_at = to_utc_iso(clock)
        return snap
