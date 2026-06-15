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


# ===========================================================================
# New: ranking, limits, filters, source evidence
# ===========================================================================


def _seed_signal_ext(
    conn,
    entity_name: str,
    signal_type: str = "risk",
    severity: str = "high",
    summary: str = "Test signal",
    why_it_matters: str = "Because it matters",
    requires_manager_attention: bool = False,
    confidence: float = 1.0,
    source_path: str = "",
    due_date: date | None = None,
    status: str = "open",
) -> str:
    """Seed a signal with full control over ranking-relevant fields."""
    sig_id = content_hash(f"ext::{entity_name}::{severity}::{summary}::{source_path}")
    conn.execute(
        """
        INSERT INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, confidence, due_date, status,
             created_at, updated_at)
        VALUES (?, ?, 'rule', ?, 'client', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [
            sig_id,
            date.today().isoformat(),
            source_path,
            entity_name,
            signal_type,
            severity,
            summary,
            why_it_matters,
            requires_manager_attention,
            confidence,
            due_date,
            status,
        ],
    )
    return sig_id


# ------------------------------------------------------------------
# Ranking
# ------------------------------------------------------------------


class TestSignalRanking:
    def test_high_ranks_above_medium(self, conn) -> None:
        _seed_signal_ext(conn, "Medium Corp", severity="medium", summary="Medium signal")
        _seed_signal_ext(conn, "High Corp", severity="high", summary="High signal")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert brief.content.index("High signal") < brief.content.index("Medium signal")

    def test_requires_manager_ranks_higher(self, conn) -> None:
        _seed_signal_ext(conn, "Normal Corp", severity="high",
                         summary="Normal signal", requires_manager_attention=False)
        _seed_signal_ext(conn, "Urgent Corp", severity="high",
                         summary="Urgent signal", requires_manager_attention=True)
        brief = generate_daily_brief(conn, target_date=date.today())
        assert brief.content.index("Urgent signal") < brief.content.index("Normal signal")

    def test_due_soon_ranks_higher(self, conn) -> None:
        today = date.today()
        _seed_signal_ext(conn, "Far Corp", severity="high", summary="Far signal",
                         due_date=today + timedelta(days=30))
        _seed_signal_ext(conn, "Near Corp", severity="high", summary="Near signal",
                         due_date=today + timedelta(days=2))
        brief = generate_daily_brief(conn, target_date=today)
        assert brief.content.index("Near signal") < brief.content.index("Far signal")


# ------------------------------------------------------------------
# Section limits and overflow
# ------------------------------------------------------------------


class TestSectionLimits:
    def test_default_limit_truncates_risks(self, conn) -> None:
        for i in range(5):
            _seed_signal_ext(conn, f"Corp{i}", severity="high", summary=f"Risk alpha{i}")
        brief = generate_daily_brief(conn, target_date=date.today())
        shown = sum(1 for i in range(5) if f"Risk alpha{i}" in brief.content)
        assert shown == 3  # default risk limit is 3

    def test_overflow_line_shown_when_items_hidden(self, conn) -> None:
        for i in range(5):
            _seed_signal_ext(conn, f"Corp{i}", severity="high", summary=f"Risk beta{i}")
        brief = generate_daily_brief(conn, target_date=date.today())
        # 5 total - 3 shown = 2 hidden
        assert "2 additional risk signal(s) hidden" in brief.content

    def test_max_items_overrides_default_limit(self, conn) -> None:
        for i in range(6):
            _seed_signal_ext(conn, f"Corp{i}", severity="high", summary=f"Risk gamma{i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=5)
        shown = sum(1 for i in range(6) if f"Risk gamma{i}" in brief.content)
        assert shown == 5

    def test_max_items_one_reduces_to_one(self, conn) -> None:
        for i in range(4):
            _seed_signal_ext(conn, f"Corp{i}", severity="high", summary=f"Risk delta{i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=1)
        shown = sum(1 for i in range(4) if f"Risk delta{i}" in brief.content)
        assert shown == 1
        assert "3 additional risk signal(s) hidden" in brief.content


# ------------------------------------------------------------------
# Low-priority filter
# ------------------------------------------------------------------


class TestLowPriorityFilter:
    def test_low_severity_hidden_by_default(self, conn) -> None:
        _seed_signal_ext(conn, "Low Corp", severity="low", summary="Low-priority signal")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Low-priority signal" not in brief.content

    def test_include_low_priority_shows_low_signals(self, conn) -> None:
        _seed_signal_ext(conn, "Low Corp", severity="low", summary="Low-priority signal")
        brief = generate_daily_brief(conn, target_date=date.today(), include_low_priority=True)
        assert "Low-priority signal" in brief.content

    def test_high_signals_always_shown(self, conn) -> None:
        _seed_signal_ext(conn, "High Corp", severity="high", summary="High-priority signal")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "High-priority signal" in brief.content


# ------------------------------------------------------------------
# Source evidence
# ------------------------------------------------------------------


class TestSourceEvidence:
    def test_source_path_basename_shown_in_brief(self, conn) -> None:
        _seed_signal_ext(conn, "Source Corp", severity="high", summary="Risky thing",
                         source_path="/vault/client_notes.md")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "client_notes.md" in brief.content

    def test_no_source_path_does_not_error(self, conn) -> None:
        _seed_signal_ext(conn, "No Source Corp", severity="high", summary="No source sig",
                         source_path="")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "No source sig" in brief.content


# ===========================================================================
# Global max_items budget
# ===========================================================================


class TestGlobalMaxItems:
    def test_max_items_total_respected_across_sections(self, conn) -> None:
        """When max_items is set, total signals shown across all sections <= max_items."""
        for i in range(10):
            _seed_signal_ext(conn, f"Risk{i}", signal_type="risk",
                             severity="high", summary=f"Risk signal {i}")
            _seed_signal_ext(conn, f"Person{i}", signal_type="people_health",
                             severity="medium", summary=f"People signal {i}")
            _seed_signal_ext(conn, f"Deal{i}", signal_type="sow_loe_review",
                             severity="high", summary=f"Deal signal {i}")

        brief = generate_daily_brief(conn, target_date=date.today(), max_items=8)
        shown = sum(
            1
            for prefix in ("Risk signal", "People signal", "Deal signal")
            for i in range(10)
            if f"{prefix} {i}" in brief.content
        )
        assert shown <= 8

    def test_max_items_shown_signals_field(self, conn) -> None:
        for i in range(20):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=5)
        assert brief.shown_signals == 5

    def test_max_items_shown_less_than_total_when_few_signals(self, conn) -> None:
        for i in range(3):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=10)
        assert brief.shown_signals == 3
        assert len(brief.signal_ids) == 3

    def test_max_items_all_shown_when_equal(self, conn) -> None:
        for i in range(5):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=5)
        assert brief.shown_signals == 5

    def test_shown_signals_in_content_header(self, conn) -> None:
        """Template header shows shown/total numbers."""
        for i in range(20):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=7)
        assert "7" in brief.content
        assert "20" in brief.content

    def test_include_low_priority_still_respects_max_items(self, conn) -> None:
        for i in range(20):
            _seed_signal_ext(conn, f"Corp{i}", severity="low", summary=f"Low {i}")
        brief = generate_daily_brief(
            conn, target_date=date.today(), max_items=5, include_low_priority=True
        )
        assert brief.shown_signals <= 5

    def test_max_items_none_uses_per_section_defaults(self, conn) -> None:
        """Without max_items, per-section defaults apply (risks default = 3)."""
        for i in range(6):
            _seed_signal_ext(conn, f"Corp{i}", signal_type="risk", summary=f"Risk {i}")
        brief = generate_daily_brief(conn, target_date=date.today())
        shown = sum(1 for i in range(6) if f"Risk {i}" in brief.content)
        assert shown == 3


# ===========================================================================
# Brief output shown_signals field
# ===========================================================================


class TestBriefOutputCounts:
    def test_shown_signals_zero_for_empty_db(self, conn) -> None:
        brief = generate_daily_brief(conn, target_date=date.today())
        assert brief.shown_signals == 0

    def test_shown_signals_equals_total_when_under_default_limit(self, conn) -> None:
        for i in range(2):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert brief.shown_signals == 2

    def test_signal_ids_contains_all_open_signals(self, conn) -> None:
        for i in range(5):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert len(brief.signal_ids) == 5

    def test_shown_less_than_total_when_limit_applied(self, conn) -> None:
        for i in range(8):
            _seed_signal_ext(conn, f"Corp{i}", signal_type="risk", summary=f"Risk {i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=3)
        assert brief.shown_signals == 3
        assert len(brief.signal_ids) == 8


# ===========================================================================
# Deduplication
# ===========================================================================


class TestDeduplication:
    def test_dedup_same_source_same_type_keeps_one(self, conn) -> None:
        """Multiple signals from the same source note + signal_type -> only 1 shown."""
        for i in range(3):
            _seed_signal_ext(
                conn, f"Corp{i}",
                source_path="/vault/notes/noisy_note.md",
                signal_type="risk",
                summary=f"Dup signal {i}",
                severity="high",
            )
        brief = generate_daily_brief(conn, target_date=date.today())
        shown = sum(1 for i in range(3) if f"Dup signal {i}" in brief.content)
        assert shown == 1

    def test_dedup_different_sources_all_shown(self, conn) -> None:
        for i in range(3):
            _seed_signal_ext(
                conn, f"Corp{i}",
                source_path=f"/vault/notes/note_{i}.md",
                signal_type="risk",
                summary=f"Unique signal {i}",
                severity="high",
            )
        brief = generate_daily_brief(conn, target_date=date.today())
        shown = sum(1 for i in range(3) if f"Unique signal {i}" in brief.content)
        assert shown == 3

    def test_dedup_empty_source_path_not_deduplicated(self, conn) -> None:
        """Computed signals (source_path='') are never deduplicated."""
        for i in range(3):
            _seed_signal_ext(
                conn, f"Corp{i}",
                source_path="",
                signal_type="risk",
                summary=f"Computed {i}",
            )
        brief = generate_daily_brief(conn, target_date=date.today())
        shown = sum(1 for i in range(3) if f"Computed {i}" in brief.content)
        assert shown == 3

    def test_dedup_different_types_same_source_both_shown(self, conn) -> None:
        """Same source but different signal_type -> both kept (different dedup key)."""
        _seed_signal_ext(
            conn, "Corp A",
            source_path="/vault/notes/shared.md",
            signal_type="risk",
            summary="Risk from shared",
        )
        _seed_signal_ext(
            conn, "Corp B",
            source_path="/vault/notes/shared.md",
            signal_type="people_health",
            summary="People from shared",
        )
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Risk from shared" in brief.content
        assert "People from shared" in brief.content


# ===========================================================================
# Priority ranking
# ===========================================================================


class TestPriorityRankingCrossType:
    def test_due_soon_deal_ranks_above_generic_risk(self, conn) -> None:
        today = date.today()
        _seed_signal_ext(
            conn, "Generic Note", signal_type="risk", severity="medium",
            summary="Generic concern noted", confidence=0.7,
        )
        _seed_signal_ext(
            conn, "Urgent Deal", signal_type="sow_loe_review", severity="high",
            summary="SOW unsigned 2 days until close",
            due_date=today + timedelta(days=2),
            requires_manager_attention=True,
        )
        brief = generate_daily_brief(conn, target_date=today, max_items=2)
        assert "SOW unsigned 2 days until close" in brief.content

    def test_capacity_gap_included_in_budget(self, conn) -> None:
        _seed_signal_ext(
            conn, "Engineer A", signal_type="utilization_risk", severity="high",
            summary="120% allocation overalloc",
        )
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=5)
        assert "120% allocation overalloc" in brief.content

    def test_close_date_soon_beats_low_priority(self, conn) -> None:
        today = date.today()
        _seed_signal_ext(
            conn, "Big Deal", signal_type="sow_loe_review", severity="high",
            summary="Close in 3 days",
            due_date=today + timedelta(days=3),
        )
        _seed_signal_ext(
            conn, "Generic Corp", signal_type="risk", severity="low",
            summary="Low priority note",
        )
        brief = generate_daily_brief(conn, target_date=today, max_items=1)
        assert "Close in 3 days" in brief.content
        assert "Low priority note" not in brief.content

