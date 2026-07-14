from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo


TIMEZONE_UNKNOWN = "TIMEZONE_UNKNOWN"


def normalize_to_utc(value: Any, *, source_timezone: str | None = None) -> datetime:
    """Return an aware UTC datetime; naive values require an explicit source timezone."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("EMPTY_DATETIME")
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        if not source_timezone or source_timezone == "unknown":
            raise ValueError(TIMEZONE_UNKNOWN)
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
        except (KeyError, ValueError) as exc:
            raise ValueError(TIMEZONE_UNKNOWN) from exc
    return parsed.astimezone(timezone.utc)


def calculate_age_hours(observed_at_utc: datetime, received_at_utc: datetime) -> float:
    """Calculate data age only from timezone-aware UTC timestamps."""
    if observed_at_utc.tzinfo is None or received_at_utc.tzinfo is None:
        raise ValueError(TIMEZONE_UNKNOWN)
    observed = observed_at_utc.astimezone(timezone.utc)
    received = received_at_utc.astimezone(timezone.utc)
    return round(max(0.0, (received - observed).total_seconds() / 3600), 1)
