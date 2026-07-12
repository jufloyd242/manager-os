"""Observability tests for the live `manager-os project-docs-fetch` path.

`execute_retrieval` is mocked in every test here — no live Gemini/Workspace calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from typer.testing import CliRunner

from manager_os.cli import app
from manager_os.db import get_connection
from manager_os.ingest.project_doc_retrieval import (
    RetrievalResult,
    RetrievalDiagnostics,
    RetrievalStatus,
    RankedCandidate,
)

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


def _make_success_result(docs: list[dict]) -> RetrievalResult:
    """Build a mock RetrievalResult with SUCCESS status."""
    candidates = [
        RankedCandidate(
            title=d["title"],
            url=d["url"],
            document_type=d.get("document_type", "other"),
            why_matched=d.get("why_matched", ""),
            source="google_drive",
            file_id=d.get("file_id", ""),
            confidence=d.get("confidence", 0.9),
            score=d.get("score", 80.0),
            match_reasons=d.get("match_reasons", ["exact OPP in title"]),
        )
        for d in docs
    ]
    return RetrievalResult(
        status=RetrievalStatus.SUCCESS,
        documents=candidates,
        diagnostics=RetrievalDiagnostics(
            canonical_opp="OPP0",
            search_variants=["OPP0"],
            query_attempts=[{"query_id": "stage1_opp_only", "stage": 1, "term": "OPP_ONLY", "query_type": "opp_only", "candidate_count": len(docs)}],
            deduplicated_count=len(docs),
            accepted_count=len(docs),
            rejected_count=0,
        ),
    )


def test_live_fetch_with_documents_prints_summary_and_persists(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_project(db_path)

    fake_result = _make_success_result([
        {"title": "SOW Doc", "url": "http://example.com/sow", "document_type": "sow", "confidence": 0.9},
    ])

    with patch(
        "manager_os.ingest.project_doc_retrieval.execute_retrieval",
        return_value=fake_result,
    ) as mock_exec:
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", "OPP0", "--limit", "3"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code == 0, result.output
    mock_exec.assert_called_once()
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

    fake_result = RetrievalResult(
        status=RetrievalStatus.ZERO_CANDIDATES,
        diagnostics=RetrievalDiagnostics(canonical_opp="OPP0", search_variants=["OPP0"]),
    )

    with patch(
        "manager_os.ingest.project_doc_retrieval.execute_retrieval",
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

    fake_result = RetrievalResult(
        status=RetrievalStatus.PROVIDER_UNAVAILABLE,
        error="Gemini CLI failed: boom",
        diagnostics=RetrievalDiagnostics(
            canonical_opp="OPP0",
            search_variants=["OPP0"],
            provider_errors=["Query stage1_opp_only: Gemini CLI failed: boom"],
        ),
    )

    with patch(
        "manager_os.ingest.project_doc_retrieval.execute_retrieval",
        return_value=fake_result,
    ):
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", "OPP0", "--verbose"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )

    assert result.exit_code != 0
    assert "Provider unavailable" in result.output


def test_project_docs_fetch_dry_run_concurrency_lock(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    _seed_project(db_path)

    # Hold an exclusive read-write lock on the database
    lock_conn = get_connection(db_path)

    try:
        # Invoke project-docs-fetch in dry-run mode
        result = runner.invoke(
            app,
            ["project-docs-fetch", "--opportunity-number", "OPP0", "--dry-run"],
            env={"MANAGER_OS_DB_PATH": db_path},
        )
    finally:
        lock_conn.close()

    assert result.exit_code == 0, result.output
    assert "Dry Run" in result.output
    assert "Query Plan" in result.output


def test_dry_run_shows_opp_first_query_plan(tmp_path):
    """Dry run must show OPP-first query plan with separate keyword queries."""
    db_path = str(tmp_path / "test.duckdb")
    _seed_project(db_path)

    result = runner.invoke(
        app,
        ["project-docs-fetch", "--opportunity-number", "OPP0", "--dry-run"],
        env={"MANAGER_OS_DB_PATH": db_path},
    )

    assert result.exit_code == 0, result.output
    # Stage 1 OPP_ONLY must appear first
    assert "opp_only" in result.output
    # Stage 2 keyword queries must appear
    assert "opp_plus_keyword" in result.output
    # Must show search variants
    assert "Search variants" in result.output
