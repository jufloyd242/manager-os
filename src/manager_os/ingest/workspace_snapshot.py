"""Read workspace snapshots into DuckDB tables.

Reads JSON snapshots produced by ``workspace_gemini.py`` retrieval helpers
and writes them into the appropriate DuckDB tables:

- Forecast snapshots → ``staffing_forecast``
- Calendar snapshots → ``meetings``
- Activity snapshots → ``raw_documents`` + ``notes`` (as a daily summary)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from manager_os.db import content_hash

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Result
# ------------------------------------------------------------------


@dataclass
class IngestResult:
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _snapshot_path(subdir: str, target_date: date, base_dir: str = "") -> Path:
    """Return the expected snapshot file path for a given date."""
    root = Path(base_dir) if base_dir else Path("data/raw/workspace_snapshots")
    return root / subdir / f"{target_date.isoformat()}.json"


def _read_snapshot(path: Path) -> dict | None:
    """Read and parse a JSON snapshot file. Returns None if missing."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read snapshot %s: %s", path, exc)
        return None


def _snapshot_exists(subdir: str, target_date: date) -> bool:
    """Check whether a snapshot file exists for the given date."""
    return _snapshot_path(subdir, target_date).exists()


# ------------------------------------------------------------------
# 1. Forecast snapshot ingestion
# ------------------------------------------------------------------

def ingest_workspace_forecast_snapshot(
    conn,
    target_date: date,
    force: bool = False,
) -> IngestResult:
    """Read a forecast snapshot and write rows into ``staffing_forecast``.

    Expected JSON shape::

        {
            "ok": true,
            "source_title": "...",
            "source_url": "...",
            "retrieved_at": "...",
            "rows": [
                {"person": "...", "week_start": "...", "allocation_pct": N,
                 "project": "...", "client": "..."},
                ...
            ]
        }
    """
    result = IngestResult()
    path = _snapshot_path("forecast", target_date)
    data = _read_snapshot(path)
    if data is None:
        result.errors.append(f"No forecast snapshot for {target_date} at {path}")
        return result

    rows = data.get("rows", data.get("items", []))
    if not rows:
        result.errors.append(f"Empty forecast snapshot at {path}")
        return result

    now = datetime.utcnow()
    for row in rows:
        person = str(row.get("person", row.get("person_name", ""))).strip()
        if not person:
            result.failed += 1
            continue
        week_start = row.get("week_start", "")
        # Handle allocation as string ("50%", "100%") or number
        raw_alloc = row.get("allocation_pct", 0)
        try:
            if isinstance(raw_alloc, str):
                allocation_pct = float(raw_alloc.strip().rstrip("%"))
            else:
                allocation_pct = float(raw_alloc)
        except (ValueError, TypeError):
            allocation_pct = 0.0
        project = str(row.get("project", "")).strip()
        client = str(row.get("client", "")).strip()

        try:
            ws = str(week_start)[:10]  # YYYY-MM-DD
        except Exception:
            result.failed += 1
            continue

        row_id = content_hash(f"ws::forecast::{person}::{ws}::{client}::{project}")
        existing = conn.execute(
            "SELECT id FROM staffing_forecast WHERE id = ?", [row_id]
        ).fetchone()
        if existing and not force:
            result.skipped += 1
            continue

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO staffing_forecast
                    (id, person_id, person_name, week_start, client, project,
                     allocation_pct, forecast_type, notes, ingested_at)
                VALUES (?, '', ?, ?, ?, ?,
                        ?, 'workspace_gemini', 'Retrieved from Google Workspace via Gemini CLI', ?)
                """,
                [row_id, person, ws, client, project, allocation_pct, now],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Failed to write forecast row %s: %s", person, exc)
            result.failed += 1

    return result


# ------------------------------------------------------------------
# 2. Calendar snapshot ingestion
# ------------------------------------------------------------------

def ingest_workspace_calendar_snapshot(
    conn,
    target_date: date,
    force: bool = False,
) -> IngestResult:
    """Read a calendar snapshot and write rows into ``meetings``.

    Expected JSON shape::

        {
            "ok": true,
            "source": "google_calendar_gemini",
            "retrieved_at": "...",
            "events": [
                {
                    "title": "...",
                    "start_time": "2026-06-16T09:00:00",
                    "end_time": "2026-06-16T09:30:00",
                    "attendees": ["..."],
                    "location": "...",          or "meet_link": "..."
                    "description_summary": "...",
                    "external_id": "..."
                },
                ...
            ]
        }
    """
    result = IngestResult()
    path = _snapshot_path("calendar", target_date)
    data = _read_snapshot(path)
    if data is None:
        result.errors.append(f"No calendar snapshot for {target_date} at {path}")
        return result

    events = data.get("events", data.get("items", []))
    if not events:
        result.errors.append(f"Empty calendar snapshot at {path}")
        return result

    now = datetime.utcnow()
    for ev in events:
        title = str(ev.get("title", "")).strip()
        if not title:
            result.failed += 1
            continue
        start_time = ev.get("start_time", "")
        external_id = ev.get("external_id", "")

        meeting_id = content_hash(f"ws::calendar::{external_id or title}::{start_time}")
        existing = conn.execute(
            "SELECT id FROM meetings WHERE id = ?", [meeting_id]
        ).fetchone()
        if existing and not force:
            result.skipped += 1
            continue

        attendees = ev.get("attendees", [])
        if not isinstance(attendees, list):
            attendees = [str(attendees)] if attendees else []
        location = ev.get("location", ev.get("meet_link", ""))

        try:
            meeting_date_str = str(start_time)[:10] if start_time else target_date.isoformat()
        except Exception:
            meeting_date_str = target_date.isoformat()

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO meetings
                    (id, meeting_date, start_time, title, attendees,
                     linked_entities, source, external_id, updated_at)
                VALUES (?, ?, ?, ?, ?, '[]', 'google_calendar_gemini', ?, ?)
                """,
                [
                    meeting_id, meeting_date_str, start_time, title,
                    json.dumps(attendees), external_id or "", now,
                ],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Failed to write meeting %s: %s", title, exc)
            result.failed += 1

    return result


