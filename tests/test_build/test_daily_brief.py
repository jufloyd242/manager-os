"""Tests for the daily brief generator (build/daily_brief.py)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.build.daily_brief import generate_daily_brief, write_brief_to_file


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def _seed_signal(conn, entity_name: str = "Acme Corp", signal_type: str = "risk",
                 severity: str = "high", summary: str = "Test signal",
                 why_it_matters: str = "Because it matters",
                 signal_date: date | None = None,
                 status: str = "open") -> str:
    sig_id = content_hash(f"{entity_name}::{signal_type}::{severity}::{summary}")
    sd = (signal_date or date.today()).isoformat()
    conn.execute(
        """
        INSERT INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, confidence, status, created_at, updated_at)
        VALUES (?, ?, 'rule', '', 'client', ?, ?, ?, ?, ?, TRUE, 1.0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [sig_id, sd, entity_name, signal_type, severity, summary, why_it_matters, status],
    )
    return sig_id


def _seed_action_item(conn, description: str = "Follow up with Alice",
                      assigned_to: str = "manager",
                      due_date: date | None = None) -> None:
    from manager_os.db import content_hash
    ai_id = content_hash(f"ai::{description}")
    conn.execute(
        """
        INSERT INTO action_items
            (id, assigned_to, description, due_date, status, created_at)
        VALUES (?, ?, ?, ?, 'open', CURRENT_TIMESTAMP)
        """,
        [ai_id, assigned_to, description, due_date],
    )


# ------------------------------------------------------------------
# Core generation
# ------------------------------------------------------------------


def test_generate_brief_with_signals(conn) -> None:
    _seed_signal(conn, entity_name="Acme Corp", summary="Data pipeline at risk")
    _seed_signal(conn, entity_name="Alice Chen", signal_type="people_health",
                 severity="medium", summary="Stale 1:1 with Alice Chen")

    brief = generate_daily_brief(conn, target_date=date.today())

    assert brief.content
    assert "Acme Corp" in brief.content
    assert "Alice Chen" in brief.content
    assert len(brief.signal_ids) == 2


def test_generate_brief_empty_db(conn) -> None:
    """A brief with no signals should still render without errors."""
    brief = generate_daily_brief(conn, target_date=date.today())
    assert brief.content
    assert str(date.today()) in brief.content
    assert brief.signal_ids == []


def test_generate_brief_includes_action_items(conn) -> None:
    _seed_action_item(conn, "Schedule architecture review", due_date=date.today())
    brief = generate_daily_brief(conn, target_date=date.today())
    assert "architecture review" in brief.content.lower()


def test_generate_brief_omits_acknowledged_signals(conn) -> None:
    _seed_signal(conn, entity_name="Acme Corp", summary="Old signal", status="acknowledged")
    brief = generate_daily_brief(conn, target_date=date.today())
    # acknowledged signals not shown (only open)
    assert "Old signal" not in brief.content


def test_generate_brief_date_in_header(conn) -> None:
    target = date(2026, 6, 13)
    brief = generate_daily_brief(conn, target_date=target)
    assert "2026-06-13" in brief.content


def test_generate_brief_critical_in_own_section(conn) -> None:
    _seed_signal(conn, entity_name="BigClient", signal_type="risk", severity="critical",
                 summary="Client escalation imminent")
    brief = generate_daily_brief(conn, target_date=date.today())
    assert "BigClient" in brief.content
    # Critical section should appear
    assert "Critical" in brief.content


def test_generate_brief_people_signal_section(conn) -> None:
    _seed_signal(conn, entity_name="Bob Martinez", signal_type="people_health",
                 severity="medium", summary="No 1:1 in 20 days")
    brief = generate_daily_brief(conn, target_date=date.today())
    assert "Bob Martinez" in brief.content


def test_generate_brief_deal_signal_section(conn) -> None:
    _seed_signal(conn, entity_name="Big Retail Recs v2", signal_type="sow_loe_review",
                 severity="high", summary="SOW unsigned in 4 days")
    brief = generate_daily_brief(conn, target_date=date.today())
    assert "Big Retail Recs v2" in brief.content


# ------------------------------------------------------------------
# DB persistence
# ------------------------------------------------------------------


def test_brief_written_to_db(conn) -> None:
    _seed_signal(conn)
    generate_daily_brief(conn, target_date=date.today())
    row = conn.execute("SELECT brief_date, content FROM daily_briefs").fetchone()
    assert row is not None
    assert row[1]  # content is non-empty


def test_brief_overwrite_on_regenerate(conn) -> None:
    _seed_signal(conn)
    generate_daily_brief(conn, target_date=date.today())
    generate_daily_brief(conn, target_date=date.today())
    count = conn.execute("SELECT COUNT(*) FROM daily_briefs").fetchone()[0]
    assert count == 1  # INSERT OR REPLACE — only one row per date


# ------------------------------------------------------------------
# File output
# ------------------------------------------------------------------


def test_write_brief_to_file(conn, tmp_path: Path) -> None:
    _seed_signal(conn, summary="Important risk")
    brief = generate_daily_brief(conn, target_date=date(2026, 6, 13))
    out_file = write_brief_to_file(brief, output_path=str(tmp_path / "test_brief.md"))
    assert out_file.exists()
    content = out_file.read_text()
    assert "Important risk" in content
    assert "2026-06-13" in content


def test_write_brief_default_path(conn, tmp_path: Path, monkeypatch) -> None:
    """Default path uses output/daily_briefs/YYYY-MM-DD.md."""
    import manager_os.build.daily_brief as bd_module
    monkeypatch.setattr(bd_module, "_OUTPUT_DIR", tmp_path)

    _seed_signal(conn)
    brief = generate_daily_brief(conn, target_date=date(2026, 6, 13))
    out_file = write_brief_to_file(brief)
    assert out_file.name == "2026-06-13.md"
    assert out_file.exists()
