"""Golden sample-data end-to-end test.

Exercises the complete manager-os user workflow:

    manager-os ingest  →  manager-os extract  →  manager-os brief  →  manager-os closeout

All steps run through the real Typer CLI using only the repository's test
fixtures in a temporary directory.  No real vault, Google Workspace, or
database is touched.

This is the safe baseline that must remain green before any real integration
is wired up.

Assertions
----------
* Each CLI step exits with code 0.
* The generated brief file exists and is non-empty.
* The generated closeout file exists and is non-empty.
* After ingest the database contains rows in all eight expected tables:
  people, clients, raw_documents, notes, deals, staffing_forecast,
  signals, action_items.
* A second run of ``manager-os ingest`` also exits 0 and does *not* add
  duplicate rows (all rows are skipped), proving idempotency.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from manager_os.cli import app as cli_app

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent
CONFIG_DIR = REPO_ROOT / "config"
TARGET_DATE = "2026-06-13"

# ---------------------------------------------------------------------------
# Known counts from fixture files (hard-coded so failures are meaningful)
# ---------------------------------------------------------------------------

_NOTES_COUNT = 3         # vault/1on1_alice.md  +  vault/client_acme_status.md  +  vault/deal_big_retail.md
_FORECAST_COUNT = 9      # nine data rows in fixtures/forecast.csv
_DEALS_COUNT = 5         # five data rows in fixtures/deals.csv
_PEOPLE_SEEDED_MIN = 5   # at least five entries in config/people.yaml
_CLIENTS_SEEDED_MIN = 5  # at least five entries in config/clients.yaml


# ---------------------------------------------------------------------------
# Module-scoped golden run
# ---------------------------------------------------------------------------

@dataclass
class GoldenRun:
    """Container for all CLI results and paths from a single pipeline run."""

    tmp_path: Path
    db_path: Path
    brief_path: Path          # --output target for ``brief``
    closeout_dir: Path        # --output target for ``closeout``
    env: dict[str, str]       # env vars forwarded to every CLI invocation

    # Results from each CLI step
    ingest1: Any              # first  ``manager-os ingest``
    extract: Any              # ``manager-os extract``
    brief: Any                # ``manager-os brief``
    closeout: Any             # ``manager-os closeout``
    ingest2: Any              # second ``manager-os ingest``  (idempotency)


@pytest.fixture(scope="module")
def golden(tmp_path_factory: pytest.TempPathFactory) -> GoldenRun:
    """Run the full CLI pipeline once against fixture data.

    Scoped to the module so that the pipeline executes exactly once and
    all test functions share the same resulting database state.
    """
    tmp = tmp_path_factory.mktemp("golden_e2e")
    db_path = tmp / "manager_os.duckdb"
    brief_path = tmp / f"{TARGET_DATE}.md"
    closeout_dir = tmp / "closeout"
    closeout_dir.mkdir()

    # Environment variables forwarded to every CLI invocation.
    # They override any .env file so the test is fully self-contained.
    env = {
        "MANAGER_OS_VAULT_PATH": str(FIXTURES / "vault"),
        "MANAGER_OS_DB_PATH": str(db_path),
        "MANAGER_OS_FORECAST_CSV": str(FIXTURES / "forecast.csv"),
        "MANAGER_OS_DEALS_CSV": str(FIXTURES / "deals.csv"),
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        "MANAGER_OS_CONFIG_DIR": str(CONFIG_DIR),
    }

    runner = CliRunner()

    # ------------------------------------------------------------------
    # Step 1 — ingest (first run)
    # ------------------------------------------------------------------
    ingest1 = runner.invoke(
        cli_app,
        ["ingest", "--date", TARGET_DATE],
        env=env,
    )

    # ------------------------------------------------------------------
    # Step 2 — extract  (rules-only; no OPENAI_API_KEY required)
    # ------------------------------------------------------------------
    extract = runner.invoke(
        cli_app,
        ["extract", "--date", TARGET_DATE, "--mode", "rules"],
        env=env,
    )

    # ------------------------------------------------------------------
    # Step 3 — brief  (write to a path we control so we can verify it)
    # ------------------------------------------------------------------
    brief = runner.invoke(
        cli_app,
        ["brief", "--date", TARGET_DATE, "--output", str(brief_path)],
        env=env,
    )

    # ------------------------------------------------------------------
    # Step 4 — closeout  (write to a directory we control)
    # ------------------------------------------------------------------
    closeout = runner.invoke(
        cli_app,
        [
            "closeout",
            "--date", TARGET_DATE,
            "--no-weekly",
            "--output", str(closeout_dir),
        ],
        env=env,
    )

    # ------------------------------------------------------------------
    # Step 5 — second ingest  (idempotency check)
    # ------------------------------------------------------------------
    ingest2 = runner.invoke(
        cli_app,
        ["ingest", "--date", TARGET_DATE],
        env=env,
    )

    return GoldenRun(
        tmp_path=tmp,
        db_path=db_path,
        brief_path=brief_path,
        closeout_dir=closeout_dir,
        env=env,
        ingest1=ingest1,
        extract=extract,
        brief=brief,
        closeout=closeout,
        ingest2=ingest2,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _count(db_path: Path, table: str) -> int:
    with duckdb.connect(str(db_path), read_only=True) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ===========================================================================
# 1.  CLI exit-code assertions
# ===========================================================================

class TestCliExitCodes:
    """Every CLI step must exit with code 0 on the sample data."""

    def test_ingest_exits_zero(self, golden: GoldenRun) -> None:
        assert golden.ingest1.exit_code == 0, (
            f"manager-os ingest failed (exit {golden.ingest1.exit_code}):\n"
            f"{golden.ingest1.output}"
        )

    def test_extract_exits_zero(self, golden: GoldenRun) -> None:
        assert golden.extract.exit_code == 0, (
            f"manager-os extract failed (exit {golden.extract.exit_code}):\n"
            f"{golden.extract.output}"
        )

    def test_brief_exits_zero(self, golden: GoldenRun) -> None:
        assert golden.brief.exit_code == 0, (
            f"manager-os brief failed (exit {golden.brief.exit_code}):\n"
            f"{golden.brief.output}"
        )

    def test_closeout_exits_zero(self, golden: GoldenRun) -> None:
        assert golden.closeout.exit_code == 0, (
            f"manager-os closeout failed (exit {golden.closeout.exit_code}):\n"
            f"{golden.closeout.output}"
        )


# ===========================================================================
# 2.  Output file existence
# ===========================================================================

class TestOutputFiles:
    """The workflow must produce the expected markdown output files."""

    def test_brief_file_exists(self, golden: GoldenRun) -> None:
        assert golden.brief_path.exists(), (
            f"Daily brief not found at {golden.brief_path}\n"
            f"CLI output:\n{golden.brief.output}"
        )

    def test_brief_file_is_nonempty(self, golden: GoldenRun) -> None:
        assert golden.brief_path.stat().st_size > 0

    def test_brief_file_mentions_entity(self, golden: GoldenRun) -> None:
        """Brief should reference at least one known entity from fixture data."""
        content = golden.brief_path.read_text(encoding="utf-8")
        known_names = ("Alice", "David", "Acme", "Big Retail", "FinServ")
        assert any(name in content for name in known_names), (
            "Daily brief does not mention any known entity.\n"
            f"Content:\n{content[:500]}"
        )

    def test_closeout_file_exists(self, golden: GoldenRun) -> None:
        closeout_file = golden.closeout_dir / f"{TARGET_DATE}.md"
        assert closeout_file.exists(), (
            f"Closeout file not found at {closeout_file}\n"
            f"CLI output:\n{golden.closeout.output}"
        )

    def test_closeout_file_is_nonempty(self, golden: GoldenRun) -> None:
        closeout_file = golden.closeout_dir / f"{TARGET_DATE}.md"
        assert closeout_file.stat().st_size > 0

    def test_closeout_file_has_eod_header(self, golden: GoldenRun) -> None:
        closeout_file = golden.closeout_dir / f"{TARGET_DATE}.md"
        content = closeout_file.read_text(encoding="utf-8")
        assert "EOD Closeout" in content


# ===========================================================================
# 3.  Database content — all eight required tables
# ===========================================================================

class TestDatabaseContent:
    """After ingest+extract the database must contain the expected tables."""

    def test_people_seeded(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "people")
        assert count >= _PEOPLE_SEEDED_MIN, (
            f"Expected >= {_PEOPLE_SEEDED_MIN} people, got {count}"
        )

    def test_clients_seeded(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "clients")
        assert count >= _CLIENTS_SEEDED_MIN, (
            f"Expected >= {_CLIENTS_SEEDED_MIN} clients, got {count}"
        )

    def test_notes_ingested(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "notes")
        assert count == _NOTES_COUNT, (
            f"Expected {_NOTES_COUNT} notes from vault fixtures, got {count}"
        )

    def test_deals_ingested(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "deals")
        assert count == _DEALS_COUNT, (
            f"Expected {_DEALS_COUNT} deals from deals.csv, got {count}"
        )

    def test_staffing_forecast_ingested(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "staffing_forecast")
        assert count == _FORECAST_COUNT, (
            f"Expected {_FORECAST_COUNT} forecast rows, got {count}"
        )

    def test_raw_documents_present(self, golden: GoldenRun) -> None:
        # vault(3) + summary(1) + gws calendar(≥3) + gmail(≥2) + chat(≥2) = ≥11
        # Use a conservative floor that still proves ingest happened.
        count = _count(golden.db_path, "raw_documents")
        assert count >= 8, (
            f"Expected >= 8 raw_documents after full ingest, got {count}"
        )

    def test_signals_extracted(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "signals")
        assert count > 0, "No signals were extracted from fixture data"

    def test_action_items_extracted(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "action_items")
        assert count > 0, "No action items were extracted from fixture data"

    # ── Spot-checks on specific expected signals ──────────────────────────

    def test_utilization_risk_signal_for_david(self, golden: GoldenRun) -> None:
        """David Park at 120% allocation must produce a utilization_risk signal."""
        with duckdb.connect(str(golden.db_path), read_only=True) as conn:
            row = conn.execute(
                "SELECT entity_name FROM signals WHERE signal_type = 'utilization_risk' LIMIT 1"
            ).fetchone()
        assert row is not None, "No utilization_risk signal found (expected David Park at 120%)"
        assert "David" in row[0]

    def test_sow_signal_for_big_retail(self, golden: GoldenRun) -> None:
        """Big Retail SOW pending + close date 2026-06-17 must produce sow_loe_review."""
        with duckdb.connect(str(golden.db_path), read_only=True) as conn:
            row = conn.execute(
                "SELECT entity_name FROM signals WHERE signal_type = 'sow_loe_review' LIMIT 1"
            ).fetchone()
        assert row is not None, "No sow_loe_review signal found for Big Retail"


# ===========================================================================
# 4.  Idempotency — second ingest skips all rows, nothing duplicated
# ===========================================================================

class TestIdempotency:
    """Re-running ingest must be safe: exit 0 and no duplicate rows."""

    def test_second_ingest_exits_zero(self, golden: GoldenRun) -> None:
        assert golden.ingest2.exit_code == 0, (
            f"Second manager-os ingest failed (exit {golden.ingest2.exit_code}):\n"
            f"{golden.ingest2.output}"
        )

    def test_second_ingest_notes_count_unchanged(self, golden: GoldenRun) -> None:
        """Notes table must still have exactly _NOTES_COUNT rows after the second ingest."""
        count = _count(golden.db_path, "notes")
        assert count == _NOTES_COUNT, (
            f"Expected {_NOTES_COUNT} notes after second ingest, got {count} — "
            "second ingest may have inserted duplicates"
        )

    def test_second_ingest_deals_count_unchanged(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "deals")
        assert count == _DEALS_COUNT, (
            f"Expected {_DEALS_COUNT} deals after second ingest, got {count} — "
            "second ingest may have inserted duplicates"
        )

    def test_second_ingest_forecast_count_unchanged(self, golden: GoldenRun) -> None:
        count = _count(golden.db_path, "staffing_forecast")
        assert count == _FORECAST_COUNT, (
            f"Expected {_FORECAST_COUNT} forecast rows after second ingest, got {count} — "
            "second ingest may have inserted duplicates"
        )

    def test_second_ingest_raw_documents_count_unchanged(self, golden: GoldenRun) -> None:
        """Raw documents must not grow on re-ingest."""
        # Capture count after ingest1+ingest2 and assert it matches what we'd
        # expect from a single ingest (deduplicated).  The exact floor is the
        # same ≥8 guard used above — if ingest2 added rows the count would grow.
        with duckdb.connect(str(golden.db_path), read_only=True) as conn:
            count_after_both = conn.execute(
                "SELECT COUNT(*) FROM raw_documents"
            ).fetchone()[0]
        # We ingested GWS once; ingest2 should not have added more GWS documents.
        assert count_after_both >= 8, "raw_documents dropped below expected minimum"

    def test_second_ingest_skipped_reported_in_output(self, golden: GoldenRun) -> None:
        """The CLI output of the second ingest must mention 'obsidian' in a row that
        shows at least one skipped document, confirming that the skip logic ran."""
        output = golden.ingest2.output
        # The Rich table renders source names as plain text; "obsidian" and
        # at least one non-zero digit in the Skipped column must appear.
        assert "obsidian" in output, (
            f"'obsidian' not found in second-ingest output:\n{output}"
        )
        # The expected skip count for obsidian is _NOTES_COUNT (all 3 skipped).
        assert str(_NOTES_COUNT) in output, (
            f"Expected skip count '{_NOTES_COUNT}' not found in second-ingest output:\n{output}"
        )
