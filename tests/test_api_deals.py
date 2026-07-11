"""Contract tests for GET /api/deals."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection

TARGET_DATE = date(2026, 6, 29)


def _seed_deal(conn, **overrides):
    """Insert a deal row with defaults."""
    defaults = {
        "id": "d1",
        "account": "Acme Corp",
        "deal_name": "Acme AI Platform",
        "deal_id": "OPP-ACME-001",
        "stage": "Negotiation",
        "close_date": date(2026, 7, 15),
        "technical_owner": "Alok Gupta",
        "ae_name": "Sarah Chen",
        "requested_roles": '["AI Engineer"]',
        "loe_status": "pending",
        "sow_status": "in_progress",
        "staffing_feasibility": "feasible",
        "blockers": "",
        "next_action": "Review SOW draft",
        "next_steps": "Schedule review",
        "delivery_comment": "",
        "forecast_category": "commit",
        "probability": 80.0,
        "services_amount": 500000.0,
        "last_status_changed_date": date(2026, 6, 20),
        "source_format": "netsuite",
    }
    defaults.update(overrides)
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO deals (id, account, deal_name, deal_id, stage, close_date,
           technical_owner, ae_name, requested_roles, loe_status, sow_status,
           staffing_feasibility, blockers, next_action, next_steps, delivery_comment,
           forecast_category, probability, services_amount, last_status_changed_date,
           source_format, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [defaults[k] for k in [
            "id", "account", "deal_name", "deal_id", "stage", "close_date",
            "technical_owner", "ae_name", "requested_roles", "loe_status", "sow_status",
            "staffing_feasibility", "blockers", "next_action", "next_steps", "delivery_comment",
            "forecast_category", "probability", "services_amount", "last_status_changed_date",
            "source_format",
        ]] + [now],
    )


def test_deals_returns_seeded_deal(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_deal(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/deals")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1
    assert any(d["deal_name"] == "Acme AI Platform" for d in body["deals"])


def test_deals_search_filter(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_deal(conn, id="d1", deal_name="Acme AI Platform")
    _seed_deal(conn, id="d2", deal_name="Beta Analytics", account="Beta Inc")
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/deals", params={"search": "Beta"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["deals"][0]["deal_name"] == "Beta Analytics"


def test_deals_attention_classification(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    # Past close date → critical
    _seed_deal(conn, id="d1", deal_name="Overdue Deal", close_date=date(2026, 6, 1))
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/deals")

    assert resp.status_code == 200
    body = resp.json()
    overdue = [d for d in body["deals"] if d["deal_name"] == "Overdue Deal"]
    assert len(overdue) == 1
    assert overdue[0]["attention_level"] == "critical"


def test_deals_attention_only_filter(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_deal(conn, id="d1", deal_name="Normal Deal", close_date=date(2026, 8, 1))
    _seed_deal(conn, id="d2", deal_name="Overdue Deal", close_date=date(2026, 6, 1))
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/deals", params={"attention_only": "true"})

    assert resp.status_code == 200
    body = resp.json()
    assert all(d["attention_level"] in ("critical", "high") for d in body["deals"])


def test_deals_empty_state(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/deals")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["deals"] == []


def test_deals_uncapped_results(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    for i in range(10):
        _seed_deal(conn, id=f"d{i}", deal_name=f"Deal {i}", account=f"Account {i}")
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/deals", params={"limit": "200"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 10


def test_deals_freshness(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_deal(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/deals")

    assert resp.status_code == 200
    body = resp.json()
    assert "freshness" in body
