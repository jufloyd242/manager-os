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


def _seed_raw_document(conn, source_path: str = "notes/2024-01-01.md", metadata: dict | None = None) -> str:
    import uuid

    if metadata is None:
        metadata = {"source_tier": "signal"}
    raw_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO raw_documents (id, ingested_at, source_type, source_path,
                                      content_hash, content, metadata)
           VALUES (?, CURRENT_TIMESTAMP, 'obsidian', ?, 'hash', '', ?)""",
        [raw_id, source_path, json.dumps(metadata)],
    )
    return raw_id


def _seed_note(conn, raw_document_id: str, body: str, entity_name: str = "Alice Chen") -> None:
    import uuid

    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                              entity_name, title, body, tags, created_at)
           VALUES (?, ?, ?, '1on1', 'person', ?, 'Note', ?, '[]', CURRENT_TIMESTAMP)""",
        [str(uuid.uuid4()), raw_document_id, date.today().isoformat(), entity_name, body],
    )


def test_run_llm_extraction_writes_signals(conn) -> None:
    raw_id = _seed_raw_document(conn)
    _seed_note(conn, raw_id, "Alice is blocked on Acme data access. Risk of delay on pipeline milestone.")
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
    raw_id = _seed_raw_document(conn)
    _seed_note(conn, raw_id, "Risk on Acme project delivery.")
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
    raw_id = _seed_raw_document(conn)
    _seed_note(conn, raw_id, "All good today, no issues noted.")
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value="[]"):
        result = run_llm_extraction(conn)
    assert result.written == 0


def test_run_llm_extraction_logs_failure_on_bad_response(conn) -> None:
    raw_id = _seed_raw_document(conn)
    _seed_note(conn, raw_id, "Something is wrong with this client.")
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


def test_run_llm_extraction_limit_limits_candidates(conn) -> None:
    """--llm-limit should cap the number of notes sent to the LLM."""
    for i in range(5):
        raw_id = _seed_raw_document(conn, source_path=f"notes/note-{i}.md")
        _seed_note(conn, raw_id, f"Risk {i}.", entity_name=f"Person {i}")
    mock_response = json.dumps([_valid_signal()])
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value=mock_response) as gen_mock:
        result = run_llm_extraction(conn, max_candidates=2)
    assert result.candidates_considered == 2
    assert gen_mock.call_count == 2


def test_run_llm_extraction_source_path_filter(conn) -> None:
    """--llm-source-path should only process matching notes."""
    raw_alice = _seed_raw_document(conn, source_path="work/alice.md")
    _seed_note(conn, raw_alice, "Alice risk.", entity_name="Alice")
    raw_bob = _seed_raw_document(conn, source_path="work/bob.md")
    _seed_note(conn, raw_bob, "Bob risk.", entity_name="Bob")
    mock_response = json.dumps([_valid_signal()])
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value=mock_response) as gen_mock:
        result = run_llm_extraction(conn, source_path_filter="alice")
    assert result.candidates_considered == 1
    assert gen_mock.call_count == 1


def test_run_llm_extraction_note_id_filter(conn) -> None:
    """--llm-note-id should only process the matching note."""
    import uuid

    raw_target = _seed_raw_document(conn, source_path="work/target.md")
    target_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                              entity_name, title, body, tags, created_at)
           VALUES (?, ?, ?, '1on1', 'person', ?, 'Note', ?, '[]', CURRENT_TIMESTAMP)""",
        [target_id, raw_target, date.today().isoformat(), "Target", "Target risk."],
    )
    raw_other = _seed_raw_document(conn, source_path="work/other.md")
    _seed_note(conn, raw_other, "Other risk.", entity_name="Other")
    mock_response = json.dumps([_valid_signal()])
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value=mock_response) as gen_mock:
        result = run_llm_extraction(conn, note_id=target_id)
    assert result.candidates_considered == 1
    assert gen_mock.call_count == 1


def test_run_llm_extraction_since_days_filter(conn) -> None:
    """--llm-since-days should only process recent notes."""
    from datetime import timedelta

    old_date = (date.today() - timedelta(days=30)).isoformat()
    recent_date = (date.today() - timedelta(days=2)).isoformat()

    raw_old = _seed_raw_document(conn, source_path="work/old.md")
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                              entity_name, title, body, tags, created_at)
           VALUES ('old-id', ?, ?, '1on1', 'person', ?, 'Note', ?, '[]', CURRENT_TIMESTAMP)""",
        [raw_old, old_date, "Old", "Old risk."],
    )
    raw_recent = _seed_raw_document(conn, source_path="work/recent.md")
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                              entity_name, title, body, tags, created_at)
           VALUES ('recent-id', ?, ?, '1on1', 'person', ?, 'Note', ?, '[]', CURRENT_TIMESTAMP)""",
        [raw_recent, recent_date, "Recent", "Recent risk."],
    )
    mock_response = json.dumps([_valid_signal()])
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value=mock_response) as gen_mock:
        result = run_llm_extraction(conn, since_days=7)
    assert result.candidates_considered == 1
    assert gen_mock.call_count == 1


def test_run_llm_extraction_progress_callback(conn) -> None:
    """Progress callback should receive stage and candidate events."""
    raw_id = _seed_raw_document(conn)
    _seed_note(conn, raw_id, "Risk note.")
    events = []

    def _cb(event, payload):
        events.append((event, payload))

    mock_response = json.dumps([_valid_signal()])
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value=mock_response):
        run_llm_extraction(conn, progress_callback=_cb)

    event_names = {e[0] for e in events}
    assert "stage_start" in event_names
    assert "stage_end" in event_names
    assert "candidate_start" in event_names
    assert "candidate_end" in event_names


def test_run_llm_extraction_unlimited_when_limit_zero(conn) -> None:
    """max_candidates=None should process all available signal notes."""
    for i in range(5):
        raw_id = _seed_raw_document(conn, source_path=f"notes/note-{i}.md")
        _seed_note(conn, raw_id, f"Risk {i}.", entity_name=f"Person {i}")
    mock_response = json.dumps([_valid_signal()])
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.llm.gemini_cli.generate", return_value=mock_response) as gen_mock:
        result = run_llm_extraction(conn, max_candidates=None)
    assert result.candidates_considered == 5
    assert gen_mock.call_count == 5
