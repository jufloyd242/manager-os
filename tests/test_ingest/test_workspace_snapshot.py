"""Tests for workspace snapshot ingestion into DuckDB.

Uses fake JSON snapshots written to tmp directories — no real Gemini calls.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from manager_os.db import get_connection
from manager_os.ingest.workspace_snapshot import (
    IngestResult,
    _snapshot_path,
    _snapshot_exists,
    _read_snapshot,
    ingest_workspace_forecast_snapshot,
    ingest_workspace_calendar_snapshot,
    ingest_workspace_activity_snapshot,
)

TODAY = date.today()


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def snapshot_dir(tmp_path: Path):
    """Provide a tmp-based snapshot directory."""
    snap_dir = tmp_path / "workspace_snapshots"
    snap_dir.mkdir()
    return snap_dir


# ------------------------------------------------------------------
# _read_snapshot
# ------------------------------------------------------------------


def test_read_snapshot_valid(snapshot_dir: Path) -> None:
    path = snapshot_dir / "forecast" / f"{TODAY.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ok": True, "rows": []}))
    data = _read_snapshot(path)
    assert data is not None
    assert data["ok"] is True


def test_read_snapshot_missing(snapshot_dir: Path) -> None:
    path = snapshot_dir / "missing.json"
    data = _read_snapshot(path)
    assert data is None


def test_read_snapshot_malformed(snapshot_dir: Path) -> None:
    path = snapshot_dir / "bad.json"
    path.write_text("not json")
    data = _read_snapshot(path)
    assert data is None


def test_snapshot_exists(snapshot_dir: Path) -> None:
    subdir = "forecast"
    (snapshot_dir / subdir).mkdir(parents=True, exist_ok=True)
    (snapshot_dir / subdir / f"{TODAY.isoformat()}.json").write_text("{}")
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=snapshot_dir / subdir / f"{TODAY.isoformat()}.json"):
        assert _snapshot_exists(subdir, TODAY) is True


# ------------------------------------------------------------------
# Forecast snapshot ingestion
# ------------------------------------------------------------------


def _write_forecast_snapshot(snapshot_dir: Path, data: dict) -> Path:
    sub = snapshot_dir / "forecast"
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / f"{TODAY.isoformat()}.json"
    path.write_text(json.dumps(data))
    return path


def test_forecast_snapshot_ingests_rows(conn, snapshot_dir: Path) -> None:
    _write_forecast_snapshot(snapshot_dir, {
        "ok": True,
        "source_title": "Test Forecast",
        "rows": [
            {"person": "Alice Chen", "week_start": "2026-06-15", "allocation_pct": 100,
             "project": "Acme", "client": "Acme Corp"},
            {"person": "Bob Smith", "week_start": "2026-06-15", "allocation_pct": 80,
             "project": "Big Retail", "client": "Big Retail Co"},
        ],
    })
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=snapshot_dir / "forecast" / f"{TODAY.isoformat()}.json"):
        r = ingest_workspace_forecast_snapshot(conn, TODAY)
    assert r.ingested == 2
    assert r.failed == 0
    rows = conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0]
    assert rows == 2


def test_forecast_snapshot_no_snapshot(conn, snapshot_dir: Path) -> None:
    path = snapshot_dir / "forecast" / f"{TODAY.isoformat()}.json"
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=path):
        r = ingest_workspace_forecast_snapshot(conn, TODAY)
    assert r.ingested == 0
    assert len(r.errors) >= 1


def test_forecast_snapshot_empty_rows(conn, snapshot_dir: Path) -> None:
    _write_forecast_snapshot(snapshot_dir, {"ok": True, "rows": []})
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=snapshot_dir / "forecast" / f"{TODAY.isoformat()}.json"):
        r = ingest_workspace_forecast_snapshot(conn, TODAY)
    assert r.ingested == 0
    assert len(r.errors) >= 1


def test_forecast_snapshot_idempotent(conn, snapshot_dir: Path) -> None:
    _write_forecast_snapshot(snapshot_dir, {
        "ok": True,
        "rows": [
            {"person": "Alice Chen", "week_start": "2026-06-15", "allocation_pct": 100,
             "project": "Acme", "client": "Acme Corp"},
        ],
    })
    p = snapshot_dir / "forecast" / f"{TODAY.isoformat()}.json"
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=p):
        ingest_workspace_forecast_snapshot(conn, TODAY)
        r2 = ingest_workspace_forecast_snapshot(conn, TODAY)
    assert r2.skipped >= 1
    count = conn.execute("SELECT COUNT(*) FROM staffing_forecast").fetchone()[0]
    assert count == 1


def test_forecast_snapshot_skips_missing_person(conn, snapshot_dir: Path) -> None:
    _write_forecast_snapshot(snapshot_dir, {
        "ok": True,
        "rows": [
            {"person": "", "week_start": "2026-06-15", "allocation_pct": 100},
        ],
    })
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=snapshot_dir / "forecast" / f"{TODAY.isoformat()}.json"):
        r = ingest_workspace_forecast_snapshot(conn, TODAY)
    assert r.failed >= 1
    assert r.ingested == 0


# ------------------------------------------------------------------
# Calendar snapshot ingestion
# ------------------------------------------------------------------


def _write_calendar_snapshot(snapshot_dir: Path, data: dict) -> Path:
    sub = snapshot_dir / "calendar"
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / f"{TODAY.isoformat()}.json"
    path.write_text(json.dumps(data))
    return path


def test_calendar_snapshot_ingests_events(conn, snapshot_dir: Path) -> None:
    _write_calendar_snapshot(snapshot_dir, {
        "ok": True,
        "source": "google_calendar_gemini",
        "events": [
            {
                "title": "Team Standup",
                "start_time": "2026-06-16T09:00:00",
                "end_time": "2026-06-16T09:30:00",
                "attendees": ["team@example.com"],
                "external_id": "evt_001",
            }
        ],
    })
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=snapshot_dir / "calendar" / f"{TODAY.isoformat()}.json"):
        r = ingest_workspace_calendar_snapshot(conn, TODAY)
    assert r.ingested == 1
    rows = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    assert rows == 1


def test_calendar_snapshot_no_snapshot(conn, snapshot_dir: Path) -> None:
    path = snapshot_dir / "calendar" / f"{TODAY.isoformat()}.json"
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=path):
        r = ingest_workspace_calendar_snapshot(conn, TODAY)
    assert r.ingested == 0
    assert len(r.errors) >= 1


def test_calendar_snapshot_idempotent(conn, snapshot_dir: Path) -> None:
    _write_calendar_snapshot(snapshot_dir, {
        "ok": True,
        "events": [
            {"title": "Standup", "start_time": "2026-06-16T09:00",
             "external_id": "evt_1"}
        ],
    })
    p = snapshot_dir / "calendar" / f"{TODAY.isoformat()}.json"
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=p):
        ingest_workspace_calendar_snapshot(conn, TODAY)
        r2 = ingest_workspace_calendar_snapshot(conn, TODAY)
    assert r2.skipped >= 1


# ------------------------------------------------------------------
# Activity snapshot ingestion
# ------------------------------------------------------------------


def _write_activity_snapshot(snapshot_dir: Path, data: dict) -> Path:
    sub = snapshot_dir / "activity"
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / f"{TODAY.isoformat()}.json"
    path.write_text(json.dumps(data))
    return path


def test_activity_snapshot_ingests_note(conn, snapshot_dir: Path) -> None:
    _write_activity_snapshot(snapshot_dir, {
        "ok": True,
        "source": "google_workspace_gemini",
        "summary": "Two docs updated",
        "items": [
            {"type": "doc_updated", "title": "Forecast Sheet",
             "description": "Rows updated", "requires_attention": False},
        ],
    })
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=snapshot_dir / "activity" / f"{TODAY.isoformat()}.json"):
        r = ingest_workspace_activity_snapshot(conn, TODAY)
    assert r.ingested >= 1
    docs = conn.execute("SELECT COUNT(*) FROM raw_documents WHERE source_type='workspace_gemini'").fetchone()[0]
    assert docs == 1


def test_activity_snapshot_no_snapshot(conn, snapshot_dir: Path) -> None:
    path = snapshot_dir / "activity" / f"{TODAY.isoformat()}.json"
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=path):
        r = ingest_workspace_activity_snapshot(conn, TODAY)
    assert r.ingested == 0
    assert len(r.errors) >= 1


def test_activity_snapshot_idempotent(conn, snapshot_dir: Path) -> None:
    _write_activity_snapshot(snapshot_dir, {
        "ok": True,
        "summary": "Some activity",
        "items": [],
    })
    p = snapshot_dir / "activity" / f"{TODAY.isoformat()}.json"
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=p):
        ingest_workspace_activity_snapshot(conn, TODAY)
        r2 = ingest_workspace_activity_snapshot(conn, TODAY)
    assert r2.skipped >= 1


def test_activity_snapshot_empty(conn, snapshot_dir: Path) -> None:
    _write_activity_snapshot(snapshot_dir, {
        "ok": True,
        "summary": "",
        "items": [],
    })
    with patch("manager_os.ingest.workspace_snapshot._snapshot_path", return_value=snapshot_dir / "activity" / f"{TODAY.isoformat()}.json"):
        r = ingest_workspace_activity_snapshot(conn, TODAY)
    assert r.ingested == 0
    assert len(r.errors) >= 1