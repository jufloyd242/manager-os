"""Contract tests for GET /api/health."""

from __future__ import annotations

from fastapi.testclient import TestClient

from manager_os.api.app import create_app


def test_health_returns_ok():
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "service": "manager-os-api"}
