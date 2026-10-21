from __future__ import annotations

from datetime import timedelta

from rec_agent.catalog import Catalog
from rec_agent.config import Settings, get_settings
from rec_agent.models import FeedbackRequest
from rec_agent.store import Store
from rec_agent.timeutil import to_utc_iso, utc_now


class FeedbackService:
    """Feedback writes only to Store (TTL-aware); never mutates Agent path."""

    def __init__(
        self,
        catalog: Catalog,
        store: Store,
        settings: Settings | None = None,
    ) -> None:
        self.catalog = catalog
        self.store = store
        self.settings = settings or get_settings()

    def apply(self, task_id: str, body: FeedbackRequest) -> dict:
        task = self.store.get_task(task_id)
        if not task:
            return {"ok": False, "error": "task_not_found"}

        user_id = task.get("request", {}).get("user_id")
        if not user_id:
            return {"ok": False, "error": "user_missing"}

        event_time = to_utc_iso(body.event_time)

        if body.event_type == "not_interested":
            if not body.merchant_id:
                return {"ok": False, "error": "merchant_id_required"}
            returned = {m["merchant_id"] for m in task.get("merchants", [])}
            if body.merchant_id not in returned:
                return {"ok": False, "error": "merchant_not_in_task"}
            expires = utc_now() + timedelta(days=self.settings.not_interested_ttl_days)
            self.store.upsert_not_interested(
                user_id,
                body.merchant_id,
                expires_at=to_utc_iso(expires),
                created_at=event_time,
            )
            self.store.record_exposure(
                user_id,
                body.merchant_id,
                "not_interested",
                event_time=event_time,
                task_id=task_id,
            )
        elif body.event_type in {"impression", "click"}:
            if not body.merchant_id:
                return {"ok": False, "error": "merchant_id_required"}
            self.store.record_exposure(
                user_id,
                body.merchant_id,
                body.event_type,
                event_time=event_time,
                task_id=task_id,
            )
        else:
            return {"ok": False, "error": "unsupported_event"}

        self.store.add_feedback(task_id, user_id, body.merchant_id, body.event_type, event_time)
        return {"ok": True}
