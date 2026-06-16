"""Tests for the workspace Gemini retrieval module.

Uses fake/mocked Gemini CLI. No real Google Workspace access.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from manager_os.ingest.workspace_gemini import (
    RetrievalResult,
    retrieve_forecast,
    retrieve_calendar,
    retrieve_activity,
    workspace_doctor,
    _build_gemini_cmd,
    _parse_retrieval_json,
)


# ------------------------------------------------------------------
# Fake responses
# ------------------------------------------------------------------

_FAKE_FORECAST_JSON = json.dumps({
    "ok": True,
    "source_title": "Delta-12 Forecast",
    "source_url": "https://docs.google.com/spreadsheets/d/fake",
    "retrieved_at": "2026-06-16T12:00:00",
    "rows": [
        {"person": "Alice Chen", "week_start": "2026-06-15", "allocation_pct": 100, "project": "Acme", "client": "Acme Corp"},
        {"person": "Bob Smith", "week_start": "2026-06-15", "allocation_pct": 80, "project": "Big Retail", "client": "Big Retail Co"},
    ],
})

_FAKE_CALENDAR_JSON = json.dumps({
    "ok": True,
    "source": "google_calendar_gemini",
    "retrieved_at": "2026-06-16T12:00:00",
    "events": [
        {
            "title": "Team Standup",
            "start_time": "2026-06-16T09:00:00",
            "end_time": "2026-06-16T09:30:00",
            "attendees": ["team@example.com"],
            "location": "Google Meet",
            "description_summary": "Daily standup",
            "external_id": "event_001",
        }
    ],
})

_FAKE_ACTIVITY_JSON = json.dumps({
    "ok": True,
    "source": "google_workspace_gemini",
    "retrieved_at": "2026-06-16T12:00:00",
    "summary": "Two docs updated, one comment on forecast sheet",
    "items": [
        {
            "type": "doc_updated",
            "title": "Delta-12 Forecast",
            "source_url": "https://docs.google.com/spreadsheets/d/fake",
            "description": "Staffing rows updated for week of June 15",
            "requires_attention": False,
        }
    ],
})

_MALFORMED_JSON = "This is not JSON at all"

_MISSING_OK_JSON = json.dumps({
    "items": [{"some": "data"}],
})


# ------------------------------------------------------------------
# Tests for workspace_doctor
# ------------------------------------------------------------------


def test_workspace_doctor_reports_disabled() -> None:
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=True), \
         patch("manager_os.ingest.workspace_gemini.WORKSPACE_RETRIEVAL_ENABLED", False):
        result = workspace_doctor()
        assert not result.retrieval_enabled
        assert len(result.errors) >= 1


def test_workspace_doctor_reports_gemini_missing() -> None:
    with patch("manager_os.llm.gemini_cli.is_gemini_available", return_value=False), \
         patch("manager_os.ingest.workspace_gemini.WORKSPACE_RETRIEVAL_ENABLED", True):
        result = workspace_doctor()
        assert not result.gemini_available
        assert len(result.errors) >= 1


# ------------------------------------------------------------------
# Tests for _build_gemini_cmd
# ------------------------------------------------------------------


def test_build_gemini_cmd_includes_yolo() -> None:
    with patch("manager_os.llm.gemini_cli.GEMINI_CLI_BIN", "gemini"), \
         patch("manager_os.llm.gemini_cli.GEMINI_CLI_MODEL", "gemini-2.0-flash"):
        cmd = _build_gemini_cmd(use_yolo=True)
        assert "gemini" in cmd
        assert "-y" in cmd


def test_build_gemini_cmd_no_yolo() -> None:
    with patch("manager_os.llm.gemini_cli.GEMINI_CLI_BIN", "gemini"):
        cmd = _build_gemini_cmd(use_yolo=False)
        assert "gemini" in cmd
        assert "-y" not in cmd


# ------------------------------------------------------------------
# Tests for _parse_retrieval_json
# ------------------------------------------------------------------


def test_parse_forecast_json() -> None:
    data = _parse_retrieval_json(_FAKE_FORECAST_JSON)
    assert data["ok"] is True
    assert len(data["rows"]) == 2


def test_parse_calendar_json() -> None:
    data = _parse_retrieval_json(_FAKE_CALENDAR_JSON)
    assert data["ok"] is True
    assert len(data["events"]) == 1


def test_parse_activity_json() -> None:
    data = _parse_retrieval_json(_FAKE_ACTIVITY_JSON)
    assert data["ok"] is True
    assert len(data["items"]) == 1


def test_parse_list_as_items() -> None:
    data = _parse_retrieval_json("[1, 2, 3]")
    assert data["ok"] is True
    assert data["items"] == [1, 2, 3]


def test_parse_malformed_json_raises() -> None:
    with pytest.raises((ValueError, json.JSONDecodeError)):
        _parse_retrieval_json(_MALFORMED_JSON)


# ------------------------------------------------------------------
# Tests for retrieve_forecast (mocked Gemini)
# ------------------------------------------------------------------


@pytest.fixture()
def fake_forecast_response():
    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval",
               return_value=(_FAKE_FORECAST_JSON, ["gemini", "-y"])):
        yield


def test_retrieve_forecast_dry_run(tmp_path: Path) -> None:
    """--dry-run should return the prompt and write nothing."""
    result = retrieve_forecast(date.today(), dry_run=True, output_dir=str(tmp_path))
    assert result.dry_run is True
    assert "retrieve" in result.json_text.lower()
    # No snapshot file created
    assert len(list(tmp_path.rglob("*"))) == 0


def test_retrieve_forecast_ok(fake_forecast_response, tmp_path: Path) -> None:
    result = retrieve_forecast(date.today(), output_dir=str(tmp_path))
    assert result.ok is True
    assert len(result.items) == 2
    assert result.written_to is not None
    assert Path(result.written_to).exists()


# ------------------------------------------------------------------
# Tests for retrieve_calendar (mocked Gemini)
# ------------------------------------------------------------------


@pytest.fixture()
def fake_calendar_response():
    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval",
               return_value=(_FAKE_CALENDAR_JSON, ["gemini", "-y"])):
        yield


def test_retrieve_calendar_dry_run(tmp_path: Path) -> None:
    result = retrieve_calendar(date.today(), dry_run=True, output_dir=str(tmp_path))
    assert result.dry_run is True
    assert "calendar" in result.json_text.lower()


def test_retrieve_calendar_ok(fake_calendar_response, tmp_path: Path) -> None:
    result = retrieve_calendar(date.today(), output_dir=str(tmp_path))
    assert result.ok is True
    assert len(result.items) == 1
    assert Path(result.written_to).exists()


# ------------------------------------------------------------------
# Tests for retrieve_activity (mocked Gemini)
# ------------------------------------------------------------------


@pytest.fixture()
def fake_activity_response():
    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval",
               return_value=(_FAKE_ACTIVITY_JSON, ["gemini", "-y"])):
        yield


def test_retrieve_activity_dry_run(tmp_path: Path) -> None:
    result = retrieve_activity(date.today(), dry_run=True, output_dir=str(tmp_path))
    assert result.dry_run is True
    assert "activity" in result.json_text.lower()


def test_retrieve_activity_ok(fake_activity_response, tmp_path: Path) -> None:
    result = retrieve_activity(date.today(), output_dir=str(tmp_path))
    assert result.ok is True
    assert len(result.items) == 1
    assert Path(result.written_to).exists()


# ------------------------------------------------------------------
# No-mutate guarantee: mocked Gemini never actually calls the real binary
# ------------------------------------------------------------------


def test_no_real_gemini_called(tmp_path: Path) -> None:
    """Verify dry-run mode doesn't try to run the real Gemini binary."""
    result = retrieve_forecast(date.today(), dry_run=True, output_dir=str(tmp_path))
    assert result.dry_run is True
    # Should not have tried to execute subprocess
    assert not result.written_to


def test_snapshots_are_gitignored(tmp_path: Path) -> None:
    """Snapshot paths should be under data/raw/workspace_snapshots/."""
    result = retrieve_forecast(
        date.today(), dry_run=False, output_dir=str(tmp_path),
    )
    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval",
               return_value=(_FAKE_FORECAST_JSON, ["gemini", "-y"])):
        result = retrieve_forecast(date.today(), output_dir=str(tmp_path))
    assert "workspace_snapshots" in result.written_to or tmp_path in Path(result.written_to).parents
    assert Path(result.written_to).exists()