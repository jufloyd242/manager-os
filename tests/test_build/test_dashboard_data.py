"""Tests for dashboard_data.py query functions (Issues #14–#17)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.build.dashboard_data import (
    get_client_rows,
    get_deal_rows,
    get_forecast_rows,
    get_forecast_summary,
    get_forecast_week_list,
    get_people_allocation_for_week,
    get_people_rows,
    get_today_signals,
)


@pytest.fixture()
def conn():
    return get_connection(":memory:")


# ------------------------------------------------------------------
# Shared seed helpers
# ------------------------------------------------------------------


def _seed_signal(conn, entity_name: str, entity_type: str, signal_type: str = "risk",
                 severity: str = "high", status: str = "open",
                 signal_date: date | None = None) -> str:
    sig_id = content_hash(f"{entity_name}::{entity_type}::{signal_type}::{severity}")
    conn.execute(
        """
        INSERT INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, confidence, status, created_at, updated_at)
        VALUES (?, ?, 'rule', '', ?, ?, ?, ?, 'Test signal', '',
                TRUE, 1.0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [sig_id, (signal_date or date.today()).isoformat(), entity_type,
         entity_name, signal_type, severity, status],
    )
    return sig_id


def _seed_note(conn, entity_name: str, entity_type: str = "person",
               note_type: str = "1on1", note_date: date | None = None) -> None:
    import uuid
    conn.execute(
        """
        INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                           entity_name, title, body, tags, created_at)
        VALUES (?, 'raw', ?, ?, ?, ?, 'Test note', 'body', '[]', CURRENT_TIMESTAMP)
        """,
        [str(uuid.uuid4()), (note_date or date.today()).isoformat(),
         note_type, entity_type, entity_name],
    )


def _seed_deal(conn, deal_name: str, account: str = "Acme Corp",
               close_date: date | None = None, sow_status: str = "pending") -> None:
    row_id = content_hash(f"{account}::{deal_name}")
    conn.execute(
        """
        INSERT INTO deals (id, account, deal_name, stage, close_date, sow_status, loe_status, updated_at)
        VALUES (?, ?, ?, 'SOW Review', ?, ?, 'signed', CURRENT_TIMESTAMP)
        """,
        [row_id, account, deal_name,
         (close_date or date.today() + timedelta(days=10)).isoformat(), sow_status],
    )


def _seed_forecast(
    conn,
    person_name: str,
    week_start: date,
    alloc: float,
    client: str = "Acme",
    fc_type: str = "confirmed",
    planned_hours: float | None = None,
    target_hours: float | None = None,
) -> None:
    row_id = content_hash(f"{person_name}::{week_start}::{client}::proj")
    conn.execute(
        """
        INSERT INTO staffing_forecast
            (id, person_name, week_start, client, project, allocation_pct,
             forecast_type, ingested_at, planned_hours, target_hours)
        VALUES (?, ?, ?, ?, 'proj', ?, ?, CURRENT_TIMESTAMP, ?, ?)
        """,
        [row_id, person_name, week_start.isoformat(), client, alloc, fc_type,
         planned_hours, target_hours],
    )


# ==================================================================
# Issue #14 — People tab
# ==================================================================


def test_get_people_rows_from_signals(conn) -> None:
    _seed_signal(conn, "Alice Chen", "person", severity="high")
    _seed_signal(conn, "Bob Martinez", "person", severity="medium")
    rows = get_people_rows(conn)
    names = [r.name for r in rows]
    assert "Alice Chen" in names
    assert "Bob Martinez" in names


def test_get_people_rows_from_1on1_notes(conn) -> None:
    _seed_note(conn, "Carmen Liu", note_type="1on1")
    rows = get_people_rows(conn)
    names = [r.name for r in rows]
    assert "Carmen Liu" in names


def test_get_people_rows_signal_count(conn) -> None:
    _seed_signal(conn, "Alice Chen", "person", severity="high")
    _seed_signal(conn, "Alice Chen", "person", signal_type="people_health", severity="medium")
    rows = get_people_rows(conn)
    alice = next(r for r in rows if r.name == "Alice Chen")
    assert alice.open_signal_count == 2


