"""Integration test: full ingest pipeline on all fixtures."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.deals import ingest_deals
from manager_os.ingest.forecast import ingest_forecast
from manager_os.ingest.obsidian import ingest_vault
from manager_os.ingest.workspace_summary import ingest_summary

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "vault"
    shutil.copytree(FIXTURES / "vault", dest)
    return dest


def test_full_ingest_pipeline(conn, vault_dir: Path) -> None:
    """Ingest all sources and verify expected row counts."""
    r_vault = ingest_vault(str(vault_dir), conn)
    assert r_vault.ingested == 3, f"Expected 3 vault notes, got {r_vault.ingested}"

    r_forecast = ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    assert r_forecast.ingested == 9, f"Expected 9 forecast rows, got {r_forecast.ingested}"

    r_deals = ingest_deals(str(FIXTURES / "deals.csv"), conn)
    assert r_deals.ingested == 5, f"Expected 5 deal rows, got {r_deals.ingested}"

    r_summary = ingest_summary(str(FIXTURES / "summaries"), date(2026, 6, 13), conn)
    assert r_summary.ingested == 1, f"Expected 1 summary, got {r_summary.ingested}"

    # Verify raw_documents has 4 rows (3 vault + 1 summary; forecast/deals don't write raw_documents)
    raw_count = conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
    assert raw_count == 4

    # Verify notes has 3 rows (one per vault note)
    note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert note_count == 3

    # Verify staffing_forecast has 9 rows
    fc_count = conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0]
    assert fc_count == 9

    # Verify deals has 5 rows
    deal_count = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    assert deal_count == 5


def test_full_ingest_idempotent(conn, vault_dir: Path) -> None:
    """Re-running the full pipeline twice should not create duplicate records."""
    for _ in range(2):
        ingest_vault(str(vault_dir), conn)
        ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
        ingest_deals(str(FIXTURES / "deals.csv"), conn)
        ingest_summary(str(FIXTURES / "summaries"), date(2026, 6, 13), conn)

    assert conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 5
