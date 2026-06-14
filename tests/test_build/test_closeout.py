"""Tests for build/closeout.py (Issue #21)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.build.closeout import (
    generate_closeout,
    write_closeout_to_file,
    _get_signal_stats,
    _get_unresolved_high_signals,
    _get_decisions_for_date,
)


@pytest.fixture()
def conn():
    return get_connection(":memory:")


# ------------------------------------------------------------------
# Seed helpers
# ------------------------------------------------------------------


def _seed_signal(conn, severity: str = "high", status: str = "open",
                 signal_date: date | None = None, entity_name: str = "Alice",
                 signal_type: str = "risk") -> str:
    sig_id = content_hash(f"test::{entity_name}::{signal_type}::{severity}::{status}")
    conn.execute(
        """
        INSERT OR REPLACE INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, confidence, status, created_at, updated_at)
        VALUES (?, ?, 'rule', '', 'person', ?, ?, ?, 'Test signal', '',
                TRUE, 1.0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [sig_id, (signal_date or date.today()).isoformat(), entity_name, signal_type, severity, status],
    )
    return sig_id


def _seed_decision(conn, description: str, decision_date: date | None = None,
                   entity_name: str = "Team") -> None:
    row_id = content_hash(f"dec::{description[:40]}")
    conn.execute(
        """
        INSERT OR REPLACE INTO decisions
            (id, entity_type, entity_name, description, decision_date, status, owner, source_note_id, created_at)
        VALUES (?, 'team', ?, ?, ?, 'made', '', NULL, CURRENT_TIMESTAMP)
        """,
        [row_id, entity_name, description, (decision_date or date.today()).isoformat()],
    )


def _seed_action_item(conn, status: str = "open") -> None:
    ai_id = content_hash(f"ai::{status}::test")
    conn.execute(
        """
        INSERT OR REPLACE INTO action_items (id, assigned_to, description, status, created_at)
        VALUES (?, 'Alice', 'Follow up on Acme', ?, CURRENT_TIMESTAMP)
        """,
        [ai_id, status],
    )


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------


def test_stats_new_today(conn) -> None:
    _seed_signal(conn)
    stats = _get_signal_stats(conn, date.today())
    assert stats.new_today >= 1


def test_stats_still_open(conn) -> None:
    _seed_signal(conn, status="open")
    stats = _get_signal_stats(conn, date.today())
    assert stats.still_open >= 1


def test_stats_resolved_today(conn) -> None:
    sig_id = _seed_signal(conn, status="resolved")
    stats = _get_signal_stats(conn, date.today())
    assert stats.resolved_today >= 1


def test_stats_action_items_open(conn) -> None:
    _seed_action_item(conn, status="open")
    stats = _get_signal_stats(conn, date.today())
    assert stats.action_items_open >= 1


def test_stats_empty_db(conn) -> None:
    stats = _get_signal_stats(conn, date.today())
    assert stats.new_today == 0
    assert stats.still_open == 0
    assert stats.resolved_today == 0
    assert stats.action_items_open == 0


# ------------------------------------------------------------------
# Unresolved signals
# ------------------------------------------------------------------


def test_unresolved_high_signals(conn) -> None:
    _seed_signal(conn, severity="high", status="open")
    _seed_signal(conn, severity="critical", status="open", entity_name="Bob", signal_type="blocker")
    rows = _get_unresolved_high_signals(conn)
    assert len(rows) == 2


def test_unresolved_excludes_resolved(conn) -> None:
    _seed_signal(conn, severity="high", status="resolved")
    rows = _get_unresolved_high_signals(conn)
    assert rows == []


def test_unresolved_excludes_low_medium(conn) -> None:
    _seed_signal(conn, severity="low", status="open")
    _seed_signal(conn, severity="medium", status="open", entity_name="Carol", signal_type="follow_up")
    rows = _get_unresolved_high_signals(conn)
    assert rows == []


# ------------------------------------------------------------------
# Decisions
# ------------------------------------------------------------------


