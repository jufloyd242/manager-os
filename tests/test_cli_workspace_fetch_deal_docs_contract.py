"""Contract tests for `manager-os workspace-fetch-deal-docs`.

Confirms --opportunity-number is explicitly rejected/redirected rather than
silently treated as a raw --deal-id (the `deals` table has no
opportunity_number column, so that alias could silently match zero rows and
report success with zero work done).

No live Gemini/Workspace calls in any of these tests.
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from manager_os.cli import app

runner = CliRunner()


def test_opportunity_number_is_rejected_with_guidance(tmp_path):
    db_path = str(tmp_path / "test.duckdb")

    with patch("manager_os.ingest.drive_deal_docs.fetch_deal_docs") as mock_fetch:
        result = runner.invoke(
            app,
            ["workspace-fetch-deal-docs", "--opportunity-number", "OPP031267", "--dry-run"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code != 0
    assert "project-docs-fetch" in result.output
    assert "OPP031267" in result.output
    mock_fetch.assert_not_called()


def test_opportunity_number_and_deal_id_together_is_rejected(tmp_path):
    db_path = str(tmp_path / "test.duckdb")

    with patch("manager_os.ingest.drive_deal_docs.fetch_deal_docs") as mock_fetch:
        result = runner.invoke(
            app,
            [
                "workspace-fetch-deal-docs",
                "--deal-id", "DEAL123",
                "--opportunity-number", "OPP031267",
                "--dry-run",
            ],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code != 0
    assert "project-docs-fetch" in result.output
    mock_fetch.assert_not_called()


def test_deal_id_only_still_works_unchanged(tmp_path):
    db_path = str(tmp_path / "test.duckdb")

    result = runner.invoke(
        app,
        ["workspace-fetch-deal-docs", "--deal-id", "DEAL123", "--dry-run"],
        env={"MANAGER_OS_DB_PATH": db_path},
    )

    assert result.exit_code == 0, result.output
    assert "DEAL123" in result.output
