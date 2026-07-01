"""Contract tests for the Daily Operating Loop sections of `manager-os daily`.

Deterministic, local-DB-only. No live Gemini/Workspace/Drive/Calendar/Chat/
Sheets/OpenAI calls are made or allowed in any test here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from manager_os.cli import app
from manager_os.db import get_connection

runner = CliRunner()
REPO_ROOT = Path(__file__).parent.parent
TARGET_DATE = date(2026, 6, 29)


def _env(db_path: str) -> dict[str, str]:
    return {
        "MANAGER_OS_DB_PATH": db_path,
        "MANAGER_OS_CONFIG_DIR": str(REPO_ROOT / "config"),
        "MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED": "false",
        "MANAGER_OS_FORECAST_SOURCE": "local_csv",
    }


def _run(db_path: str, *extra_args: str):
    args = [
        "daily", "--dry-run", "--no-workspace", "--skip-project-index",
        "--date", TARGET_DATE.isoformat(),
        *extra_args,
    ]
    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        result = runner.invoke(app, args, env=_env(db_path))
    return result, mock_run


def _seed_baseline_note(conn) -> None:
    """The existing extract dry-run preview requires >=1 note to proceed;
    seed one trivial note so it doesn't short-circuit with exit 1 in tests
    that only care about the Daily Operating Loop sections."""
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type, entity_name, title, body, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["note1", "doc1", TARGET_DATE, "1on1", "person", "Seed Person", "Seed", "Seed body", "[]", now],
    )


# ------------------------------------------------------------------
# Test 1: baseline contract — sections present, no live calls
# ------------------------------------------------------------------


def test_daily_operating_loop_sections_present_no_live_calls(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    conn.close()

    result, mock_run = _run(db_path)

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()

    out = result.output
    assert "Daily Operating Loop" in out
    assert "People / Staffing" in out
    assert "Meetings" in out
    assert "Projects / Deals" in out
    assert "Document Gaps" in out or "Project Documents" in out
    assert "Recommended Actions" in out


# ------------------------------------------------------------------
# Test 2: over-allocated person surfaces with pct + reason
# ------------------------------------------------------------------


def test_daily_shows_overallocated_person(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO staffing_forecast
            (id, person_id, person_name, week_start, client, project, allocation_pct, forecast_type, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["fc1", "", "Alice Chen", TARGET_DATE, "Acme Corp", "Platform", 120.0, "confirmed", now],
    )
    conn.close()

    result, mock_run = _run(db_path)

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "Alice Chen" in result.output
    assert "120" in result.output
    assert "overallocated" in result.output.lower()


# ------------------------------------------------------------------
# Test 3: meeting without prep surfaces with title + start time + reason
# ------------------------------------------------------------------


def test_daily_shows_meeting_needing_prep(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["mtg1", TARGET_DATE, "10:00", "Client Sync", '["alice@example.com"]', "[]", "test", "", now],
    )
    conn.close()

    result, mock_run = _run(db_path)

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "Client Sync" in result.output
    assert "10:00" in result.output
    assert "prep" in result.output.lower()


# ------------------------------------------------------------------
# Test 4: project with no documents surfaces a fetch suggestion
# ------------------------------------------------------------------


def test_daily_shows_document_gap_with_fetch_suggestion(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ["project::OPP9", "No Docs Project", "NoDocs Client", "OPP9", now, now],
    )
    conn.close()

    result, mock_run = _run(db_path)

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "OPP9" in result.output
    assert "NoDocs Client" in result.output
    assert "project-docs-fetch" in result.output
    assert "--opportunity-number" in result.output
    assert "--dry-run" in result.output


# ------------------------------------------------------------------
# Test 5: feedback learning candidate surfaces in a learning summary
# ------------------------------------------------------------------


def test_daily_shows_feedback_learning_summary(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO feedback_learning_candidates
            (id, pattern_type, source_path, entity_name, signal_type, rating, event_count,
             example_item_ids, suggested_action, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "flc1", "noisy_signal_type", "", "Acme Corp", "risk", "noisy", 5,
            "[]", "Consider suppressing 'risk' signals for Acme Corp", "pending", now, now,
        ],
    )
    conn.close()

    result, mock_run = _run(db_path)

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "Acme Corp" in result.output
    assert "noisy" in result.output.lower()
