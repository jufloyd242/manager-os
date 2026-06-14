"""Tests for signal status audit trail (Issue #25)."""

from __future__ import annotations

from datetime import date

import pytest

from manager_os.db import content_hash, get_connection, list_tables
from manager_os.build.dashboard_data import (
    update_signal_status,
    get_signal_status_history,
)


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def _seed_signal(conn, status: str = "open") -> str:
    sig_id = content_hash(f"test::signal::{status}")
    conn.execute(
        """
        INSERT OR REPLACE INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, confidence, status, created_at, updated_at)
        VALUES (?, ?, 'rule', '', 'person', 'Alice Chen', 'risk', 'high',
                'Test signal', '', TRUE, 1.0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [sig_id, date.today().isoformat(), status],
    )
    return sig_id


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------


def test_signal_status_log_table_exists(conn) -> None:
    tables = list_tables(conn)
    assert "signal_status_log" in tables


# ------------------------------------------------------------------
# update_signal_status
# ------------------------------------------------------------------


def test_update_signal_status_changes_status(conn) -> None:
    sig_id = _seed_signal(conn)
    update_signal_status(conn, sig_id, "acknowledged")
    row = conn.execute("SELECT status FROM signals WHERE id = ?", [sig_id]).fetchone()
    assert row[0] == "acknowledged"


def test_update_signal_status_writes_log(conn) -> None:
    sig_id = _seed_signal(conn)
    update_signal_status(conn, sig_id, "acknowledged")
    count = conn.execute(
        "SELECT COUNT(*) FROM signal_status_log WHERE signal_id = ?", [sig_id]
    ).fetchone()[0]
    assert count == 1


def test_update_signal_status_log_has_old_and_new(conn) -> None:
    sig_id = _seed_signal(conn, status="open")
    update_signal_status(conn, sig_id, "resolved")
    row = conn.execute(
        "SELECT old_status, new_status FROM signal_status_log WHERE signal_id = ?", [sig_id]
    ).fetchone()
    assert row[0] == "open"
    assert row[1] == "resolved"


def test_update_signal_status_log_records_changed_by(conn) -> None:
    sig_id = _seed_signal(conn)
    update_signal_status(conn, sig_id, "dismissed", changed_by="cli")
    row = conn.execute(
        "SELECT changed_by FROM signal_status_log WHERE signal_id = ?", [sig_id]
    ).fetchone()
    assert row[0] == "cli"


def test_update_signal_status_log_records_note(conn) -> None:
    sig_id = _seed_signal(conn)
    update_signal_status(conn, sig_id, "acknowledged", note="Discussed in standup")
    row = conn.execute(
        "SELECT note FROM signal_status_log WHERE signal_id = ?", [sig_id]
    ).fetchone()
    assert row[0] == "Discussed in standup"


def test_update_signal_status_multiple_transitions(conn) -> None:
    sig_id = _seed_signal(conn, status="open")
    update_signal_status(conn, sig_id, "acknowledged")
    update_signal_status(conn, sig_id, "resolved")
    count = conn.execute(
        "SELECT COUNT(*) FROM signal_status_log WHERE signal_id = ?", [sig_id]
    ).fetchone()[0]
    assert count == 2


def test_update_nonexistent_signal_graceful(conn) -> None:
    # Should not raise
    update_signal_status(conn, "nonexistent-id", "acknowledged")
    count = conn.execute("SELECT COUNT(*) FROM signal_status_log").fetchone()[0]
    assert count == 1  # log entry still written with old_status="unknown"


# ------------------------------------------------------------------
# get_signal_status_history
# ------------------------------------------------------------------


def test_get_signal_status_history_returns_ordered(conn) -> None:
    sig_id = _seed_signal(conn, status="open")
    update_signal_status(conn, sig_id, "acknowledged")
    update_signal_status(conn, sig_id, "resolved")
    history = get_signal_status_history(conn, sig_id)
    assert len(history) == 2
    assert history[0]["new_status"] == "acknowledged"
    assert history[1]["new_status"] == "resolved"


def test_get_signal_status_history_empty_for_unknown(conn) -> None:
    history = get_signal_status_history(conn, "no-such-id")
    assert history == []


def test_get_signal_status_history_contains_changed_by(conn) -> None:
    sig_id = _seed_signal(conn)
    update_signal_status(conn, sig_id, "dismissed", changed_by="dashboard")
    history = get_signal_status_history(conn, sig_id)
    assert history[0]["changed_by"] == "dashboard"
