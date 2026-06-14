"""Tests for the forecast CSV ingestor."""

from __future__ import annotations

from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.forecast import ingest_forecast

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def test_ingest_forecast_success(conn) -> None:
    result = ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    assert result.ingested == 9
    assert result.failed == 0
    count = conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0]
    assert count == 9


def test_ingest_forecast_idempotent(conn) -> None:
    ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    result2 = ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    assert result2.ingested == 0
    assert result2.skipped == 9
    assert conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0] == 9


def test_ingest_forecast_force(conn) -> None:
    ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    result2 = ingest_forecast(str(FIXTURES / "forecast.csv"), conn, force=True)
    assert result2.ingested == 9
    assert conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0] == 9


def test_ingest_forecast_missing_person_column(tmp_path: Path, conn) -> None:
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("week_start,client,allocation_pct\n2026-06-16,Acme,100\n")
    with pytest.raises(ValueError, match="person"):
        ingest_forecast(str(bad_csv), conn)


def test_ingest_forecast_missing_week_column(tmp_path: Path, conn) -> None:
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("person,client,allocation_pct\nAlice,Acme,100\n")
    with pytest.raises(ValueError, match="week_start"):
        ingest_forecast(str(bad_csv), conn)


def test_ingest_forecast_malformed_date_row(tmp_path: Path, conn) -> None:
    csv = tmp_path / "partial_bad.csv"
    csv.write_text(
        "person,week_start,client,allocation_pct,forecast_type\n"
        "Alice Chen,2026-06-16,Acme,100,confirmed\n"
        "Bob Martinez,NOT-A-DATE,Acme,80,confirmed\n"
    )
    result = ingest_forecast(str(csv), conn)
    assert result.ingested == 1
    assert result.failed == 1


def test_ingest_forecast_allocation_as_percentage_string(tmp_path: Path, conn) -> None:
    csv = tmp_path / "pct.csv"
    csv.write_text(
        "person,week_start,client,allocation_pct,forecast_type\n"
        "Alice Chen,2026-06-16,Acme,80%,confirmed\n"
    )
    result = ingest_forecast(str(csv), conn)
    assert result.ingested == 1
    row = conn.execute("SELECT allocation_pct FROM staffing_forecast").fetchone()
    assert row[0] == 80.0