def test_get_people_rows_highest_severity(conn) -> None:
    _seed_signal(conn, "Alice Chen", "person", severity="medium")
    _seed_signal(conn, "Alice Chen", "person", signal_type="utilization_risk", severity="high")
    rows = get_people_rows(conn)
    alice = next(r for r in rows if r.name == "Alice Chen")
    assert alice.highest_severity == "high"


def test_get_people_rows_days_since_1on1(conn) -> None:
    old_date = date.today() - timedelta(days=20)
    _seed_note(conn, "Alice Chen", note_type="1on1", note_date=old_date)
    rows = get_people_rows(conn, as_of=date.today())
    alice = next(r for r in rows if r.name == "Alice Chen")
    assert alice.days_since_1on1 == 20


def test_get_people_rows_no_signals_still_appears(conn) -> None:
    _seed_note(conn, "David Park", note_type="1on1")
    rows = get_people_rows(conn)
    david = next((r for r in rows if r.name == "David Park"), None)
    assert david is not None
    assert david.open_signal_count == 0
    assert david.highest_severity is None


def test_get_people_rows_morale_from_signal_severity(conn) -> None:
    _seed_signal(conn, "Elena Torres", "person", severity="critical")
    rows = get_people_rows(conn)
    elena = next(r for r in rows if r.name == "Elena Torres")
    assert elena.morale == "red"


def test_get_people_rows_allocation_from_forecast(conn) -> None:
    _seed_note(conn, "Alice Chen", note_type="1on1")
    _seed_forecast(conn, "Alice Chen", date.today(), 80.0)
    rows = get_people_rows(conn, as_of=date.today())
    alice = next(r for r in rows if r.name == "Alice Chen")
    assert alice.allocation_pct == 80.0


def test_get_people_rows_empty_db(conn) -> None:
    rows = get_people_rows(conn)
    assert rows == []


# ==================================================================
# Issue #15 — Clients tab
# ==================================================================


def test_get_client_rows_from_signals(conn) -> None:
    _seed_signal(conn, "Acme Corp", "client", severity="high")
    rows = get_client_rows(conn)
    names = [r["name"] for r in rows]
    assert "Acme Corp" in names


def test_get_client_rows_health_red_for_critical(conn) -> None:
    _seed_signal(conn, "Acme Corp", "client", severity="critical")
    rows = get_client_rows(conn)
    acme = next(r for r in rows if r["name"] == "Acme Corp")
    assert acme["health"] == "red"


def test_get_client_rows_health_yellow_for_high(conn) -> None:
    _seed_signal(conn, "Big Retail Co", "client", severity="high")
    rows = get_client_rows(conn)
    brc = next(r for r in rows if r["name"] == "Big Retail Co")
    assert brc["health"] == "yellow"


def test_get_client_rows_health_green_for_no_signals(conn) -> None:
    # Add client via notes but no signals
    _seed_note(conn, "FinServ Partners", entity_type="client", note_type="client")
    rows = get_client_rows(conn)
    fs = next((r for r in rows if r["name"] == "FinServ Partners"), None)
    assert fs is not None
    assert fs["health"] == "green"


def test_get_client_rows_sorted_red_first(conn) -> None:
    _seed_signal(conn, "Green Client", "client", severity="low")
    _seed_signal(conn, "Red Client", "client", severity="critical")
    _seed_signal(conn, "Yellow Client", "client", severity="high")
    rows = get_client_rows(conn)
    healths = [r["health"] for r in rows]
    red_idx = healths.index("red")
    yellow_idx = healths.index("yellow")
    green_idx = healths.index("green")
    assert red_idx < yellow_idx < green_idx


def test_get_client_rows_last_update_date(conn) -> None:
    nd = date.today() - timedelta(days=5)
    _seed_note(conn, "Acme Corp", entity_type="client", note_type="client", note_date=nd)
    rows = get_client_rows(conn)
    acme = next(r for r in rows if r["name"] == "Acme Corp")
    assert acme["last_update_date"] == nd


