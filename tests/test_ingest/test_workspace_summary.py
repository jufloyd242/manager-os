"""Tests for the workspace summary ingestor."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.workspace_summary import ingest_summary

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def test_ingest_summary_success(conn) -> None:
    result = ingest_summary(
        str(FIXTURES / "summaries"),
        date(2026, 6, 13),
        conn,
    )
    assert result.ingested == 1
    assert result.failed == 0
    count = conn.execute(
        "SELECT COUNT(*) FROM raw_documents WHERE source_type = 'workspace_summary'"
    ).fetchone()[0]
    assert count == 1


def test_ingest_summary_idempotent(conn) -> None:
    ingest_summary(str(FIXTURES / "summaries"), date(2026, 6, 13), conn)
    result2 = ingest_summary(str(FIXTURES / "summaries"), date(2026, 6, 13), conn)
    assert result2.ingested == 0
    assert result2.skipped == 1
    assert conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0] == 1


def test_ingest_summary_force(conn) -> None:
    ingest_summary(str(FIXTURES / "summaries"), date(2026, 6, 13), conn)
    result2 = ingest_summary(str(FIXTURES / "summaries"), date(2026, 6, 13), conn, force=True)
    assert result2.ingested == 1
    assert conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0] == 1


def test_ingest_summary_missing_date_returns_zero(conn) -> None:
    """Missing file for a date is not an error — returns empty result."""
    result = ingest_summary(str(FIXTURES / "summaries"), date(2000, 1, 1), conn)
    assert result.ingested == 0
    assert result.failed == 0
    assert result.skipped == 0


def test_ingest_summary_missing_dir_returns_zero(conn) -> None:
    result = ingest_summary("/nonexistent/dir", date(2026, 6, 13), conn)
    assert result.ingested == 0
    assert result.failed == 0


def test_ingest_summary_stores_content(conn) -> None:
    ingest_summary(str(FIXTURES / "summaries"), date(2026, 6, 13), conn)
    row = conn.execute(
        "SELECT content, metadata FROM raw_documents WHERE source_type = 'workspace_summary'"
    ).fetchone()
    assert row is not None
    assert "Acme" in row[0]  # fixture mentions Acme
