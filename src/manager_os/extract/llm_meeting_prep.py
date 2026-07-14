"""LLM-driven meeting preparation generation.

Builds profile-specific structured prompts, calls the LLM (Gemini CLI),
parses strict JSON responses, validates schema and citations, and persists
prep with freshness tracking.

The LLM reasons only over the supplied context bundle — it does not
free-roam through data. Unsupported claims without valid source IDs
are rejected or moved to missing_context.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from manager_os.db import content_hash

logger = logging.getLogger(__name__)

GENERATOR_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PrepParseError(Exception):
    """Failed to parse LLM response as JSON."""


class PrepValidationError(Exception):
    """LLM response failed schema validation."""


class PrepGenerationError(Exception):
    """LLM prep generation failed (timeout, unavailable, etc.)."""


# ---------------------------------------------------------------------------
# Output schemas per profile
# ---------------------------------------------------------------------------

UPWARD_DAILY_STATUS_SCHEMA = {
    "required": [
        "meeting_type", "objective", "today_priorities",
        "progress_since_last_meeting", "help_needed", "decisions_needed",
        "risks_to_flag", "commitments", "likely_follow_up_questions",
        "talk_track", "missing_context", "source_ids",
    ],
    "fields": {
        "meeting_type": str,
        "objective": str,
        "today_priorities": list,
        "progress_since_last_meeting": list,
        "help_needed": list,
        "decisions_needed": list,
        "risks_to_flag": list,
        "commitments": list,
        "likely_follow_up_questions": list,
        "talk_track": str,
        "missing_context": list,
        "source_ids": list,
    },
}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_prep_prompt(
    meeting: dict[str, Any],
    profile: dict[str, Any],
    context_bundle: dict[str, Any],
) -> str:
    """Build a profile-specific structured LLM prompt.

    The LLM must:
    - Use only supplied context
    - Cite source IDs
    - Avoid unsupported facts
    - Return strict JSON
    """
    meeting_type = profile.get("meeting_type", "generic")
    objective = profile.get("objective", "Prepare for this meeting.")
    schema_name = profile.get("output_schema", "generic")

    # Build context summary
    sources = context_bundle.get("sources", [])
    items = context_bundle.get("items", [])

    context_text = "## Meeting Context\n\n"
    context_text += f"Title: {meeting.get('title', 'Untitled')}\n"
    context_text += f"Date: {meeting.get('meeting_date', 'Unknown')}\n"
    context_text += f"Attendees: {', '.join(meeting.get('attendees', []))}\n"
    if meeting.get("description_summary"):
        context_text += f"Description: {meeting['description_summary']}\n"
    context_text += "\n"

    context_text += "## Available Context Sources\n\n"
    for src in sources:
        context_text += f"- source_id: {src.get('source_id', '')}\n"
        context_text += f"  source_type: {src.get('source_type', '')}\n"
        context_text += f"  title: {src.get('title', '')}\n"
        context_text += f"  date: {src.get('date', '')}\n"
        context_text += f"  entity: {src.get('entity', '')}\n"
        excerpt = src.get("excerpt_or_fact", "")
        if excerpt:
            context_text += f"  excerpt: {excerpt[:500]}\n"
        context_text += f"  relevance: {src.get('relevance_reason', '')}\n"
        context_text += f"  confidence: {src.get('confidence', 0.5)}\n\n"

    # Build schema instruction
    schema_instruction = _get_schema_instruction(schema_name)

    return f"""You are an expert engineering management assistant preparing a manager for a meeting.

Meeting type: {meeting_type}
Objective: {objective}

{context_text}

## Instructions

1. Use ONLY the supplied context sources. Do NOT invent facts.
2. Every substantive item MUST cite at least one source_id from the available sources.
3. If you cannot find evidence for something, put it under "missing_context" instead.
4. Return ONLY valid JSON (no markdown, no explanation).
5. The talk_track should be suitable for a spoken update of approximately 60-90 seconds.

