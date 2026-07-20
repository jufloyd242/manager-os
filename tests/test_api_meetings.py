"""Contract tests for GET /api/meetings."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection

TARGET_DATE = date(2026, 6, 29)


def test_meetings_returns_seeded_meeting_for_date(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["m1", TARGET_DATE, "09:00", "1:1 with Jordan Lee", '["Jordan Lee"]', "[]", "calendar", "ext1", now],
    )
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/meetings", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    titles = {m["title"] for m in body["meetings"]}
    assert "1:1 with Jordan Lee" in titles


def test_meetings_invalid_date_returns_400(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/meetings", params={"date": "not-a-date"})

    assert resp.status_code == 400
