"""Tests for LLM signal extraction (Issue #20).

Uses a mock Gemini CLI provider — no real API calls are made.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from manager_os.db import get_connection
from manager_os.extract.llm_signals import (
    LLMExtractionUnavailable,
    _parse_llm_response,
    run_llm_extraction,
)


@pytest.fixture()
def conn():
    return get_connection(":memory:")


# ------------------------------------------------------------------
# _parse_llm_response
# ------------------------------------------------------------------


def _valid_signal(**overrides) -> dict:
    return {
        "entity_type": "person",
        "entity_name": "Alice Chen",
        "signal_type": "risk",
        "severity": "high",
        "summary": "Alice is stretched thin on two concurrent engagements.",
        "why_it_matters": "May affect delivery quality.",
        "requires_manager_attention": True,
        "confidence": 0.9,
        **overrides,
    }


def test_parse_valid_response() -> None:
    raw = json.dumps([_valid_signal()])
    result = _parse_llm_response(raw)
    assert len(result) == 1
    assert result[0]["entity_name"] == "Alice Chen"


def test_parse_filters_invalid_entity_type() -> None:
    raw = json.dumps([_valid_signal(entity_type="organization")])
    result = _parse_llm_response(raw)
    assert result == []


def test_parse_filters_invalid_signal_type() -> None:
    raw = json.dumps([_valid_signal(signal_type="unknown_type")])
    result = _parse_llm_response(raw)
    assert result == []


def test_parse_filters_invalid_severity() -> None:
    raw = json.dumps([_valid_signal(severity="urgent")])
    result = _parse_llm_response(raw)
    assert result == []


def test_parse_filters_empty_summary() -> None:
    raw = json.dumps([_valid_signal(summary="")])
    result = _parse_llm_response(raw)
    assert result == []


def test_parse_clamps_confidence() -> None:
    raw = json.dumps([_valid_signal(confidence=9999.0)])
    result = _parse_llm_response(raw)
    assert result[0]["confidence"] == 1.0


def test_parse_empty_array() -> None:
    assert _parse_llm_response("[]") == []


def test_parse_with_json_wrapped_in_markdown() -> None:
    raw = (
        "```json\n"
        + json.dumps([_valid_signal(summary="At risk.")])
        + "\n```"
    )
    result = _parse_llm_response(raw)
    assert len(result) == 1


def test_parse_bad_json_raises() -> None:
    with pytest.raises(ValueError):
        _parse_llm_response("not json at all")


# ------------------------------------------------------------------
# LLMExtractionUnavailable
# ------------------------------------------------------------------


def test_unavailable_when_llm_disabled(conn) -> None:
    with patch.dict(
        "os.environ",
        {"MANAGER_OS_LLM_ENABLED": "false"},
        clear=False,
    ), patch("manager_os.llm.gemini_cli.LLM_ENABLED", False):
        with pytest.raises(LLMExtractionUnavailable):
            run_llm_extraction(conn)


# ------------------------------------------------------------------
# run_llm_extraction (mocked)
# ------------------------------------------------------------------


def _seed_note(conn, body: str, entity_name: str = "Alice Chen") -> None:
    import uuid

    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                              entity_name, title, body, tags, created_at)
           VALUES (?, 'raw', ?, '1on1', 'person', ?, 'Note', ?, '[]', CURRENT_TIMESTAMP)""",
        [str(uuid.uuid4()), date.today().isoformat(), entity_name, body],
    )


def _seed_raw_document(conn, source_path: str = "notes/2024-01-01.md", metadata: dict | None = None) -> None:
    import uuid

    if metadata is None:
        metadata = {"source_tier": "signal"}
    conn.execute(
        """INSERT INTO raw_documents (id, ingested_at, source_type, source_path,
                                      content_hash, content, metadata)
           VALUES (?, CURRENT_TIMESTAMP, 'obsidian', ?, 'hash', '', ?)""",
        [str(uuid.uuid4()), source_path, json.dumps(metadata)],
    )


def test_run_llm_extraction_writes_signals(conn) -> None:
    _seed_raw_document(conn)
    _seed_note(conn, "Alice is blocked on Acme data access. Risk of delay on pipeline milestone.")
    mock_response = json.dumps([_valid_signal(
        signal_type="blocker",
        summary="Alice is blocked on Acme data access.",
        why_it_matters="Risk of delay.",
    )])
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value=mock_response):
        result = run_llm_extraction(conn)
    assert result.written == 1
    row = conn.execute("SELECT source, signal_type, severity FROM signals").fetchone()
    assert row[0] == "llm"
    assert row[1] == "blocker"
    assert row[2] == "high"


def test_run_llm_extraction_idempotent(conn) -> None:
    _seed_raw_document(conn)
    _seed_note(conn, "Risk on Acme project delivery.")
    mock_response = json.dumps([_valid_signal(
        entity_type="client",
        entity_name="Acme Corp",
        signal_type="risk",
        summary="Risk on Acme project delivery.",
    )])
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value=mock_response):
        run_llm_extraction(conn)
        result2 = run_llm_extraction(conn)
    assert result2.skipped >= 1
    count = conn.execute("SELECT COUNT(*) FROM signals WHERE source='llm'").fetchone()[0]
    assert count == 1


def test_run_llm_extraction_empty_response(conn) -> None:
    _seed_raw_document(conn)
    _seed_note(conn, "All good today, no issues noted.")
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value="[]"):
        result = run_llm_extraction(conn)
    assert result.written == 0


def test_run_llm_extraction_logs_failure_on_bad_response(conn) -> None:
    _seed_raw_document(conn)
    _seed_note(conn, "Something is wrong with this client.")
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value="This is not JSON at all !!!"):
        result = run_llm_extraction(conn)
    assert result.failed == 1
    fail_count = conn.execute(
        "SELECT COUNT(*) FROM extraction_failures WHERE error_type = 'llm_error'"
    ).fetchone()[0]
    assert fail_count == 1


def test_run_llm_extraction_empty_db(conn) -> None:
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value="[]"):
        result = run_llm_extraction(conn)
    assert result.written == 0
    assert result.failed == 0
