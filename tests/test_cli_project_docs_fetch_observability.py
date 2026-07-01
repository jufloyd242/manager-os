"""Observability tests for the live `manager-os project-docs-fetch` path.

`search_drive_for_project_docs` is mocked in every test here — no live
Gemini/Workspace calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from manager_os.cli import app
from manager_os.db import get_connection
from manager_os.ingest.project_drive_docs import DriveSearchResult, ProjectDocument

runner = CliRunner()


def _seed_project(db_path: str) -> None:
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ["project::OPP0", "Project Zero", "Client Zero", "OPP0", now, now],
    )
    conn.close()


def test_live_fetch_with_documents_prints_summary_and_persists(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_project(db_path)

    fake_result = DriveSearchResult(
        documents=[
            ProjectDocument(
                project_id="",
                opportunity_number="OPP0",
                client="Client Zero",
                project_name="Project Zero",
                document_type="sow",
                title="SOW Doc",
                url="http://example.com/sow",
                retrieved_at="2026-06-30T00:00:00",
                confidence=0.9,
            )
        ]
    )

    with patch(
        "manager_os.ingest.project_drive_docs.search_drive_for_project_docs",
        return_value=fake_result,
    ) as mock_search:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", "OPP0", "--limit", "3"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_search.assert_called_once()
    assert "OPP0" in result.output
    assert "Client Zero" in result.output
    assert "Found 1 document" in result.output
    assert "Inserted" in result.output

    conn = get_connection(db_path)
    count = conn.execute("SELECT COUNT(*) FROM project_documents WHERE opportunity_number = 'OPP0'").fetchone()[0]
    conn.close()
    assert count == 1


def test_live_fetch_no_documents_prints_message_and_exits_zero(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_project(db_path)

    fake_result = DriveSearchResult(documents=[], errors=[], warnings=[])

    with patch(
        "manager_os.ingest.project_drive_docs.search_drive_for_project_docs",
        return_value=fake_result,
    ):
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", "OPP0"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    assert "No documents found" in result.output


def test_live_fetch_errors_are_printed_and_exit_nonzero(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_project(db_path)

    fake_result = DriveSearchResult(documents=[], errors=["Gemini CLI failed: boom"], warnings=[])

    with patch(
        "manager_os.ingest.project_drive_docs.search_drive_for_project_docs",
        return_value=fake_result,
    ):
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", "OPP0"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code != 0
    assert "boom" in result.output
