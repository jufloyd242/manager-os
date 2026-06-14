"""Workspace summary ingestor.

Reads a daily markdown/text summary file from the configured summaries
directory and writes it to the raw_documents table.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from manager_os.db import content_hash, get_connection
from manager_os.schemas import RawDocument

logger = logging.getLogger(__name__)

_EXTENSIONS = [".md", ".txt"]


@dataclass
class IngestResult:
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _find_summary_file(summary_dir: Path, target_date: date) -> Path | None:
    """Find a summary file for the given date. Tries .md then .txt."""
    date_str = target_date.isoformat()
    for ext in _EXTENSIONS:
        candidate = summary_dir / f"{date_str}{ext}"
        if candidate.exists():
            return candidate
    return None


def _stable_id(source_path: str) -> str:
    return content_hash(source_path)


def _doc_exists(conn, doc_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM raw_documents WHERE id = ?", [doc_id]
    ).fetchone()
    return row is not None


def ingest_summary(
    summary_dir: str,
    target_date: date,
    conn,
    force: bool = False,
) -> IngestResult:
    """Ingest the workspace summary file for a given date.

    Args:
        summary_dir: Directory containing YYYY-MM-DD.md summary files.
        target_date: The date whose summary to ingest.
        conn: Open DuckDB connection.
        force: If True, re-ingest even if content_hash is unchanged.

    Returns:
        IngestResult with ingested/skipped/failed counts.
    """
    result = IngestResult()
    summary_path = Path(summary_dir)

    if not summary_path.exists():
        logger.warning("Summary directory does not exist: %s", summary_dir)
        return result

    file_path = _find_summary_file(summary_path, target_date)
    if file_path is None:
        logger.info("No summary file found for %s in %s", target_date, summary_dir)
        return result

    try:
        raw_text = file_path.read_text(encoding="utf-8", errors="replace")
        c_hash = content_hash(raw_text)
        source_path = str(file_path.resolve())
        doc_id = _stable_id(source_path)

        if not force and _doc_exists(conn, doc_id):
            result.skipped += 1
            return result

        mtime = datetime.fromtimestamp(
            file_path.stat().st_mtime, tz=timezone.utc
        ).replace(tzinfo=None)

        doc = RawDocument(
            id=doc_id,
            source_type="workspace_summary",
            source_path=source_path,
            file_modified_at=mtime,
            content_hash=c_hash,
            content=raw_text,
            metadata={"summary_date": target_date.isoformat()},
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO raw_documents
                (id, ingested_at, source_type, source_path, file_modified_at,
                 content_hash, content, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                doc.id,
                doc.ingested_at,
                doc.source_type,
                doc.source_path,
                doc.file_modified_at,
                doc.content_hash,
                doc.content,
                json.dumps(doc.metadata),
            ],
        )
        result.ingested += 1

    except Exception as exc:
        logger.error("Failed to ingest summary for %s: %s", target_date, exc)
        result.failed += 1
        result.errors.append(str(exc))

    return result
