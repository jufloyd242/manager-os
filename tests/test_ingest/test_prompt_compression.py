"""Tests proving compressed Gemini CLI prompt templates stay parser-compatible.

All Gemini CLI calls are mocked. No live Gemini/Workspace/Drive access ever
happens in this file.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

import pytest

from manager_os.ingest.workspace_gemini import (
    retrieve_forecast,
    retrieve_calendar,
    retrieve_activity,
)


# ------------------------------------------------------------------
# Task 1: workspace_gemini.py prompt compression
# ------------------------------------------------------------------


@patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval")
def test_forecast_compressed_json_parses_and_prompt_budget(mock_run):
    mock_run.return_value = (
        json.dumps(
            {
                "ok": True,
                "source_title": "AI/ML",
                "source_url": "https://docs.google.com/x",
                "retrieved_at": "2026-06-18T00:00:00Z",
                "rows": [
                    {
                        "person": "Alice",
                        "week_start": "2026-06-15",
                        "allocation_pct": 100,
                        "project": "X",
                        "client": "Y",
                    }
                ],
            }
        ),
        "cmd",
    )

    result = retrieve_forecast(
        target_date=date(2026, 6, 18),
        dry_run=False,
        output_dir="/tmp",
    )

    assert result.ok is True
    assert len(result.items) == 1
    assert result.items[0]["person"] == "Alice"

    dry_result = retrieve_forecast(target_date=date(2026, 6, 18), dry_run=True)

    # Budget is a regression guard: the live prompt now only pays for
    # _READ_ONLY_PREFIX once (previously double-injected), and the dry-run
    # preview is composed identically to the real live prompt so it stays
    # representative of what is actually sent.
    assert len(dry_result.json_text) < 700
    assert "source_url: string (Google Sheets URL" not in dry_result.json_text
    assert "ONLY JSON" in dry_result.json_text or "Return ONLY" in dry_result.json_text


@patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval")
def test_calendar_compressed_json_parses_and_prompt_budget(mock_run):
    mock_run.return_value = (
        json.dumps(
            {
                "ok": True,
                "source": "google_calendar_gemini",
                "retrieved_at": "2026-06-18T00:00:00Z",
                "events": [
                    {
                        "title": "Standup",
                        "start_time": "2026-06-18T09:00:00",
                        "end_time": "2026-06-18T09:30:00",
                        "attendees": ["team@example.com"],
                        "location": "Google Meet",
                        "description_summary": "Daily standup",
                        "external_id": "evt_1",
                    }
                ],
            }
        ),
        "cmd",
    )

    result = retrieve_calendar(target_date=date(2026, 6, 18), dry_run=False)

    assert result.ok is True
    assert len(result.items) == 1
    assert result.items[0]["title"] == "Standup"

    dry_result = retrieve_calendar(target_date=date(2026, 6, 18), dry_run=True)

    assert len(dry_result.json_text) < 750
    assert "description_summary: string (brief, max 200 chars)" not in dry_result.json_text
    assert "ONLY JSON" in dry_result.json_text or "Return ONLY" in dry_result.json_text


@patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval")
def test_activity_compressed_json_parses_and_prompt_budget(mock_run):
    mock_run.return_value = (
        json.dumps(
            {
                "ok": True,
                "source": "google_chat_activity_summary",
                "source_url": "https://chat.google.com/x",
                "retrieved_at": "2026-06-18T00:00:00Z",
                "summary_date": "2026-06-18",
                "summary": "All quiet",
                "items": [
                    {
                        "type": "doc_update",
                        "title": "Forecast updated",
                        "description": "Rows refreshed",
                        "source_url": "https://docs.google.com/x",
                        "requires_attention": False,
                        "assigned_to": "unknown",
                        "due_date": None,
                        "entity_type": "workspace",
                        "entity_name": "Forecast",
                        "confidence": 0.9,
                    }
                ],
                "action_items": [],
            }
        ),
        "cmd",
    )

    result = retrieve_activity(
        target_date=date(2026, 6, 18),
        dry_run=False,
        chat_url="https://chat.google.com/u/0/app/chat/X",
    )

    assert result.ok is True
    assert len(result.items) == 1

    dry_result = retrieve_activity(
        target_date=date(2026, 6, 18),
        dry_run=True,
        chat_url="https://chat.google.com/u/0/app/chat/X",
    )

    # Required for tests/test_ingest/test_workspace_activity_chat.py to keep passing.
    assert "Open this Google Chat space/app URL:" in dry_result.json_text
    assert "Do not send, edit, delete, or modify" in dry_result.json_text
    assert "read-only mode" in dry_result.json_text.lower()

    assert len(dry_result.json_text) < 1000
    assert "ONLY JSON" in dry_result.json_text or "Return ONLY" in dry_result.json_text


# ------------------------------------------------------------------
# Task 2: project_drive_docs.py _build_drive_search_prompt compression
# ------------------------------------------------------------------


def test_drive_search_prompt_is_compressed_and_safe():
    from manager_os.ingest.project_drive_docs import _build_drive_search_prompt

    prompt = _build_drive_search_prompt("OPP123", "Acme", "Project X")

    assert len(prompt) < 700
    assert "OPP123" in prompt
    assert "Acme" in prompt
    assert "Project X" in prompt
    assert "read-only" in prompt.lower()
    assert "metadata" in prompt.lower()
    assert "ONLY JSON" in prompt or "Return ONLY" in prompt
    assert "Return metadata only. Do not download full documents." not in prompt


# ------------------------------------------------------------------
# Task 3: batch_search_drive_for_projects
# ------------------------------------------------------------------


def test_batch_search_empty_projects_returns_empty_dict():
    from manager_os.ingest.project_drive_docs import batch_search_drive_for_projects

    with patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval") as mock_run:
        result = batch_search_drive_for_projects([], dry_run=False)

    assert result == {}
    mock_run.assert_not_called()


@patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval")
def test_batch_search_dry_run_does_not_call_runtime_retrieval(mock_run):
    from manager_os.ingest.project_drive_docs import batch_search_drive_for_projects

    results = batch_search_drive_for_projects(
        [{"opportunity_number": "OPP1", "client": "C1", "project_name": "P1"}],
        dry_run=True,
    )

    mock_run.assert_not_called()
    assert set(results.keys()) == {"OPP1"}
    assert results["OPP1"].documents == []
    assert any("dry" in w.lower() for w in results["OPP1"].warnings)


@patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval")
def test_batch_search_drive_for_projects_parses_results(mock_run):
    from manager_os.ingest.project_drive_docs import batch_search_drive_for_projects

    mock_run.return_value = (
        json.dumps(
            {
                "ok": True,
                "retrieved_at": "2026-06-18T00:00:00Z",
                "results": {
                    "OPP1": [
                        {
                            "document_type": "sow",
                            "title": "SOW1",
                            "url": "http://1",
                            "confidence": 0.9,
                            "why_matched": "matched",
                        }
                    ],
                    "OPP2": [],
                },
            }
        ),
        "cmd",
    )

    projects = [
        {"opportunity_number": "OPP1", "client": "C1", "project_name": "P1"},
        {"opportunity_number": "OPP2", "client": "C2", "project_name": "P2"},
    ]

    results = batch_search_drive_for_projects(projects, dry_run=False)

    assert set(results.keys()) == {"OPP1", "OPP2"}
    assert len(results["OPP1"].documents) == 1
    assert results["OPP1"].documents[0].document_type == "sow"
    assert len(results["OPP2"].documents) == 0
    mock_run.assert_called_once()


@patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval")
def test_batch_search_missing_opp_in_response_returns_empty_with_warning(mock_run):
    from manager_os.ingest.project_drive_docs import batch_search_drive_for_projects

    mock_run.return_value = (
        json.dumps(
            {
                "ok": True,
                "retrieved_at": "2026-06-18T00:00:00Z",
                "results": {"OPP1": []},
            }
        ),
        "cmd",
    )

    projects = [
        {"opportunity_number": "OPP1", "client": "C1", "project_name": "P1"},
        {"opportunity_number": "OPP2", "client": "C2", "project_name": "P2"},
    ]

    results = batch_search_drive_for_projects(projects, dry_run=False)

    assert set(results.keys()) == {"OPP1", "OPP2"}
    assert results["OPP2"].documents == []
    assert any("omitted" in w.lower() or "missing" in w.lower() for w in results["OPP2"].warnings)


@patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval")
def test_batch_search_chunks_by_batch_size(mock_run):
    from manager_os.ingest.project_drive_docs import batch_search_drive_for_projects

    mock_run.return_value = (
        json.dumps({"ok": True, "retrieved_at": "2026-06-18T00:00:00Z", "results": {}}),
        "cmd",
    )

    projects = [
        {"opportunity_number": f"OPP{i}", "client": "C", "project_name": "P"}
        for i in range(3)
    ]

    results = batch_search_drive_for_projects(projects, batch_size=2, dry_run=False)

    assert mock_run.call_count == 2
    assert set(results.keys()) == {"OPP0", "OPP1", "OPP2"}


@patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval")
def test_batch_search_does_not_swallow_parse_errors(mock_run):
    from manager_os.ingest.project_drive_docs import batch_search_drive_for_projects

    mock_run.return_value = ("not json", "cmd")

    with pytest.raises(Exception):
        batch_search_drive_for_projects(
            [{"opportunity_number": "OPP1", "client": "C1", "project_name": "P1"}],
            dry_run=False,
        )


@patch("manager_os.ingest.project_drive_docs._run_gemini_retrieval")
def test_batch_search_raises_on_ok_false(mock_run):
    from manager_os.ingest.project_drive_docs import batch_search_drive_for_projects

    mock_run.return_value = (
        json.dumps({"ok": False, "error": "boom", "results": {}}),
        "cmd",
    )

    with pytest.raises(RuntimeError):
        batch_search_drive_for_projects(
            [{"opportunity_number": "OPP1", "client": "C1", "project_name": "P1"}],
            dry_run=False,
        )
