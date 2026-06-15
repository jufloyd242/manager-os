"""Action item extraction from note bodies.

Uses regex patterns to detect commitments, follow-ups, TODOs, and
waiting-on items. Writes ActionItem records to DuckDB.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from manager_os.db import content_hash
from manager_os.schemas import ActionItem, NoteRecord

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Relative date resolution
# ------------------------------------------------------------------

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}


def _resolve_relative_date(phrase: str, anchor: date) -> date | None:
    """Resolve relative date strings to an absolute date."""
    p = phrase.lower().strip().rstrip(".")
    if p in ("eod", "end of day", "today"):
        return anchor
    if p in ("eow", "end of week", "end of the week"):
        # Friday of anchor's week
        days_until_friday = (4 - anchor.weekday()) % 7
        return anchor + timedelta(days=days_until_friday if days_until_friday else 7)
    if p in ("next week", "next friday"):
        return anchor + timedelta(days=(7 - anchor.weekday() + 4) % 7 + 7)
    if p in ("tomorrow",):
        return anchor + timedelta(days=1)
    for name, wd in _WEEKDAYS.items():
        if p == name or p == f"next {name}":
            delta = (wd - anchor.weekday()) % 7
            if delta == 0:
                delta = 7
            if p.startswith("next "):
                delta = (wd - anchor.weekday()) % 7 + 7
            return anchor + timedelta(days=delta)
    # Try parsing as ISO date
    try:
        return date.fromisoformat(phrase.strip())
    except ValueError:
        pass
    return None


# ------------------------------------------------------------------
# Regex patterns
# ------------------------------------------------------------------

# Pattern 1: "I will / I'll / we will + action verb" → manager commitment
_MANAGER_COMMITMENT_RE = re.compile(
    r"(?:^|(?<=\n)|(?<=\. ))"
    r"(?:I(?:'ll| will| should| need to| plan to| am going to)|"
    r"(?:we|We)(?: will| need to| should| plan to))"
    r"\s+(.+?)(?:\s+by\s+([^,.\n]+))?[.\n]?$",
    re.IGNORECASE | re.MULTILINE,
)

# Pattern 2: TODO / Action Item markers
_TODO_RE = re.compile(
    r"(?:^[-*]\s+)?(?:TODO|Action\s*Item|Action\s*:|AI\s*:)\s*[:\-]?\s*(.+?)(?:\s+by\s+([^,.\n]+))?[.\n]?$",
    re.IGNORECASE | re.MULTILINE,
)

# Pattern 3: "Waiting on <person/team> [to/for ...]"
_WAITING_ON_RE = re.compile(
    r"[Ww]aiting\s+on\s+(.+?)(?:\s+to\s+(.+?)|\s+for\s+(.+?))?(?:\s+by\s+([^,.\n]+))?[.\n]?$",
    re.MULTILINE,
)

# Pattern 4: follow-up mentions
_FOLLOWUP_RE = re.compile(
    r"(?:^[-*]\s+)?(?:follow[\s-]?up|followup|reach\s+out|loop\s+in|connect\s+with|check\s+in\s+with)"
    r"(?:\s+with\s+(.+?))?(?:\s+by\s+([^,.\n]+))?[.\n]?$",
    re.IGNORECASE | re.MULTILINE,
)


# ------------------------------------------------------------------
# Dedup helper
# ------------------------------------------------------------------


def _ai_dedup_id(source_note_id: str, description: str) -> str:
    return content_hash(f"{source_note_id}::{description[:120]}")


def _ai_exists(conn, ai_id: str) -> bool:
    row = conn.execute("SELECT id FROM action_items WHERE id = ?", [ai_id]).fetchone()
    return row is not None


def _write_action_item(conn, ai: ActionItem) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO action_items
            (id, signal_id, source_note_id, assigned_to, description, due_date, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [ai.id, ai.signal_id, ai.source_note_id, ai.assigned_to, ai.description,
         ai.due_date, ai.status, ai.created_at],
    )


@dataclass
class ExtractionResult:
    written: int = 0
    skipped: int = 0
    failed: int = 0
    action_items: list[ActionItem] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------
# Main extractor
# ------------------------------------------------------------------


def extract_action_items(note: NoteRecord, conn, force: bool = False) -> ExtractionResult:
    """Extract action items from a note's body and write them to DuckDB.

    Args:
        note: A NoteRecord whose body will be scanned.
        conn: Open DuckDB connection.
        force: If True, overwrite existing action items for this note.
    """
    result = ExtractionResult()
    body = note.body or ""
    anchor = note.note_date or date.today()
    seen_descriptions: set[str] = set()

    candidates: list[ActionItem] = []

    # --- Pattern 1: Manager commitments ---
    for m in _MANAGER_COMMITMENT_RE.finditer(body):
        description = m.group(1).strip().rstrip(".")
        if len(description) < 8 or len(description.split()) < 2:
            continue
        due_str = m.group(2)
        due_date = _resolve_relative_date(due_str, anchor) if due_str else None
        candidates.append(ActionItem(
            source_note_id=note.id,
            assigned_to="manager",
            description=description,
            due_date=due_date,
        ))

    # --- Pattern 2: TODOs ---
    for m in _TODO_RE.finditer(body):
        description = m.group(1).strip().rstrip(".")
        if len(description) < 8 or len(description.split()) < 2:
            continue
        due_str = m.group(2)
        due_date = _resolve_relative_date(due_str, anchor) if due_str else None
        candidates.append(ActionItem(
            source_note_id=note.id,
            assigned_to="manager",
            description=description,
            due_date=due_date,
        ))

    # --- Pattern 3: Waiting on ---
    for m in _WAITING_ON_RE.finditer(body):
        assignee_raw = m.group(1).strip().rstrip(".,")
        # Cap fragment length — long fragments indicate the regex ran too far
        if len(assignee_raw) > 50:
            assignee_raw = assignee_raw[:50].rsplit(" ", 1)[0]
        action = (m.group(2) or m.group(3) or "").strip().rstrip(".")
        if len(action) > 80:
            action = action[:80].rsplit(" ", 1)[0] + "…"
        due_str = m.group(4)
        due_date = _resolve_relative_date(due_str, anchor) if due_str else None
        description = f"Waiting on {assignee_raw}" + (f" to {action}" if action else "")
        candidates.append(ActionItem(
            source_note_id=note.id,
            assigned_to=assignee_raw,
            description=description,
            due_date=due_date,
        ))

    # --- Pattern 4: Follow-ups ---
    for m in _FOLLOWUP_RE.finditer(body):
        with_whom = (m.group(1) or "").strip()
        due_str = m.group(2)
        due_date = _resolve_relative_date(due_str, anchor) if due_str else None
        description = "Follow up" + (f" with {with_whom}" if with_whom else "")
        candidates.append(ActionItem(
            source_note_id=note.id,
            assigned_to="manager",
            description=description,
            due_date=due_date,
        ))

    # Write to DB, deduplicating by description
    for ai in candidates:
        desc_key = ai.description.lower().strip()
        if desc_key in seen_descriptions:
            result.skipped += 1
            result.skip_reasons["duplicate_within_note"] = (
                result.skip_reasons.get("duplicate_within_note", 0) + 1
            )
            continue
        seen_descriptions.add(desc_key)

        ai_id = _ai_dedup_id(note.id, ai.description)
        ai = ai.model_copy(update={"id": ai_id})

        if not force and _ai_exists(conn, ai_id):
            result.skipped += 1
            result.skip_reasons["action_item_already_exists"] = (
                result.skip_reasons.get("action_item_already_exists", 0) + 1
            )
            continue

        try:
            _write_action_item(conn, ai)
            result.written += 1
            result.action_items.append(ai)
        except Exception as exc:
            logger.error("Failed to write action item: %s", exc)
            result.failed += 1

    return result


def extract_action_items_from_all_notes(
    conn, run_date: date | None = None, force: bool = False
) -> ExtractionResult:
    """Run action item extraction across all notes in the DB."""
    from manager_os.schemas import NoteRecord

    rows = conn.execute(
        "SELECT id, raw_document_id, note_date, note_type, entity_type, "
        "entity_name, title, body, tags, created_at FROM notes"
    ).fetchall()

    combined = ExtractionResult()
    for row in rows:
        note_type_val = row[3] or ""
        # Skip meta/template/instruction notes — they contain boilerplate patterns
        if note_type_val.lower() in ("template", "meta", "instructions", "prompt", "index"):
            combined.skipped += 1
            combined.skip_reasons["junk_note_type"] = (
                combined.skip_reasons.get("junk_note_type", 0) + 1
            )
            continue
        note = NoteRecord(
            id=row[0],
            raw_document_id=row[1],
            note_date=row[2],
            note_type=note_type_val,
            entity_type=row[4] or "",
            entity_name=row[5] or "",
            title=row[6] or "",
            body=row[7] or "",
            tags=json.loads(row[8]) if row[8] else [],
        )
        r = extract_action_items(note, conn, force=force)
        combined.written += r.written
        combined.skipped += r.skipped
        combined.failed += r.failed
        combined.action_items.extend(r.action_items)
        for reason, count in r.skip_reasons.items():
            combined.skip_reasons[reason] = combined.skip_reasons.get(reason, 0) + count

    return combined
