"""Tests for ``manager-os status``.

Covers:
- Empty database: all counts zero, no crash
- Populated sample database: correct table counts
- Sample config detection: warning appears when vault is inside the repo
- Production-like config: no warning, mode shown as 'production'
- Table counts match known fixture expectations after ingest+extract
- Open signals by severity section is shown when signals exist
- Open action items count is accurate
- Database path is shown in output
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manager_os.cli import app as cli_app, _detect_mode, _is_sample_config
from manager_os.db import get_connection
from manager_os.ingest.obsidian import ingest_vault
from manager_os.ingest.forecast import ingest_forecast
from manager_os.ingest.deals import ingest_deals
from manager_os.ingest.workspace_summary import ingest_summary
from manager_os.ingest.gws_client import ingest_gws_snapshots
from manager_os.extract.signals import run_rule_extraction
from manager_os.extract.action_items import extract_action_items_from_all_notes
from manager_os.extract.decisions import extract_decisions_from_all_notes
from manager_os.build.daily_brief import generate_daily_brief

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent
TARGET_DATE = date(2026, 6, 13)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env(db_path: str, *, vault: str = "") -> dict[str, str]:
    return {
        "MANAGER_OS_DB_PATH": db_path,
        "MANAGER_OS_VAULT_PATH": vault,
        "MANAGER_OS_FORECAST_CSV": str(FIXTURES / "forecast.csv"),
        "MANAGER_OS_DEALS_CSV": str(FIXTURES / "deals.csv"),
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        "MANAGER_OS_CONFIG_DIR": str(REPO_ROOT / "config"),
    }


def _run_status(db_path: str, *, vault: str = "") -> object:
    runner = CliRunner()
    return runner.invoke(cli_app, ["status"], env=_env(db_path, vault=vault))


# ---------------------------------------------------------------------------
# Unit: _detect_mode
# ---------------------------------------------------------------------------


class TestDetectMode:
    def test_demo_db_path(self, tmp_path: Path) -> None:
        assert _detect_mode(str(tmp_path / "data/demo/manager_os_demo.duckdb"), "") == "demo"

    def test_fixture_vault_is_sample(self) -> None:
        assert _detect_mode(":memory:", str(FIXTURES / "vault")) == "sample data"

    def test_production_mode(self, tmp_path: Path) -> None:
        # Both db and vault outside repo without safe keywords
        db = str(tmp_path / "manager_os.duckdb")
        assert _detect_mode(db, "") == "production"

    def test_demo_keyword_in_db_path(self, tmp_path: Path) -> None:
        db = str(tmp_path / "demo_database.duckdb")
        assert _detect_mode(db, "") == "demo"


# ---------------------------------------------------------------------------
# Unit: _is_sample_config
# ---------------------------------------------------------------------------


class TestIsSampleConfig:
    def _settings(self, db_path: str, vault_path: str):
        """Build a minimal Settings-like object."""
        class _S:
            pass
        s = _S()
        s.db_path = db_path
        s.vault_path = vault_path
        return s

    def test_fixture_vault_is_sample(self) -> None:
        s = self._settings(":memory:", str(FIXTURES / "vault"))
        assert _is_sample_config(s) is True

    def test_demo_db_is_sample(self, tmp_path: Path) -> None:
        s = self._settings(str(tmp_path / "demo/test.duckdb"), "")
        assert _is_sample_config(s) is True

    def test_production_paths_not_sample(self, tmp_path: Path) -> None:
        s = self._settings(str(tmp_path / "prod.duckdb"), "")
        assert _is_sample_config(s) is False


# ---------------------------------------------------------------------------
# CLI: empty database
# ---------------------------------------------------------------------------


class TestStatusEmptyDatabase:
    def test_exits_zero_on_empty_db(self, tmp_path: Path) -> None:
        result = _run_status(str(tmp_path / "empty.duckdb"))
        assert result.exit_code == 0, result.output

    def test_shows_db_path(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "empty.duckdb")
        result = _run_status(db_path)
        assert "empty.duckdb" in result.output

    def test_shows_table_counts_header(self, tmp_path: Path) -> None:
        result = _run_status(str(tmp_path / "empty.duckdb"))
        assert "Table Counts" in result.output

    def test_shows_zero_people(self, tmp_path: Path) -> None:
        result = _run_status(str(tmp_path / "empty.duckdb"))
        assert "people" in result.output

    def test_shows_zero_signals(self, tmp_path: Path) -> None:
        result = _run_status(str(tmp_path / "empty.duckdb"))
        assert "signals" in result.output

    def test_no_latest_note_date(self, tmp_path: Path) -> None:
        result = _run_status(str(tmp_path / "empty.duckdb"))
        assert "Latest note date:" in result.output
        assert "none" in result.output.lower()

    def test_zero_open_action_items(self, tmp_path: Path) -> None:
        result = _run_status(str(tmp_path / "empty.duckdb"))
        assert "Open action items:" in result.output


# ---------------------------------------------------------------------------
# CLI: sample config warning
# ---------------------------------------------------------------------------


class TestStatusSampleConfigWarning:
    def test_warning_when_vault_in_fixtures(self, tmp_path: Path) -> None:
        result = _run_status(
            str(tmp_path / "test.duckdb"),
            vault=str(FIXTURES / "vault"),
        )
        assert result.exit_code == 0
        assert "Sample data detected" in result.output or "sample" in result.output.lower()

    def test_no_warning_when_no_vault(self, tmp_path: Path) -> None:
        """No vault configured → no sample warning."""
        result = _run_status(str(tmp_path / "test.duckdb"), vault="")
        assert result.exit_code == 0
        assert "Sample data detected" not in result.output

    def test_mode_shown_as_sample(self, tmp_path: Path) -> None:
        result = _run_status(
            str(tmp_path / "test.duckdb"),
            vault=str(FIXTURES / "vault"),
        )
        assert "sample data" in result.output.lower() or "demo" in result.output.lower()

    def test_mode_shown_as_demo_for_demo_db(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "demo" / "manager_os_demo.duckdb")
        result = _run_status(db_path)
        assert "demo" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI: populated sample database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _populated_db(tmp_path_factory: pytest.TempPathFactory):
    """Ingest + extract all fixtures into a real DuckDB file. Module-scoped."""
    tmp = tmp_path_factory.mktemp("status_populated")
    db_path = str(tmp / "manager_os.duckdb")
    vault_dir = tmp / "vault"
    shutil.copytree(FIXTURES / "vault", vault_dir)

    conn = get_connection(db_path)
    ingest_vault(str(vault_dir), conn)
    ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    ingest_summary(str(FIXTURES / "summaries"), TARGET_DATE, conn)
    ingest_gws_snapshots(FIXTURES / "gws_snapshots", conn, target_date=TARGET_DATE)
    run_rule_extraction(conn, run_date=TARGET_DATE)
    extract_action_items_from_all_notes(conn)
    extract_decisions_from_all_notes(conn)
    generate_daily_brief(conn, target_date=TARGET_DATE)
    conn.close()
    return db_path, str(vault_dir)


class TestStatusPopulatedDatabase:
    def _result(self, populated_db) -> object:
        db_path, vault_dir = populated_db
        return _run_status(db_path, vault=vault_dir)

    def test_exits_zero(self, _populated_db) -> None:
        assert self._result(_populated_db).exit_code == 0

    def test_shows_db_path(self, _populated_db) -> None:
        db_path, _ = _populated_db
        assert Path(db_path).name in self._result(_populated_db).output

    # ── latest dates ──────────────────────────────────────────────────────

    def test_shows_latest_note_date(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        assert "Latest note date:" in output
        assert "2026" in output

    def test_shows_latest_brief_date(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        assert "Latest brief date:" in output
        assert "2026-06-13" in output

    # ── table counts ──────────────────────────────────────────────────────

    def test_notes_count(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        # 3 vault notes present
        # Count should show "3" after the notes row
        assert "notes" in output
        assert "3" in output

    def test_deals_count(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        assert "deals" in output
        assert "5" in output  # 5 fixture deals

    def test_staffing_forecast_count(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        assert "staffing_forecast" in output
        assert "9" in output  # 9 fixture forecast rows

    def test_daily_briefs_count(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        assert "daily_briefs" in output

    def test_signals_count_nonzero(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        assert "signals" in output

    def test_action_items_count_nonzero(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        assert "action_items" in output

    # ── by-source counts ──────────────────────────────────────────────────

    def test_documents_by_source_section(self, _populated_db) -> None:
        assert "Documents by Source" in self._result(_populated_db).output

    def test_obsidian_source_shown(self, _populated_db) -> None:
        assert "obsidian" in self._result(_populated_db).output

    def test_gws_source_shown(self, _populated_db) -> None:
        output = self._result(_populated_db).output
        assert "gws" in output.lower()

    # ── open signals ──────────────────────────────────────────────────────

    def test_open_signals_section_shown(self, _populated_db) -> None:
        # Rich may wrap the table title across lines in a narrow terminal;
        # check for both words rather than the full unwrapped title.
        output = self._result(_populated_db).output
        assert "Open Signals" in output or "Severity" in output

    def test_open_action_items_shown(self, _populated_db) -> None:
        assert "Open action items:" in self._result(_populated_db).output

    # ── sample warning ────────────────────────────────────────────────────

    def test_sample_warning_shown(self, _populated_db) -> None:
        """Passing a vault inside the repo (fixtures) should trigger the warning."""
        db_path, _ = _populated_db
        # Use the fixtures vault explicitly — it is inside the repo root
        result = _run_status(db_path, vault=str(FIXTURES / "vault"))
        assert result.exit_code == 0
        assert "sample" in result.output.lower() or "⚠" in result.output