def test_get_client_rows_empty_db(conn) -> None:
    rows = get_client_rows(conn)
    assert rows == []


# ==================================================================
# Issue #16 — Deals tab
# ==================================================================


def test_get_deal_rows_basic(conn) -> None:
    _seed_deal(conn, "Big Deal", close_date=date.today() + timedelta(days=5))
    rows = get_deal_rows(conn)
    assert len(rows) == 1
    assert rows[0].deal_name == "Big Deal"


def test_get_deal_rows_days_to_close(conn) -> None:
    close = date.today() + timedelta(days=3)
    _seed_deal(conn, "Closing Soon", close_date=close)
    rows = get_deal_rows(conn, as_of=date.today())
    row = rows[0]
    assert row.days_to_close == 3


def test_get_deal_rows_signal_count(conn) -> None:
    _seed_deal(conn, "My Deal")
    _seed_signal(conn, "My Deal", "deal", severity="high")
    rows = get_deal_rows(conn)
    row = rows[0]
    assert row.open_signal_count == 1
    assert row.highest_severity == "high"


def test_get_deal_rows_no_signals(conn) -> None:
    _seed_deal(conn, "Clean Deal")
    rows = get_deal_rows(conn)
    assert rows[0].open_signal_count == 0
    assert rows[0].highest_severity is None


def test_get_deal_rows_sorted_by_close_date(conn) -> None:
    _seed_deal(conn, "Far Deal", close_date=date.today() + timedelta(days=30))
    _seed_deal(conn, "Near Deal", close_date=date.today() + timedelta(days=2))
    rows = get_deal_rows(conn)
    assert rows[0].deal_name == "Near Deal"


def test_get_deal_rows_empty_db(conn) -> None:
    rows = get_deal_rows(conn)
    assert rows == []


# ==================================================================
# Issue #17 — Forecast tab
# ==================================================================


def test_get_forecast_rows_basic(conn) -> None:
    _seed_forecast(conn, "Alice Chen", date.today(), 80.0)
    rows = get_forecast_rows(conn, as_of=date.today())
    assert len(rows) == 1
    assert rows[0].person_name == "Alice Chen"
    assert rows[0].allocation_pct == 80.0


def test_get_forecast_rows_overallocated_flag(conn) -> None:
    _seed_forecast(conn, "David Park", date.today(), 120.0)
    rows = get_forecast_rows(conn, as_of=date.today())
    row = rows[0]
    assert row.is_overallocated is True
    assert row.is_underallocated is False


def test_get_forecast_rows_underallocated_flag(conn) -> None:
    _seed_forecast(conn, "Alice Chen", date.today(), 40.0)
    rows = get_forecast_rows(conn, as_of=date.today())
    assert rows[0].is_underallocated is True
    assert rows[0].is_overallocated is False


def test_get_forecast_rows_excludes_beyond_60_days(conn) -> None:
    far = date.today() + timedelta(days=70)
    _seed_forecast(conn, "Alice Chen", far, 100.0)
    rows = get_forecast_rows(conn, as_of=date.today())
    assert rows == []


def test_get_forecast_summary_buckets(conn) -> None:
    today = date.today()
    _seed_forecast(conn, "Alice Chen", today, 100.0)
    _seed_forecast(conn, "David Park", today, 120.0)
    _seed_forecast(conn, "Bob Martinez", today, 40.0)
    summary = get_forecast_summary(conn, as_of=today)
    assert "2w" in summary
    assert "30d" in summary
    assert "60d" in summary
    assert "David Park" in summary["2w"]["overallocated"]
    assert "Bob Martinez" in summary["2w"]["underallocated"]


def test_get_forecast_summary_multi_week_at_100_no_overallocated(conn) -> None:
    """A person at 100% for two weeks must not appear 200% overallocated."""
    today = date.today()
    next_week = today + timedelta(days=7)
    _seed_forecast(conn, "Alice Chen", today, 100.0)
    _seed_forecast(conn, "Alice Chen", next_week, 100.0)
    summary = get_forecast_summary(conn, as_of=today)
    assert "Alice Chen" not in summary["2w"]["overallocated"], (
        "Person at 100%/week should NOT be overallocated, even across multiple weeks"
    )
    assert "Alice Chen" not in summary["2w"]["underallocated"]


