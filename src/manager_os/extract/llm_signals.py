"""LLM-based signal extraction via Gemini CLI.

All LLM calls route through the local Gemini CLI binary using existing
Vertex AI authentication — no API keys, no OpenAI SDK.

Only notes classified as ``signal`` tier are sent to Gemini.  Context-tier
notes are included as related context when explicitly attached to a signal
note but do not produce standalone signals.

Source tier determination uses ``config/source_scope.yaml`` via
``manager_os.scope.classify_source``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from manager_os.db import content_hash
from manager_os.schemas import Signal

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Availability
# ------------------------------------------------------------------


class LLMExtractionUnavailable(RuntimeError):
    """Raised when Gemini CLI is not configured or not available."""


# ------------------------------------------------------------------
# Prompt construction
# ------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert engineering management assistant. Given a manager's Obsidian
note, extract actionable management signals as strict JSON.

Return ONLY a JSON array (no markdown, no explanation). Each element must have:
  entity_type: "person" | "client" | "deal" | "team" | "practice"
  entity_name: string (canonical name of the entity)
  signal_type: "risk" | "blocker" | "ask" | "decision" | "staffing_change" |
               "deal_change" | "client_update" | "follow_up" | "stale_item" |
               "people_health" | "utilization_risk" | "sow_loe_review"
  severity: "critical" | "high" | "medium" | "low"
  summary: string (one sentence, max 120 chars)
  why_it_matters: string (one sentence, max 200 chars, include specific evidence)
  requires_manager_attention: boolean
  confidence: float between 0.0 and 1.0

Rules:
- Use ONLY the provided source text. Do NOT invent people, clients, or deals.
- Cite specific evidence in why_it_matters.
- Omit weak/speculative items. Prefer returning [] over guessing.
- Return [] when no current manager action, risk, or decision exists.
- Ignore reference material, process documentation, training content, and
  historical notes. These are not actionable.
- Mark old/historical items as "stale_item" with severity="low".
- Do NOT calculate allocation %, capacity %, close-date math, or financials.
- Do NOT infer weakly. Every item needs direct evidence from the note text.
- Only extract items the manager (Justin) should care about as a manager.
- Be conservative: fewer, higher-quality items are better than many speculative ones.
"""


