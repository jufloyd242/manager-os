"""Google Workspace snapshot ingestor.

Reads pre-saved JSON snapshot files exported from Gmail, Calendar, and Chat.
No live GWS API calls are made here — this module reads files that have
already been saved to the local filesystem.

Expected directory layout (configurable via MANAGER_OS_GWS_SNAPSHOT_DIR):
  gws_snapshots/
    calendar/YYYY-MM-DD.json    # list of calendar event objects
    gmail/YYYY-MM-DD.json       # list of Gmail thread objects
    chat/YYYY-MM-DD.json        # list of Chat message objects

Each snapshot JSON file is a list of objects in the shape described below.

Calendar event shape (minimum):
  {
    "id": "...",
    "summary": "Meeting title",
    "start": {"dateTime": "2026-06-13T10:00:00-07:00"},  # or "date"
    "end":   {"dateTime": "2026-06-13T11:00:00-07:00"},
    "attendees": [{"email": "alice@example.com", "displayName": "Alice Chen"}],
    "description": "Optional body text"
  }

Gmail thread shape (minimum):
  {
    "id": "thread-abc",
    "snippet": "First line of thread",
    "messages": [
      {"id": "msg-1", "from": "alice@example.com",
       "subject": "Subject line", "date": "2026-06-13",
       "body": "Full body text"}
    ]
  }

Chat message shape (minimum):
  {
    "id": "msg-xyz",
    "sender": "alice@example.com",
    "createTime": "2026-06-13T09:15:00Z",
    "text": "Message body",
    "spaceName": "Space Name / DM"
  }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from manager_os.db import content_hash

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# IngestResult
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    source: str = "gws"


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------


def _parse_gws_datetime(value: str | dict) -> datetime | None:
    """Parse a GWS dateTime or date field to a Python datetime."""
    if isinstance(value, dict):
        dt_str = value.get("dateTime") or value.get("date")
        if not dt_str:
            return None
        value = dt_str
    try:
        # ISO format with optional timezone
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Date-only
        return datetime.combine(date.fromisoformat(value), datetime.min.time())
    except Exception:
        return None


def _parse_gws_date(value: str | dict) -> date | None:
    dt = _parse_gws_datetime(value)
    return dt.date() if dt else None


# ---------------------------------------------------------------------------
# Calendar ingestion
# ---------------------------------------------------------------------------


def _ingest_calendar_file(path: Path, conn, force: bool) -> IngestResult:
    result = IngestResult(source="gws:calendar")
    try:
        events = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read calendar snapshot %s: %s", path, exc)
        result.failed += 1
        return result

    if not isinstance(events, list):
        logger.warning("Calendar snapshot %s is not a list", path)
        result.failed += 1
        return result

    for event in events:
        if not isinstance(event, dict):
            continue
        ext_id = event.get("id", "")
        title = event.get("summary", "").strip() or "(no title)"
        start = event.get("start", {})
        end = event.get("end", {})
        meeting_date = _parse_gws_date(start)
        if not meeting_date:
            continue

        start_time = ""
        start_dt = _parse_gws_datetime(start)
        if start_dt:
            start_time = start_dt.strftime("%H:%M")

        attendees = [
            a.get("displayName") or a.get("email", "")
            for a in event.get("attendees", [])
            if isinstance(a, dict)
        ]

        row_id = content_hash(f"gws:calendar::{ext_id or title}::{meeting_date}")
        exists = conn.execute("SELECT id FROM meetings WHERE id = ?", [row_id]).fetchone()
        if exists and not force:
            result.skipped += 1
            continue

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO meetings
                    (id, meeting_date, start_time, title, attendees, linked_entities,
                     source, external_id, updated_at)
                VALUES (?, ?, ?, ?, ?, '[]', 'gws:calendar', ?, ?)
                """,
                [row_id, meeting_date, start_time, title,
                 json.dumps(attendees), ext_id, datetime.utcnow()],
            )

            # Also ingest as a raw_document for signal extraction
            body = event.get("description", "") or ""
            doc_id = content_hash(f"gws:calendar:raw::{ext_id or title}::{meeting_date}")
            doc_hash = content_hash(f"{title}{body}")
            existing_doc = conn.execute(
                "SELECT content_hash FROM raw_documents WHERE id = ?", [doc_id]
            ).fetchone()
            if not existing_doc or existing_doc[0] != doc_hash or force:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO raw_documents
                        (id, ingested_at, source_type, source_path, content_hash, content, metadata)
                    VALUES (?, ?, 'gws', ?, ?, ?, ?)
                    """,
                    [doc_id, datetime.utcnow(), str(path), doc_hash,
                     f"{title}\n\n{body}".strip(), json.dumps({"type": "calendar_event"})],
                )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Failed to ingest calendar event %s: %s", ext_id, exc)
            result.failed += 1

    return result


# ---------------------------------------------------------------------------
# Gmail ingestion
# ---------------------------------------------------------------------------


def _ingest_gmail_file(path: Path, conn, force: bool) -> IngestResult:
    result = IngestResult(source="gws:gmail")
    try:
        threads = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read Gmail snapshot %s: %s", path, exc)
        result.failed += 1
        return result

    if not isinstance(threads, list):
        result.failed += 1
        return result

    for thread in threads:
        if not isinstance(thread, dict):
            continue
        thread_id = thread.get("id", "")
        snippet = thread.get("snippet", "")
        messages = thread.get("messages", [])
        if not messages:
            continue

        # Derive date from first message
        first_msg = messages[0] if isinstance(messages[0], dict) else {}
        msg_date_str = first_msg.get("date", "")
        try:
            msg_date = date.fromisoformat(str(msg_date_str)[:10]) if msg_date_str else date.today()
        except Exception:
            msg_date = date.today()

        subject = first_msg.get("subject", snippet or "(no subject)")
        body_parts = [
            f"From: {m.get('from', '')}\nDate: {m.get('date', '')}\n\n{m.get('body', '')}"
            for m in messages if isinstance(m, dict)
        ]
        full_body = "\n\n---\n\n".join(body_parts)

        doc_id = content_hash(f"gws:gmail::{thread_id or subject}::{msg_date}")
        doc_hash = content_hash(full_body)

        existing = conn.execute(
            "SELECT content_hash FROM raw_documents WHERE id = ?", [doc_id]
        ).fetchone()
        if existing and existing[0] == doc_hash and not force:
            result.skipped += 1
            continue

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO raw_documents
                    (id, ingested_at, source_type, source_path, content_hash, content, metadata)
                VALUES (?, ?, 'gmail', ?, ?, ?, ?)
                """,
                [doc_id, datetime.utcnow(), str(path), doc_hash,
                 f"Subject: {subject}\n\n{full_body}",
                 json.dumps({"type": "gmail_thread", "thread_id": thread_id, "date": str(msg_date)})],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Failed to ingest Gmail thread %s: %s", thread_id, exc)
            result.failed += 1

    return result