def test_get_forecast_summary_one_over_one_ok_is_overallocated(conn) -> None:
    """One overallocated week makes the person overallocated."""
    today = date.today()
    next_week = today + timedelta(days=7)
    _seed_forecast(conn, "David Park", today, 120.0)
    _seed_forecast(conn, "David Park", next_week, 100.0)
    summary = get_forecast_summary(conn, as_of=today)
    assert "David Park" in summary["2w"]["overallocated"]


def test_get_forecast_summary_one_under_one_ok_is_underallocated(conn) -> None:
    """One underallocated week makes the person underallocated."""
    today = date.today()
    next_week = today + timedelta(days=7)
    _seed_forecast(conn, "Bob Martinez", today, 50.0)
    _seed_forecast(conn, "Bob Martinez", next_week, 100.0)
    summary = get_forecast_summary(conn, as_of=today)
    assert "Bob Martinez" in summary["2w"]["underallocated"]


def test_get_forecast_summary_exactly_100_is_available(conn) -> None:
    """Person at exactly 100% should be 'available' (fully allocated), not under/over."""
    today = date.today()
    _seed_forecast(conn, "Alice Chen", today, 100.0)
    summary = get_forecast_summary(conn, as_of=today)
    assert "Alice Chen" not in summary["2w"]["overallocated"]
    assert "Alice Chen" not in summary["2w"]["underallocated"]
    assert "Alice Chen" in summary["2w"]["available"]


def test_get_today_signals_min_severity_filter(conn) -> None:
    _seed_signal(conn, "Acme", "client", severity="low")
    _seed_signal(conn, "FinServ", "client", severity="high")
    sigs = get_today_signals(conn, min_severity="medium")
    severities = {s.severity for s in sigs}
    assert "low" not in severities
    assert "high" in severities


# ==================================================================
# Forecast allocation math fixes
# ==================================================================


class TestPerWeekAllocation:
    """Allocation must be per-week, not summed across all weeks."""

    def test_allocation_is_for_current_week_only(self, conn) -> None:
        """get_people_rows must not sum across multiple forecast weeks."""
        today = date.today()
        next_week = today + timedelta(days=7)
        # Seed Alice on two different weeks
        _seed_forecast(conn, "Alice Chen", today,      80.0, fc_type="confirmed")
        _seed_forecast(conn, "Alice Chen", next_week, 100.0, fc_type="confirmed")
        _seed_note(conn, "Alice Chen", note_type="1on1")

        rows = get_people_rows(conn, as_of=today)
        alice = next(r for r in rows if r.name == "Alice Chen")
        # Must show current week's 80%, NOT 80+100=180%
        assert alice.allocation_pct == 80.0, (
            f"Expected 80% (current week only), got {alice.allocation_pct}"
        )

    def test_allocation_uses_nearest_future_week(self, conn) -> None:
        """When as_of falls between weeks, use the nearest week on or after as_of."""
        today = date.today()
        future_week = today + timedelta(days=3)
        _seed_forecast(conn, "Bob Martinez", future_week, 60.0)
        _seed_note(conn, "Bob Martinez", note_type="1on1")

        rows = get_people_rows(conn, as_of=today)
        bob = next(r for r in rows if r.name == "Bob Martinez")
        assert bob.allocation_pct == 60.0

    def test_allocation_zero_weeks_shows_zero(self, conn) -> None:
        """Person with no forecast rows has 0% allocation."""
        _seed_note(conn, "Carmen Liu", note_type="1on1")
        rows = get_people_rows(conn, as_of=date.today())
        carmen = next((r for r in rows if r.name == "Carmen Liu"), None)
        assert carmen is not None
        assert carmen.allocation_pct == 0.0