def _build_user_prompt(
    note_date: str,
    entity_name: str,
    entity_type: str,
    body: str,
    source_tier: str = "signal",
) -> str:
    from manager_os.llm.gemini_cli import LLM_MAX_CHARS_PER_NOTE
    lines = [
        f"Date: {note_date}",
        f"Entity: {entity_name} ({entity_type})",
        f"Source tier: {source_tier}",
        "",
        "Note body:",
        body[:LLM_MAX_CHARS_PER_NOTE],
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------
# Parse and validate
# ------------------------------------------------------------------

_VALID_ENTITY_TYPES = {"person", "client", "deal", "team", "practice"}
_VALID_SIGNAL_TYPES = {
    "risk", "blocker", "ask", "decision", "staffing_change", "deal_change",
    "client_update", "follow_up", "stale_item", "meeting_prep",
    "people_health", "utilization_risk", "sow_loe_review",
}
_VALID_SEVERITIES = {"critical", "high", "medium", "low"}


def _parse_llm_response(raw: str) -> list[dict]:
    """Parse and validate the LLM JSON response. Returns valid signal dicts."""
    from manager_os.llm.gemini_cli import _extract_json

    try:
        clean = _extract_json(raw)
        parsed = json.loads(clean)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Could not parse LLM response: {raw[:300]}") from exc

    if not isinstance(parsed, list):
        raise ValueError("Expected a JSON array from LLM.")

    valid = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        et = item.get("entity_type", "")
        st = item.get("signal_type", "")
        sev = item.get("severity", "")
        if et not in _VALID_ENTITY_TYPES:
            continue
        if st not in _VALID_SIGNAL_TYPES:
            continue
        if sev not in _VALID_SEVERITIES:
            continue
        summary = str(item.get("summary", "")).strip()
        if not summary:
            continue
        item["entity_type"] = et
        item["signal_type"] = st
        item["severity"] = sev
        item["summary"] = summary[:200]
        item["why_it_matters"] = str(item.get("why_it_matters", ""))[:200]
        item["confidence"] = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
        item["requires_manager_attention"] = bool(item.get("requires_manager_attention", False))
        valid.append(item)
    return valid


# ------------------------------------------------------------------
# ExtractionResult
# ------------------------------------------------------------------


@dataclass
class LLMExtractionResult:
    written: int = 0
    skipped: int = 0
    failed: int = 0
    items: list[Signal] = field(default_factory=list)

    # Tier/candidate counters for observability
    candidates_considered: int = 0
    candidates_skipped_excluded: int = 0
    candidates_skipped_context: int = 0
    candidates_skipped_empty_body: int = 0


# ------------------------------------------------------------------
# Candidate selection (tier-aware)
# ------------------------------------------------------------------


def _select_llm_candidates(
    conn,
    max_candidates: int | None = None,
    source_path_filter: str | None = None,
    note_id: str | None = None,
    since_days: int | None = None,
) -> tuple[list[dict], int, int, int]:
    """Return note rows that are signal-tier and have a non-empty body.

    Uses source tier metadata in raw_documents.metadata JSON when available.
    Falls back to running ``classify_source`` on the source_path.

    Args:
        conn: Open DuckDB connection.
        max_candidates: Maximum rows to consider. ``None`` means unlimited.
        source_path_filter: If set, only consider notes whose source_path
            contains this substring.
        note_id: If set, only consider the note with this exact id.
        since_days: If set, only consider notes whose note_date is within
            this many days of today.

    Returns:
        Tuple of (candidates, excluded_count, context_count, empty_count).
    """
    params: list[Any] = []
    where_clauses = ["n.body != ''", "n.body IS NOT NULL"]

    if note_id:
        where_clauses.append("n.id = ?")
        params.append(note_id)
    if source_path_filter:
        where_clauses.append("rd.source_path LIKE ?")
        params.append(f"%{source_path_filter}%")
    if since_days is not None:
        cutoff = date.today() - timedelta(days=since_days)
        where_clauses.append("n.note_date >= ?")
        params.append(cutoff.isoformat())

    # Fetch a generous pool, filter tiers in Python, then cap
    # This ensures context/excluded notes don't consume the candidate limit.
    fetch_limit = 500 if max_candidates is None or max_candidates > 500 else max_candidates * 3

    query = f"""
        SELECT n.id, n.note_date, n.entity_name, n.entity_type, n.body,
               rd.source_path, rd.metadata
        FROM notes n
        LEFT JOIN raw_documents rd ON rd.id = n.raw_document_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY CASE WHEN n.entity_name IS NOT NULL AND n.entity_name != '' THEN 0 ELSE 1 END,
                 n.note_date DESC
        LIMIT ?
    """
    params.append(fetch_limit)

    rows = conn.execute(query, params).fetchall()

    # Try to resolve tiers from stored metadata or classify on the fly
    import os as _os
    vault_root = _os.environ.get("MANAGER_OS_VAULT_PATH", "")

    candidates = []
    excluded_cnt = context_cnt = empty_cnt = 0

    for row in rows:
        note_id, note_date_raw, entity_name, entity_type, body, source_path, metadata_raw = row
        body = (body or "").strip()
        if not body:
            empty_cnt += 1
            continue

        # Determine source tier
        tier = _resolve_tier_from_metadata(metadata_raw, source_path, vault_root)

        if tier == "excluded":
            excluded_cnt += 1
            continue
        if tier == "context":
            # Context notes are not standalone LLM candidates
            context_cnt += 1
            continue

        try:
            nd = note_date_raw if isinstance(note_date_raw, date) else date.fromisoformat(str(note_date_raw))
        except Exception:
            nd = date.today()

        candidates.append({
            "note_id": note_id,
            "note_date": nd,
            "entity_name": entity_name or "",
            "entity_type": entity_type or "person",
            "body": body,
            "source_path": source_path or "",
        })

    # Cap candidates AFTER tier filtering so excluded/context notes
    # do not consume the limit.
    if max_candidates is not None and len(candidates) > max_candidates:
        candidates = candidates[:max_candidates]

    return candidates, excluded_cnt, context_cnt, empty_cnt


def _resolve_tier_from_metadata(
    metadata_raw: str | None,
    source_path: str,
    vault_root: str,
) -> str:
    """Resolve source tier: check stored metadata first, then classify.

    Returns 'signal', 'context', or 'excluded'.
    """
    # Try stored metadata
    if metadata_raw:
        try:
            meta = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
            tier = meta.get("source_tier", "")
            if tier in ("signal", "context", "excluded"):
                return tier
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    # Fall back to classifying on the fly
    try:
        from manager_os.scope import classify_source, load_source_scope
        config = load_source_scope()
        result = classify_source(
            source_path=source_path,
            vault_root=vault_root,
            config=config,
        )
        return result.source_tier
    except Exception:
        return "signal"  # safe default


# ------------------------------------------------------------------
# Core extraction (Gemini CLI — no OpenAI)
# ------------------------------------------------------------------


def _extract_signals_from_note_llm(
    conn,
    note_id: str,
    note_date: date,
    entity_name: str,
    entity_type: str,
    body: str,
    source_path: str,
    timeout_seconds: int | None = None,
) -> LLMExtractionResult:
    """Send a single note to Gemini CLI and write resulting signals to DB."""
    result = LLMExtractionResult()

    user_prompt = _build_user_prompt(
        note_date.isoformat(), entity_name, entity_type, body
    )

    raw_response = ""
    try:
        from manager_os.llm.gemini_cli import generate, GeminiUnavailable
        raw_response = generate(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            timeout=timeout_seconds,
        )
        signal_dicts = _parse_llm_response(raw_response)
    except GeminiUnavailable:
        raise LLMExtractionUnavailable(
            "Gemini CLI is not available. Run 'manager-os llm-doctor' to diagnose."
        )
    except ValueError as exc:
        logger.warning("LLM parse failed for note %s: %s", note_id, exc)
        _log_extraction_failure(conn, source_path, user_prompt, raw_response, str(exc))
        result.failed += 1
        return result
    except Exception as exc:
        logger.warning("LLM call failed for note %s: %s", note_id, exc)
        _log_extraction_failure(conn, source_path, user_prompt, raw_response, str(exc))
        result.failed += 1
        return result

    for sig_dict in signal_dicts:
        sig_id = content_hash(
            f"{note_date}::{source_path}::{sig_dict['signal_type']}::{sig_dict['entity_name']}::llm"
        )
        exists = conn.execute("SELECT id FROM signals WHERE id = ?", [sig_id]).fetchone()
        if exists:
            result.skipped += 1
            continue

        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO signals
                    (id, signal_date, source, source_path, entity_type, entity_name,
                     signal_type, severity, summary, why_it_matters,
                     requires_manager_attention, owner, due_date, confidence,
                     status, created_at, updated_at)
                VALUES (?, ?, 'llm', ?, ?, ?, ?, ?, ?, ?,
                        ?, '', NULL, ?, 'open', ?, ?)
                """,
                [
                    sig_id,
                    note_date.isoformat(),
                    source_path,
                    sig_dict["entity_type"],
                    sig_dict.get("entity_name", entity_name),
                    sig_dict["signal_type"],
                    sig_dict["severity"],
                    sig_dict["summary"],
                    sig_dict.get("why_it_matters", ""),
                    sig_dict["requires_manager_attention"],
                    sig_dict["confidence"],
                    datetime.utcnow(),
                    datetime.utcnow(),
                ],
            )
            result.written += 1
        except Exception as exc:
            logger.warning("Failed to write LLM signal %s: %s", sig_id, exc)
            result.failed += 1

    return result


def _log_extraction_failure(
    conn, source_path: str, prompt: str, raw_output: str, error_detail: str
) -> None:
    fail_id = content_hash(f"llm_fail::{source_path}::{datetime.utcnow().isoformat()}")
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO extraction_failures
                (id, failed_at, source_path, prompt_used, raw_llm_output,
                 error_type, error_detail, status)
            VALUES (?, ?, ?, ?, ?, 'llm_error', ?, 'pending_review')
            """,
            [fail_id, datetime.utcnow(), source_path,
             prompt[:2000], raw_output[:2000], error_detail[:500]],
        )
    except Exception:
        pass


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------


