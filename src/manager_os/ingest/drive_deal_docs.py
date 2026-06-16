"""Read-only retrieval of Deal SOW and Deal Sheet links from Google Drive.

For each deal, searches Google Drive via Gemini CLI for:
  - INT SOW (document type: int_sow)
  - Deal Sheet (document type: deal_sheet)

Retrieved metadata (title, url) is stored locally in:
  data/raw/workspace_snapshots/deal_docs/YYYY-MM-DD.json

And ingested into the DuckDB table ``deal_documents``.

NO writes to Google Drive or any external system are made.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from manager_os.db import content_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema / DB helpers
# ---------------------------------------------------------------------------

_DEAL_DOCUMENTS_DDL = """
CREATE TABLE IF NOT EXISTS deal_documents (
    id            VARCHAR PRIMARY KEY,
    deal_id       VARCHAR NOT NULL,
    account       VARCHAR,
    deal_name     VARCHAR,
    document_type VARCHAR NOT NULL,   -- 'int_sow' | 'deal_sheet'
    title         VARCHAR,
    url           VARCHAR,
    source        VARCHAR,
    retrieved_at  TIMESTAMP NOT NULL,
    search_status VARCHAR,            -- 'found' | 'not_found' | 'error'
    error         VARCHAR
);
"""

_DEAL_DOCUMENTS_INDEX = """
CREATE INDEX IF NOT EXISTS deal_documents_deal_id_idx ON deal_documents (deal_id);
CREATE INDEX IF NOT EXISTS deal_documents_type_idx ON deal_documents (document_type);
"""


def ensure_deal_documents_table(conn) -> None:
    """Create the deal_documents table if it does not exist."""
    conn.execute(_DEAL_DOCUMENTS_DDL)
    conn.execute(_DEAL_DOCUMENTS_INDEX)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class DealDocResult:
    deal_id: str
    account: str
    deal_name: str
    document_type: str  # 'int_sow' | 'deal_sheet'
    title: str = ""
    url: str = ""
    source: str = ""
    search_status: str = "not_found"
    error: str = ""


@dataclass
class FetchResult:
    fetched: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = False
    results: list[DealDocResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

_READ_ONLY_PREFIX = """You are operating in read-only mode.
Do NOT create, edit, delete, send, move, or modify anything in Google Drive.
Retrieve only the requested information.
Return STRICT JSON only — no prose, no markdown fences.
If you cannot find a document, return {"found": false, "reason": "..."}.
Include the Google Drive URL (webViewLink) for each document you find.
"""


def build_drive_search_prompt(deal_id: str, deal_name: str, account: str) -> str:
    """Build the Gemini CLI prompt to search Google Drive for a deal's docs."""
    return f"""{_READ_ONLY_PREFIX}

Search Google Drive for documents related to this deal:
  - Opportunity Number / Deal ID: {deal_id}
  - Deal Name: {deal_name}
  - Account: {account}

Find the following documents:
1. INT SOW (Internal Statement of Work) — look for files with "INT SOW", "SOW", or "Statement of Work"
   combined with the opportunity number or deal/account name.
2. Deal Sheet — look for files with "Deal Sheet" combined with the opportunity number or account name.

For each found document return:
{{
  "int_sow": {{
    "found": true,
    "title": "<exact Google Drive file title>",
    "url": "<webViewLink>",
    "source": "Google Drive"
  }},
  "deal_sheet": {{
    "found": true,
    "title": "<exact Google Drive file title>",
    "url": "<webViewLink>",
    "source": "Google Drive"
  }}
}}

If a document is not found, set "found": false and omit title/url.
Only return the JSON object — nothing else.
"""


# ---------------------------------------------------------------------------
# Gemini CLI execution
# ---------------------------------------------------------------------------


