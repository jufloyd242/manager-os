"""Contract tests locking in `manager-os project-docs-fetch` as the primary
project-document fetch command.

No live Gemini/Workspace calls in any of these tests — dry-run/print-prompt
paths must never reach subprocess/runtime retrieval, and even live-mode tests
mock the retrieval layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from manager_os.cli import app
from manager_os.db import get_connection

runner = CliRunner()


def _seed_projects(db_path: str, count: int) -> None:
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    for i in range(count):
        conn.execute(
            """
            INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [f"project::OPP{i}", f"Project {i}", f"Client {i}", f"OPP{i}", now, now],
        )
    conn.close()


# ------------------------------------------------------------------
# Single-project dry-run
# ------------------------------------------------------------------


def test_dry_run_resolves_project_and_prints_details(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 1)

    with patch("manager_os.ingest.project_drive_docs.subprocess.run") as mock_run:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", "OPP0", "--dry-run"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "project::OPP0" in result.output
    assert "OPP0" in result.output
    assert "Project 0" in result.output
    assert "Client 0" in result.output
    assert "Limit" in result.output
    assert "Timeout" in result.output


def test_dry_run_normalizes_whitespace_and_case(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 1)

    with patch("manager_os.ingest.project_drive_docs.subprocess.run") as mock_run:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", " opp0 ", "--dry-run"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "OPP0" in result.output


def test_dry_run_missing_project_exits_nonzero_with_diagnostics(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 1)

    result = runner.invoke(
        app,
        ["project-docs-fetch", "--opportunity-number", "OPP_DOES_NOT_EXIST", "--dry-run"],
        env={"MANAGER_OS_DB_PATH": db_path},
    )

    assert result.exit_code != 0
    assert "not found" in result.output.lower()
    assert "Total projects in index" in result.output
    assert "search-projects" in result.output
    assert "index-projects" in result.output


# ------------------------------------------------------------------
# Single-project print-prompt
# ------------------------------------------------------------------


def test_print_prompt_prints_exact_prompt_no_live_call(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 1)

    with patch("manager_os.ingest.project_drive_docs.subprocess.run") as mock_run:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", "OPP0", "--print-prompt"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "read-only" in result.output.lower()
    assert "metadata" in result.output.lower()
    assert "ONLY JSON" in result.output or "Return ONLY" in result.output


# ------------------------------------------------------------------
# Batch dry-run / print-prompt
# ------------------------------------------------------------------


def test_batch_dry_run_selects_exact_limit_no_live_call(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 5)

    with patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval") as mock_run:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--batch", "--dry-run", "--limit-projects", "2"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "Projects selected: 2" in result.output


def test_batch_print_prompt_no_live_call(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 5)

    with patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval") as mock_run:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--batch", "--print-prompt", "--limit-projects", "2"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "Batch Prompt" in result.output
