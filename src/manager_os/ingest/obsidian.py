"""Obsidian vault ingestor.

Recursively walks an Obsidian vault directory, parses each .md file
(YAML frontmatter + body), computes a content hash for deduplication,
and writes RawDocument + NoteRecord rows to DuckDB.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from manager_os.db import content_hash, get_connection
from manager_os.schemas import NoteRecord, RawDocument

logger = logging.getLogger(__name__)

# Directories to skip inside the vault
_SKIP_DIRS = {".obsidian", ".git", ".trash", "templates"}

# Frontmatter type field → canonical note_type
_TYPE_MAP = {
    "1on1": "1on1",
    "one-on-one": "1on1",
    "client": "client",
    "client-status": "client",
    "deal": "deal",
    "meeting": "meeting",
    "team": "team",
    "practice": "practice",
}

# Directory name heuristics → note_type fallback
_DIR_TYPE_MAP = {
    "1on1": "1on1",
    "1-on-1": "1on1",
    "one-on-ones": "1on1",
    "clients": "client",
    "client-notes": "client",
    "deals": "deal",
    "deal-notes": "deal",
    "meetings": "meeting",
    "team": "team",
    "practice": "practice",
}


@dataclass
class IngestResult:
    ingested: int = 0
    ingested_with_warnings: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)


def _infer_note_type(fm: dict, file_path: Path) -> str:
    """Infer note_type from frontmatter 'type' field, then directory name."""
    raw_type = str(fm.get("type", "")).lower().strip()
    if raw_type in _TYPE_MAP:
        return _TYPE_MAP[raw_type]

    for part in reversed(file_path.parts):
        part_lower = part.lower()
        if part_lower in _DIR_TYPE_MAP:
            return _DIR_TYPE_MAP[part_lower]

    return ""


def _parse_date(value: object) -> str | None:
    """Parse a frontmatter date value into an ISO string, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    # datetime.date or datetime.datetime from frontmatter YAML parse
    try:
        return value.isoformat()  # type: ignore[union-attr]
    except AttributeError:
        return str(value)


def _doc_exists(conn, source_path: str, c_hash: str) -> bool:
    """Return True if a raw_document with this path+hash already exists."""
    row = conn.execute(
        "SELECT id FROM raw_documents WHERE source_path = ? AND content_hash = ?",
        [source_path, c_hash],
    ).fetchone()
    return row is not None


def _write_raw_document(conn, doc: RawDocument) -> None:
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


def _write_note(conn, note: NoteRecord) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO notes
            (id, raw_document_id, note_date, note_type, entity_type,
             entity_name, title, body, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            note.id,
            note.raw_document_id,
            note.note_date,
            note.note_type,
            note.entity_type,
            note.entity_name,
            note.title,
            note.body,
            json.dumps(note.tags),
            note.created_at,
        ],
    )


def ingest_vault(vault_path: str, conn, force: bool = False) -> IngestResult:
    """Ingest all .md files from the given vault path into DuckDB.

    Args:
        vault_path: Absolute or relative path to the Obsidian vault root.
        conn: Open DuckDB connection with schema already initialized.
        force: If True, re-ingest files even if content_hash is unchanged.

    Returns:
        IngestResult with counts of ingested, skipped, and failed files.
    """
    result = IngestResult()
    vault = Path(vault_path)

    if not vault.exists():
        raise FileNotFoundError(f"Vault path does not exist: {vault_path}")

    for md_file in vault.rglob("*.md"):
        # Skip hidden files and configured skip directories
        if any(part.startswith(".") for part in md_file.parts):
            continue
        if any(skip in md_file.parts for skip in _SKIP_DIRS):
            continue

        try:
            _ingest_file(md_file, conn, force, result)
        except Exception as exc:
            logger.warning("Failed to ingest %s: %s", md_file, exc)
            result.failed += 1
            result.errors.append(f"{md_file}: {exc}")

    return result


def _strip_frontmatter_block(raw_text: str) -> str:
    """Remove the leading '---' frontmatter block from raw markdown text.

    Used as fallback when YAML parse fails — returns the body as best-effort text
    so the document content is still searchable/extractable.
    """
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return raw_text.strip()
    # Find the closing '---' (or '...')
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() in ("---", "..."):
            return "\n".join(lines[i + 1:]).strip()
    # No closing delimiter found — return everything after first line
    return "\n".join(lines[1:]).strip()


def _ingest_file(
    md_file: Path,
    conn,
    force: bool,
    result: IngestResult,
) -> None:
    raw_text = md_file.read_text(encoding="utf-8", errors="replace")
    c_hash = content_hash(raw_text)
    source_path = str(md_file.resolve())

    if not force and _doc_exists(conn, source_path, c_hash):
        result.skipped += 1
        result.skip_reasons["duplicate_content_hash"] = (
            result.skip_reasons.get("duplicate_content_hash", 0) + 1
        )
        return

    # Parse frontmatter — tolerate malformed YAML by falling back to body-only
    fm: dict = {}
    body: str = ""
    frontmatter_warning: str | None = None
    try:
        post = frontmatter.loads(raw_text)
        fm = dict(post.metadata)
        body = post.content.strip()
    except Exception as exc:
        frontmatter_warning = f"frontmatter parse error in {md_file}: {exc}"
        logger.warning("%s", frontmatter_warning)
        # Preserve the raw text as body; strip any leading "---" delimiter block
        # so extraction doesn't choke on raw YAML syntax
        body = _strip_frontmatter_block(raw_text)

    # Build title from frontmatter or filename
    title = str(fm.get("title", md_file.stem.replace("_", " ").replace("-", " ")))

    # Note type
    note_type = _infer_note_type(fm, md_file)

    # Entity info
    entity_name = str(fm.get("entity", fm.get("person", fm.get("client", ""))))
    entity_type = _infer_entity_type(note_type)

    # Note date
    note_date_str = _parse_date(fm.get("date"))

    # Tags
    raw_tags = fm.get("tags", [])
    tags: list[str] = raw_tags if isinstance(raw_tags, list) else [str(raw_tags)]

    # File modification time
    mtime = datetime.fromtimestamp(md_file.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)

    doc = RawDocument(
        id=content_hash(source_path),  # stable ID — same path always same ID
        source_type="obsidian",
        source_path=source_path,
        file_modified_at=mtime,
        content_hash=c_hash,
        content=raw_text,
        metadata={k: str(v) for k, v in fm.items() if k not in ("tags",)},
    )

    note = NoteRecord(
        id=content_hash(source_path + ":note"),  # stable, paired with doc
        raw_document_id=doc.id,
        note_date=note_date_str,  # type: ignore[arg-type]
        note_type=note_type,
        entity_type=entity_type,
        entity_name=entity_name,
        title=title,
        body=body,
        tags=tags,
    )

    _write_raw_document(conn, doc)
    _write_note(conn, note)
    if frontmatter_warning:
        result.ingested_with_warnings += 1
        result.warnings.append(frontmatter_warning)
    else:
        result.ingested += 1


def _infer_entity_type(note_type: str) -> str:
    return {
        "1on1": "person",
        "client": "client",
        "deal": "deal",
        "meeting": "team",
        "team": "team",
        "practice": "practice",
    }.get(note_type, "")