def _run_gemini_cli(
    prompt: str,
    *,
    bin_path: str = "gemini",
    model: str = "gemini-2.0-flash",
    timeout: int = 60,
    yolo: bool = True,
    workdir: str = "",
    extra_args: list[str] | None = None,
    dry_run: bool = False,
) -> tuple[bool, str, str]:
    """Run Gemini CLI and return (success, stdout, stderr).

    In dry_run mode returns a placeholder JSON response without running the CLI.
    """
    if dry_run:
        placeholder = json.dumps({
            "int_sow": {"found": False, "reason": "dry-run — no CLI call made"},
            "deal_sheet": {"found": False, "reason": "dry-run — no CLI call made"},
        })
        return True, placeholder, ""

    cmd = [bin_path, "-m", model]
    if yolo:
        cmd.extend(["-y"])
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend(["-p", prompt])

    env = {**os.environ}
    cwd = workdir or None

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    except FileNotFoundError:
        return False, "", f"Gemini CLI not found at '{bin_path}'"
    except subprocess.TimeoutExpired:
        return False, "", f"Gemini CLI timed out after {timeout}s"
    except Exception as exc:
        return False, "", str(exc)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def _parse_doc_response(raw: str) -> dict[str, Any]:
    """Extract and parse the JSON object from Gemini CLI output."""
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        inner = [l for l in lines if not l.startswith("```")]
        text = "\n".join(inner).strip()

    # Find the first { ... } JSON block
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return {}

    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Snapshot I/O
# ---------------------------------------------------------------------------


def _snapshot_path(snapshot_dir: str, target_date: date) -> Path:
    p = Path(snapshot_dir) / "deal_docs"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{target_date.isoformat()}.json"


def _write_snapshot(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, default=str)


