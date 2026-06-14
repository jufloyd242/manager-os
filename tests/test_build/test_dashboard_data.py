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


def _seed_forecast(conn, person_name: str, week_start: date, alloc: float,
                   client: str = "Acme", fc_type: str = "confirmed") -> None:
    row_id = content_hash(f"{person_name}::{week_start}::{client}::proj")
    conn.execute(
        """
        INSERT INTO staffing_forecast
            (id, person_name, week_start, client, project, allocation_pct, forecast_type, ingested_at)
        VALUES (?, ?, ?, ?, 'proj', ?, ?, CURRENT_TIMESTAMP)
        """,
        [row_id, person_name, week_start.isoformat(), client, alloc, fc_type],
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
    _seed_forecast(conn, "Alice Chen", today, 80.0)
    _seed_forecast(conn, "David Park", today, 120.0)
    _seed_forecast(conn, "Bob Martinez", today, 40.0)
    summary = get_forecast_summary(conn, as_of=today)
    assert "2w" in summary
    assert "30d" in summary
    assert "60d" in summary
    assert "David Park" in summary["2w"]["overallocated"]
    assert "Bob Martinez" in summary["2w"]["underallocated"]


def test_get_forecast_summary_empty_db(conn) -> None:
    summary = get_forecast_summary(conn)
    for bucket in ("2w", "30d", "60d"):
        assert summary[bucket]["overallocated"] == []
        assert summary[bucket]["available"] == []


def test_get_today_signals_min_severity_filter(conn) -> None:
    _seed_signal(conn, "Acme", "client", severity="low")
    _seed_signal(conn, "FinServ", "client", severity="high")
    sigs = get_today_signals(conn, min_severity="medium")
    severities = {s.severity for s in sigs}
    assert "low" not in severities
    assert "high" in severities
