"""Tests for ``manager-os daily`` morning workflow command.

Covers:
- daily --dry-run runs without writes
- daily --rules-only skips LLM
- daily --no-workspace skips workspace fetch
- daily passes llm-limit to extract
- daily prints summary
- daily handles workspace failure non-fatally
- daily does not open dashboard unless --open-dashboard
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manager_os.cli import app as cli_app
from manager_os.db import get_connection, init_schema, content_hash
from manager_os.ingest.obsidian import ingest_vault
from manager_os.ingest.forecast import ingest_forecast
from manager_os.ingest.deals import ingest_deals

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent
TARGET_DATE = date(2026, 6, 13)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env(db_path: str, *, vault: str = "", home: str = "") -> dict[str, str]:
    env_vars = {
        "MANAGER_OS_DB_PATH": db_path,
        "MANAGER_OS_VAULT_PATH": vault or str(FIXTURES / "vault"),
        "MANAGER_OS_FORECAST_CSV": str(FIXTURES / "forecast.csv"),
        "MANAGER_OS_DEALS_CSV": str(FIXTURES / "deals.csv"),
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        "MANAGER_OS_CONFIG_DIR": str(REPO_ROOT / "config"),
        "MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED": "false",
    }
    if home:
        env_vars["HOME"] = home
    return env_vars


def _run(*args: str, db_path: str, vault: str = "", home: str = "") -> object:
    return CliRunner().invoke(cli_app, list(args), env=_env(db_path, vault=vault, home=home))


def _row_count(db_path: str, table: str) -> int:
    if not Path(db_path).exists():
        return 0
    conn = get_connection(db_path)
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return count


def _populate_db(db_path: str, vault_dir: str) -> None:
    """Seed a database with fixture data."""
    conn = get_connection(db_path)
    ingest_vault(vault_dir, conn)
    ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    # Seed signals so extract idempotency works
    sig_id = content_hash("test::daily::signal")
    conn.execute(
        """INSERT OR IGNORE INTO signals
           (id, signal_date, source, source_path, entity_type, entity_name,
            signal_type, severity, summary, why_it_matters,
            requires_manager_attention, confidence, status, created_at, updated_at)
           VALUES (?, ?, 'rule', '', 'client', 'TestCorp',
                   'risk', 'high', 'Test signal', '',
                   TRUE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        [sig_id, date.today().isoformat()],
    )
    conn.close()


# ===========================================================================
# Dry-run tests
# ===========================================================================


class TestDailyDryRun:
    """daily --dry-run must not write to DuckDB or retrieve workspace."""

    def test_dry_run_exits_0(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--dry-run", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        assert result.exit_code == 0, result.output

    def test_dry_run_no_new_rows_in_notes(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)
        before = _row_count(db, "notes")

        _run("daily", "--dry-run", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        after = _row_count(db, "notes")
        assert before == after

    def test_dry_run_no_new_rows_in_signals(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)
        before = _row_count(db, "signals")

        _run("daily", "--dry-run", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        after = _row_count(db, "signals")
        assert before == after

    def test_dry_run_no_brief_written(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        briefs_before = list(Path("output/daily_briefs").glob("*")) if Path("output/daily_briefs").exists() else []
        _run("daily", "--dry-run", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        briefs_after = list(Path("output/daily_briefs").glob("*")) if Path("output/daily_briefs").exists() else []
        assert len(briefs_before) == len(briefs_after)

    def test_dry_run_mentions_dry_run_in_header(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--dry-run", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        assert "DRY RUN" in result.output or "dry run" in result.output.lower()

    def test_dry_run_shows_summary(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--dry-run", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        assert "Daily Flow Complete" in result.output


# ===========================================================================
# Option tests
# ===========================================================================


class TestDailyRulesOnly:
    """--rules-only must skip LLM extraction."""

    def test_rules_only_runs_extract(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--rules-only", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert result.exit_code == 0, result.output

    def test_rules_only_header_shows_rules_mode(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--rules-only", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert "rules" in result.output.lower()

    def test_rules_only_no_llm_in_output(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--rules-only", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert "signals (llm)" not in result.output.lower()


class TestDailyNoWorkspace:
    """--no-workspace must skip workspace fetch."""

    def test_no_workspace_exits_0(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--no-workspace", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert result.exit_code == 0, result.output

    def test_no_workspace_mentions_skipped_in_header(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--no-workspace", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert "disabled" in result.output or "skipped" in result.output.lower()


class TestDailyLlmLimit:
    """--llm-limit is passed through to extraction."""

    def test_llm_limit_in_header(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--llm-limit", "7", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert result.exit_code == 0, result.output
        assert "7" in result.output  # should appear in header


class TestDailySummary:
    """daily always prints a closing summary."""

    def test_summary_printed(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        assert "Daily Flow Complete" in result.output
        assert "Ingest" in result.output or "ingest" in result.output.lower()
        assert "Extract" in result.output or "extract" in result.output.lower()
        assert "Brief" in result.output or "brief" in result.output.lower()

    def test_dry_run_summary_mentions_dry_run(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--dry-run", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert "Daily Flow Complete" in result.output
        assert "DRY RUN" in result.output or "dry run" in result.output.lower()


class TestDailyNoDashboard:
    """Dashboard must not launch unless --open-dashboard."""

    def test_default_does_not_launch_dashboard(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        # The header shows "Dashboard: no" but the dashboard must not be launched
        assert "Launching dashboard" not in result.output


class TestDailyWorkspaceFailureNonFatal:
    """Workspace failures must not block the rest of the flow."""

    def test_runs_ingest_and_extract_even_with_workspace_disabled(self, tmp_path: Path) -> None:
        """When workspace is disabled, the rest of daily still runs."""
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        # With workspace retrieval disabled, daily should still proceed
        result = _run("daily", "--date", "2026-06-13", db_path=db, vault=vault_dir)
        assert result.exit_code == 0, result.output
        assert "Daily Flow Complete" in result.output


class TestDailySkipOptions:
    """--skip-ingest, --skip-extract, --skip-brief work."""

    def test_skip_ingest_mentions_skipped(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--skip-ingest", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert "skip" in result.output.lower() or "Skipped" in result.output

    def test_skip_extract_mentions_skipped(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--skip-extract", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert "skip" in result.output.lower() or "Skipped" in result.output

    def test_skip_brief_mentions_skipped(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--skip-brief", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert "skip" in result.output.lower() or "Skipped" in result.output


class TestDailyVerbose:
    """--verbose shows more detail."""

    def test_verbose_exits_0(self, tmp_path: Path) -> None:
        db = str(tmp_path / "test.duckdb")
        vault_dir = str(tmp_path / "vault")
        shutil.copytree(FIXTURES / "vault", vault_dir)
        _populate_db(db, vault_dir)

        result = _run("daily", "--verbose", "--date", "2026-06-13",
                       db_path=db, vault=vault_dir)
        assert result.exit_code == 0, result.output
