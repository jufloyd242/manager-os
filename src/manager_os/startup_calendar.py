"""Startup calendar sync — populates the current week's meetings.

Calculates the current local week (Monday-Sunday in America/Denver),
checks freshness (6 hours), and performs one bounded read-only calendar
range sync via Gemini CLI.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from manager_os.db import get_connection
from manager_os.ingest.calendar_persistence import persist_calendar_events
from manager_os.ingest.workspace_gemini import retrieve_calendar_range

logger = logging.getLogger(__name__)

CALENDAR_SYNC_FRESHNESS_HOURS = 6


def calculate_week_range(target_date: date) -> tuple[date, date]:
    """Calculate Monday-Sunday for the week containing target_date.

    Uses ISO week definition: Monday is day 0.
    """
    weekday = target_date.weekday()  # Monday=0, Sunday=6
    monday = target_date - timedelta(days=weekday)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def should_sync_week(db_path: str, target_date: date) -> bool:
    """Check if the current week needs a calendar sync.

    Returns True if:
    - No meetings exist for the current week
    - The last successful sync is older than CALENDAR_SYNC_FRESHNESS_HOURS
    """
    week_start, week_end = calculate_week_range(target_date)
    try:
        conn = get_connection(db_path)
        row = conn.execute(
            """SELECT MAX(updated_at) FROM meetings
               WHERE meeting_date BETWEEN ? AND ?
               AND source = 'calendar_sync'""",
            [week_start, week_end],
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return True
        last_sync = row[0]
        if isinstance(last_sync, str):
            last_sync = datetime.fromisoformat(last_sync.replace("Z", "+00:00"))
        age = datetime.utcnow() - last_sync.replace(tzinfo=None) if hasattr(last_sync, 'replace') else timedelta(hours=999)
        return age.total_seconds() > CALENDAR_SYNC_FRESHNESS_HOURS * 3600
    except Exception:
        return True


def sync_current_week(
    db_path: str,
    *,
    force: bool = False,
    no_sync: bool = False,
) -> dict[str, Any]:
    """Sync the current calendar week.

    Args:
        db_path: Path to the DuckDB database.
        force: If True, sync even if freshness check says recent.
        no_sync: If True, skip sync entirely.

    Returns:
        Dict with status, week_start, week_end, and sync result info.
    """
    target_date = date.today()
    week_start, week_end = calculate_week_range(target_date)

    if no_sync:
        return {
            "status": "skipped",
            "reason": "no_calendar_sync flag",
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
        }

    if not force and not should_sync_week(db_path, target_date):
        return {
            "status": "fresh",
            "reason": "recent sync exists",
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
        }

    logger.info("Syncing calendar for %s to %s", week_start, week_end)

    try:
        result = retrieve_calendar_range(week_start, week_end, use_yolo=True, timeout=300)
    except Exception as exc:
        logger.error("Calendar range sync failed: %s", exc)
        return {
            "status": "error",
            "error": str(exc),
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
        }

    if not result.ok:
        return {
            "status": "error",
            "error": result.error,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
        }

    # Persist events grouped by date
    conn = get_connection(db_path)
    total_persisted = 0
    total_rejected = 0
    errors: list[str] = []

    # Group events by date
    events_by_date: dict[date, list[dict]] = {}
    for event in result.items:
        start_raw = event.get("start_time", event.get("start_at", ""))
        if start_raw:
            try:
                event_date = date.fromisoformat(str(start_raw)[:10])
            except (ValueError, TypeError):
                event_date = target_date
        else:
            event_date = target_date
        events_by_date.setdefault(event_date, []).append(event)

    for event_date, events in events_by_date.items():
        persist_result = persist_calendar_events(
            conn, event_date, events,
            source="calendar_sync",
            retrieved_at=result.retrieved_at or datetime.utcnow().isoformat(),
        )
        total_persisted += persist_result.persisted_count
        total_rejected += persist_result.rejected_count
        errors.extend(persist_result.errors)

    conn.close()

    return {
        "status": "synced",
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "retrieved_count": len(result.items),
        "persisted_count": total_persisted,
        "rejected_count": total_rejected,
        "errors": errors,
    }
