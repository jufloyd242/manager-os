"""Contract tests for GET /api/people."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection


def test_people_returns_seeded_rows(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO people (id, name, role, current_client, updated_at) VALUES (?, ?, ?, ?, ?)",
        ["p1", "Alice Chen", "Engineer", "Acme Corp", now],
    )
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/people")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "people" in body
    names = {p["name"] for p in body["people"]}
    assert "Alice Chen" in names


def test_people_empty_db_returns_empty_list_not_500(tmp_path, monkeypatch):
    db_path = str(tmp_path / "empty.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/people")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["people"] == []
