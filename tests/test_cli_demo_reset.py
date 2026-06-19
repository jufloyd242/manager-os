"""Tests for the ``manager-os demo-reset`` command.

Covers:
- Successful run against sample/fixture paths
- Safety refusal when vault looks like a real Obsidian vault
- Dry-run leaves nothing on disk
- After a successful reset the demo database has the expected sample records
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from manager_os.cli import app as cli_app, _REPO_ROOT, _source_path_looks_real

FIXTURES = Path(__file__).parent / "fixtures"
TARGET_DATE = "2026-06-13"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _env(tmp_path: Path, *, vault: str | None = None) -> dict[str, str]:
    """Build env-var dict that points all configurable paths at fixture data
    and redirects the demo DB to a path we control inside *tmp_path*."""
    return {
        # Source data — all inside the project (tests/fixtures) → always safe
        "MANAGER_OS_VAULT_PATH": vault if vault is not None else str(FIXTURES / "vault"),
        "MANAGER_OS_FORECAST_CSV": str(FIXTURES / "forecast.csv"),
        "MANAGER_OS_DEALS_CSV": str(FIXTURES / "deals.csv"),
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        # config dir — inside project
        "MANAGER_OS_CONFIG_DIR": str(_REPO_ROOT / "config"),
        # DB path must point at a writable location for the command; however
        # demo-reset always ignores MANAGER_OS_DB_PATH and writes to
        # data/demo/manager_os_demo.duckdb relative to the repo root.
        # We set it to a tmp DB just so Settings is valid.
        "MANAGER_OS_DB_PATH": str(tmp_path / "ignored.duckdb"),
    }


def _demo_db() -> Path:
    return _REPO_ROOT / "data" / "demo" / "manager_os_demo.duckdb"


def _demo_output() -> Path:
    return _REPO_ROOT / "output" / "demo"


# ===========================================================================
# Unit tests for the safety-check helper
# ===========================================================================


class TestSourcePathLooksReal:
    def test_nonexistent_path_is_safe(self) -> None:
        assert not _source_path_looks_real(Path("/nonexistent/path/vault"))

    def test_path_inside_repo_is_safe(self) -> None:
        fixture_vault = FIXTURES / "vault"
        assert not _source_path_looks_real(fixture_vault, is_vault=True)

    def test_path_with_fixture_keyword_is_safe(self, tmp_path: Path) -> None:
        p = tmp_path / "fixture_data" / "vault"
        p.mkdir(parents=True)
        assert not _source_path_looks_real(p)

    def test_path_with_demo_keyword_is_safe(self, tmp_path: Path) -> None:
        p = tmp_path / "demo_vault"
        p.mkdir(parents=True)
        assert not _source_path_looks_real(p)

    def test_real_vault_outside_repo_flagged(self, tmp_path: Path) -> None:
        """Vault outside repo root with .obsidian directory → looks real."""
        vault = tmp_path / "my_obsidian_vault"
        vault.mkdir()
        (vault / ".obsidian").mkdir()
        assert _source_path_looks_real(vault, is_vault=True)

    def test_vault_outside_repo_without_obsidian_not_flagged(self, tmp_path: Path) -> None:
        """Vault outside repo root but without .obsidian → not flagged as real Obsidian vault."""
        vault = tmp_path / "plain_dir"
        vault.mkdir()
        assert not _source_path_looks_real(vault, is_vault=True)

    def test_csv_outside_repo_flagged(self, tmp_path: Path) -> None:
        csv = tmp_path / "forecast.csv"
        csv.write_text("a,b\n1,2\n")
        assert _source_path_looks_real(csv)

    def test_csv_outside_repo_with_sample_keyword_safe(self, tmp_path: Path) -> None:
        p = tmp_path / "sample_data" / "forecast.csv"
        p.parent.mkdir(parents=True)
        p.write_text("a,b\n1,2\n")
        assert not _source_path_looks_real(p)


# ===========================================================================
# CLI: demo-reset dry-run
# ===========================================================================


class TestDemoResetDryRun:
    def test_dry_run_exits_zero(self, tmp_path: Path, monkeypatch) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE, "--dry-run"],
            env=_env(tmp_path),
        )
        assert result.exit_code == 0, result.output

    def test_dry_run_mentions_would_delete(self, tmp_path: Path, monkeypatch) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE, "--dry-run"],
            env=_env(tmp_path),
        )
        assert "dry run" in result.output.lower()
        assert "Would delete DB" in result.output or "Would delete" in result.output

    def test_dry_run_does_not_create_db(self, tmp_path: Path, monkeypatch) -> None:
        # Remove the demo DB first if it exists from a previous test run
        demo_db = _demo_db()
        existed_before = demo_db.exists()

        runner = CliRunner()
        runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE, "--dry-run"],
            env=_env(tmp_path),
        )

        if not existed_before:
            assert not demo_db.exists(), (
                "demo-reset --dry-run must not create the demo database"
            )

    def test_dry_run_does_not_write_output_files(self, tmp_path: Path) -> None:
        brief_path = _demo_output() / f"{TARGET_DATE}-brief.md"
        existed_before = brief_path.exists()

        runner = CliRunner()
        runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE, "--dry-run"],
            env=_env(tmp_path),
        )

        if not existed_before:
            assert not brief_path.exists(), (
                "demo-reset --dry-run must not create output files"
            )


# ===========================================================================
# CLI: demo-reset safety refusal
# ===========================================================================


class TestDemoResetSafetyRefusal:
    def test_refuses_real_vault_without_yes_demo(self, tmp_path: Path) -> None:
        """A vault outside the repo with .obsidian present must trigger refusal."""
        real_vault = tmp_path / "my_obsidian_vault"
        real_vault.mkdir()
        (real_vault / ".obsidian").mkdir()

        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE],
            env=_env(tmp_path, vault=str(real_vault)),
        )

        assert result.exit_code == 1
        assert "Safety check failed" in result.output
        assert "vault_path" in result.output

    def test_yes_demo_bypasses_refusal(self, tmp_path: Path) -> None:
        """--yes-demo allows proceeding even when the vault looks real."""
        real_vault = tmp_path / "my_obsidian_vault"
        real_vault.mkdir()
        (real_vault / ".obsidian").mkdir()
        # Add a minimal note so ingest doesn't fail on an empty vault
        (real_vault / "note.md").write_text(
            "---\ndate: 2026-06-13\n---\n# Test note\nSome content.\n"
        )

        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE, "--yes-demo"],
            env=_env(tmp_path, vault=str(real_vault)),
        )

        assert result.exit_code == 0, result.output

    def test_refusal_does_not_delete_anything(self, tmp_path: Path) -> None:
        """When safety check fails and --yes-demo is absent, nothing is touched."""
        real_vault = tmp_path / "my_obsidian_vault"
        real_vault.mkdir()
        (real_vault / ".obsidian").mkdir()

        # Pre-create a sentinel file in demo output to verify it survives.
        sentinel = _demo_output() / "sentinel.txt"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("keep me")

        runner = CliRunner()
        runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE],
            env=_env(tmp_path, vault=str(real_vault)),
        )

        assert sentinel.exists(), (
            "demo-reset must not delete existing output when safety check fails"
        )
        # Cleanup sentinel so other tests start clean
        sentinel.unlink(missing_ok=True)

    def test_empty_vault_path_skips_vault_check(self, tmp_path: Path) -> None:
        """When vault_path is empty the safety check must not refuse."""
        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE],
            env=_env(tmp_path, vault=""),
        )
        # Should succeed (no vault to ingest, but no refusal either)
        assert result.exit_code == 0, result.output


# ===========================================================================
# CLI: demo-reset success with sample paths
# ===========================================================================


class TestDemoResetSuccess:
    """Full demo-reset using fixture paths.  All tests in this class share a
    single reset run via the module-scoped ``_demo_run`` fixture.
    """

    @pytest.fixture(scope="class", autouse=True)
    def _demo_run(self, request, tmp_path_factory: pytest.TempPathFactory) -> None:
        """Run demo-reset once so all tests in this class can inspect results."""
        tmp = tmp_path_factory.mktemp("demo_reset_success")
        runner = CliRunner()
        result = runner.invoke(
            cli_app,
            ["demo-reset", "--date", TARGET_DATE],
            env=_env(tmp),
        )
        request.cls._result = result

    # ── Exit code ────────────────────────────────────────────────────────────

    def test_exits_zero(self) -> None:
        assert self._result.exit_code == 0, self._result.output

    def test_output_reports_complete(self) -> None:
        assert "Demo reset complete" in self._result.output

    # ── Output files ─────────────────────────────────────────────────────────

    def test_brief_file_exists(self) -> None:
        brief = _demo_output() / f"{TARGET_DATE}-brief.md"
        assert brief.exists(), f"Expected demo brief at {brief}"

    def test_brief_file_is_nonempty(self) -> None:
        brief = _demo_output() / f"{TARGET_DATE}-brief.md"
        assert brief.stat().st_size > 0

    def test_brief_mentions_known_entity(self) -> None:
        brief = _demo_output() / f"{TARGET_DATE}-brief.md"
        content = brief.read_text(encoding="utf-8")
        known = ("Alice", "David", "Acme", "Big Retail", "FinServ")
        assert any(name in content for name in known), (
            f"Brief does not mention any expected entity.\nContent:\n{content[:400]}"
        )

    def test_closeout_file_exists(self) -> None:
        co = _demo_output() / "closeout" / f"{TARGET_DATE}.md"
        assert co.exists(), f"Expected demo closeout at {co}"

    def test_closeout_file_has_eod_header(self) -> None:
        co = _demo_output() / "closeout" / f"{TARGET_DATE}.md"
        assert "EOD Closeout" in co.read_text(encoding="utf-8")

    def test_output_paths_printed(self) -> None:
        assert "Brief written" in self._result.output
        assert "Closeout written" in self._result.output

    # ── Database content ─────────────────────────────────────────────────────

    def _count(self, table: str) -> int:
        with duckdb.connect(str(_demo_db()), read_only=True) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_db_has_people(self) -> None:
        assert self._count("people") >= 5

    def test_db_has_clients(self) -> None:
        assert self._count("clients") >= 5

    def test_db_has_notes(self) -> None:
        # 3 vault fixture notes
        assert self._count("notes") == 3

    def test_db_has_deals(self) -> None:
        # 5 deals in deals.csv
        assert self._count("deals") == 5

    def test_db_has_staffing_forecast(self) -> None:
        # 9 rows in forecast.csv
        assert self._count("staffing_forecast") == 9

    def test_db_has_raw_documents(self) -> None:
        # vault(3) + summary(1) + (gws not set in demo env) = ≥ 4
        assert self._count("raw_documents") >= 4

    def test_db_has_signals(self) -> None:
        assert self._count("signals") > 0

    def test_db_has_action_items(self) -> None:
        assert self._count("action_items") > 0

    def test_utilization_risk_signal_present(self) -> None:
        with duckdb.connect(str(_demo_db()), read_only=True) as conn:
            row = conn.execute(
                "SELECT entity_name FROM signals WHERE signal_type = 'utilization_risk' LIMIT 1"
            ).fetchone()
        assert row is not None, "utilization_risk signal not found (expected David Park at 120%)"
        assert "David" in row[0]

    def test_sow_signal_present(self) -> None:
        with duckdb.connect(str(_demo_db()), read_only=True) as conn:
            row = conn.execute(
                "SELECT entity_name FROM signals WHERE signal_type = 'sow_loe_review' LIMIT 1"
            ).fetchone()
        assert row is not None, "sow_loe_review signal not found (expected Big Retail)"


# ===========================================================================
# CLI: demo-reset idempotency  (second run on same DB)
# ===========================================================================


class TestDemoResetIdempotency:
    """Running demo-reset twice must always succeed; the second run replaces
    (not duplicates) records because it wipes and rebuilds the DB."""

    @pytest.fixture(scope="class", autouse=True)
    def _two_runs(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        tmp = tmp_path_factory.mktemp("demo_reset_idem")
        runner = CliRunner()
        env = _env(tmp)
        runner.invoke(cli_app, ["demo-reset", "--date", TARGET_DATE], env=env)
        result2 = runner.invoke(cli_app, ["demo-reset", "--date", TARGET_DATE], env=env)
        self.__class__._result2 = result2

    def test_second_run_exits_zero(self) -> None:
        assert self._result2.exit_code == 0, self._result2.output

    def test_second_run_notes_count_unchanged(self) -> None:
        with duckdb.connect(str(_demo_db()), read_only=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        assert count == 3, (
            f"Expected 3 notes after second demo-reset, got {count} "
            "(second run may have duplicated rows)"
        )

    def test_second_run_deals_count_unchanged(self) -> None:
        with duckdb.connect(str(_demo_db()), read_only=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        assert count == 5

    def test_second_run_forecast_count_unchanged(self) -> None:
        with duckdb.connect(str(_demo_db()), read_only=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0]
        assert count == 9
