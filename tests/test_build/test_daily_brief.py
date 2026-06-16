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
    counter: int = 0,
) -> str:
    """Seed a signal with full control over ranking-relevant fields."""
    sig_id = content_hash(f"ext::{entity_name}::{severity}::{summary}::{source_path}::{counter}")
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
        assert "2 additional risk signal(s) not shown." in brief.content

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
        assert "3 additional risk signal(s) not shown." in brief.content


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
        """Template header shows candidate total; shown count does not exceed max_items."""
        for i in range(20):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=7)
        # total_signals (20) must appear in the header metadata line
        assert "20" in brief.content
        # shown_total must not exceed max_items
        assert brief.shown_signals <= 7

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
        """Multiple signals from the same source with same entity and type -> only 1 shown."""
        for i in range(3):
            _seed_signal_ext(
                conn, "Acme Corp",  # same entity for all three
                source_path="/vault/notes/noisy_note.md",
                signal_type="risk",
                summary="Acme data pipeline delayed",  # same underlying risk
                severity="high",
                counter=i,
            )
        brief = generate_daily_brief(conn, target_date=date.today())
        # With domain-aware dedupe, all three have same key -> only 1 should be in the deduped set.
        # The dedupe count is tracked internally.
        assert brief.shown_signals == 1, f"Expected 1 shown signal, got {brief.shown_signals}"

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


# ===========================================================================
# Phase 5 — global max-items, candidate header, noise, deal source
# ===========================================================================


class TestGlobalMaxItemsV2:
    """--max-items limits total primary items: signals + follow-ups + decisions."""

    def test_max_items_includes_follow_ups_in_total(self, conn) -> None:
        """Follow-ups count toward max_items, so 20 signals + 4 follow-ups != 20."""
        for i in range(20):
            _seed_signal_ext(conn, f"Risk{i}", signal_type="risk", summary=f"Risk item {i}")
        for i in range(5):
            _seed_action_item(conn, f"Follow up with person {i} about project", assigned_to="manager")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=10)
        assert brief.shown_signals <= 10, (
            f"shown_signals ({brief.shown_signals}) must be <= max_items (10)"
        )

    def test_max_items_20_total_primary_items(self, conn) -> None:
        """With 15 signals + 5 follow-ups, max_items=20 must show ≤ 20 total."""
        for i in range(15):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        for i in range(5):
            _seed_action_item(conn, f"Review outstanding items for client {i}", assigned_to="manager")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=20)
        assert brief.shown_signals <= 20

    def test_max_items_deals_take_priority_over_risks(self, conn) -> None:
        """Structured deal signals appear even when risks fill the budget."""
        for i in range(10):
            _seed_signal_ext(conn, f"Risk{i}", signal_type="risk", severity="high",
                             summary=f"Risk note {i}", confidence=0.70)
        _seed_signal_ext(conn, "Urgent Deal", signal_type="sow_loe_review", severity="high",
                         summary="Deal closes in 3 days",
                         due_date=date.today() + timedelta(days=3),
                         requires_manager_attention=True)
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=5)
        assert "Deal closes in 3 days" in brief.content


class TestHeaderCandidateCounts:
    """Header shows candidate pool and breakdown."""

    def test_header_shows_shown_of_candidates(self, conn) -> None:
        for i in range(15):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=5)
        # Header should include total candidates and max shown
        assert "5" in brief.content  # shown_total
        assert "15" in brief.content  # from total_signals

    def test_header_signals_count_line_present(self, conn) -> None:
        for i in range(3):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Signal {i}")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Signals:" in brief.content

    def test_header_decisions_count_in_signals_line(self, conn) -> None:
        _seed_signal_ext(conn, "Corp A", summary="Some signal")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Decisions:" in brief.content


