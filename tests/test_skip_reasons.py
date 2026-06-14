"""Tests for skip-reason reporting in ingest and extract modules.

Covers:
- IngestResult.skip_reasons is populated with the correct reason code on
  each duplicate-skip across all four ingest sources.
- ExtractionResult.skip_reasons is populated on duplicate-skip for signals,
  action items (two paths), and decisions.
- CLI ``manager-os ingest --verbose`` shows the skip reason table.
- CLI ``manager-os ingest`` (default) shows the safe-skip footnote.
- CLI ``manager-os extract --verbose`` shows skip reason details.
- Second run of ``manager-os extract`` (default) shows safe-skip footnote.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manager_os.cli import app as cli_app, _SAFE_SKIP_REASONS, _all_skips_safe
from manager_os.db import get_connection
from manager_os.ingest.obsidian import ingest_vault
from manager_os.ingest.forecast import ingest_forecast
from manager_os.ingest.deals import ingest_deals
from manager_os.ingest.workspace_summary import ingest_summary
from manager_os.extract.signals import run_rule_extraction
from manager_os.extract.action_items import extract_action_items_from_all_notes
from manager_os.extract.decisions import extract_decisions_from_all_notes

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent
TARGET_DATE = date(2026, 6, 13)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env(tmp_path: Path) -> dict[str, str]:
    return {
        "MANAGER_OS_VAULT_PATH": str(FIXTURES / "vault"),
        "MANAGER_OS_DB_PATH": str(tmp_path / "test.duckdb"),
        "MANAGER_OS_FORECAST_CSV": str(FIXTURES / "forecast.csv"),
        "MANAGER_OS_DEALS_CSV": str(FIXTURES / "deals.csv"),
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        "MANAGER_OS_CONFIG_DIR": str(REPO_ROOT / "config"),
    }


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def seeded_conn(conn, tmp_path: Path):
    """Ingest + extract once so the DB is populated."""
    vault_dir = tmp_path / "vault"
    shutil.copytree(FIXTURES / "vault", vault_dir)

    ingest_vault(str(vault_dir), conn)
    ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    ingest_summary(str(FIXTURES / "summaries"), TARGET_DATE, conn)
    run_rule_extraction(conn, run_date=TARGET_DATE)
    extract_action_items_from_all_notes(conn)
    extract_decisions_from_all_notes(conn)
    return conn, tmp_path / "vault"


# ===========================================================================
# Unit: IngestResult.skip_reasons
# ===========================================================================


class TestObsidianSkipReasons:
    def test_first_run_no_skips(self, conn, tmp_path: Path) -> None:
        vault_dir = tmp_path / "vault"
        shutil.copytree(FIXTURES / "vault", vault_dir)
        r = ingest_vault(str(vault_dir), conn)
        assert r.skipped == 0
        assert r.skip_reasons == {}

    def test_second_run_records_duplicate_content_hash(self, conn, tmp_path: Path) -> None:
        vault_dir = tmp_path / "vault"
        shutil.copytree(FIXTURES / "vault", vault_dir)
        ingest_vault(str(vault_dir), conn)  # first run
        r = ingest_vault(str(vault_dir), conn)  # second run
        assert r.skipped == 3
        assert r.skip_reasons.get("duplicate_content_hash") == 3
        assert "duplicate_content_hash" in _SAFE_SKIP_REASONS

    def test_skip_reasons_keys_are_safe(self, conn, tmp_path: Path) -> None:
        vault_dir = tmp_path / "vault"
        shutil.copytree(FIXTURES / "vault", vault_dir)
        ingest_vault(str(vault_dir), conn)
        r = ingest_vault(str(vault_dir), conn)
        for reason in r.skip_reasons:
            assert reason in _SAFE_SKIP_REASONS


class TestForecastSkipReasons:
    def test_second_run_records_already_exists(self, conn) -> None:
        ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
        r = ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
        assert r.skipped == 9
        assert r.skip_reasons.get("already_exists") == 9
        assert "already_exists" in _SAFE_SKIP_REASONS


class TestDealsSkipReasons:
    def test_second_run_records_already_exists(self, conn) -> None:
        ingest_deals(str(FIXTURES / "deals.csv"), conn)
        r = ingest_deals(str(FIXTURES / "deals.csv"), conn)
        assert r.skipped == 5
        assert r.skip_reasons.get("already_exists") == 5


class TestSummarySkipReasons:
    def test_second_run_records_already_exists(self, conn) -> None:
        ingest_summary(str(FIXTURES / "summaries"), TARGET_DATE, conn)
        r = ingest_summary(str(FIXTURES / "summaries"), TARGET_DATE, conn)
        assert r.skipped == 1
        assert r.skip_reasons.get("already_exists") == 1


# ===========================================================================
# Unit: ExtractionResult.skip_reasons
# ===========================================================================


class TestSignalSkipReasons:
    def test_first_run_no_skips(self, conn, tmp_path: Path) -> None:
        vault_dir = tmp_path / "vault"
        shutil.copytree(FIXTURES / "vault", vault_dir)
        ingest_vault(str(vault_dir), conn)
        ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
        ingest_deals(str(FIXTURES / "deals.csv"), conn)
        r = run_rule_extraction(conn, run_date=TARGET_DATE)
        assert r.skipped == 0
        assert r.skip_reasons == {}

    def test_second_run_records_signal_already_exists(self, conn, tmp_path: Path) -> None:
        vault_dir = tmp_path / "vault"
        shutil.copytree(FIXTURES / "vault", vault_dir)
        ingest_vault(str(vault_dir), conn)
        ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
        ingest_deals(str(FIXTURES / "deals.csv"), conn)
        run_rule_extraction(conn, run_date=TARGET_DATE)  # first run
        r = run_rule_extraction(conn, run_date=TARGET_DATE)  # second run
        assert r.skipped > 0
        assert r.skip_reasons.get("signal_already_exists", 0) == r.skipped
        assert "signal_already_exists" in _SAFE_SKIP_REASONS


class TestActionItemSkipReasons:
    def test_second_run_records_action_item_already_exists(self, conn, tmp_path: Path) -> None:
        vault_dir = tmp_path / "vault"
        shutil.copytree(FIXTURES / "vault", vault_dir)
        ingest_vault(str(vault_dir), conn)
        extract_action_items_from_all_notes(conn)  # first run
        r = extract_action_items_from_all_notes(conn)  # second run
        assert r.skipped > 0
        # All second-run skips should be already-exists
        assert r.skip_reasons.get("action_item_already_exists", 0) > 0
        for reason in r.skip_reasons:
            assert reason in _SAFE_SKIP_REASONS | {"duplicate_within_note"}

    def test_skip_reasons_are_safe_or_duplicate_within_note(self, conn, tmp_path: Path) -> None:
        """duplicate_within_note (same description twice in one note) is also safe."""
        vault_dir = tmp_path / "vault"
        shutil.copytree(FIXTURES / "vault", vault_dir)
        ingest_vault(str(vault_dir), conn)
        r = extract_action_items_from_all_notes(conn)
        # On first run, duplicate_within_note may appear but nothing else should
        for reason in r.skip_reasons:
            assert reason in _SAFE_SKIP_REASONS | {"duplicate_within_note"}, (
                f"Unexpected skip reason on first run: {reason}"
            )


class TestDecisionSkipReasons:
    def test_second_run_records_decision_already_exists(self, conn, tmp_path: Path) -> None:
        vault_dir = tmp_path / "vault"
        shutil.copytree(FIXTURES / "vault", vault_dir)
        ingest_vault(str(vault_dir), conn)
        extract_decisions_from_all_notes(conn)  # first run
        r = extract_decisions_from_all_notes(conn)  # second run
        # If any decisions were found, they should all show as already_exists
        if r.skipped > 0:
            assert r.skip_reasons.get("decision_already_exists", 0) > 0
            assert "decision_already_exists" in _SAFE_SKIP_REASONS


# ===========================================================================
# Unit: _all_skips_safe helper
# ===========================================================================


class TestAllSkipsSafeHelper:
    def test_empty_results_are_safe(self) -> None:
        assert _all_skips_safe([]) is True

    def test_all_safe_reasons(self) -> None:
        from manager_os.ingest.obsidian import IngestResult
        r = IngestResult(skipped=3, skip_reasons={"duplicate_content_hash": 3})
        assert _all_skips_safe([("obsidian", r)]) is True

    def test_unsafe_reason_returns_false(self) -> None:
        from manager_os.ingest.obsidian import IngestResult
        r = IngestResult(skipped=1, skip_reasons={"empty_file": 1})
        assert _all_skips_safe([("obsidian", r)]) is False

    def test_mixed_reasons_returns_false(self) -> None:
        from manager_os.ingest.obsidian import IngestResult
        r = IngestResult(skipped=2, skip_reasons={"already_exists": 1, "empty_file": 1})
        assert _all_skips_safe([("obsidian", r)]) is False


# ===========================================================================
# CLI: ingest --verbose shows skip reason table
# ===========================================================================


class TestIngestVerboseCLI:
    def test_verbose_second_run_shows_skip_table(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)
        runner = CliRunner()
        # First run
        runner.invoke(cli_app, ["ingest", "--date", "2026-06-13"], env=env)
        # Second run with --verbose
        result = runner.invoke(
            cli_app, ["ingest", "--date", "2026-06-13", "--verbose"], env=env
        )
        assert result.exit_code == 0, result.output
        # Should show the reason text
        assert "already exists" in result.output.lower() or "duplicate" in result.output.lower()

    def test_verbose_second_run_shows_safe_indicator(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)
        runner = CliRunner()
        runner.invoke(cli_app, ["ingest", "--date", "2026-06-13"], env=env)
        result = runner.invoke(
            cli_app, ["ingest", "--date", "2026-06-13", "--verbose"], env=env
        )
        assert result.exit_code == 0
        assert "safe to re-run" in result.output.lower() or "idempotent" in result.output.lower()

    def test_default_second_run_shows_footnote(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)
        runner = CliRunner()
        runner.invoke(cli_app, ["ingest", "--date", "2026-06-13"], env=env)
        result = runner.invoke(
            cli_app, ["ingest", "--date", "2026-06-13"], env=env
        )
        assert result.exit_code == 0
        output_lower = result.output.lower()
        # Default mode: footnote mentions "skipped" and "already exist"
        assert "skipped" in output_lower
        assert "already exist" in output_lower or "idempotent" in output_lower

    def test_default_first_run_no_footnote(self, tmp_path: Path) -> None:
        """First run has no skips so no footnote should appear."""
        env = _make_env(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["ingest", "--date", "2026-06-13"], env=env
        )
        assert result.exit_code == 0
        # No skips → no footnote
        assert "already exist" not in result.output.lower()
        assert "idempotent" not in result.output.lower()

    def test_verbose_flag_short_form(self, tmp_path: Path) -> None:
        env = _make_env(tmp_path)
        runner = CliRunner()
        runner.invoke(cli_app, ["ingest", "--date", "2026-06-13"], env=env)
        result = runner.invoke(
            cli_app, ["ingest", "--date", "2026-06-13", "-v"], env=env
        )
        assert result.exit_code == 0


# ===========================================================================
# CLI: extract --verbose shows skip reason table
# ===========================================================================


class TestExtractVerboseCLI:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path) -> None:
        """Run ingest once so extract has data to work with."""
        self._env = _make_env(tmp_path)
        runner = CliRunner()
        runner.invoke(cli_app, ["ingest", "--date", "2026-06-13"], env=self._env)

    def test_verbose_second_extract_shows_reason_table(self) -> None:
        runner = CliRunner()
        runner.invoke(cli_app, ["extract", "--date", "2026-06-13"], env=self._env)
        result = runner.invoke(
            cli_app, ["extract", "--date", "2026-06-13", "--verbose"], env=self._env
        )
        assert result.exit_code == 0, result.output
        output_lower = result.output.lower()
        assert "already exist" in output_lower or "signal already exist" in output_lower

    def test_default_second_extract_shows_footnote(self) -> None:
        runner = CliRunner()
        runner.invoke(cli_app, ["extract", "--date", "2026-06-13"], env=self._env)
        result = runner.invoke(
            cli_app, ["extract", "--date", "2026-06-13"], env=self._env
        )
        assert result.exit_code == 0, result.output
        output_lower = result.output.lower()
        assert "skipped" in output_lower
        assert "already exist" in output_lower or "idempotent" in output_lower

    def test_default_first_extract_no_footnote(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["extract", "--date", "2026-06-13"], env=self._env
        )
        assert result.exit_code == 0
        # First run: no skips → no footnote
        assert "already exist" not in result.output.lower()

    def test_extract_verbose_short_form(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_app, ["extract", "--date", "2026-06-13", "-v"], env=self._env
        )
        assert result.exit_code == 0
