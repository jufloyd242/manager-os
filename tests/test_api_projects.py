"""Contract tests for GET /api/projects."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection


def test_projects_returns_seeded_project(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ["project::OPP1", "Platform Modernization", "Acme Corp", "OPP1", now, now],
    )
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/projects")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {p["project_name"] for p in body["projects"]}
    assert "Platform Modernization" in names


def test_projects_empty_db_returns_empty_list_not_500(tmp_path, monkeypatch):
    db_path = str(tmp_path / "empty.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/projects")

    assert resp.status_code == 200, resp.text
    assert resp.json()["projects"] == []
