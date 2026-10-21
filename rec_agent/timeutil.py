from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(value: str | datetime | None) -> datetime | None:
    """Normalize ISO string or datetime to timezone-aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_utc_iso(value: str | datetime | None = None) -> str:
    dt = to_utc(value) if value is not None else utc_now()
    if dt is None:
        dt = utc_now()
    return dt.isoformat().replace("+00:00", "Z")


def parse_hour_in_zone(occurred_at: str) -> int:
    """Wall-clock hour in the timestamp's own offset (for DND)."""
    dt = to_utc(occurred_at)
    if dt is None:
        return 12
    # Re-parse original to keep local hour for DND policy
    text = occurred_at.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        local = datetime.fromisoformat(text)
    except ValueError:
        return 12
    if local.tzinfo is None:
        local = local.replace(tzinfo=timezone.utc)
    return local.hour
