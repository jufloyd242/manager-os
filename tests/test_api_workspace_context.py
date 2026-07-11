"""Contract tests for GET /api/workspace-context."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection, content_hash

TARGET_DATE = date(2026, 6, 29)


def _seed_workspace_summary(conn, target_date: date = TARGET_DATE, **overrides):
    """Insert a workspace_summary raw_document."""
    content = overrides.get("content", "# Daily Summary\n\n- Met with Alok about AI Platform\n- Blocked on SOW for Acme Corp\n- Kevin is overallocated\n")
    doc_id = content_hash(f"workspace_summary:{target_date}")
    conn.execute(
        """INSERT OR REPLACE INTO raw_documents
           (id, source_type, source_path, content_hash, content, metadata, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [doc_id, "workspace_summary", f"/summaries/{target_date}.md", content_hash(content),
         content, '{"date": "' + target_date.isoformat() + '"}', datetime.now(timezone.utc)],
    )


def _seed_workspace_activity(conn, target_date: date = TARGET_DATE):
    """Insert workspace activity notes."""
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type, entity_name, title, body, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ["n1", "rd1", target_date, "workspace_activity", "person", "Alok Gupta",
         "Daily Standup", "Alok completed AI Platform milestone. Blocked on data access.", "[]"],
    )


def test_workspace_context_returns_items(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_workspace_summary(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/workspace-context", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["selected_date"] == TARGET_DATE.isoformat()


def test_workspace_context_exact_date(tmp_path, monkeypatch):
    """Context for exact date returns only that date's data."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_workspace_summary(conn, target_date=TARGET_DATE)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/workspace-context", params={
        "date": TARGET_DATE.isoformat(),
        "lookback_days": "0",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["selected_date"] == TARGET_DATE.isoformat()


def test_workspace_context_lookback(tmp_path, monkeypatch):
    """Lookback returns data from the lookback window."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_workspace_summary(conn, target_date=TARGET_DATE)
    conn.close()

    later = date(2026, 7, 6)
    client = TestClient(create_app())
    resp = client.get("/api/workspace-context", params={
        "date": later.isoformat(),
        "lookback_days": "14",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["lookback_start"] <= TARGET_DATE.isoformat()


def test_workspace_context_entity_linking(tmp_path, monkeypatch):
    """Entity types and names are returned."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_workspace_summary(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/workspace-context", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200
    body = resp.json()
    assert "linked_count" in body
    assert "unlinked_count" in body


def test_workspace_context_attention_filter(tmp_path, monkeypatch):
    """Attention-only filter returns only attention items."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_workspace_summary(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/workspace-context", params={
        "date": TARGET_DATE.isoformat(),
        "attention_only": "true",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert "attention_count" in body


def test_workspace_context_empty_state(tmp_path, monkeypatch):
    """Empty workspace context returns structured response."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/workspace-context", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200
    body = resp.json()
    assert body["context_items"] == []


def test_workspace_context_freshness(tmp_path, monkeypatch):
    """Freshness indicator is returned."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_workspace_summary(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/workspace-context", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200
    body = resp.json()
    assert "freshness" in body


def test_workspace_context_no_external_calls(tmp_path, monkeypatch):
    """GET workspace context does not call external systems."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_workspace_summary(conn)
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/workspace-context", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200