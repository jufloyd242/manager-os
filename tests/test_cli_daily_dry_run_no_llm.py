"""Regression tests: `manager-os daily --dry-run` must never make live
LLM/Gemini/Workspace calls, and must be bounded (exit promptly).

Root cause fixed: `_do_dry_run_extract` (Phase 3 preview) previously called
the *real* `run_llm_extraction` whenever extract mode was "llm"/"both" (the
default), which shells out to Gemini CLI live even during --dry-run.
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


def _seed_note(db_path: str) -> None:
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type, entity_name, title, body, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["note1", "doc1", TARGET_DATE, "1on1", "person", "Seed Person", "Seed", "Seed body about a risk.", "[]", now],
    )
    conn.close()


def _invoke(db_path: str, *extra_args: str):
    args = [
        "daily", "--dry-run", "--no-workspace", "--skip-project-index",
        "--date", TARGET_DATE.isoformat(),
        *extra_args,
    ]
    with patch("manager_os.extract.llm_signals.run_llm_extraction") as mock_llm, \
         patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_ws:
        result = runner.invoke(app, args, env=_env(db_path))
    return result, mock_llm, mock_ws


# 1 & 2 & 3: exits 0 quickly, no LLM/Gemini/Workspace calls, default mode ("both")
def test_dry_run_exits_0_and_makes_no_llm_or_workspace_calls(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_note(db_path)

    result, mock_llm, mock_ws = _invoke(db_path)

    assert result.exit_code == 0, result.output
    mock_llm.assert_not_called()
    mock_ws.assert_not_called()


# 4: dry-run either skips extraction or runs rules-only (never real LLM output)
def test_dry_run_shows_rules_step_without_calling_llm(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_note(db_path)

    result, mock_llm, mock_ws = _invoke(db_path)

    assert result.exit_code == 0, result.output
    mock_llm.assert_not_called()
    assert "signals (rules)" in result.output


# 5: malformed/unavailable LLM candidate selection must warn, not hang/crash
def test_dry_run_llm_preview_handles_candidate_selection_failure_gracefully(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_note(db_path)

    with patch(
        "manager_os.extract.llm_signals._select_llm_candidates",
        side_effect=RuntimeError("boom"),
    ), patch("manager_os.extract.llm_signals.run_llm_extraction") as mock_llm:
        args = [
            "daily", "--dry-run", "--no-workspace", "--skip-project-index",
            "--date", TARGET_DATE.isoformat(),
        ]
        result = runner.invoke(app, args, env=_env(db_path))

    assert result.exit_code == 0, result.output
    mock_llm.assert_not_called()


# 6: --skip-extract bypasses Phase 3 entirely
def test_skip_extract_bypasses_phase_3(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_note(db_path)

    result, mock_llm, mock_ws = _invoke(db_path, "--skip-extract")

    assert result.exit_code == 0, result.output
    mock_llm.assert_not_called()
    mock_ws.assert_not_called()
    assert "Phase 3: Extract — Skipped" in result.output


# 7: --rules-only never calls the LLM path
def test_rules_only_never_calls_llm(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_note(db_path)

    result, mock_llm, mock_ws = _invoke(db_path, "--rules-only")

    assert result.exit_code == 0, result.output
    mock_llm.assert_not_called()
    mock_ws.assert_not_called()
