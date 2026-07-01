"""Contract tests for GET /api/feedback."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection


def test_feedback_returns_seeded_candidate(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO feedback_learning_candidates
            (id, pattern_type, entity_name, signal_type, rating, event_count, suggested_action, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["fb1", "signal_type", "Acme Corp", "risk", "noisy", 5, "suppress", "open", now],
    )
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/feedback")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    entities = {c["entity_name"] for c in body["candidates"]}
    assert "Acme Corp" in entities


def test_feedback_missing_table_degrades_gracefully(tmp_path, monkeypatch):
    db_path = str(tmp_path / "empty.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/feedback")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["candidates"], list)
    assert isinstance(body["warnings"], list)