# ---------------------------------------------------------------------------
# Chat ingestion
# ---------------------------------------------------------------------------


def _ingest_chat_file(path: Path, conn, force: bool) -> IngestResult:
    result = IngestResult(source="gws:chat")
    try:
        messages = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read Chat snapshot %s: %s", path, exc)
        result.failed += 1
        return result

    if not isinstance(messages, list):
        result.failed += 1
        return result

    # Group messages by space for a single raw document per space per day
    by_space: dict[str, list[dict]] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        space = msg.get("spaceName", "unknown")
        by_space.setdefault(space, []).append(msg)

    file_date_str = path.stem  # YYYY-MM-DD
    try:
        file_date = date.fromisoformat(file_date_str)
    except Exception:
        file_date = date.today()

    for space, msgs in by_space.items():
        body_parts = [
            f"{m.get('sender', 'unknown')} ({m.get('createTime', '')[:19]}): {m.get('text', '')}"
            for m in msgs
        ]
        body = "\n".join(body_parts)

        doc_id = content_hash(f"gws:chat::{space}::{file_date}")
        doc_hash = content_hash(body)

        existing = conn.execute(
            "SELECT content_hash FROM raw_documents WHERE id = ?", [doc_id]
        ).fetchone()
        if existing and existing[0] == doc_hash and not force:
            result.skipped += 1
            continue

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO raw_documents
                    (id, ingested_at, source_type, source_path, content_hash, content, metadata)
                VALUES (?, ?, 'gws', ?, ?, ?, ?)
                """,
                [doc_id, datetime.utcnow(), str(path), doc_hash,
                 f"Space: {space}\n\n{body}",
                 json.dumps({"type": "chat_messages", "space": space, "date": str(file_date)})],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Failed to ingest Chat space %s: %s", space, exc)
            result.failed += 1

    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ingest_gws_snapshots(
    snapshot_dir: str | Path,
    conn,
    target_date: date | None = None,
    force: bool = False,
) -> IngestResult:
    """Ingest all GWS snapshot files for the target date.

    Reads calendar/, gmail/, and chat/ subdirectories under snapshot_dir.
    Files are expected to be named YYYY-MM-DD.json.

    Args:
        snapshot_dir: Root directory containing gws snapshot subdirs.
        conn: Open DuckDB connection.
        target_date: Date to ingest. Defaults to today.
        force: Re-ingest even if content hash is unchanged.

    Returns:
        Aggregated IngestResult.
    """
    if target_date is None:
        target_date = date.today()

    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.exists():
        logger.info("GWS snapshot directory does not exist: %s", snapshot_dir)
        return IngestResult(source="gws")

    date_str = target_date.isoformat()
    total = IngestResult(source="gws")

    for subdir, handler in [
        ("calendar", _ingest_calendar_file),
        ("gmail", _ingest_gmail_file),
        ("chat", _ingest_chat_file),
    ]:
        snap_path = snapshot_dir / subdir / f"{date_str}.json"
        if not snap_path.exists():
            logger.debug("No %s snapshot for %s", subdir, date_str)
            continue
        r = handler(snap_path, conn, force)
        total.ingested += r.ingested
        total.skipped += r.skipped
        total.failed += r.failed

    return total