def test_decisions_for_date(conn) -> None:
    _seed_decision(conn, "Use Cloud Run for inference serving.")
    rows = _get_decisions_for_date(conn, date.today())
    assert len(rows) == 1
    assert "Cloud Run" in rows[0]["description"]


def test_decisions_excludes_other_dates(conn) -> None:
    yesterday = date.today() - timedelta(days=1)
    _seed_decision(conn, "Yesterday's decision.", decision_date=yesterday)
    rows = _get_decisions_for_date(conn, date.today())
    assert rows == []


# ------------------------------------------------------------------
# generate_closeout
# ------------------------------------------------------------------


def test_generate_closeout_returns_content(conn) -> None:
    result = generate_closeout(conn, target_date=date.today())
    assert "EOD Closeout" in result.content
    assert result.stats is not None


def test_generate_closeout_contains_decisions(conn) -> None:
    _seed_decision(conn, "Adopt Vertex AI for the ML platform.")
    result = generate_closeout(conn, target_date=date.today())
    assert "Vertex AI" in result.content


def test_generate_closeout_contains_unresolved_signals(conn) -> None:
    _seed_signal(conn, severity="critical", entity_name="Acme Corp")
    result = generate_closeout(conn, target_date=date.today())
    assert "Acme Corp" in result.content


def test_generate_closeout_no_unresolved_shows_clean_message(conn) -> None:
    result = generate_closeout(conn, target_date=date.today())
    assert "clean slate" in result.content or "None" in result.content


def test_generate_closeout_no_weekly_on_weekday(conn) -> None:
    # Find a non-Friday date
    d = date.today()
    while d.weekday() == 4:
        d += timedelta(days=1)
    result = generate_closeout(conn, target_date=d, include_weekly=None)
    assert result.weekly_exec_content is None


def test_generate_closeout_weekly_on_friday(conn) -> None:
    # Find the next Friday
    d = date.today()
    while d.weekday() != 4:
        d += timedelta(days=1)
    result = generate_closeout(conn, target_date=d, include_weekly=None)
    assert result.weekly_exec_content is not None
    assert "Weekly Exec Update" in result.weekly_exec_content


def test_generate_closeout_force_weekly(conn) -> None:
    # Force weekly even on a non-Friday
    d = date.today()
    while d.weekday() == 4:
        d += timedelta(days=1)
    result = generate_closeout(conn, target_date=d, include_weekly=True)
    assert result.weekly_exec_content is not None


def test_generate_closeout_suppress_weekly(conn) -> None:
    # Find a Friday but suppress weekly
    d = date.today()
    while d.weekday() != 4:
        d += timedelta(days=1)
    result = generate_closeout(conn, target_date=d, include_weekly=False)
    assert result.weekly_exec_content is None


def test_generate_closeout_stats_in_content(conn) -> None:
    _seed_signal(conn, severity="high", status="open")
    result = generate_closeout(conn, target_date=date.today())
    assert "1" in result.content  # new_today and still_open counts


# ------------------------------------------------------------------
# write_closeout_to_file
# ------------------------------------------------------------------


def test_write_closeout_to_file(conn, tmp_path: Path) -> None:
    result = generate_closeout(conn, target_date=date.today())
    out = write_closeout_to_file(result, date.today(), output_dir=str(tmp_path))
    assert out.exists()
    assert "EOD Closeout" in out.read_text()


def test_write_closeout_weekly_creates_second_file(conn, tmp_path: Path) -> None:
    d = date.today()
    while d.weekday() != 4:
        d += timedelta(days=1)
    result = generate_closeout(conn, target_date=d, include_weekly=True)
    write_closeout_to_file(result, d, output_dir=str(tmp_path))
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 2


def test_write_closeout_result_has_output_path(conn, tmp_path: Path) -> None:
    result = generate_closeout(conn, target_date=date.today())
    write_closeout_to_file(result, date.today(), output_dir=str(tmp_path))
    assert result.output_path is not None
    assert result.output_path.exists()
