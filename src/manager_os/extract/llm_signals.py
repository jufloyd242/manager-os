"""Optional LLM-based signal extraction.

Requires one of:
  - OPENAI_API_KEY (or OPENAI_BASE_URL for compatible endpoints)
  - MANAGER_OS_GEMINI_MODEL + a Gemini-compatible client

If no LLM credentials are available, raises LLMExtractionUnavailable
so the CLI can skip gracefully.

Each note's body is sent to the LLM with a structured prompt. The model
is asked to return a JSON list of signals. Results are written to the
signals table with source="llm". Failures are logged to extraction_failures.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from manager_os.db import content_hash
from manager_os.schemas import Signal

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Availability check
# ------------------------------------------------------------------


class LLMExtractionUnavailable(RuntimeError):
    """Raised when no LLM credentials are configured."""


def _get_openai_client():
    """Return an openai.OpenAI client or raise LLMExtractionUnavailable."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if not api_key:
        raise LLMExtractionUnavailable(
            "OPENAI_API_KEY is not set. Configure it in your .env to enable LLM extraction."
        )
    try:
        import openai  # type: ignore[import]
    except ImportError as exc:
        raise LLMExtractionUnavailable(
            "openai package is not installed. Run: pip install openai"
        ) from exc

    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return openai.OpenAI(**kwargs)


def _get_model_name() -> str:
    return os.environ.get("MANAGER_OS_LLM_MODEL", "gpt-4o-mini")


# ------------------------------------------------------------------
# Prompt construction
# ------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert management assistant. Given a note from an engineering manager,
extract actionable management signals as structured JSON.

Return ONLY a JSON array (no markdown, no explanation). Each element must have:
  entity_type: "person" | "client" | "deal" | "team" | "practice"
  entity_name: string (canonical name of the entity)
  signal_type: "risk" | "blocker" | "ask" | "decision" | "staffing_change" |
               "deal_change" | "client_update" | "follow_up" | "stale_item" |
               "people_health" | "utilization_risk" | "sow_loe_review"
  severity: "critical" | "high" | "medium" | "low"
  summary: string (one sentence, max 120 chars)
  why_it_matters: string (one sentence, max 120 chars, or "")
  requires_manager_attention: boolean
  confidence: float between 0.0 and 1.0

If no signals are present, return [].
Do NOT invent signals. Only extract what is clearly stated or strongly implied."""


def _build_user_prompt(note_date: str, entity_name: str, entity_type: str, body: str) -> str:
    return (
        f"Date: {note_date}\n"
        f"Entity: {entity_name} ({entity_type})\n\n"
        f"Note body:\n{body[:3000]}"  # cap tokens
    )


# ------------------------------------------------------------------
# Parse and validate LLM output
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
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract a JSON array from the response
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"No JSON array found in LLM response: {raw[:200]}")
        parsed = json.loads(raw[start : end + 1])

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


# ------------------------------------------------------------------
# Core extraction
# ------------------------------------------------------------------


def _extract_signals_from_note_llm(
    conn,
    note_id: str,
    note_date: date,
    entity_name: str,
    entity_type: str,
    body: str,
    source_path: str,
    client,
    model: str,
) -> LLMExtractionResult:
    result = LLMExtractionResult()

    user_prompt = _build_user_prompt(
        note_date.isoformat(), entity_name, entity_type, body
    )

    raw_response = ""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=1024,
        )
        raw_response = response.choices[0].message.content or ""
        signal_dicts = _parse_llm_response(raw_response)
    except LLMExtractionUnavailable:
        raise
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


def run_llm_extraction(conn, run_date: date | None = None) -> LLMExtractionResult:
    """Run LLM-based signal extraction across all notes.

    Raises:
        LLMExtractionUnavailable: if no API credentials are configured.
    """
    client = _get_openai_client()  # raises LLMExtractionUnavailable if not configured
    model = _get_model_name()

    if run_date is None:
        run_date = date.today()

    rows = conn.execute(
        "SELECT n.id, n.note_date, n.entity_name, n.entity_type, n.body, r.source_path "
        "FROM notes n "
        "LEFT JOIN raw_documents r ON r.id = n.raw_document_id "
        "WHERE n.body != '' AND n.body IS NOT NULL"
    ).fetchall()

    total = LLMExtractionResult()
    for row in rows:
        note_id, note_date_raw, entity_name, entity_type, body, source_path = row
        try:
            nd = note_date_raw if isinstance(note_date_raw, date) else date.fromisoformat(str(note_date_raw))
        except Exception:
            nd = run_date

        r = _extract_signals_from_note_llm(
            conn=conn,
            note_id=note_id,
            note_date=nd,
            entity_name=entity_name or "",
            entity_type=entity_type or "person",
            body=body or "",
            source_path=source_path or "",
            client=client,
            model=model,
        )
        total.written += r.written
        total.skipped += r.skipped
        total.failed += r.failed
        total.items.extend(r.items)

    return total
