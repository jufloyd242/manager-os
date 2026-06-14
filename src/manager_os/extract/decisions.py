"""Decision extraction from note bodies.

Uses regex patterns to detect explicit decisions and agreed-upon actions.
Writes Decision records to the decisions table.

Dedup key: content_hash(f"{note_id}::{description[:120]}")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

from manager_os.db import content_hash
from manager_os.schemas import Decision, NoteRecord

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Regex patterns for decision detection
# ------------------------------------------------------------------

_DECISION_PATTERNS: list[re.Pattern] = [
    # "Decision: ..." or "Decision — ..."
    re.compile(
        r"(?i)^[*-]?\s*(?:decision|decided)[:\-–—]\s*(.+?)(?:\.|$)",
        re.MULTILINE,
    ),
    # "We decided to ..." / "We agreed to ..."
    re.compile(
        r"(?i)\b(?:we\s+(?:decided|agreed|resolved|concluded)\s+(?:to|that)|"
        r"team\s+(?:decided|agreed|resolved)\s+(?:to|that))\s+(.+?)(?:[.;]|$)",
        re.MULTILINE,
    ),
    # "Going with ..." / "Will move forward with ..."
    re.compile(
        r"(?i)\b(?:going\s+with|will\s+move\s+forward\s+with|approved\s+to\s+proceed\s+with)\s+(.+?)(?:[.;]|$)",
        re.MULTILINE,
    ),
    # "Agreed: ..." or "Agreed — ..."
    re.compile(
        r"(?i)^[*-]?\s*agreed[:\-–—]\s*(.+?)(?:\.|$)",
        re.MULTILINE,
    ),
    # "Resolved to ..."
    re.compile(
        r"(?i)\bresolved\s+to\s+(.+?)(?:[.;]|$)",
        re.MULTILINE,
    ),
]

_MIN_DESCRIPTION_LEN = 8  # skip very short matches


# ------------------------------------------------------------------
# ExtractionResult
# ------------------------------------------------------------------


@dataclass
class ExtractionResult:
    written: int = 0
    skipped: int = 0
    failed: int = 0
    items: list[Decision] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------
# Core extraction
# ------------------------------------------------------------------


def _extract_raw_decisions(text: str) -> list[str]:
    """Return raw description strings matched by any pattern."""
    matches: list[str] = []
    for pat in _DECISION_PATTERNS:
        for m in pat.finditer(text):
            desc = m.group(1).strip()
            if len(desc) >= _MIN_DESCRIPTION_LEN:
                matches.append(desc)
    return matches


def extract_decisions(note: NoteRecord, conn, force: bool = False) -> ExtractionResult:
    """Extract decisions from a single note and write to DB.

    Args:
        note: The NoteRecord to scan.
        conn: Open DuckDB connection.
        force: If True, overwrite existing records.

    Returns:
        ExtractionResult with written/skipped/failed counts.
    """
    result = ExtractionResult()
    raw_descriptions = _extract_raw_decisions(note.body)

    for raw_desc in raw_descriptions:
        # Truncate for dedup key
        dedup_key = content_hash(f"{note.id}::{raw_desc[:120]}")

        exists = conn.execute(
            "SELECT id FROM decisions WHERE id = ?", [dedup_key]
        ).fetchone()

        if exists and not force:
            result.skipped += 1
            result.skip_reasons["decision_already_exists"] = (
                result.skip_reasons.get("decision_already_exists", 0) + 1
            )
            continue

        decision = Decision(
            id=dedup_key,
            entity_type=note.entity_type or "",
            entity_name=note.entity_name or "",
            description=raw_desc,
            decision_date=note.note_date,
            status="made",
            owner="",
            source_note_id=note.id,
        )

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO decisions
                    (id, entity_type, entity_name, description, decision_date,
                     status, owner, source_note_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    decision.id,
                    decision.entity_type,
                    decision.entity_name,
                    decision.description,
                    decision.decision_date.isoformat() if decision.decision_date else None,
                    decision.status,
                    decision.owner,
                    decision.source_note_id,
                    decision.created_at,
                ],
            )
            result.written += 1
            result.items.append(decision)
        except Exception as exc:
            logger.warning("Failed to write decision %s: %s", dedup_key, exc)
            result.failed += 1

    return result


def extract_decisions_from_all_notes(
    conn, force: bool = False
) -> ExtractionResult:
    """Run decision extraction across all notes in the DB.

    Args:
        conn: Open DuckDB connection.
        force: If True, overwrite existing records.

    Returns:
        Aggregated ExtractionResult.
    """
    rows = conn.execute(
        "SELECT id, raw_document_id, note_date, note_type, entity_type, "
        "entity_name, title, body, tags, created_at FROM notes"
    ).fetchall()

    total = ExtractionResult()
    for row in rows:
        try:
            note = NoteRecord(
                id=row[0],
                raw_document_id=row[1],
                note_date=row[2],
                note_type=row[3] or "",
                entity_type=row[4] or "",
                entity_name=row[5] or "",
                title=row[6] or "",
                body=row[7] or "",
                tags=[],
                created_at=row[9],
            )
            r = extract_decisions(note, conn, force=force)
            total.written += r.written
            total.skipped += r.skipped
            total.failed += r.failed
            total.items.extend(r.items)
            for reason, count in r.skip_reasons.items():
                total.skip_reasons[reason] = total.skip_reasons.get(reason, 0) + count
        except Exception as exc:
            logger.warning("Skipping note %s during decision extraction: %s", row[0], exc)
            total.failed += 1

    return total
