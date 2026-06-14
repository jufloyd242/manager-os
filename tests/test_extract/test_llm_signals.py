"""Tests for LLM signal extraction (Issue #20).

Uses a mock OpenAI client — no real API calls are made.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

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


def test_parse_valid_response() -> None:
    raw = json.dumps([{
        "entity_type": "person",
        "entity_name": "Alice Chen",
        "signal_type": "risk",
        "severity": "high",
        "summary": "Alice is stretched thin on two concurrent engagements.",
        "why_it_matters": "May affect delivery quality.",
        "requires_manager_attention": True,
        "confidence": 0.9,
    }])
    result = _parse_llm_response(raw)
    assert len(result) == 1
    assert result[0]["entity_name"] == "Alice Chen"


def test_parse_filters_invalid_entity_type() -> None:
    raw = json.dumps([{
        "entity_type": "organization",  # invalid
        "entity_name": "Acme",
        "signal_type": "risk",
        "severity": "high",
        "summary": "Something is wrong.",
        "why_it_matters": "",
        "requires_manager_attention": False,
        "confidence": 0.8,
    }])
    result = _parse_llm_response(raw)
    assert result == []


def test_parse_filters_invalid_signal_type() -> None:
    raw = json.dumps([{
        "entity_type": "client",
        "entity_name": "Acme",
        "signal_type": "unknown_type",  # invalid
        "severity": "high",
        "summary": "Something.",
        "why_it_matters": "",
        "requires_manager_attention": False,
        "confidence": 0.8,
    }])
    result = _parse_llm_response(raw)
    assert result == []


def test_parse_filters_invalid_severity() -> None:
    raw = json.dumps([{
        "entity_type": "client",
        "entity_name": "Acme",
        "signal_type": "risk",
        "severity": "urgent",  # invalid
        "summary": "Something.",
        "why_it_matters": "",
        "requires_manager_attention": False,
        "confidence": 0.8,
    }])
    result = _parse_llm_response(raw)
    assert result == []


def test_parse_filters_empty_summary() -> None:
    raw = json.dumps([{
        "entity_type": "person",
        "entity_name": "Alice",
        "signal_type": "risk",
        "severity": "medium",
        "summary": "",  # empty
        "why_it_matters": "",
        "requires_manager_attention": False,
        "confidence": 0.8,
    }])
    result = _parse_llm_response(raw)
    assert result == []


def test_parse_clamps_confidence() -> None:
    raw = json.dumps([{
        "entity_type": "person",
        "entity_name": "Alice",
        "signal_type": "risk",
        "severity": "medium",
        "summary": "Valid signal here.",
        "why_it_matters": "",
        "requires_manager_attention": False,
        "confidence": 9999.0,  # out of range
    }])
    result = _parse_llm_response(raw)
    assert result[0]["confidence"] == 1.0


def test_parse_empty_array() -> None:
    assert _parse_llm_response("[]") == []


def test_parse_with_json_wrapped_in_markdown() -> None:
    raw = "```json\n[{\"entity_type\":\"person\",\"entity_name\":\"Alice\",\"signal_type\":\"risk\",\"severity\":\"high\",\"summary\":\"At risk.\",\"why_it_matters\":\"\",\"requires_manager_attention\":false,\"confidence\":0.8}]\n```"
    # The parser should find the array even with markdown fencing
    result = _parse_llm_response(raw)
    assert len(result) == 1


def test_parse_bad_json_raises() -> None:
    with pytest.raises((ValueError, Exception)):
        _parse_llm_response("not json at all")


# ------------------------------------------------------------------
# LLMExtractionUnavailable
# ------------------------------------------------------------------


def test_unavailable_when_no_api_key(conn) -> None:
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("OPENAI_API_KEY", None)
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


def _make_mock_client(response_json: str):
    """Build a minimal mock openai client."""
    mock_choice = MagicMock()
    mock_choice.message.content = response_json
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


def test_run_llm_extraction_writes_signals(conn) -> None:
    _seed_note(conn, "Alice is blocked on Acme data access. Risk of delay on pipeline milestone.")
    mock_response = json.dumps([{
        "entity_type": "person",
        "entity_name": "Alice Chen",
        "signal_type": "blocker",
        "severity": "high",
        "summary": "Alice is blocked on Acme data access.",
        "why_it_matters": "Risk of delay.",
        "requires_manager_attention": True,
        "confidence": 0.9,
    }])
    mock_client = _make_mock_client(mock_response)
    with patch("manager_os.extract.llm_signals._get_openai_client", return_value=mock_client), \
         patch("manager_os.extract.llm_signals._get_model_name", return_value="gpt-4o-mini"):
        result = run_llm_extraction(conn)
    assert result.written == 1
    row = conn.execute("SELECT source, signal_type, severity FROM signals").fetchone()
    assert row[0] == "llm"
    assert row[1] == "blocker"
    assert row[2] == "high"


def test_run_llm_extraction_idempotent(conn) -> None:
    _seed_note(conn, "Risk on Acme project delivery.")
    mock_response = json.dumps([{
        "entity_type": "client",
        "entity_name": "Acme Corp",
        "signal_type": "risk",
        "severity": "high",
        "summary": "Risk on Acme project delivery.",
        "why_it_matters": "",
        "requires_manager_attention": True,
        "confidence": 0.85,
    }])
    mock_client = _make_mock_client(mock_response)
    with patch("manager_os.extract.llm_signals._get_openai_client", return_value=mock_client), \
         patch("manager_os.extract.llm_signals._get_model_name", return_value="gpt-4o-mini"):
        run_llm_extraction(conn)
        result2 = run_llm_extraction(conn)
    assert result2.skipped >= 1
    count = conn.execute("SELECT COUNT(*) FROM signals WHERE source='llm'").fetchone()[0]
    assert count == 1


def test_run_llm_extraction_empty_response(conn) -> None:
    _seed_note(conn, "All good today, no issues noted.")
    mock_client = _make_mock_client("[]")
    with patch("manager_os.extract.llm_signals._get_openai_client", return_value=mock_client), \
         patch("manager_os.extract.llm_signals._get_model_name", return_value="gpt-4o-mini"):
        result = run_llm_extraction(conn)
    assert result.written == 0


def test_run_llm_extraction_logs_failure_on_bad_response(conn) -> None:
    _seed_note(conn, "Something is wrong with this client.")
    mock_client = _make_mock_client("This is not JSON at all !!!")
    with patch("manager_os.extract.llm_signals._get_openai_client", return_value=mock_client), \
         patch("manager_os.extract.llm_signals._get_model_name", return_value="gpt-4o-mini"):
        result = run_llm_extraction(conn)
    assert result.failed == 1
    fail_count = conn.execute(
        "SELECT COUNT(*) FROM extraction_failures WHERE error_type = 'llm_error'"
    ).fetchone()[0]
    assert fail_count == 1


def test_run_llm_extraction_empty_db(conn) -> None:
    mock_client = _make_mock_client("[]")
    with patch("manager_os.extract.llm_signals._get_openai_client", return_value=mock_client), \
         patch("manager_os.extract.llm_signals._get_model_name", return_value="gpt-4o-mini"):
        result = run_llm_extraction(conn)
    assert result.written == 0
    assert result.failed == 0