class TestDealSourceRendering:
    """Deal signals should render with a deals:: readable source path."""

    def test_deal_source_renders_as_deals_csv(self, conn) -> None:
        """Signal with source_path='deals::OPP123' should show 'deals.csv · OPP123'."""
        sig_id = content_hash("deal_test::OPP123")
        conn.execute(
            """
            INSERT INTO signals
                (id, signal_date, source, source_path, entity_type, entity_name,
                 signal_type, severity, summary, why_it_matters,
                 requires_manager_attention, confidence, status, created_at, updated_at)
            VALUES (?, ?, 'rule', 'deals::OPP123', 'deal', 'Big Corp',
                    'sow_loe_review', 'high', 'SOW unsigned closes in 3 days', 'Revenue risk',
                    TRUE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [sig_id, date.today().isoformat()],
        )
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "deals.csv" in brief.content
        assert "OPP123" in brief.content

    def test_empty_source_path_shows_no_source(self, conn) -> None:
        _seed_signal_ext(conn, "Corp A", signal_type="sow_loe_review", summary="Unsigned SOW",
                         source_path="")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "no source" in brief.content


class TestJunkActionItemSuppression:
    """Vague/junk follow-up descriptions are suppressed."""

    def _seed_ai(self, conn, description: str, assigned_to: str = "manager") -> None:
        ai_id = content_hash(f"junk_test::{description}")
        conn.execute(
            """
            INSERT INTO action_items
                (id, assigned_to, description, status, created_at)
            VALUES (?, ?, ?, 'open', CURRENT_TIMESTAMP)
            """,
            [ai_id, assigned_to, description],
        )

    def test_short_description_suppressed(self, conn) -> None:
        from manager_os.build.daily_brief import _is_junk_action_item
        assert _is_junk_action_item("do it") is True
        assert _is_junk_action_item("this one") is True

    def test_junk_pattern_suppressed(self, conn) -> None:
        from manager_os.build.daily_brief import _is_junk_action_item
        assert _is_junk_action_item("implement isolated agents in the system") is True
        assert _is_junk_action_item("increase delivery velocity next sprint") is True
        assert _is_junk_action_item("use Expel's JIRA and confluence for tracking") is True

    def test_clear_action_item_retained(self, conn) -> None:
        from manager_os.build.daily_brief import _is_junk_action_item
        assert _is_junk_action_item("Schedule architecture review with Alice by Friday") is False
        assert _is_junk_action_item("Review SOW draft with legal team before Monday") is False

    def test_junk_ai_not_in_brief(self, conn) -> None:
        self._seed_ai(conn, "this one")
        self._seed_ai(conn, "signature")
        self._seed_ai(conn, "feedback from customer today")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "this one" not in brief.content
        assert "signature" not in brief.content

    def test_valid_ai_appears_in_brief(self, conn) -> None:
        self._seed_ai(conn, "Follow up with Alice about staffing gap on Acme project")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Alice" in brief.content or "staffing gap" in brief.content


# ---------------------------------------------------------------------------
# TestItemCounting — Requirements: counting/budgeting correctness
# ---------------------------------------------------------------------------

def _count_primary_bullets(content: str) -> int:
    """Count top-level bullet lines (lines starting with '- ') in the brief content.

    Excludes sub-bullets (lines with leading spaces before '- ') and overflow
    notice lines that start with '*'.
    """
    return sum(
        1 for line in content.splitlines()
        if line.startswith("- ") and not line.startswith("-  ")
    )


def _seed_waiting_on(conn, assignee: str, description: str) -> None:
    """Seed a non-manager action item (waiting-on)."""
    ai_id = content_hash(f"wo::{assignee}::{description}")
    conn.execute(
        """
        INSERT INTO action_items
            (id, assigned_to, description, status, created_at)
        VALUES (?, ?, ?, 'open', CURRENT_TIMESTAMP)
        """,
        [ai_id, assignee, description],
    )


class TestItemCounting:
    """Primary bullet count must match header shown_total, and max_items is global."""

    def test_shown_total_equals_rendered_bullets(self, conn) -> None:
        """brief.shown_signals must equal the number of rendered primary bullets."""
        # Mix of signals, follow-ups, and waiting-on
        for i in range(6):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Risk {i}")
        _seed_action_item(conn, "Follow up with Bob about contract renewal")
        _seed_action_item(conn, "Follow up with Carol on SOW signature")
        _seed_waiting_on(conn, "Alice", "Waiting on Alice to return signed contract")
        _seed_waiting_on(conn, "Bob", "Waiting on Bob to confirm budget approval")

        brief = generate_daily_brief(conn, target_date=date.today(), max_items=20)
        rendered = _count_primary_bullets(brief.content)
        assert brief.shown_signals == rendered, (
            f"shown_signals={brief.shown_signals} but rendered {rendered} bullets"
        )

    def test_max_items_never_exceeded(self, conn) -> None:
        """--max-items 10 must never render more than 10 primary bullets."""
        for i in range(20):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Risk {i}")
        for i in range(10):
            _seed_action_item(conn, f"Follow up with person {i} about project")
        for i in range(10):
            _seed_waiting_on(conn, f"Vendor{i}", f"Waiting on Vendor{i} to approve invoice {i}")

        brief = generate_daily_brief(conn, target_date=date.today(), max_items=10)
        rendered = _count_primary_bullets(brief.content)
        assert rendered <= 10, f"Rendered {rendered} bullets but max_items=10"
        assert brief.shown_signals <= 10

    def test_hidden_count_math(self, conn) -> None:
        """total_hidden = total_candidates - shown_total."""
        for i in range(8):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Risk {i}")
        _seed_action_item(conn, "Follow up with Dave about renewal")
        _seed_waiting_on(conn, "Legal", "Waiting on Legal to review the SOW draft")

        brief = generate_daily_brief(conn, target_date=date.today(), max_items=5)
        # Parse header line: "Showing N of M candidate item(s)"
        import re
        m = re.search(r"Showing (\d+) of (\d+) candidate", brief.content)
        assert m, "Header line not found in brief"
        shown = int(m.group(1))
        total = int(m.group(2))
        assert shown == brief.shown_signals
        hidden_in_brief = total - shown
        # hidden count must be non-negative
        assert hidden_in_brief >= 0
        # brief.shown_signals must not exceed total_candidates
        assert brief.shown_signals <= total

    def test_quality_filtered_phrase_when_fewer_than_max(self, conn) -> None:
        """When shown < max_items due to filters, header says 'after quality filters'."""
        # Seed only 2 high-quality signals but ask for 20
        _seed_signal_ext(conn, "Acme Corp", summary="Contract unsigned — deal at risk")
        _seed_signal_ext(conn, "Bob Smith", signal_type="people_health",
                         summary="Stale 1:1 with Bob Smith", severity="medium")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=20)
        assert "after quality filters" in brief.content

    def test_no_quality_filtered_phrase_when_max_reached(self, conn) -> None:
        """When shown == max_items, the 'after quality filters' phrase is absent."""
        for i in range(25):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Contract at risk {i}")
        for i in range(10):
            _seed_action_item(conn, f"Follow up with person {i} about urgent contract")
        brief = generate_daily_brief(conn, target_date=date.today(), max_items=5)
        # shown should equal max_items (5) since we have many more candidates
        assert brief.shown_signals == 5
        assert "after quality filters" not in brief.content

    def test_waiting_on_capped_within_global_budget(self, conn) -> None:
        """Waiting-on items must be capped by the global budget."""
        # Seed many signals and many waiting-on items
        for i in range(15):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Risk {i}")
        for i in range(10):
            _seed_waiting_on(conn, f"Vendor{i}", f"Waiting on Vendor{i} to deliver item {i}")

        brief = generate_daily_brief(conn, target_date=date.today(), max_items=8)
        rendered = _count_primary_bullets(brief.content)
        assert rendered <= 8, f"Rendered {rendered} bullets but max_items=8"

    def test_cli_and_markdown_candidate_counts_agree(self, conn) -> None:
        """shown_signals on DailyBrief matches the header line in the markdown."""
        for i in range(5):
            _seed_signal_ext(conn, f"Corp{i}", summary=f"Risk {i}")
        _seed_action_item(conn, "Follow up with manager about Q3 review")
        _seed_waiting_on(conn, "Legal", "Waiting on Legal to sign off on contract")

        brief = generate_daily_brief(conn, target_date=date.today(), max_items=20)
        import re
        m = re.search(r"Showing (\d+) of (\d+) candidate", brief.content)
        assert m, "Header 'Showing N of M' line not found"
        header_shown = int(m.group(1))
        assert header_shown == brief.shown_signals, (
            f"Markdown header says {header_shown} but brief.shown_signals={brief.shown_signals}"
        )


