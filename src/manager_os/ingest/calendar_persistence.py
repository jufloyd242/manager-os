"""Canonical calendar event persistence — one shared path for CLI and API.

Serializes JSON fields with json.dumps() (never str()), validates each event,
rejects invalid individual events without crashing the batch, and returns
structured persistence results with honest counts and diagnostics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from manager_os.db import content_hash

logger = logging.getLogger(__name__)


@dataclass
class CalendarPersistenceResult:
    """Structured result of persisting calendar events."""
    retrieved_count: int = 0
    persisted_count: int = 0
    rejected_count: int = 0
    replaced_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    persisted_meetings: list[dict[str, Any]] = field(default_factory=list)


def _normalize_attendees(raw: Any) -> list[str]:
    """Normalize attendees to a list of strings."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(a) for a in raw if a]
    if isinstance(raw, str):
        # Try JSON parse first
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(a) for a in parsed if a]
        except (json.JSONDecodeError, ValueError):
            pass
        # Fallback: treat as single attendee
        return [raw] if raw.strip() else []
    return []


def _normalize_linked_entities(raw: Any) -> list[dict[str, Any]]:
    """Normalize linked entities to a list of dicts."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [item if isinstance(item, dict) else {"value": str(item)} for item in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [item if isinstance(item, dict) else {"value": str(item)} for item in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def persist_calendar_events(
    conn,
    target_date: date,
    events: list[dict[str, Any]],
    *,
    source: str = "calendar_sync",
    retrieved_at: str = "",
) -> CalendarPersistenceResult:
    """Persist calendar events to the meetings table.

    Validates each event, normalizes fields, generates stable IDs when needed,
    serializes JSON with json.dumps(), and uses INSERT OR REPLACE for dedup.

    Invalid individual events are rejected with a structured reason — they do
    not crash the entire batch.

    Args:
        conn: DuckDB connection.
        target_date: The meeting date for all events.
        events: List of event dicts from the retrieval provider.
        source: Source label (e.g. "calendar_sync", "gws:calendar").
        retrieved_at: ISO timestamp of retrieval.

    Returns:
        CalendarPersistenceResult with counts, warnings, errors, and
        persisted meeting dicts.
    """
    result = CalendarPersistenceResult(retrieved_count=len(events))
    now = datetime.utcnow()

    for i, event in enumerate(events):
        if not isinstance(event, dict):
            result.rejected_count += 1
            result.errors.append(f"Event {i}: not a dict, skipping")
            continue

        # Validate required fields
        title = str(event.get("title", event.get("summary", ""))).strip()
        if not title:
            result.rejected_count += 1
            result.errors.append(f"Event {i}: missing title")
            continue

        start_time = str(event.get("start_time", "")).strip()
        if not start_time:
            result.rejected_count += 1
            result.errors.append(f"Event {i}: missing start_time")
            continue

        # Normalize optional fields
        end_time = event.get("end_time")
        end_time = str(end_time).strip() if end_time else None

        attendees = _normalize_attendees(event.get("attendees"))
        linked_entities = _normalize_linked_entities(event.get("linked_entities"))

        external_id = str(event.get("external_id", event.get("id", ""))).strip()
        location = event.get("location")
        location = str(location).strip() if location else None
        description_summary = event.get("description_summary")
        description_summary = str(description_summary).strip() if description_summary else None

        # Generate stable ID
        if external_id:
            meeting_id = content_hash(f"calendar::{external_id}::{target_date.isoformat()}")
        else:
            meeting_id = content_hash(f"calendar::{title}::{start_time}::{target_date.isoformat()}")

        # Check if exists (for replaced_count)
        existing = conn.execute(
            "SELECT id FROM meetings WHERE id = ?", [meeting_id]
        ).fetchone()

        try:
            conn.execute(
                """INSERT OR REPLACE INTO meetings
                   (id, meeting_date, start_time, end_time, title, attendees,
                    linked_entities, source, external_id, location,
                    description_summary, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    meeting_id,
                    target_date,
                    start_time,
                    end_time,
                    title,
                    json.dumps(attendees),
                    json.dumps(linked_entities),
                    source,
                    external_id or meeting_id,
                    location,
                    description_summary,
                    now,
                ],
            )
            result.persisted_count += 1
            if existing:
                result.replaced_count += 1
            result.persisted_meetings.append({
                "id": meeting_id,
                "meeting_date": target_date.isoformat(),
                "start_time": start_time,
                "end_time": end_time,
                "title": title,
                "attendees": attendees,
                "linked_entities": linked_entities,
                "source": source,
                "external_id": external_id or meeting_id,
                "location": location,
                "description_summary": description_summary,
            })
        except Exception as exc:
            result.rejected_count += 1
            # Don't include private event content in error
            result.errors.append(f"Event {i} (title={title[:20]}...): database error: {type(exc).__name__}")

    return result
