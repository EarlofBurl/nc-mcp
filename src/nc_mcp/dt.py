"""Datetime helpers shared by the calendar and tasks modules."""

from __future__ import annotations

from datetime import UTC, datetime, date
from zoneinfo import ZoneInfo

from .config import Config


class DateTimeError(ValueError):
    pass


def parse_dt(value: str, cfg: Config) -> datetime | date:
    """Parse an ISO datetime or date string.

    - "2025-12-24"               -> date (all-day)
    - "2025-12-24T18:00"         -> datetime in the default timezone
    - "2025-12-24T18:00:00+01:00" -> tz-aware datetime
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if len(text) == 10:  # YYYY-MM-DD
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise DateTimeError(f"Invalid date {value!r}, expected YYYY-MM-DD") from exc
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DateTimeError(
            f"Invalid datetime {value!r}, expected ISO format like 2025-12-24T18:00 or 2025-12-24T18:00:00+01:00"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(cfg.default_tz))
    return parsed


def to_utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def caldav_time_range(value: datetime) -> str:
    """Format a datetime for use in a CalDAV time-range filter."""
    return to_utc_naive(value).strftime("%Y%m%dT%H%M%SZ")


def iso(dt: datetime | date) -> str:
    return dt.isoformat()
