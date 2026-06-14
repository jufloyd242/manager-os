"""Tests for the deals CSV ingestor."""

from __future__ import annotations

from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.deals import ingest_deals

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def test_ingest_deals_success(conn) -> None:
    result = ingest_deals(str(FIXTURES / "deals.csv"), conn)
    assert result.ingested == 5
    assert result.failed == 0
    count = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert count == 5


def test_ingest_deals_idempotent(conn) -> None:
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    result2 = ingest_deals(str(FIXTURES / "deals.csv"), conn)
    assert result2.ingested == 0
    assert result2.skipped == 5
    assert conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 5


def test_ingest_deals_force(conn) -> None:
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    result2 = ingest_deals(str(FIXTURES / "deals.csv"), conn, force=True)
    assert result2.ingested == 5
    assert conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 5


def test_ingest_deals_missing_account_column(tmp_path: Path, conn) -> None:
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("deal_name,stage\nSome Deal,Proposal\n")
    with pytest.raises(ValueError, match="account"):
        ingest_deals(str(bad_csv), conn)


def test_ingest_deals_missing_deal_name_column(tmp_path: Path, conn) -> None:
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("account,stage\nAcme,Proposal\n")
    with pytest.raises(ValueError, match="deal_name"):
        ingest_deals(str(bad_csv), conn)


def test_ingest_deals_malformed_close_date(tmp_path: Path, conn) -> None:
    csv = tmp_path / "partial_bad.csv"
    csv.write_text(
        "account,deal_name,stage,close_date,loe_status,sow_status\n"
        "Acme Corp,Good Deal,Proposal,2026-06-20,signed,pending\n"
        "Bad Corp,Bad Deal,Proposal,NOT-A-DATE,not-started,not-started\n"
    )
    result = ingest_deals(str(csv), conn)
    assert result.ingested == 1
    assert result.failed == 1


def test_ingest_deals_stores_sow_status(conn) -> None:
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    row = conn.execute(
        "SELECT sow_status FROM deals WHERE deal_name = 'Big Retail Recs v2'"
    ).fetchone()
    assert row is not None
    assert row[0] == "pending"


def test_ingest_deals_close_date_parsed(conn) -> None:
    from datetime import date
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    row = conn.execute(
        "SELECT close_date FROM deals WHERE deal_name = 'Big Retail Recs v2'"
    ).fetchone()
    assert row is not None
    assert str(row[0]) == "2026-06-17"
