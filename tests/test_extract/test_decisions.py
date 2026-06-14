"""Tests for extract/decisions.py (Issue #19)."""

from __future__ import annotations

from datetime import date

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.extract.decisions import (
    extract_decisions,
    extract_decisions_from_all_notes,
    _extract_raw_decisions,
)
from manager_os.schemas import NoteRecord


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def _make_note(body: str, entity_name: str = "Alice Chen",
               entity_type: str = "person", note_date: date | None = None) -> NoteRecord:
    return NoteRecord(
        raw_document_id="raw001",
        note_date=note_date or date.today(),
        note_type="1on1",
        entity_type=entity_type,
        entity_name=entity_name,
        title="Test Note",
        body=body,
    )


# ------------------------------------------------------------------
# Pattern matching tests
# ------------------------------------------------------------------


def test_decision_colon_prefix() -> None:
    matches = _extract_raw_decisions("Decision: we will use Vertex AI for the pipeline.")
    assert any("Vertex AI" in m for m in matches)


def test_decided_colon_prefix() -> None:
    matches = _extract_raw_decisions("Decided: move forward with BigQuery migration.")
    assert any("BigQuery" in m for m in matches)


def test_we_decided_to() -> None:
    matches = _extract_raw_decisions("We decided to delay the launch by one week.")
    assert any("delay" in m for m in matches)


def test_we_agreed_to() -> None:
    matches = _extract_raw_decisions("We agreed to reduce scope on the feature store milestone.")
    assert any("reduce scope" in m for m in matches)


def test_going_with() -> None:
    matches = _extract_raw_decisions("Going with the BigQuery approach instead of Spanner.")
    assert any("BigQuery" in m for m in matches)


def test_agreed_colon_prefix() -> None:
    matches = _extract_raw_decisions("Agreed: Bob will own the migration plan.")
    assert any("Bob" in m for m in matches)


def test_resolved_to() -> None:
    matches = _extract_raw_decisions("Resolved to escalate the staffing issue to leadership.")
    assert any("escalate" in m for m in matches)


def test_no_matches_returns_empty() -> None:
    matches = _extract_raw_decisions("This is a regular meeting note with no decisions.")
    assert matches == []


def test_short_match_excluded() -> None:
    matches = _extract_raw_decisions("Decision: ok.")
    assert matches == []


def test_multiple_decisions_in_body() -> None:
    body = (
        "Decision: proceed with Phase 2.\n"
        "We agreed to pause the Acme engagement until SOW is signed.\n"
        "Going with Vertex AI for model serving."
    )
    matches = _extract_raw_decisions(body)
    assert len(matches) >= 3


# ------------------------------------------------------------------
# extract_decisions (single note)
# ------------------------------------------------------------------


def test_extract_decisions_writes_to_db(conn) -> None:
    note = _make_note("Decision: adopt Vertex AI as the primary ML platform.")
    result = extract_decisions(note, conn)
    assert result.written == 1
    row = conn.execute("SELECT description, entity_name FROM decisions").fetchone()
    assert row is not None
    assert "Vertex AI" in row[0]
    assert row[1] == "Alice Chen"


def test_extract_decisions_dedup_skips_on_rerun(conn) -> None:
    note = _make_note("Decision: use BigQuery for analytics.")
    extract_decisions(note, conn)
    result2 = extract_decisions(note, conn)
    assert result2.skipped == 1
    assert result2.written == 0


def test_extract_decisions_force_overwrites(conn) -> None:
    note = _make_note("Decision: use BigQuery for analytics.")
    extract_decisions(note, conn)
    result2 = extract_decisions(note, conn, force=True)
    assert result2.written == 1
    count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert count == 1


def test_extract_decisions_entity_fields(conn) -> None:
    note = _make_note(
        "We agreed to extend the engagement with Acme Corp.",
        entity_name="Acme Corp",
        entity_type="client",
    )
    extract_decisions(note, conn)
    row = conn.execute("SELECT entity_type, entity_name FROM decisions").fetchone()
    assert row[0] == "client"
    assert row[1] == "Acme Corp"


def test_extract_decisions_decision_date(conn) -> None:
    nd = date(2026, 6, 1)
    note = _make_note("Decision: go live on July 1st.", note_date=nd)
    extract_decisions(note, conn)
    row = conn.execute("SELECT decision_date FROM decisions").fetchone()
    assert str(row[0]) == "2026-06-01"


def test_extract_decisions_status_is_made(conn) -> None:
    note = _make_note("We decided to freeze the feature backlog.")
    extract_decisions(note, conn)
    row = conn.execute("SELECT status FROM decisions").fetchone()
    assert row[0] == "made"


def test_extract_decisions_no_matches_returns_zeros(conn) -> None:
    note = _make_note("Generic update — no decisions made today.")
    result = extract_decisions(note, conn)
    assert result.written == 0
    assert result.skipped == 0


# ------------------------------------------------------------------
# extract_decisions_from_all_notes
# ------------------------------------------------------------------


def _seed_note_in_db(conn, body: str, entity_name: str = "Alice") -> None:
    import uuid
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                              entity_name, title, body, tags, created_at)
           VALUES (?, 'raw', ?, '1on1', 'person', ?, 'Note', ?, '[]', CURRENT_TIMESTAMP)""",
        [str(uuid.uuid4()), date.today().isoformat(), entity_name, body],
    )


def test_extract_from_all_notes_aggregates(conn) -> None:
    _seed_note_in_db(conn, "Decision: use Cloud Run for serving.")
    _seed_note_in_db(conn, "Agreed: defer the Spanner migration.")
    _seed_note_in_db(conn, "No decisions here.")

    result = extract_decisions_from_all_notes(conn)
    assert result.written == 2


def test_extract_from_all_notes_idempotent(conn) -> None:
    _seed_note_in_db(conn, "Decision: go with GKE for workload isolation.")
    extract_decisions_from_all_notes(conn)
    result2 = extract_decisions_from_all_notes(conn)
    assert result2.skipped >= 1
    count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert count == 1


def test_extract_from_all_notes_empty_db(conn) -> None:
    result = extract_decisions_from_all_notes(conn)
    assert result.written == 0
    assert result.failed == 0