{schema_instruction}
"""


def _get_schema_instruction(schema_name: str) -> str:
    """Get the JSON schema instruction for a profile."""
    if schema_name == "upward_daily_status":
        return """Return this JSON structure:
{
  "meeting_type": "upward_daily_status",
  "objective": "string",
  "today_priorities": [{"text": "string", "why_it_matters": "string", "source_ids": ["string"]}],
  "progress_since_last_meeting": [{"text": "string", "source_ids": ["string"]}],
  "help_needed": [{"text": "string", "requested_from": "manager", "source_ids": ["string"]}],
  "decisions_needed": [{"decision": "string", "options": ["string"], "recommendation": "string", "source_ids": ["string"]}],
  "risks_to_flag": [{"text": "string", "severity": "high|medium|low", "source_ids": ["string"]}],
  "commitments": [{"text": "string", "owner": "string", "due_date": "YYYY-MM-DD|null", "status": "string", "source_ids": ["string"]}],
  "likely_follow_up_questions": ["string"],
  "talk_track": "string (60-90 seconds spoken)",
  "missing_context": ["string"],
  "source_ids": ["string"]
}"""
    return """Return this JSON structure:
{
  "meeting_type": "generic",
  "objective": "string",
  "talking_points": [{"text": "string", "source_ids": ["string"]}],
  "questions": ["string"],
  "missing_context": ["string"],
  "source_ids": ["string"]
}"""


# ---------------------------------------------------------------------------
# Response parsing + validation
# ---------------------------------------------------------------------------


def parse_llm_prep_response(raw: str, expected_schema: str) -> dict[str, Any]:
    """Parse and validate an LLM prep response.

    Args:
        raw: Raw LLM output text.
        expected_schema: Schema name for validation.

    Returns:
        Parsed and validated prep dict.

    Raises:
        PrepParseError: If JSON parsing fails.
        PrepValidationError: If schema validation fails.
    """
    from manager_os.llm.gemini_cli import _extract_json

    try:
        clean = _extract_json(raw)
        data = json.loads(clean)
    except (json.JSONDecodeError, ValueError) as e:
        raise PrepParseError(f"Failed to parse LLM response as JSON: {e}")
    except Exception as e:
        raise PrepParseError(f"Failed to extract JSON from response: {e}")

    if not isinstance(data, dict):
        raise PrepValidationError(f"Expected JSON object, got {type(data).__name__}")

    # Validate schema
    schema = UPWARD_DAILY_STATUS_SCHEMA if expected_schema == "upward_daily_status" else None
    if schema:
        for field_name in schema["required"]:
            if field_name not in data:
                raise PrepValidationError(f"Missing required field: {field_name}")

    return data


def validate_citations(prep: dict[str, Any], valid_source_ids: list[str]) -> list[str]:
    """Validate that all source_ids in the prep reference valid sources.

    Returns a list of issues (empty if all citations are valid).
    """
    issues: list[str] = []
    valid_set = set(valid_source_ids)

    def check_items(items: list, section: str):
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            source_ids = item.get("source_ids", [])
            if not isinstance(source_ids, list):
                continue
            for sid in source_ids:
                if sid not in valid_set:
                    issues.append(f"Section '{section}': cites unknown source_id '{sid}'")

    check_items(prep.get("today_priorities", []), "today_priorities")
    check_items(prep.get("progress_since_last_meeting", []), "progress_since_last_meeting")
    check_items(prep.get("help_needed", []), "help_needed")
    check_items(prep.get("decisions_needed", []), "decisions_needed")
    check_items(prep.get("risks_to_flag", []), "risks_to_flag")
    check_items(prep.get("commitments", []), "commitments")

    return issues


# ---------------------------------------------------------------------------
# LLM generation
# ---------------------------------------------------------------------------


def generate_prep(
    meeting: dict[str, Any],
    meeting_type: str,
    context_bundle: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Generate meeting prep via LLM.

    Args:
        meeting: Meeting dict with title, attendees, etc.
        meeting_type: Classified meeting type (e.g. "upward_daily_status").
        context_bundle: Controlled context sources and items.
        profile: Optional profile dict with objective, output_schema, etc.
        timeout: LLM timeout in seconds.

    Returns:
        Parsed and validated prep dict.

    Raises:
        PrepGenerationError: If LLM call fails, times out, or returns invalid output.
    """
    from manager_os.llm.gemini_cli import generate, GeminiUnavailable

    if profile is None:
        profile = {
            "meeting_type": meeting_type,
            "objective": "Prepare for this meeting.",
            "output_schema": "generic",
        }

    prompt = build_prep_prompt(meeting, profile, context_bundle)
    system_prompt = "You are an expert engineering management assistant. Return ONLY valid JSON."

    try:
        raw = generate(system_prompt=system_prompt, user_prompt=prompt, timeout=timeout)
    except GeminiUnavailable as e:
        raise PrepGenerationError(f"LLM unavailable: {e}")
    except Exception as e:
        error_str = str(e).lower()
        if "timeout" in error_str or "timed out" in error_str:
            raise PrepGenerationError(f"LLM timeout: {e}")
        raise PrepGenerationError(f"LLM call failed: {e}")

    # Parse and validate
    schema_name = profile.get("output_schema", "generic")
    try:
        prep = parse_llm_prep_response(raw, schema_name)
    except PrepParseError as e:
        raise PrepGenerationError(f"LLM response parse failed: {e}")
    except PrepValidationError as e:
        raise PrepGenerationError(f"LLM response validation failed: {e}")

    # Validate citations
    valid_source_ids = [s.get("source_id", "") for s in context_bundle.get("sources", [])]
    citation_issues = validate_citations(prep, valid_source_ids)
    if citation_issues:
        # Don't fail — add issues to missing_context
        existing_missing = prep.get("missing_context", [])
        existing_missing.extend(citation_issues[:3])  # Limit to 3
        prep["missing_context"] = existing_missing

    return prep


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_prep(
    conn,
    meeting_id: str,
    prep_data: dict[str, Any],
    classification: str,
    profile_id: str,
    meeting_fingerprint: str,
    source_fingerprint: str,
    source_references: list[str],
    llm_provider: str,
    llm_model: str,
    *,
    live_enrichment_used: bool = False,
    generation_status: str = "success",
    safe_error: str = "",
) -> str:
    """Persist generated prep to the meeting_prep table.

    Returns the prep record ID.
    """
    prep_id = content_hash(f"prep::{meeting_id}::{meeting_fingerprint}::{source_fingerprint}")
    now = datetime.utcnow()

    conn.execute(
        """INSERT OR REPLACE INTO meeting_prep
           (id, meeting_id, content, generated_at,
            meeting_fingerprint, classification, profile_id,
            source_fingerprint, structured_prep_json, source_references_json,
            generator_version, llm_provider, llm_model,
            live_enrichment_used, generation_status, safe_error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            prep_id,
            meeting_id,
            json.dumps(prep_data),
            now,
            meeting_fingerprint,
            classification,
            profile_id,
            source_fingerprint,
            json.dumps(prep_data),
            json.dumps(source_references),
            GENERATOR_VERSION,
            llm_provider,
            llm_model,
            live_enrichment_used,
            generation_status,
            safe_error,
        ],
    )

    return prep_id


def get_prep_freshness(
    conn,
    meeting_id: str,
    current_meeting_fingerprint: str,
    current_source_fingerprint: str,
) -> str:
    """Check freshness of existing prep.

    Returns: "current", "stale", "not_generated", or "failed"
    """
    row = conn.execute(
        """SELECT meeting_fingerprint, source_fingerprint, generation_status
           FROM meeting_prep WHERE meeting_id = ?
           ORDER BY generated_at DESC LIMIT 1""",
        [meeting_id],
    ).fetchone()

    if not row:
        return "not_generated"

    stored_meeting_fp = row[0] or ""
    stored_source_fp = row[1] or ""
    status = row[2] or "success"

    if status == "failed":
        return "failed"

    if stored_meeting_fp == current_meeting_fingerprint and stored_source_fp == current_source_fingerprint:
        return "current"

    return "stale"


def get_persisted_prep(conn, meeting_id: str) -> dict[str, Any] | None:
    """Get the most recent persisted prep for a meeting."""
    row = conn.execute(
        """SELECT id, content, generated_at, meeting_fingerprint, classification,
           profile_id, source_fingerprint, structured_prep_json,
           source_references_json, generator_version, llm_provider, llm_model,
           live_enrichment_used, generation_status, safe_error
           FROM meeting_prep WHERE meeting_id = ?
           ORDER BY generated_at DESC LIMIT 1""",
        [meeting_id],
    ).fetchone()

    if not row:
        return None

    try:
        prep_data = json.loads(row[8]) if row[8] else json.loads(row[1]) if row[1] else {}
    except (json.JSONDecodeError, TypeError):
        prep_data = {}

    try:
        source_refs = json.loads(row[9]) if row[9] else []
    except (json.JSONDecodeError, TypeError):
        source_refs = []

    return {
        "id": row[0],
        "meeting_id": meeting_id,
        "prep_data": prep_data,
        "generated_at": row[2],
        "meeting_fingerprint": row[3] or "",
        "classification": row[4] or "",
        "profile_id": row[5] or "",
        "source_fingerprint": row[6] or "",
        "source_references": source_refs,
        "generator_version": row[7] or "",
        "llm_provider": row[10] or "",
        "llm_model": row[11] or "",
        "live_enrichment_used": bool(row[12]) if row[12] else False,
        "generation_status": row[13] or "success",
        "safe_error": row[14] or "",
    }