def run_llm_extraction(
    conn,
    run_date: date | None = None,
    max_candidates: int | None = None,
    timeout_seconds: int | None = None,
    source_path_filter: str | None = None,
    note_id: str | None = None,
    since_days: int | None = None,
    progress_callback: Any | None = None,
) -> LLMExtractionResult:
    """Run LLM-based signal extraction across signal-tier notes.

    Uses Gemini CLI.  Only signal-tier notes are sent to the model.

    Args:
        conn: Open DuckDB connection.
        run_date: Date stamp for generated signals. Defaults to today.
        max_candidates: Maximum notes to send to the LLM. ``None`` means
            unlimited. Defaults to ``MANAGER_OS_LLM_MAX_CANDIDATES`` or 25.
        timeout_seconds: Per-note LLM timeout. ``None`` uses the provider default.
        source_path_filter: Optional substring filter on ``raw_documents.source_path``.
        note_id: Optional exact note id to process.
        since_days: Optional age filter (notes newer than N days).
        progress_callback: Optional callable invoked with progress events.
            Signature: ``callback(event: str, payload: dict) -> None``.
            Events: ``stage_start``, ``stage_end``, ``candidate_start``,
            ``candidate_end``, ``counts``.

    Raises:
        LLMExtractionUnavailable: if Gemini CLI is not configured.
    """
    import os as _os
    import time as _time

    if run_date is None:
        run_date = date.today()

    if max_candidates is None:
        max_candidates = int(_os.environ.get("MANAGER_OS_LLM_MAX_CANDIDATES", "25"))

    # Quick availability check
    from manager_os.llm.gemini_cli import is_gemini_available, GeminiUnavailable
    if not is_gemini_available():
        raise LLMExtractionUnavailable(
            "Gemini CLI is not available. Set MANAGER_OS_GEMINI_CLI_BIN or run 'manager-os llm-doctor'."
        )

    _emit_progress(progress_callback, "stage_start", {
        "stage": "select_candidates",
        "message": "Selecting LLM candidates",
    })
    select_start = _time.monotonic()
    candidates, excluded_cnt, context_cnt, empty_cnt = _select_llm_candidates(
        conn,
        max_candidates=max_candidates,
        source_path_filter=source_path_filter,
        note_id=note_id,
        since_days=since_days,
    )
    select_elapsed = _time.monotonic() - select_start
    _emit_progress(progress_callback, "stage_end", {
        "stage": "select_candidates",
        "elapsed_seconds": select_elapsed,
        "candidate_count": len(candidates),
        "skipped_excluded": excluded_cnt,
        "skipped_context": context_cnt,
        "skipped_empty": empty_cnt,
    })

    total = LLMExtractionResult(
        candidates_considered=len(candidates),
        candidates_skipped_excluded=excluded_cnt,
        candidates_skipped_context=context_cnt,
        candidates_skipped_empty_body=empty_cnt,
    )

    _emit_progress(progress_callback, "stage_start", {
        "stage": "llm_extraction",
        "message": f"Extracting signals from {len(candidates)} candidate note(s)",
    })
    extract_start = _time.monotonic()

    for idx, c in enumerate(candidates, start=1):
        _emit_progress(progress_callback, "candidate_start", {
            "stage": "llm_extraction",
            "index": idx,
            "total": len(candidates),
            "note_id": c["note_id"],
            "source_path": c["source_path"],
        })
        r = _extract_signals_from_note_llm(
            conn=conn,
            note_id=c["note_id"],
            note_date=c["note_date"],
            entity_name=c["entity_name"],
            entity_type=c["entity_type"],
            body=c["body"],
            source_path=c["source_path"],
            timeout_seconds=timeout_seconds,
        )
        total.written += r.written
        total.skipped += r.skipped
        total.failed += r.failed
        total.items.extend(r.items)
        _emit_progress(progress_callback, "candidate_end", {
            "stage": "llm_extraction",
            "index": idx,
            "total": len(candidates),
            "note_id": c["note_id"],
            "written": total.written,
            "skipped": total.skipped,
            "failed": total.failed,
        })

    extract_elapsed = _time.monotonic() - extract_start
    _emit_progress(progress_callback, "stage_end", {
        "stage": "llm_extraction",
        "elapsed_seconds": extract_elapsed,
        "written": total.written,
        "skipped": total.skipped,
        "failed": total.failed,
    })

    return total


def _emit_progress(
    callback: Any | None,
    event: str,
    payload: dict,
) -> None:
    """Invoke *callback* if provided, swallowing any errors."""
    if callback is None:
        return
    try:
        callback(event, payload)
    except Exception:
        pass

