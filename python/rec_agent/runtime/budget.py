from __future__ import annotations

from datetime import datetime

from rec_agent.config import Settings, get_settings
from rec_agent.timeutil import to_utc, utc_now


class DeadlineBudget:
    """UTC deadline gate checked before every model / tool call."""

    def __init__(self, deadline_at: datetime, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        dl = to_utc(deadline_at)
        if dl is None:
            dl = utc_now()
        self.deadline_at = dl

    def remaining_ms(self, now: datetime | None = None) -> float:
        clock = to_utc(now) or utc_now()
        return (self.deadline_at - clock).total_seconds() * 1000.0

    def allow_model(self, now: datetime | None = None) -> bool:
        return self.remaining_ms(now) > float(self.settings.model_min_remaining_ms)

    def allow_tool(self, tool_timeout_ms: int | None = None, now: datetime | None = None) -> bool:
        need = float(tool_timeout_ms if tool_timeout_ms is not None else self.settings.tool_timeout_ms)
        return self.remaining_ms(now) > need
