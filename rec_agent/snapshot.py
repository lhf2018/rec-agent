from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy

from rec_agent.catalog import Catalog
from rec_agent.models import HardConstraints, ShortTermIntent, UserSnapshot


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_expired(expires_at: str, now: datetime) -> bool:
    exp = _parse_dt(expires_at)
    if exp is None:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    cmp = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return exp <= cmp


class SnapshotBuilder:
    """Assemble a read-only UserSnapshot; Agent must never mutate profile via this path."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def build(self, user_id: str, *, now: datetime | None = None) -> UserSnapshot | None:
        raw = self.catalog.get_user(user_id)
        if not raw:
            return None
        snap = deepcopy(raw)
        clock = now or datetime.now(timezone.utc)
        active_short: list[ShortTermIntent] = []
        for intent in snap.short_term:
            if _is_expired(intent.expires_at, clock):
                continue
            active_short.append(intent)
        snap.short_term = active_short

        # not_interested with optional expires stored as merchant_id|expires_at in catalog meta:
        # MVP keeps plain ids; TTL pruning happens in FeedbackService when timestamps exist.
        hard = HardConstraints(
            dietary_forbid=list(snap.hard.dietary_forbid),
            blocked_merchants=list(snap.hard.blocked_merchants),
            not_interested_merchants=list(snap.hard.not_interested_merchants),
            budget_min=snap.hard.budget_min,
            budget_max=snap.hard.budget_max,
        )
        snap.hard = hard
        snap.built_at = clock.isoformat()
        return snap
