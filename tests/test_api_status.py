"""Contract tests for GET /api/status."""

from __future__ import annotations

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection


def _client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    monkeypatch.setenv("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false")
    return TestClient(create_app()), db_path


def test_status_ok_with_empty_db(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    # Ensure schema exists but everything is empty.
    conn = get_connection(db_path)
    conn.close()

    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["db_path"] == db_path
    assert body["workspace_enabled"] is False
    assert isinstance(body["sources"], list)
    names = {s["name"] for s in body["sources"]}
    assert {"projects", "people", "meetings", "signals", "staffing_forecast"} <= names
    for source in body["sources"]:
        assert source["status"] in ("available", "empty", "missing")
        assert "count" in source
        assert "warnings" in source
    assert isinstance(body["warnings"], list)


def test_status_uses_env_db_path_not_hardcoded(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO people (id, name, updated_at) VALUES ('p1', 'Alice', now())"
    )
    conn.close()

    resp = client.get("/api/status")
    body = resp.json()
    assert body["db_path"] == db_path
    people_source = next(s for s in body["sources"] if s["name"] == "people")
    assert people_source["count"] == 1
    assert people_source["status"] == "available"


def test_status_missing_db_file_does_not_crash(tmp_path, monkeypatch):
    # Point at a DB path that has never been created.
    db_path = str(tmp_path / "does_not_exist.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["warnings"], list)


def test_status_detailed_freshness_stale_and_unknown(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    
    from datetime import datetime, timedelta
    old_time = datetime.now() - timedelta(days=2)
    conn.execute(
        "INSERT INTO people (id, name, updated_at) VALUES ('p1', 'Alice', ?)",
        [old_time]
    )
    conn.close()

    resp = client.get("/api/status")
    body = resp.json()
    people_source = next(s for s in body["sources"] if s["name"] == "people")
    assert people_source["count"] == 1
    assert people_source["freshness"] == "stale"
    assert "stale" in people_source["explanation"].lower()


def test_status_detailed_freshness_fresh(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    
    conn.execute(
        "INSERT INTO people (id, name, updated_at) VALUES ('p1', 'Alice', now())"
    )
    conn.close()

    resp = client.get("/api/status")
    body = resp.json()
    people_source = next(s for s in body["sources"] if s["name"] == "people")
    assert people_source["count"] == 1
    assert people_source["freshness"] == "fresh"
    assert "updated" in people_source["explanation"].lower()
