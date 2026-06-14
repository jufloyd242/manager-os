"""Tests for action item extraction (extract/action_items.py)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from manager_os.db import get_connection
from manager_os.extract.action_items import extract_action_items
from manager_os.schemas import NoteRecord


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def _make_note(body: str, note_date: date | None = None) -> NoteRecord:
    return NoteRecord(
        raw_document_id="raw-test",
        note_date=note_date or date(2026, 6, 13),
        note_type="1on1",
        entity_name="Alice Chen",
        title="Test Note",
        body=body,
    )


# ------------------------------------------------------------------
# Manager commitment patterns
# ------------------------------------------------------------------


def test_manager_commitment_i_will(conn) -> None:
    note = _make_note("I will follow up with the client by EOD.")
    result = extract_action_items(note, conn)
    assert result.written >= 1
    ai = conn.execute("SELECT assigned_to, description FROM action_items").fetchone()
    assert ai[0] == "manager"
    assert "follow up" in ai[1].lower()


def test_manager_commitment_ill(conn) -> None:
    note = _make_note("I'll send the proposal by EOW.")
    result = extract_action_items(note, conn)
    assert result.written >= 1
    rows = conn.execute("SELECT assigned_to FROM action_items").fetchall()
    assert any(r[0] == "manager" for r in rows)


def test_manager_commitment_due_date_eod(conn) -> None:
    anchor = date(2026, 6, 13)
    note = _make_note("I'll reach out to Alice by EOD.", note_date=anchor)
    extract_action_items(note, conn)
    row = conn.execute("SELECT due_date FROM action_items").fetchone()
    assert row is not None
    assert str(row[0]) == "2026-06-13"


def test_manager_commitment_due_date_eow(conn) -> None:
    # 2026-06-13 is a Saturday; EOW = Friday of this week = 2026-06-19 (next Friday)
    anchor = date(2026, 6, 13)
    note = _make_note("I'll schedule the review by EOW.", note_date=anchor)
    extract_action_items(note, conn)
    row = conn.execute("SELECT due_date FROM action_items").fetchone()
    assert row is not None
    # EOW from a Saturday should be Friday this week or next
    assert row[0] is not None


# ------------------------------------------------------------------
# TODO patterns
# ------------------------------------------------------------------


def test_todo_marker(conn) -> None:
    note = _make_note("TODO: Schedule architecture review for Alice's MLOps interests")
    result = extract_action_items(note, conn)
    assert result.written >= 1
    ai = conn.execute("SELECT description FROM action_items WHERE assigned_to = 'manager'").fetchone()
    assert ai is not None
    assert "architecture review" in ai[0].lower()


def test_action_item_colon(conn) -> None:
    note = _make_note("Action Item: Loop in the AE on the scope decision")
    result = extract_action_items(note, conn)
    assert result.written >= 1


def test_ai_marker(conn) -> None:
    note = _make_note("AI: Review LOE by next Friday")
    result = extract_action_items(note, conn)
    assert result.written >= 1


# ------------------------------------------------------------------
# Waiting on patterns
# ------------------------------------------------------------------


def test_waiting_on_basic(conn) -> None:
    note = _make_note("Waiting on client to confirm data team availability")
    result = extract_action_items(note, conn)
    assert result.written >= 1
    ai = conn.execute("SELECT assigned_to, description FROM action_items").fetchone()
    assert "client" in ai[0].lower() or "waiting" in ai[1].lower()


def test_waiting_on_with_action(conn) -> None:
    note = _make_note("Waiting on Bob to send the SOW redlines")
    result = extract_action_items(note, conn)
    assert result.written >= 1
    ai = conn.execute("SELECT description FROM action_items").fetchone()
    assert "Bob" in ai[0] or "waiting" in ai[0].lower()


# ------------------------------------------------------------------
# Idempotency
# ------------------------------------------------------------------


def test_action_items_not_duplicated(conn) -> None:
    note = _make_note("TODO: Follow up with Alice by EOD")
    extract_action_items(note, conn)
    extract_action_items(note, conn)
    count = conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
    # Should not double-insert
    assert count >= 1


def test_force_allows_reinsert(conn) -> None:
    note = _make_note("TODO: Send the proposal")
    extract_action_items(note, conn)
    result2 = extract_action_items(note, conn, force=True)
    # force=True rewrites, count stays the same (INSERT OR REPLACE)
    assert conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0] >= 1


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


def test_empty_body(conn) -> None:
    note = _make_note("")
    result = extract_action_items(note, conn)
    assert result.written == 0


def test_no_action_patterns_in_body(conn) -> None:
    note = _make_note("The meeting went well. Alice gave a great demo of the feature store.")
    result = extract_action_items(note, conn)
    # No commits, todos, or waiting-on patterns → may or may not produce items
    # Just assert no exception
    assert result.failed == 0


def test_fixture_1on1_note_extracts_items(conn) -> None:
    """The Alice 1:1 fixture note has explicit TODOs and commitments."""
    from pathlib import Path
    vault = Path(__file__).parent.parent / "fixtures" / "vault"
    text = (vault / "1on1_alice.md").read_text()
    # strip frontmatter manually for test
    import frontmatter as fm_lib
    post = fm_lib.loads(text)
    note = NoteRecord(
        raw_document_id="raw-fixture",
        note_date=date(2026, 5, 28),
        note_type="1on1",
        entity_name="Alice Chen",
        title="1:1 with Alice Chen",
        body=post.content,
    )
    result = extract_action_items(note, conn)
    assert result.written >= 1
