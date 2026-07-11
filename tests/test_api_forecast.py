"""Contract tests for GET /api/forecast."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection


def _seed_forecast(conn, **overrides):
    """Insert a staffing_forecast row with defaults."""
    defaults = {
        "id": "f1",
        "person_id": "p1",
        "person_name": "Alok Gupta",
        "week_start": date.today(),
        "client": "Acme Corp",
        "project": "AI Platform",
        "allocation_pct": 100.0,
        "planned_hours": 40.0,
        "target_hours": 40.0,
        "forecast_type": "confirmed",
        "notes": "",
    }
    defaults.update(overrides)
    from datetime import datetime, timezone
    conn.execute(
        """INSERT INTO staffing_forecast
           (id, person_id, person_name, week_start, client, project,
            allocation_pct, planned_hours, target_hours, forecast_type, notes,
            ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [defaults[k] for k in [
            "id", "person_id", "person_name", "week_start", "client", "project",
            "allocation_pct", "planned_hours", "target_hours", "forecast_type", "notes",
        ]] + [datetime.now(timezone.utc)],
    )


def test_forecast_returns_allocation(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_forecast(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/forecast")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["person_count"] >= 1
    assert any(p["person_name"] == "Alok Gupta" for p in body["people"])


def test_forecast_overallocated(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_forecast(conn, id="f1", person_name="Alok Gupta", planned_hours=50.0, target_hours=40.0, allocation_pct=125.0)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/forecast")

    assert resp.status_code == 200
    body = resp.json()
    alok = [p for p in body["people"] if p["person_name"] == "Alok Gupta"]
    assert len(alok) > 0
    assert alok[0]["classification"] == "overallocated"


def test_forecast_underutilized(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_forecast(conn, id="f1", person_name="Alok Gupta", planned_hours=20.0, target_hours=40.0, allocation_pct=50.0)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/forecast")

    assert resp.status_code == 200
    body = resp.json()
    alok = [p for p in body["people"] if p["person_name"] == "Alok Gupta"]
    assert len(alok) > 0
    assert alok[0]["classification"] == "underutilized"


def test_forecast_available(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_forecast(conn, id="f1", person_name="Alok Gupta", planned_hours=0.0, target_hours=40.0, allocation_pct=0.0)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/forecast")

    assert resp.status_code == 200
    body = resp.json()
    alok = [p for p in body["people"] if p["person_name"] == "Alok Gupta"]
    assert len(alok) > 0
    assert alok[0]["classification"] == "available"


def test_forecast_unknown(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_forecast(conn, id="f1", person_name="Alok Gupta", planned_hours=0.0, target_hours=None, allocation_pct=0.0)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/forecast")

    assert resp.status_code == 200
    body = resp.json()
    alok = [p for p in body["people"] if p["person_name"] == "Alok Gupta"]
    assert len(alok) > 0
    assert alok[0]["classification"] == "unknown"


def test_forecast_exceptions_only(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_forecast(conn, id="f1", person_name="Alok Gupta", planned_hours=50.0, target_hours=40.0, allocation_pct=125.0)
    _seed_forecast(conn, id="f2", person_name="Kevin Tuuri", planned_hours=40.0, target_hours=40.0, allocation_pct=100.0)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/forecast", params={"exceptions_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    for p in body["people"]:
        assert p["classification"] in ("overallocated", "underutilized")


def test_forecast_empty_state(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/forecast")

    assert resp.status_code == 200
    body = resp.json()
    assert body["person_count"] == 0


def test_forecast_week_selection(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_forecast(conn, id="f1", person_name="Alok Gupta", week_start=date(2026, 7, 6))
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/forecast", params={"week_start": "2026-07-06"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["selected_week"] == "2026-07-06"


def test_forecast_freshness(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_forecast(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/forecast")

    assert resp.status_code == 200
    body = resp.json()
    assert "freshness" in body
