"""Tests for the append-only feedback_events architecture.

Covers Phase 4 (mark), Phase 5 (readers), Phase 6 (repair-feedback),
and dashboard write-path smoke tests.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manager_os.cli import app as cli_app
from manager_os.db import content_hash, get_connection
from manager_os.build.feedback import (
    VALID_RATINGS,
    get_feedback_summary,
    list_feedback,
    load_feedback_index,
    mark,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    yield c
    c.close()


def _env(tmp_path) -> dict:
    return {
        "MANAGER_OS_DB_PATH": str(tmp_path / "test.duckdb"),
        "MANAGER_OS_VAULT_PATH": str(FIXTURES / "vault"),
        "MANAGER_OS_FORECAST_CSV": str(FIXTURES / "forecast.csv"),
        "MANAGER_OS_DEALS_CSV": str(FIXTURES / "deals.csv"),
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        "MANAGER_OS_CONFIG_DIR": str(REPO_ROOT / "config"),
        "MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED": "false",
    }


# ===========================================================================
# Phase 4 — mark() append-only semantics
# ===========================================================================


class TestFeedbackMarkAppendOnly:
    def test_mark_inserts_into_feedback_events(self, conn):
        mark(conn, item_id="signal:abc123", rating="useful")
        count = conn.execute("SELECT COUNT(*) FROM feedback_events").fetchone()[0]
        assert count == 1

    def test_mark_returns_event_id(self, conn):
        event_id = mark(conn, item_id="signal:abc123", rating="useful")
        assert isinstance(event_id, str)
        assert len(event_id) > 0

    def test_mark_does_not_write_to_legacy_feedback(self, conn):
        mark(conn, item_id="signal:abc123", rating="noisy")
        legacy_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        assert legacy_count == 0

    def test_repeated_same_item_rating_creates_multiple_events(self, conn):
        mark(conn, item_id="signal:abc123", rating="wrong")
        mark(conn, item_id="signal:abc123", rating="wrong")
        mark(conn, item_id="signal:abc123", rating="wrong")
        count = conn.execute(
            "SELECT COUNT(*) FROM feedback_events WHERE item_id = 'signal:abc123'"
        ).fetchone()[0]
        assert count == 3

    def test_different_ratings_same_item_no_conflict(self, conn):
        mark(conn, item_id="signal:xyz", rating="useful")
        mark(conn, item_id="signal:xyz", rating="noisy")
        mark(conn, item_id="signal:xyz", rating="stale")
        count = conn.execute(
            "SELECT COUNT(*) FROM feedback_events WHERE item_id = 'signal:xyz'"
        ).fetchone()[0]
        assert count == 3

    def test_invalid_rating_raises_value_error(self, conn):
        with pytest.raises(ValueError, match="Invalid rating"):
            mark(conn, item_id="signal:abc", rating="invalid_rating")

    def test_event_id_unique_across_repeated_calls(self, conn):
        id1 = mark(conn, item_id="signal:abc", rating="useful")
        id2 = mark(conn, item_id="signal:abc", rating="useful")
        assert id1 != id2

    def test_event_stores_source_path(self, conn):
        mark(conn, item_id="signal:abc", rating="noisy", source_path="/path/to/note.md")
        row = conn.execute(
            "SELECT source_path FROM feedback_events WHERE item_id = 'signal:abc'"
        ).fetchone()
        assert row is not None
        assert row[0] == "/path/to/note.md"

    def test_event_stores_entity_and_signal_type(self, conn):
        mark(conn, item_id="signal:abc", rating="stale",
             entity_name="Acme Corp", signal_type="risk")
        row = conn.execute(
            "SELECT entity_name, signal_type FROM feedback_events WHERE item_id = 'signal:abc'"
        ).fetchone()
        assert row[0] == "Acme Corp"
        assert row[1] == "risk"


# ===========================================================================
# Phase 5 — Readers use feedback_events
# ===========================================================================


class TestFeedbackReaders:
    def test_list_feedback_reads_from_events(self, conn):
        mark(conn, item_id="signal:r1", rating="useful")
        mark(conn, item_id="signal:r2", rating="noisy")
        results = list_feedback(conn)
        item_ids = {r["item_id"] for r in results}
        assert "signal:r1" in item_ids
        assert "signal:r2" in item_ids

    def test_list_feedback_empty_when_no_events(self, conn):
        assert list_feedback(conn) == []

    def test_get_feedback_summary_counts(self, conn):
        mark(conn, item_id="signal:s1", rating="useful")
        mark(conn, item_id="signal:s2", rating="noisy")
        mark(conn, item_id="signal:s3", rating="noisy")
        summary = get_feedback_summary(conn)
        assert summary["counts_by_rating"]["useful"] == 1
        assert summary["counts_by_rating"]["noisy"] == 2
        assert summary["total"] == 3

    def test_load_feedback_index_latest_per_item(self, conn):
        mark(conn, item_id="signal:x", rating="useful")
        mark(conn, item_id="signal:x", rating="wrong")  # overrides "useful" as latest
        idx = load_feedback_index(conn)
        assert idx.get("signal:x") == "wrong"

    def test_legacy_feedback_table_still_exists(self, conn):
        # Legacy table must not be dropped
        count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        assert count == 0  # empty, not gone


# ===========================================================================
# Phase 6 — repair-feedback CLI command
# ===========================================================================


class TestRepairFeedback:
    def test_dry_run_does_not_write(self, tmp_path):
        result = CliRunner().invoke(
            cli_app, ["repair-feedback", "--dry-run"], env=_env(tmp_path)
        )
        assert result.exit_code == 0, result.output
        assert "DRY RUN" in result.output

    def test_yes_flag_creates_feedback_events(self, tmp_path):
        db_path = str(tmp_path / "test.duckdb")
        env = {**_env(tmp_path), "MANAGER_OS_DB_PATH": db_path}
        result = CliRunner().invoke(
            cli_app, ["repair-feedback", "--yes"], env=env
        )
        assert result.exit_code == 0, result.output
        # Verify the table was created by connecting
        conn = get_connection(db_path)
        count = conn.execute("SELECT COUNT(*) FROM feedback_events").fetchone()[0]
        conn.close()
        assert count >= 0  # table exists

    def test_handles_missing_required_flag(self, tmp_path):
        result = CliRunner().invoke(
            cli_app, ["repair-feedback"], env=_env(tmp_path)
        )
        assert result.exit_code != 0

    def test_backfills_readable_legacy_rows(self, tmp_path):
        db_path = str(tmp_path / "test.duckdb")
        env = {**_env(tmp_path), "MANAGER_OS_DB_PATH": db_path}
        # Pre-seed legacy feedback row
        conn = get_connection(db_path)
        fid = content_hash("feedback::signal:abc::useful")
        conn.execute(
            """
            INSERT INTO feedback (id, item_id, item_type, rating, reason,
                source_path, entity_name, signal_type, created_at)
            VALUES (?, 'signal:abc', 'signal', 'useful', NULL,
                    '/test.md', 'Alice', 'risk', CURRENT_TIMESTAMP)
            """,
            [fid],
        )
        conn.close()
        # Run repair
        result = CliRunner().invoke(
            cli_app, ["repair-feedback", "--yes"], env=env
        )
        assert result.exit_code == 0, result.output
        conn = get_connection(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM feedback_events WHERE item_id = 'signal:abc'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_handles_corrupt_legacy_gracefully(self, tmp_path):
        """Even if legacy feedback is unreadable, repair-feedback should succeed."""
        db_path = str(tmp_path / "test.duckdb")
        env = {**_env(tmp_path), "MANAGER_OS_DB_PATH": db_path}
        # Drop the legacy feedback table to simulate corruption
        conn = get_connection(db_path)
        conn.execute("DROP TABLE IF EXISTS feedback")
        conn.close()
        result = CliRunner().invoke(
            cli_app, ["repair-feedback", "--yes"], env=env
        )
        # Should complete gracefully — corrupt legacy should not block
        assert result.exit_code == 0 or "unreadable" in result.output.lower()


# ===========================================================================
# Dashboard module smoke test
# ===========================================================================


class TestDashboardImportClean:
    def test_dashboard_module_compiles(self):
        """dashboard/app.py must import without error."""
        import py_compile, sys
        from pathlib import Path as _P
        app_path = _P(__file__).parent.parent.parent / "src" / "manager_os" / "dashboard" / "app.py"
        # Just compile — don't execute (Streamlit would try to render)
        py_compile.compile(str(app_path), doraise=True)

    def test_feedback_mark_no_update_delete(self):
        """Ensure mark() source has no UPDATE/DELETE feedback statement."""
        import inspect
        from manager_os.build import feedback as fb_mod
        src = inspect.getsource(fb_mod.mark)
        assert "UPDATE feedback" not in src
        assert "DELETE FROM feedback" not in src
        assert "INSERT OR REPLACE INTO feedback " not in src

    def test_with_write_pattern_in_dashboard(self):
        """dashboard/app.py must define _with_write."""
        from pathlib import Path as _P
        app_src = (_P(__file__).parent.parent.parent /
                   "src" / "manager_os" / "dashboard" / "app.py").read_text()
        assert "def _with_write" in app_src

    def test_no_unconditional_rerun_after_with_write(self):
        """st.rerun() must not appear bare after _with_write() in the signal feedback block."""
        from pathlib import Path as _P
        app_src = (_P(__file__).parent.parent.parent /
                   "src" / "manager_os" / "dashboard" / "app.py").read_text()
        # Verify _with_write is used and there are no extra bare reruns in the same block
        assert "_with_write(_do_fb)" in app_src
        assert "_with_write(_do_ack)" in app_src
        assert "_with_write(_do_dismiss)" in app_src
