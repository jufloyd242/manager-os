"""Tests for meeting prep generator (extract/meeting_prep.py)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from manager_os.config import ClientConfig, PersonConfig
from manager_os.db import content_hash, get_connection
from manager_os.extract.entities import EntityResolver
from manager_os.extract.meeting_prep import (
    generate_meeting_prep,
    enrich_meeting_prep_with_llm,
    write_meeting_prep_to_file,
)
from manager_os.schemas import MeetingRecord


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def resolver() -> EntityResolver:
    people = [PersonConfig(name="Alice Chen", aliases=["Alice", "alice", "Alice Chen"])]
    clients = [ClientConfig(name="Acme Corp", aliases=["Acme", "acme", "Acme Corp"])]
    return EntityResolver(people, clients, {})


def _seed_note(conn, entity_name: str, entity_type: str = "person",
               note_type: str = "1on1", body: str = "Meeting notes.",
               note_date: date | None = None) -> None:
    import uuid
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                              entity_name, title, body, tags, created_at)
           VALUES (?, 'raw', ?, ?, ?, ?, 'Test Note', ?, '[]', CURRENT_TIMESTAMP)""",
        [str(uuid.uuid4()), (note_date or date.today()).isoformat(),
         note_type, entity_type, entity_name, body],
    )


def _seed_signal(conn, entity_name: str, entity_type: str = "person",
                 signal_type: str = "risk", severity: str = "high") -> None:
    sig_id = content_hash(f"sig::{entity_name}::{signal_type}")
    conn.execute(
        """INSERT INTO signals
               (id, signal_date, source, source_path, entity_type, entity_name,
                signal_type, severity, summary, why_it_matters,
                requires_manager_attention, confidence, status, created_at, updated_at)
           VALUES (?, ?, 'rule', '', ?, ?, ?, ?, 'Test signal', '',
                   TRUE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        [sig_id, date.today().isoformat(), entity_type, entity_name, signal_type, severity],
    )


def _make_meeting(title: str = "Sync with Alice",
                  attendees: list[str] | None = None,
                  linked_entities: list[dict] | None = None) -> MeetingRecord:
    return MeetingRecord(
        meeting_date=date.today(),
        start_time="10:00",
        title=title,
        attendees=attendees or ["Alice Chen"],
        linked_entities=linked_entities or [],
    )


# ------------------------------------------------------------------
# Basic generation
# ------------------------------------------------------------------


def test_generate_meeting_prep_returns_record(conn, resolver) -> None:
    meeting = _make_meeting()
    prep = generate_meeting_prep(meeting, conn, resolver)
    assert prep.content
    assert prep.meeting_id == meeting.id


def test_generate_meeting_prep_contains_title(conn, resolver) -> None:
    meeting = _make_meeting(title="Weekly Acme Sync")
    prep = generate_meeting_prep(meeting, conn, resolver)
    assert "Weekly Acme Sync" in prep.content


def test_generate_meeting_prep_contains_attendee(conn, resolver) -> None:
    meeting = _make_meeting(attendees=["Alice Chen", "Bob Martinez"])
    prep = generate_meeting_prep(meeting, conn, resolver)
    assert "Alice Chen" in prep.content


# ------------------------------------------------------------------
# Entity context
# ------------------------------------------------------------------


def test_meeting_prep_includes_last_note(conn, resolver) -> None:
    _seed_note(conn, "Alice Chen", entity_type="person", note_type="1on1",
               body="Alice mentioned she's stretched thin on Acme.")
    meeting = _make_meeting(attendees=["Alice Chen"])
    prep = generate_meeting_prep(meeting, conn, resolver)
    assert "stretched thin" in prep.content


def test_meeting_prep_resolves_client_from_title(conn, resolver) -> None:
    _seed_note(conn, "Acme Corp", entity_type="client", note_type="client",
               body="Acme data pipeline is at risk.")
    meeting = _make_meeting(title="Acme Corp Weekly Review", attendees=[])
    prep = generate_meeting_prep(meeting, conn, resolver)
    assert "Acme Corp" in prep.content


def test_meeting_prep_linked_entities(conn) -> None:
    _seed_note(conn, "Acme Corp", entity_type="client", note_type="client",
               body="Feature store milestone coming up.")
    meeting = MeetingRecord(
        meeting_date=date.today(),
        title="Client Review",
        linked_entities=[{"entity_type": "client", "entity_name": "Acme Corp"}],
    )
    prep = generate_meeting_prep(meeting, conn)
    assert "Acme Corp" in prep.content


# ------------------------------------------------------------------
# Signals and action items
# ------------------------------------------------------------------


def test_meeting_prep_includes_open_signals(conn, resolver) -> None:
    _seed_signal(conn, "Alice Chen", entity_type="person", severity="high")
    meeting = _make_meeting(attendees=["Alice Chen"])
    prep = generate_meeting_prep(meeting, conn, resolver)
    assert "Test signal" in prep.content


def test_meeting_prep_suggested_questions_risk(conn, resolver) -> None:
    _seed_signal(conn, "Alice Chen", signal_type="risk")
    meeting = _make_meeting(attendees=["Alice Chen"])
    prep = generate_meeting_prep(meeting, conn, resolver)
    assert "Suggested Questions" in prep.content
    assert "risk" in prep.content.lower()


def test_meeting_prep_no_signals_still_has_questions(conn, resolver) -> None:
    meeting = _make_meeting()
    prep = generate_meeting_prep(meeting, conn, resolver)
    assert "Suggested Questions" in prep.content


# ------------------------------------------------------------------
# DB persistence and idempotency
# ------------------------------------------------------------------


def test_meeting_prep_written_to_db(conn, resolver) -> None:
    meeting = _make_meeting()
    generate_meeting_prep(meeting, conn, resolver)
    row = conn.execute("SELECT meeting_id FROM meeting_prep").fetchone()
    assert row[0] == meeting.id


def test_meeting_prep_idempotent(conn, resolver) -> None:
    meeting = _make_meeting()
    generate_meeting_prep(meeting, conn, resolver)
    generate_meeting_prep(meeting, conn, resolver)
    count = conn.execute("SELECT COUNT(*) FROM meeting_prep").fetchone()[0]
    assert count == 1


def test_stored_prep_lookup_by_meeting_id(conn, resolver) -> None:
    """Dashboard stored-prep lookup: query by meeting_id dict key.

    Verifies the pattern used in dashboard app.py line ~624:
      conn.execute("SELECT content, generated_at FROM meeting_prep WHERE meeting_id = ?", [chosen["id"]])
    """
    meeting = _make_meeting(title="QBR Prep", attendees=["Alice Chen"])
    generate_meeting_prep(meeting, conn, resolver)

    # Simulate dashboard lookup — meeting dict from get_meetings_for_date
    meeting_dict = {"id": meeting.id, "title": meeting.title,
                    "meeting_date": meeting.meeting_date, "start_time": meeting.start_time,
                    "attendees": meeting.attendees}

    prep_row = conn.execute(
        "SELECT content, generated_at FROM meeting_prep WHERE meeting_id = ?",
        [meeting_dict["id"]],
    ).fetchone()

    assert prep_row is not None, "Stored prep must be found by meeting_dict['id']"
    assert "QBR Prep" in prep_row[0]
    assert "Alice Chen" in prep_row[0]


# ------------------------------------------------------------------
# File output
# ------------------------------------------------------------------


def test_write_meeting_prep_to_file(conn, resolver, tmp_path: Path) -> None:
    meeting = _make_meeting(title="Alice 1:1")
    prep = generate_meeting_prep(meeting, conn, resolver)
    out = write_meeting_prep_to_file(prep, "Alice 1:1", date.today(),
                                     output_path=str(tmp_path / "prep.md"))
    assert out.exists()
    assert "Alice" in out.read_text()


# ------------------------------------------------------------------
# LLM enrichment (Issue #24)
# ------------------------------------------------------------------


def _make_mock_openai(synthesis: str):
    mock_choice = MagicMock()
    mock_choice.message.content = synthesis
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create.return_value = mock_resp
    mock_openai_module = MagicMock()
    mock_openai_module.OpenAI.return_value = mock_client_instance
    return mock_openai_module


def test_enrich_appends_ai_synthesis(conn, resolver) -> None:
    import sys
    meeting = _make_meeting()
    prep = generate_meeting_prep(meeting, conn, resolver)
    synthesis = "## 🤖 AI Synthesis\n\n**Key things to know**\n- Alice is stretched thin.\n\n**Top 3 questions**\n- How are you?"
    mock_openai = _make_mock_openai(synthesis)
    with patch.dict("sys.modules", {"openai": mock_openai}), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        enriched = enrich_meeting_prep_with_llm(prep, conn)
    assert "AI Synthesis" in enriched.content
    assert enriched.content != prep.content


def test_enrich_persists_to_db(conn, resolver) -> None:
    import os
    meeting = _make_meeting()
    prep = generate_meeting_prep(meeting, conn, resolver)
    synthesis = "## 🤖 AI Synthesis\n\n- Key point."
    mock_openai = _make_mock_openai(synthesis)
    with patch.dict("sys.modules", {"openai": mock_openai}), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        enrich_meeting_prep_with_llm(prep, conn)
    row = conn.execute("SELECT content FROM meeting_prep WHERE id = ?", [prep.id]).fetchone()
    assert "AI Synthesis" in row[0]


def test_enrich_no_op_without_api_key(conn, resolver) -> None:
    import os
    meeting = _make_meeting()
    prep = generate_meeting_prep(meeting, conn, resolver)
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    with patch.dict("os.environ", env, clear=True):
        enriched = enrich_meeting_prep_with_llm(prep, conn)
    assert enriched.content == prep.content


def test_enrich_no_op_on_llm_error(conn, resolver) -> None:
    meeting = _make_meeting()
    prep = generate_meeting_prep(meeting, conn, resolver)
    mock_client_instance = MagicMock()
    mock_client_instance.chat.completions.create.side_effect = RuntimeError("API down")
    mock_openai = MagicMock()
    mock_openai.OpenAI.return_value = mock_client_instance
    with patch.dict("sys.modules", {"openai": mock_openai}), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        enriched = enrich_meeting_prep_with_llm(prep, conn)
    assert enriched.content == prep.content


def test_enrich_no_op_on_empty_response(conn, resolver) -> None:
    meeting = _make_meeting()
    prep = generate_meeting_prep(meeting, conn, resolver)
    mock_openai = _make_mock_openai("")
    with patch.dict("sys.modules", {"openai": mock_openai}), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        enriched = enrich_meeting_prep_with_llm(prep, conn)
    assert enriched.content == prep.content
