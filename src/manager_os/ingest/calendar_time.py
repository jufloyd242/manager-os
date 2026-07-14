"""Canonical calendar time normalization.

One backend utility used for:
- Single-date sync
- Weekly startup sync
- Snapshot ingestion
- API persistence
- Meeting queries
- Deduplication
- Chronological sorting

Uses Python zoneinfo.ZoneInfo. No heavyweight timezone library.

Canonical user timezone: America/Denver.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DEFAULT_DISPLAY_TIMEZONE = "America/Denver"
_DENVER = ZoneInfo(DEFAULT_DISPLAY_TIMEZONE)


@dataclass(frozen=True)
class NormalizedCalendarTime:
    """Result of normalizing a calendar event's time fields.

    Fields:
        start_raw: Raw provider start_time value (preserved).
        end_raw: Raw provider end_time value (preserved).
        start_at_utc: Normalized UTC datetime instant, or None if all-day/invalid.
        end_at_utc: Normalized UTC datetime instant, or None.
        event_timezone: IANA timezone name (e.g. "America/Denver").
        local_start_date: Calendar date in America/Denver.
        is_all_day: True for all-day events.
        warnings: Tuple of warning strings for ambiguous/invalid times.
    """
    start_raw: str | None
    end_raw: str | None
    start_at_utc: datetime | None
    end_at_utc: datetime | None
    event_timezone: str
    local_start_date: date
    is_all_day: bool
    warnings: tuple[str, ...]


def _parse_rfc3339(s: str) -> datetime | None:
    """Parse an RFC3339 timestamp (with offset or Z) into an aware datetime."""
    s = s.strip()
    if not s:
        return None
    # Handle Z suffix
    s_normalized = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(s_normalized)
        if dt.tzinfo is not None:
            return dt
        return None  # No offset → not RFC3339
    except (ValueError, TypeError):
        return None


def _parse_naive_iso(s: str) -> datetime | None:
    """Parse a naive ISO timestamp (no offset)."""
    s = s.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt
        return None  # Has offset → not naive
    except (ValueError, TypeError):
        return None


def _parse_plain_time(s: str) -> tuple[int, int] | None:
    """Parse a plain HH:MM or HH:MM:SS string."""
    s = s.strip()
    if not s:
        return None
    # Match HH:MM or HH:MM:SS
    match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if not match:
        return None
    h = int(match.group(1))
    m = int(match.group(2))
    if h > 23 or m > 59:
        return None
    return (h, m)


def _infer_timezone_from_offset(offset_str: str, ref_date: date) -> str:
    """Infer an IANA timezone from a UTC offset and reference date.

    For Denver: UTC-6 in summer (MDT), UTC-7 in winter (MST).
    """
    # Parse offset
    match = re.match(r"^([+-])(\d{2}):?(\d{2})$", offset_str)
    if not match:
        return DEFAULT_DISPLAY_TIMEZONE
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    total_offset = sign * (hours * 60 + minutes)

    # Check if this offset matches Denver at the reference date
    denver_offset = _DENVER.utcoffset(
        datetime(ref_date.year, ref_date.month, ref_date.day, 12, 0, 0, tzinfo=_DENVER)
    )
    if denver_offset is not None:
        denver_total = int(denver_offset.total_seconds() / 60)
        if total_offset == denver_total:
            return DEFAULT_DISPLAY_TIMEZONE

    # Can't infer — use default
    return DEFAULT_DISPLAY_TIMEZONE


def normalize_calendar_event_time(
    event: dict[str, Any],
    *,
    display_timezone: str = DEFAULT_DISPLAY_TIMEZONE,
    target_date: date | None = None,
) -> NormalizedCalendarTime:
    """Normalize a calendar event's time fields.

    Parsing precedence for timed events:
    1. Timestamp contains Z or explicit offset → parse as aware datetime → UTC
    2. Timestamp has no offset but event provides IANA timezone → interpret in that timezone
    3. Timestamp has no offset and no event timezone → assume America/Denver, emit warning
    4. Plain HH:MM → combine with target_date in America/Denver, emit warning
    5. Invalid → reject with warning

    For all-day events:
    - Preserve the provider's calendar date
    - Set is_all_day = True
    - Do not shift through UTC
    - start_at_utc and end_at_utc are None

    Args:
        event: Event dict with start_time, end_time, timezone, is_all_day, start_date, etc.
        display_timezone: IANA timezone for deriving local_start_date (default America/Denver).
        target_date: Fallback date for plain HH:MM values.

    Returns:
        NormalizedCalendarTime with all fields populated.
    """
    warnings: list[str] = []
    dt_zone = ZoneInfo(display_timezone)

    # Check for all-day first
    is_all_day = bool(event.get("is_all_day", False))
    start_date_str = event.get("start_date", event.get("start_date_raw", ""))

    if is_all_day or (start_date_str and not event.get("start_time")):
        # All-day event
        if start_date_str:
            try:
                local_date = date.fromisoformat(str(start_date_str)[:10])
            except (ValueError, TypeError):
                local_date = target_date or date.today()
                warnings.append("Invalid start_date for all-day event, using fallback date")
        else:
            # Try to extract date from start_time if present
            start_raw_for_date = str(event.get("start_time", ""))
            if start_raw_for_date and len(start_raw_for_date) >= 10:
                try:
                    local_date = date.fromisoformat(start_raw_for_date[:10])
                except (ValueError, TypeError):
                    local_date = target_date or date.today()
            else:
                local_date = target_date or date.today()

        return NormalizedCalendarTime(
            start_raw=str(event.get("start_time", "")) or None,
            end_raw=str(event.get("end_time", "")) or None,
            start_at_utc=None,
            end_at_utc=None,
            event_timezone=event.get("timezone", display_timezone) or display_timezone,
            local_start_date=local_date,
            is_all_day=True,
            warnings=tuple(warnings),
        )

    # Timed event
    start_raw = str(event.get("start_time", "")).strip()
    end_raw = str(event.get("end_time", "")).strip() or None
    event_tz_str = event.get("timezone", "") or ""

    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    event_timezone: str = event_tz_str or display_timezone

    if not start_raw:
        # No start time at all
        local_date = target_date or date.today()
        warnings.append("No start_time provided, using fallback date")
        return NormalizedCalendarTime(
            start_raw=None,
            end_raw=end_raw or None,
            start_at_utc=None,
            end_at_utc=None,
            event_timezone=event_timezone,
            local_start_date=local_date,
            is_all_day=False,
            warnings=tuple(warnings),
        )

    # 1. Try RFC3339 (with offset or Z)
    aware_dt = _parse_rfc3339(start_raw)
    if aware_dt is not None:
        start_at_utc = aware_dt.astimezone(timezone.utc)
        # Infer timezone if not provided
        if not event_tz_str:
            # Try to infer from offset
            offset_str = ""
            if start_raw.endswith("Z"):
                offset_str = "+00:00"
            else:
                match = re.search(r"([+-]\d{2}:?\d{2})$", start_raw)
                if match:
                    offset_str = match.group(1)
            if offset_str:
                event_timezone = _infer_timezone_from_offset(
                    offset_str,
                    aware_dt.astimezone(dt_zone).date(),
                )
    else:
        # 2. Try naive ISO with event timezone
        naive_dt = _parse_naive_iso(start_raw)
        if naive_dt is not None:
            tz = ZoneInfo(event_tz_str) if event_tz_str else dt_zone
            if not event_tz_str:
                warnings.append(
                    f"Ambiguous calendar time assumed {display_timezone}"
                )
            aware_dt = naive_dt.replace(tzinfo=tz)
            start_at_utc = aware_dt.astimezone(timezone.utc)
        else:
            # 3. Try plain HH:MM
            plain = _parse_plain_time(start_raw)
            if plain is not None:
                if target_date is None:
                    warnings.append("Plain time without target date, cannot determine full instant")
                    local_date = date.today()
                else:
                    h, m = plain
                    naive_dt = datetime(target_date.year, target_date.month, target_date.day, h, m)
                    aware_dt = naive_dt.replace(tzinfo=dt_zone)
                    start_at_utc = aware_dt.astimezone(timezone.utc)
                    warnings.append(f"Plain time '{start_raw}' assumed {display_timezone}")
                    local_date = target_date
            else:
                # 4. Invalid
                warnings.append(f"Invalid timestamp: '{start_raw}'")
                local_date = target_date or date.today()
                return NormalizedCalendarTime(
                    start_raw=start_raw,
                    end_raw=end_raw,
                    start_at_utc=None,
                    end_at_utc=None,
                    event_timezone=event_timezone,
                    local_start_date=local_date,
                    is_all_day=False,
                    warnings=tuple(warnings),
                )

    # Parse end time
    if end_raw:
        end_aware = _parse_rfc3339(end_raw)
        if end_aware is not None:
            end_at_utc = end_aware.astimezone(timezone.utc)
        else:
            end_naive = _parse_naive_iso(end_raw)
            if end_naive is not None:
                tz = ZoneInfo(event_tz_str) if event_tz_str else dt_zone
                end_at_utc = end_naive.replace(tzinfo=tz).astimezone(timezone.utc)
            else:
                end_plain = _parse_plain_time(end_raw)
                if end_plain is not None and target_date:
                    h, m = end_plain
                    end_naive = datetime(target_date.year, target_date.month, target_date.day, h, m)
                    end_at_utc = end_naive.replace(tzinfo=dt_zone).astimezone(timezone.utc)

    # Derive local_start_date from UTC instant in display timezone
    if start_at_utc is not None:
        local_date = start_at_utc.astimezone(dt_zone).date()
    elif target_date:
        local_date = target_date
    else:
        local_date = date.today()

    return NormalizedCalendarTime(
        start_raw=start_raw or None,
        end_raw=end_raw or None,
        start_at_utc=start_at_utc,
        end_at_utc=end_at_utc,
        event_timezone=event_timezone,
        local_start_date=local_date,
        is_all_day=False,
        warnings=tuple(warnings),
    )