def _read_snapshot(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------


def fetch_deal_docs_for_deal(
    deal_id: str,
    deal_name: str,
    account: str,
    *,
    bin_path: str = "gemini",
    model: str = "gemini-2.0-flash",
    timeout: int = 60,
    yolo: bool = True,
    workdir: str = "",
    extra_args: list[str] | None = None,
    dry_run: bool = False,
) -> list[DealDocResult]:
    """Fetch SOW and Deal Sheet links for a single deal from Google Drive.

    Returns a list of DealDocResult (one per document type).
    """
    prompt = build_drive_search_prompt(deal_id, deal_name, account)
    ok, stdout, stderr = _run_gemini_cli(
        prompt,
        bin_path=bin_path,
        model=model,
        timeout=timeout,
        yolo=yolo,
        workdir=workdir,
        extra_args=extra_args,
        dry_run=dry_run,
    )

    results: list[DealDocResult] = []

    if not ok:
        error_msg = stderr or "Gemini CLI returned non-zero exit"
        for doc_type in ("int_sow", "deal_sheet"):
            results.append(DealDocResult(
                deal_id=deal_id, account=account, deal_name=deal_name,
                document_type=doc_type, search_status="error", error=error_msg,
            ))
        return results

    parsed = _parse_doc_response(stdout)
    if not parsed:
        logger.warning("Could not parse Gemini response for deal %s: %r", deal_id, stdout[:200])
        for doc_type in ("int_sow", "deal_sheet"):
            results.append(DealDocResult(
                deal_id=deal_id, account=account, deal_name=deal_name,
                document_type=doc_type, search_status="error",
                error="Could not parse JSON response",
            ))
        return results

    for doc_type in ("int_sow", "deal_sheet"):
        doc = parsed.get(doc_type, {})
        if not isinstance(doc, dict):
            doc = {}

        found = doc.get("found", False)
        if found:
            results.append(DealDocResult(
                deal_id=deal_id, account=account, deal_name=deal_name,
                document_type=doc_type,
                title=doc.get("title", ""),
                url=doc.get("url", ""),
                source=doc.get("source", "Google Drive"),
                search_status="found",
            ))
        else:
            results.append(DealDocResult(
                deal_id=deal_id, account=account, deal_name=deal_name,
                document_type=doc_type, search_status="not_found",
                error=doc.get("reason", ""),
            ))

    return results


def fetch_deal_docs(
    conn,
    *,
    snapshot_dir: str,
    target_date: date | None = None,
    deal_id_filter: str | None = None,
    limit: int | None = None,
    bin_path: str = "gemini",
    model: str = "gemini-2.0-flash",
    timeout: int = 60,
    yolo: bool = True,
    workdir: str = "",
    extra_args: list[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> FetchResult:
    """Fetch SOW and Deal Sheet links for active deals.

    Args:
        conn: Open DuckDB connection.
        snapshot_dir: Base directory for workspace snapshots.
        target_date: Date label for snapshot (defaults to today).
        deal_id_filter: If set, only fetch for this deal_id.
        limit: Max deals to process.
        dry_run: If True, skips actual CLI calls.
        force: Re-fetch even if results already in DB.

    Returns:
        FetchResult with counts and DealDocResult list.
    """
    if target_date is None:
        target_date = date.today()

    ensure_deal_documents_table(conn)

    # Load deals from DB
    query = "SELECT id, deal_id, deal_name, account FROM deals WHERE deal_name != '' ORDER BY deal_name"
    deal_rows = conn.execute(query).fetchall()

    # Filter by deal_id if provided
    if deal_id_filter:
        deal_rows = [
            r for r in deal_rows
            if r[1] == deal_id_filter or r[0] == deal_id_filter
        ]

    # Apply limit
    if limit is not None and limit > 0:
        deal_rows = deal_rows[:limit]

    if not deal_rows:
        logger.info("No deals found to fetch docs for")
        return FetchResult(dry_run=dry_run)

    result = FetchResult(dry_run=dry_run)
    snapshot_records: list[dict] = []
    retrieved_at = datetime.utcnow()

    for db_id, deal_id_val, deal_name, account in deal_rows:
        effective_id = deal_id_val or db_id

        # Skip if already fetched and not forcing
        if not force:
            existing = conn.execute(
                "SELECT COUNT(*) FROM deal_documents WHERE deal_id = ? AND search_status = 'found'",
                [effective_id],
            ).fetchone()[0]
            if existing > 0:
                result.skipped += 1
                continue

        logger.info("Fetching deal docs for: %s (%s)", deal_name, effective_id)
        doc_results = fetch_deal_docs_for_deal(
            deal_id=effective_id,
            deal_name=deal_name,
            account=account or "",
            bin_path=bin_path,
            model=model,
            timeout=timeout,
            yolo=yolo,
            workdir=workdir,
            extra_args=extra_args,
            dry_run=dry_run,
        )

        for doc in doc_results:
            row_id = content_hash(
                f"deal_doc::{doc.deal_id}::{doc.document_type}::{doc.url or 'nf'}"
            )
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO deal_documents
                        (id, deal_id, account, deal_name, document_type,
                         title, url, source, retrieved_at, search_status, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        row_id, doc.deal_id, doc.account, doc.deal_name,
                        doc.document_type, doc.title, doc.url, doc.source,
                        retrieved_at, doc.search_status, doc.error or None,
                    ],
                )
                result.fetched += 1
                result.results.append(doc)
            except Exception as exc:
                logger.warning("Failed to write deal doc record: %s", exc)
                result.failed += 1

            snapshot_records.append({
                "deal_id": doc.deal_id,
                "account": doc.account,
                "deal_name": doc.deal_name,
                "document_type": doc.document_type,
                "title": doc.title,
                "url": doc.url,
                "source": doc.source,
                "search_status": doc.search_status,
                "error": doc.error,
                "retrieved_at": retrieved_at.isoformat(),
            })

    # Write snapshot
    if snapshot_records and not dry_run:
        snap_path = _snapshot_path(snapshot_dir, target_date)
        _write_snapshot(snap_path, snapshot_records)
        logger.info("Wrote deal docs snapshot to %s", snap_path)

    return result


# ---------------------------------------------------------------------------
# Ingest from existing snapshot
# ---------------------------------------------------------------------------


def ingest_deal_docs_snapshot(
    conn,
    snapshot_path: Path | str,
    force: bool = False,
) -> FetchResult:
    """Ingest a previously-saved deal_docs snapshot JSON into deal_documents table."""
    ensure_deal_documents_table(conn)
    result = FetchResult()

    records = _read_snapshot(Path(snapshot_path))
    if not records:
        return result

    for rec in records:
        deal_id = rec.get("deal_id", "")
        doc_type = rec.get("document_type", "")
        url = rec.get("url", "")
        row_id = content_hash(f"deal_doc::{deal_id}::{doc_type}::{url or 'nf'}")

        if not force:
            existing = conn.execute(
                "SELECT id FROM deal_documents WHERE id = ?", [row_id]
            ).fetchone()
            if existing:
                result.skipped += 1
                continue

        try:
            retrieved_at = rec.get("retrieved_at", datetime.utcnow().isoformat())
            conn.execute(
                """
                INSERT OR REPLACE INTO deal_documents
                    (id, deal_id, account, deal_name, document_type,
                     title, url, source, retrieved_at, search_status, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row_id,
                    deal_id,
                    rec.get("account", ""),
                    rec.get("deal_name", ""),
                    doc_type,
                    rec.get("title", ""),
                    url,
                    rec.get("source", ""),
                    retrieved_at,
                    rec.get("search_status", "not_found"),
                    rec.get("error"),
                ],
            )
            result.fetched += 1
        except Exception as exc:
            logger.warning("Failed to ingest deal doc record: %s", exc)
            result.failed += 1

    return result
