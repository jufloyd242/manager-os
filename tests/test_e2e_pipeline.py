"""End-to-end pipeline integration test (Issue #27).

Runs the full ingest → extract → brief → closeout pipeline using fixture data
and verifies the system produces meaningful output at each stage.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.obsidian import ingest_vault
from manager_os.ingest.forecast import ingest_forecast
from manager_os.ingest.deals import ingest_deals
from manager_os.ingest.workspace_summary import ingest_summary
from manager_os.ingest.gws_client import ingest_gws_snapshots
from manager_os.extract.entities import EntityResolver
from manager_os.extract.signals import run_rule_extraction
from manager_os.extract.action_items import extract_action_items_from_all_notes
from manager_os.extract.decisions import extract_decisions_from_all_notes
from manager_os.extract.meeting_prep import generate_meeting_prep
from manager_os.build.daily_brief import generate_daily_brief
from manager_os.build.closeout import generate_closeout
from manager_os.build.dashboard_data import (
    get_today_signals,
    get_open_action_items,
    get_people_rows,
    get_client_rows,
    get_deal_rows,
    get_forecast_rows,
    get_forecast_summary,
    update_signal_status,
    get_signal_status_history,
)
from manager_os.schemas import MeetingRecord

FIXTURES = Path(__file__).parent / "fixtures"
_TARGET_DATE = date(2026, 6, 13)


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "vault"
    shutil.copytree(FIXTURES / "vault", dest)
    return dest


# ==================================================================
# Full pipeline fixture
# ==================================================================


@pytest.fixture()
def seeded_conn(conn, vault_dir: Path):
    """Run the full ingest + extract pipeline once and return the conn."""
    # Ingest
    ingest_vault(str(vault_dir), conn)
    ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    ingest_summary(str(FIXTURES / "summaries"), _TARGET_DATE, conn)
    ingest_gws_snapshots(FIXTURES / "gws_snapshots", conn, target_date=_TARGET_DATE)

    # Extract
    run_rule_extraction(conn, run_date=_TARGET_DATE)
    extract_action_items_from_all_notes(conn)
    extract_decisions_from_all_notes(conn)

    return conn


# ==================================================================
# Ingest layer
# ==================================================================


def test_e2e_ingest_raw_documents(seeded_conn) -> None:
    count = seeded_conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
    # 3 vault + 1 summary + 3 calendar events (with descriptions) + 2 gmail threads + 2 chat spaces
    assert count >= 8


def test_e2e_ingest_meetings_from_calendar(seeded_conn) -> None:
    count = seeded_conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    assert count == 3  # 3 events in fixture


def test_e2e_ingest_notes(seeded_conn) -> None:
    count = seeded_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert count == 3


def test_e2e_ingest_forecast(seeded_conn) -> None:
    count = seeded_conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0]
    assert count == 9


def test_e2e_ingest_deals(seeded_conn) -> None:
    count = seeded_conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert count == 5


# ==================================================================
# Extract layer
# ==================================================================


def test_e2e_signals_generated(seeded_conn) -> None:
    count = seeded_conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count > 0


def test_e2e_risk_signal_from_note(seeded_conn) -> None:
    row = seeded_conn.execute(
        "SELECT signal_type FROM signals WHERE signal_type = 'risk' LIMIT 1"
    ).fetchone()
    assert row is not None


def test_e2e_utilization_signal_for_david(seeded_conn) -> None:
    """David Park at 120% should trigger a utilization_risk signal."""
    row = seeded_conn.execute(
        "SELECT entity_name FROM signals WHERE signal_type = 'utilization_risk'"
    ).fetchone()
    assert row is not None
    assert "David" in row[0]


def test_e2e_sow_signal_for_big_retail(seeded_conn) -> None:
    """Big Retail with close date within 7 days + SOW pending should produce sow_loe_review."""
    row = seeded_conn.execute(
        "SELECT entity_name FROM signals WHERE signal_type = 'sow_loe_review'"
    ).fetchone()
    assert row is not None


def test_e2e_action_items_extracted(seeded_conn) -> None:
    count = seeded_conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
    assert count > 0


def test_e2e_decisions_extracted_or_zero(seeded_conn) -> None:
    # No decisions in fixture notes — just verify no error, count >= 0
    count = seeded_conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert count >= 0


# ==================================================================
# Daily brief
# ==================================================================


def test_e2e_daily_brief_generates(seeded_conn, tmp_path: Path) -> None:
    brief = generate_daily_brief(seeded_conn, target_date=_TARGET_DATE)
    assert brief.content
    assert brief.signal_ids


def test_e2e_daily_brief_contains_risk_section(seeded_conn) -> None:
    brief = generate_daily_brief(seeded_conn, target_date=_TARGET_DATE)
    # Brief should mention at least one entity
    assert any(name in brief.content for name in ("David", "Alice", "Acme", "Big Retail"))


def test_e2e_daily_brief_written_to_db(seeded_conn) -> None:
    generate_daily_brief(seeded_conn, target_date=_TARGET_DATE)
    count = seeded_conn.execute("SELECT COUNT(*) FROM daily_briefs").fetchone()[0]
    assert count == 1


# ==================================================================
# Closeout
# ==================================================================


def test_e2e_closeout_generates(seeded_conn) -> None:
    result = generate_closeout(seeded_conn, target_date=_TARGET_DATE)
    assert "EOD Closeout" in result.content
    assert result.stats.still_open > 0


def test_e2e_closeout_weekly_on_request(seeded_conn) -> None:
    result = generate_closeout(seeded_conn, target_date=_TARGET_DATE, include_weekly=True)
    assert result.weekly_exec_content is not None
    assert "Weekly Exec Update" in result.weekly_exec_content


# ==================================================================
# Dashboard queries
# ==================================================================


def test_e2e_get_today_signals_returns_results(seeded_conn) -> None:
    sigs = get_today_signals(seeded_conn, min_severity="low")
    assert len(sigs) > 0


def test_e2e_get_open_action_items(seeded_conn) -> None:
    items = get_open_action_items(seeded_conn)
    assert isinstance(items, list)


def test_e2e_get_people_rows(seeded_conn) -> None:
    rows = get_people_rows(seeded_conn, as_of=_TARGET_DATE)
    assert len(rows) > 0


def test_e2e_get_client_rows(seeded_conn) -> None:
    rows = get_client_rows(seeded_conn, as_of=_TARGET_DATE)
    assert isinstance(rows, list)


def test_e2e_get_deal_rows(seeded_conn) -> None:
    rows = get_deal_rows(seeded_conn, as_of=_TARGET_DATE)
    assert len(rows) == 5


def test_e2e_get_forecast_rows(seeded_conn) -> None:
    rows = get_forecast_rows(seeded_conn, as_of=_TARGET_DATE)
    assert len(rows) > 0


def test_e2e_forecast_summary_has_buckets(seeded_conn) -> None:
    summary = get_forecast_summary(seeded_conn, as_of=_TARGET_DATE)
    assert "2w" in summary and "30d" in summary and "60d" in summary


# ==================================================================
# Signal audit trail (end-to-end)
# ==================================================================


def test_e2e_signal_status_audit(seeded_conn) -> None:
    sigs = get_today_signals(seeded_conn, min_severity="low")
    assert sigs
    sig = sigs[0]
    update_signal_status(seeded_conn, sig.id, "acknowledged", changed_by="e2e_test")
    history = get_signal_status_history(seeded_conn, sig.id)
    assert len(history) == 1
    assert history[0]["new_status"] == "acknowledged"
    assert history[0]["changed_by"] == "e2e_test"


# ==================================================================
# Meeting prep
# ==================================================================


def test_e2e_meeting_prep_from_calendar(seeded_conn) -> None:
    """Meetings ingested from GWS calendar should produce prep documents."""
    row = seeded_conn.execute(
        "SELECT id, meeting_date, start_time, title, attendees, linked_entities, source, external_id "
        "FROM meetings LIMIT 1"
    ).fetchone()
    import json
    mtg = MeetingRecord(
        id=row[0],
        meeting_date=row[1],
        start_time=row[2] or "",
        title=row[3],
        attendees=json.loads(row[4]) if row[4] else [],
        linked_entities=json.loads(row[5]) if row[5] else [],
        source=row[6] or "",
        external_id=row[7] or "",
    )
    prep = generate_meeting_prep(mtg, seeded_conn)
    assert prep.content
    assert mtg.title in prep.content
