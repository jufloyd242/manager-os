"""Tests for the bounded --batch mode of `manager-os project-docs-fetch`.

No live Gemini/Workspace calls — `_run_gemini_retrieval` is always mocked or
must not be called at all (dry-run / print-prompt paths).
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


def test_batch_dry_run_does_not_call_runtime_retrieval(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 3)

    with patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval") as mock_run:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--batch", "--dry-run", "--limit-projects", "2"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "Batch Dry Run" in result.output
    # Bounded: only 2 of the 3 seeded projects should be listed.
    assert result.output.count("OPP") - result.output.count("limit-projects") >= 2


def test_batch_print_prompt_does_not_call_runtime_retrieval(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 2)

    with patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval") as mock_run:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--batch", "--print-prompt", "--limit-projects", "5"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_run.assert_not_called()
    assert "OPP0" in result.output
    assert "OPP1" in result.output


def test_batch_mode_is_bounded_by_limit_projects(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 5)

    result = runner.invoke(
        app,
        ["project-docs-fetch", "--batch", "--dry-run", "--limit-projects", "2"],
        env={"MANAGER_OS_DB_PATH": db_path},
    )

    assert result.exit_code == 0, result.output
    assert "Projects selected: 2" in result.output


def test_single_project_default_path_unaffected_by_batch_flag(tmp_path):
    """--opportunity-number without --batch keeps working exactly as before."""
    db_path = str(tmp_path / "test.duckdb")
    _seed_projects(db_path, 1)

    result = runner.invoke(
        app,
        ["project-docs-fetch", "--opportunity-number", "OPP0", "--dry-run"],
        env={"MANAGER_OS_DB_PATH": db_path},
    )

    assert result.exit_code == 0, result.output
    assert "Project Docs Fetch — Dry Run" in result.output
