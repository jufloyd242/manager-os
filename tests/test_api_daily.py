"""Contract tests for GET /api/daily.

Mirrors the local-DB-only, no-live-call guarantees already proven for
`manager-os daily` in tests/test_cli_daily_operating_loop.py.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection

TARGET_DATE = date(2026, 6, 29)

EXPECTED_KEYS = {
    "date",
    "people_staffing",
    "meetings",
    "projects_deals",
    "document_gaps",
    "feedback_learning",
    "recommended_actions",
    "warnings",
}


def _client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    monkeypatch.setenv("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false")
    return TestClient(create_app()), db_path


def _seed_baseline_note(conn) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type, entity_name, title, body, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["note1", "doc1", TARGET_DATE, "1on1", "person", "Seed Person", "Seed", "Seed body", "[]", now],
    )


def test_daily_returns_expected_section_keys(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    assert set(body.keys()) == EXPECTED_KEYS
    assert body["date"] == TARGET_DATE.isoformat()
    for key in (
        "people_staffing",
        "meetings",
        "projects_deals",
        "document_gaps",
        "feedback_learning",
        "recommended_actions",
        "warnings",
    ):
        assert isinstance(body[key], list)


def test_daily_does_not_call_live_gemini_or_workspace(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200
    mock_run.assert_not_called()


def test_daily_missing_empty_db_does_not_crash(tmp_path, monkeypatch):
    # Point at a DB path that has never been created / has no seeded data.
    db_path = str(tmp_path / "empty.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    monkeypatch.setenv("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false")
    client = TestClient(create_app())

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    assert set(body.keys()) == EXPECTED_KEYS


def test_daily_invalid_date_returns_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/daily", params={"date": "not-a-date"})

    assert resp.status_code == 400
    body = resp.json()
    assert "detail" in body


def test_daily_defaults_to_today_when_no_date_given(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily")

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    assert set(body.keys()) == EXPECTED_KEYS