# ------------------------------------------------------------------
# 3. Workspace activity snapshot ingestion
# ------------------------------------------------------------------

def ingest_workspace_activity_snapshot(
    conn,
    target_date: date,
    force: bool = False,
) -> IngestResult:
    """Read an activity snapshot and write a daily summary note.

    Expected JSON shape::

        {
            "ok": true,
            "source": "google_workspace_gemini",
            "retrieved_at": "...",
            "summary": "Two docs updated, one comment ...",
            "items": [
                {"type": "...", "title": "...", "description": "...", ...},
                ...
            ]
        }

    Writes a single ``raw_documents`` row and a single ``notes`` row
    representing the daily workspace activity summary.
    """
    result = IngestResult()
    path = _snapshot_path("activity", target_date)
    data = _read_snapshot(path)
    if data is None:
        result.errors.append(f"No activity snapshot for {target_date} at {path}")
        return result

    summary_text = str(data.get("summary", "")).strip()
    items = data.get("items", [])
    if not summary_text and not items:
        result.errors.append(f"Empty activity snapshot at {path}")
        return result

    # Build a markdown body from the items
    body_lines = [f"# Workspace Activity — {target_date}", "", summary_text, ""]
    for it in items:
        item_type = str(it.get("type", "other")).replace("_", " ").title()
        title = str(it.get("title", "Untitled"))
        desc = str(it.get("description", ""))
        url = str(it.get("source_url", ""))
        needs_attn = it.get("requires_attention", False)
        prefix = "🔴" if needs_attn else "•"
        body_lines.append(f"{prefix} **{item_type}**: {title}")
        if desc:
            body_lines.append(f"  {desc}")
        if url:
            body_lines.append(f"  {url}")
        body_lines.append("")
    body = "\n".join(body_lines)

    doc_id = content_hash(f"ws::activity::{target_date.isoformat()}")
    note_id = content_hash(f"ws::activity_note::{target_date.isoformat()}")

    existing = conn.execute(
        "SELECT id FROM raw_documents WHERE id = ?", [doc_id]
    ).fetchone()
    if existing and not force:
        result.skipped += 1
        return result

    now = datetime.utcnow()

    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO raw_documents
                (id, ingested_at, source_type, source_path, file_modified_at,
                 content_hash, content, metadata)
            VALUES (?, ?, 'workspace_gemini', ?, ?, ?, ?, ?)
            """,
            [
                doc_id, now, str(path), now,
                content_hash(body), body,
                json.dumps({
                    "source_tier": "signal",
                    "scope_reason": "workspace activity snapshot",
                    "retrieved_at": data.get("retrieved_at", ""),
                }),
            ],
        )
        result.ingested += 1
    except Exception as exc:
        logger.warning("Failed to write activity document: %s", exc)
        result.failed += 1
        return result

    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO notes
                (id, raw_document_id, note_date, note_type, entity_type,
                 entity_name, title, body, tags, created_at)
            VALUES (?, ?, ?, 'summary', 'team', '', ?, ?, '[]', ?)
            """,
            [note_id, doc_id, target_date.isoformat(),
             "Workspace activity", body, now],
        )
    except Exception as exc:
        logger.warning("Failed to write activity note: %s", exc)
        result.failed += 1

    # Phase 4: Ingest Chat activity as first-class action items
    action_items = data.get("action_items", [])
    attention_items = [i for i in items if i.get("requires_attention")]

    # Combine and dedupe
    seen_actions: set[str] = set()
    for ai in action_items:
        desc = str(ai.get("description", "")).strip()
        if not desc:
            continue
        dedup_key = content_hash(f"workspace_activity::{target_date.isoformat()}::{ai.get('source_url', '')}::{desc}")
        if dedup_key in seen_actions:
            continue
        seen_actions.add(dedup_key)

        assigned_to = str(ai.get("assigned_to", "manager")).strip() or "manager"
        due_date = ai.get("due_date")
        entity_type = str(ai.get("entity_type", "workspace")).strip() or "workspace"
        entity_name = str(ai.get("entity_name", "")).strip()
        source_url = str(ai.get("source_url", ""))

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO action_items
                    (id, signal_id, source_note_id, assigned_to, description,
                     due_date, status, created_at, updated_at, source_url)
                VALUES (?, NULL, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                [
                    dedup_key, note_id, assigned_to, desc,
                    due_date, now, now, source_url,
                ],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Failed to write workspace action item: %s", exc)
            result.failed += 1

    for ai in attention_items:
        desc = str(ai.get("description", "")).strip()
        if not desc:
            continue
        dedup_key = content_hash(f"workspace_activity::{target_date.isoformat()}::{ai.get('source_url', '')}::{desc}")
        if dedup_key in seen_actions:
            continue
        seen_actions.add(dedup_key)

        assigned_to = str(ai.get("assigned_to", "manager")).strip() or "manager"
        due_date = ai.get("due_date")
        entity_type = str(ai.get("entity_type", "workspace")).strip() or "workspace"
        entity_name = str(ai.get("entity_name", "")).strip()
        source_url = str(ai.get("source_url", ""))

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO action_items
                    (id, signal_id, source_note_id, assigned_to, description,
                     due_date, status, created_at, updated_at, source_url)
                VALUES (?, NULL, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                [
                    dedup_key, note_id, assigned_to, desc,
                    due_date, now, now, source_url,
                ],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Failed to write workspace attention action item: %s", exc)
            result.failed += 1

    return result