class TestHoursBasedAllocation:
    """planned_hours / target_hours must yield the correct percentage."""

    def test_planned_over_target_gives_correct_pct(self, conn) -> None:
        today = date.today()
        alloc_rows = get_people_allocation_for_week(conn, week_start=today)
        # Empty DB → empty list
        assert alloc_rows == []

    def test_full_week_40_of_40_is_100_pct(self, conn) -> None:
        today = date.today()
        _seed_forecast(conn, "Alex Rivera", today, alloc=100.0,
                       fc_type="capacity",
                       planned_hours=40.0, target_hours=40.0)
        rows = get_people_allocation_for_week(conn, week_start=today)
        assert len(rows) == 1
        assert rows[0]["allocation_pct"] == 100.0
        assert rows[0]["planned_hours"] == 40.0
        assert rows[0]["target_hours"] == 40.0
        assert rows[0]["warning"] is None

    def test_partial_week_24_of_40_is_60_pct(self, conn) -> None:
        today = date.today()
        _seed_forecast(conn, "Jordan Lee", today, alloc=60.0,
                       fc_type="capacity",
                       planned_hours=24.0, target_hours=40.0)
        rows = get_people_allocation_for_week(conn, week_start=today)
        assert rows[0]["allocation_pct"] == 60.0

    def test_overallocation_warning_above_150(self, conn) -> None:
        today = date.today()
        _seed_forecast(conn, "Sam Chen", today, alloc=160.0,
                       fc_type="capacity",
                       planned_hours=64.0, target_hours=40.0)
        rows = get_people_allocation_for_week(conn, week_start=today)
        assert rows[0]["warning"] is not None
        assert "dangerously" in rows[0]["warning"] or "160" in rows[0]["warning"]

    def test_overallocation_warning_above_100(self, conn) -> None:
        today = date.today()
        _seed_forecast(conn, "Dana Kim", today, alloc=120.0,
                       fc_type="capacity",
                       planned_hours=48.0, target_hours=40.0)
        rows = get_people_allocation_for_week(conn, week_start=today)
        assert rows[0]["warning"] is not None

    def test_zero_target_shows_no_capacity_warning(self, conn) -> None:
        today = date.today()
        _seed_forecast(conn, "Lee Park", today, alloc=0.0,
                       fc_type="capacity",
                       planned_hours=32.0, target_hours=0.0)
        rows = get_people_allocation_for_week(conn, week_start=today)
        assert rows[0]["warning"] == "no capacity target"

    def test_blank_planned_hours_counts_as_zero(self, conn) -> None:
        today = date.today()
        _seed_forecast(conn, "Alex Zero", today, alloc=0.0,
                       fc_type="capacity",
                       planned_hours=0.0, target_hours=40.0)
        rows = get_people_allocation_for_week(conn, week_start=today)
        assert rows[0]["planned_hours"] == 0.0
        assert rows[0]["allocation_pct"] == 0.0


class TestPipelineRowsExcluded:
    """Pipeline/candidate demand rows must NOT count as engineer allocation."""

    def _seed_pipeline(self, conn, week_start: date) -> None:
        row_id = content_hash(f"pipeline::Alpha Inc::{week_start}")
        conn.execute(
            """
            INSERT INTO forecast_pipeline_demand
                (id, source_section, week_start, prospect_or_deal, probability,
                 requested_allocation, skillset, demand_hours, candidate_people,
                 staffing_status, record_type, forecast_type, source_row,
                 ingested_at)
            VALUES (?, 'AI', ?, 'Alpha Inc', 0.8, 20, 'ML', 20, '["Alex/Jordan"]',
                    'unassigned', 'pipeline_demand', 'pipeline_demand', 1,
                    CURRENT_TIMESTAMP)
            """,
            [row_id, week_start.isoformat()],
        )

    def test_pipeline_demand_not_in_person_alloc(self, conn) -> None:
        today = date.today()
        self._seed_pipeline(conn, today)
        rows = get_people_allocation_for_week(conn, week_start=today)
        # Pipeline rows should NOT produce person allocation entries
        person_names = {r["person_name"] for r in rows}
        assert "Alpha Inc" not in person_names
        assert "Alex/Jordan" not in person_names

    def test_pipeline_rows_dont_inflate_people_tab(self, conn) -> None:
        today = date.today()
        self._seed_pipeline(conn, today)
        _seed_forecast(conn, "Real Engineer", today, alloc=80.0)
        _seed_note(conn, "Real Engineer", note_type="1on1")
        rows = get_people_rows(conn, as_of=today)
        real = next((r for r in rows if r.name == "Real Engineer"), None)
        assert real is not None
        assert real.allocation_pct == 80.0  # must not be inflated by pipeline


