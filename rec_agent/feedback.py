from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rec_agent.catalog import Catalog
from rec_agent.config import Settings, get_settings
from rec_agent.models import FeedbackRequest
from rec_agent.store import Store


class FeedbackService:
    """Feedback pipeline updates negative preference outside the Agent path."""

    def __init__(
        self,
        catalog: Catalog,
        store: Store,
        settings: Settings | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.settings = settings or get_settings()
        # merchant_id -> expires_at iso for TTL pruning
        self._ni_expires: dict[str, dict[str, str]] = {}

    def apply(self, task_id: str, body: FeedbackRequest) -> dict:
        task = self.store.get_task(task_id)
        if not task:
            return {"ok": False, "error": "task_not_found"}

        user_id = task.get("request", {}).get("user_id")
        if not user_id:
            return {"ok": False, "error": "user_missing"}

        event_time = body.event_time or datetime.now(timezone.utc).isoformat()

        if body.event_type == "not_interested":
            if not body.merchant_id:
                return {"ok": False, "error": "merchant_id_required"}
            returned = {m["merchant_id"] for m in task.get("merchants", [])}
            if body.merchant_id not in returned:
                return {"ok": False, "error": "merchant_not_in_task"}
            self.catalog.apply_not_interested(user_id, body.merchant_id)
            expires = (
                datetime.now(timezone.utc)
                + timedelta(days=self.settings.not_interested_ttl_days)
            ).isoformat()
            self._ni_expires.setdefault(user_id, {})[body.merchant_id] = expires

        elif body.event_type in {"impression", "click"}:
            # soft signals: persist only; do not rewrite long-term profile
            pass
        else:
            return {"ok": False, "error": "unsupported_event"}

        self.store.add_feedback(task_id, user_id, body.merchant_id, body.event_type, event_time)
        return {"ok": True}

    def prune_expired_not_interested(self, user_id: str) -> None:
        user = self.catalog.get_user(user_id)
        if not user:
            return
        expires = self._ni_expires.get(user_id, {})
        now = datetime.now(timezone.utc)
        kept: list[str] = []
        for mid in user.hard.not_interested_merchants:
            exp_s = expires.get(mid)
            if not exp_s:
                kept.append(mid)
                continue
            try:
                exp = datetime.fromisoformat(exp_s)
            except ValueError:
                kept.append(mid)
                continue
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp > now:
                kept.append(mid)
        user.hard.not_interested_merchants = kept
