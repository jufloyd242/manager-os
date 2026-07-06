"""Unit tests for the GET /api/analytics/staffing-balance endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from fastapi.testclient import TestClient
import pytest

from manager_os.api.app import create_app
from manager_os.db import get_connection


def test_staffing_balance_endpoint(tmp_path, monkeypatch):
    """Test the staffing balance API under normal allocation scenarios."""
    db_path = str(tmp_path / "test_staffing.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    
    # Initialize DB connection and insert test data
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    
    # Seed one overallocated and one underallocated person
    conn.execute(
        "INSERT INTO people (id, name, role, current_client, allocation_pct, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ["p1", "Alice Chen", "Engineer", "Acme Corp", 120.0, now],
    )
    conn.execute(
        "INSERT INTO people (id, name, role, current_client, allocation_pct, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        ["p2", "Bob Smith", "Designer", "Beta Inc", 50.0, now],
    )
    conn.close()

    # Create test client and hit the endpoint
    client = TestClient(create_app())
    resp = client.get("/api/analytics/staffing-balance")

    # Assert correct response and structures
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    
    assert "original_allocations" in payload
    assert "balanced_allocations" in payload
    assert "transfers" in payload
    assert "overallocated_before" in payload
    assert "underallocated_before" in payload
    
    assert payload["original_allocations"]["Alice Chen"] == 120.0
    assert payload["original_allocations"]["Bob Smith"] == 50.0
    
    assert payload["balanced_allocations"]["Alice Chen"] == 100.0
    assert payload["balanced_allocations"]["Bob Smith"] == 70.0
    
    assert len(payload["transfers"]) == 1
    transfer = payload["transfers"][0]
    assert transfer["from"] == "Alice Chen"
    assert transfer["to"] == "Bob Smith"
    assert transfer["amount"] == 20.0


def test_staffing_balance_empty_db(tmp_path, monkeypatch):
    """Test the staffing balance API when the DB is empty."""
    db_path = str(tmp_path / "empty_staffing.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    
    client = TestClient(create_app())
    resp = client.get("/api/analytics/staffing-balance")
    
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["original_allocations"] == {}
    assert payload["balanced_allocations"] == {}
    assert payload["transfers"] == []