class TestForecastWeekList:
    """get_forecast_week_list returns correct sorted distinct weeks."""

    def test_returns_weeks_on_or_after_as_of(self, conn) -> None:
        today = date.today()
        past = today - timedelta(days=7)
        _seed_forecast(conn, "Alice", past, 80.0)
        _seed_forecast(conn, "Alice", today, 90.0)
        weeks = get_forecast_week_list(conn, as_of=today)
        assert today in weeks
        assert past not in weeks

    def test_weeks_sorted_ascending(self, conn) -> None:
        today = date.today()
        w2 = today + timedelta(days=7)
        w3 = today + timedelta(days=14)
        _seed_forecast(conn, "Alice", w3, 80.0)
        _seed_forecast(conn, "Alice", today, 80.0)
        _seed_forecast(conn, "Alice", w2, 80.0)
        weeks = get_forecast_week_list(conn, as_of=today)
        assert weeks == sorted(weeks)


class TestForecastSummaryTotalsMatch:
    """Dashboard totals must match what the forecast has for that week."""

    def test_per_week_totals_consistent_with_fixture(self, conn) -> None:
        """Ingest the wide fixture and verify dashboard allocation is per-week."""
        import os
        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "wide_forecast.csv"
        )
        from manager_os.ingest.forecast import ingest_forecast
        ingest_forecast(fixture, conn)

        # Alex Rivera: week of 2026-06-16 has planned=40, target=40 → 100%
        rows = get_people_allocation_for_week(
            conn, week_start=date(2026, 6, 16)
        )
        alex = next((r for r in rows if r["person_name"] == "Alex Rivera"), None)
        assert alex is not None, "Alex Rivera not found in fixture allocation"
        assert alex["target_hours"] == 40.0
        assert alex["planned_hours"] == 40.0
        assert alex["allocation_pct"] == 100.0

        # Alex Rivera: week of 2026-06-30 has planned=0, target=40 → 0%
        rows_june30 = get_people_allocation_for_week(
            conn, week_start=date(2026, 6, 30)
        )
        alex_june30 = next(
            (r for r in rows_june30 if r["person_name"] == "Alex Rivera"), None
        )
        assert alex_june30 is not None
        assert alex_june30["planned_hours"] == 0.0
        assert alex_june30["allocation_pct"] == 0.0

    def test_no_ai_ml_section_double_counting(self, conn) -> None:
        """Engineers appearing in both AI and ML sections are counted once per week."""
        today = date.today()
        # Two rows for same person in same week (different sections/clients) — should sum
        row_id1 = content_hash(f"double::Alice::{today}::AI")
        row_id2 = content_hash(f"double::Alice::{today}::ML")
        for rid, section, ph, th in [
            (row_id1, "AI", 20.0, 40.0),
            (row_id2, "ML", 20.0, 40.0),
        ]:
            conn.execute(
                """
                INSERT INTO staffing_forecast
                    (id, person_name, week_start, client, project, allocation_pct,
                     forecast_type, ingested_at, planned_hours, target_hours)
                VALUES (?, 'Alice', ?, '', ?, 50.0, 'capacity', CURRENT_TIMESTAMP, ?, ?)
                """,
                [rid, today.isoformat(), section, ph, th],
            )
        rows = get_people_allocation_for_week(conn, week_start=today)
        alice = next((r for r in rows if r["person_name"] == "Alice"), None)
        assert alice is not None
        # 20+20=40 planned / MAX(target)=40 → 100% (sum planned, max target)
        assert alice["planned_hours"] == 40.0
        assert alice["allocation_pct"] == 100.0
