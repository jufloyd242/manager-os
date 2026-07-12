"""Tests for calendar sync persistence — schema, ingestion, API contract.

All Gemini CLI calls are mocked — no live Workspace calls.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection


TARGET_DATE = date(2026, 7, 10)
TARGET_DATE_STR = "2026-07-10"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_legacy_meeting(conn, meeting_id: str = "m1") -> None:
    """Insert a meeting using only the OLD schema columns (no end_time etc.)."""
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO meetings (id, meeting_date, start_time, title, attendees,
                              linked_entities, source, external_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [meeting_id, TARGET_DATE, "09:00", "Legacy Meeting",
         '["Alice"]', "[]", "calendar", "ext1", now],
    )


def _make_event(
    title: str = "Team Standup",
    start_time: str = "09:00",
    end_time: str = "09:30",
    attendees: list[str] | None = None,
    location: str = "Room A",
    description_summary: str = "Daily sync",
    external_id: str = "evt-001",
) -> dict:
    if attendees is None:
        attendees = ["alice@example.com", "bob@example.com"]
    return {
        "id": external_id,
        "title": title,
        "start_time": start_time,
        "end_time": end_time,
        "attendees": attendees,
        "linked_entities": [],
        "location": location,
        "description_summary": description_summary,
        "external_id": external_id,
    }


# ---------------------------------------------------------------------------
# Phase 1: Schema migration tests
# ---------------------------------------------------------------------------


class TestMeetingsSchemaMigration:
    def test_fresh_db_has_all_meeting_columns(self):
        """Fresh database must include end_time, location, description_summary."""
        conn = get_connection(":memory:")
        rows = conn.execute("DESCRIBE meetings").fetchall()
        col_names = {row[0] for row in rows}
        expected = {
            "id", "meeting_date", "start_time", "end_time", "title",
            "attendees", "linked_entities", "source", "external_id",
            "location", "description_summary", "updated_at",
        }
        assert expected.issubset(col_names), f"Missing: {expected - col_names}"

    def test_legacy_schema_migrates_correctly(self, tmp_path):
        """A database created with the old schema (no end_time/location/desc)
        must gain those columns after migration runs."""
        db_path = str(tmp_path / "legacy.duckdb")
        conn = get_connection(db_path)
        # Simulate legacy: drop the new columns if they exist, then re-init
        conn.execute("DROP TABLE IF EXISTS meetings")
        conn.execute("""
            CREATE TABLE meetings (
                id VARCHAR PRIMARY KEY,
                meeting_date DATE NOT NULL,
                start_time VARCHAR,
                title VARCHAR NOT NULL,
                attendees JSON,
                linked_entities JSON,
                source VARCHAR,
                external_id VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
        """)
        _seed_legacy_meeting(conn)
        conn.close()

        # Re-open — triggers init_schema + migrations
        conn2 = get_connection(db_path)
        rows = conn2.execute("DESCRIBE meetings").fetchall()
        col_names = {row[0] for row in rows}
        assert "end_time" in col_names
        assert "location" in col_names
        assert "description_summary" in col_names
        conn2.close()

    def test_migration_is_idempotent(self, tmp_path):
        """Running init_schema multiple times must not error or duplicate columns."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        from manager_os.db import init_schema
        init_schema(conn)
        init_schema(conn)
        init_schema(conn)
        rows = conn.execute("DESCRIBE meetings").fetchall()
        col_names = [row[0] for row in rows]
        # No duplicate column names
        assert len(col_names) == len(set(col_names))
        conn.close()

    def test_existing_rows_survive_migration(self, tmp_path):
        """Pre-existing meeting data must survive migration."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        _seed_legacy_meeting(conn, "survivor-1")
        conn.close()

        conn2 = get_connection(db_path)
        row = conn2.execute(
            "SELECT title FROM meetings WHERE id = ?", ["survivor-1"]
        ).fetchone()
        assert row is not None
        assert row[0] == "Legacy Meeting"
        conn2.close()


# ---------------------------------------------------------------------------
# Phase 2: Canonical calendar persistence tests
# ---------------------------------------------------------------------------


class TestPersistCalendarEvents:
    def test_one_event_persists(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [_make_event()]
        result = persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        assert result.persisted_count == 1
        assert result.retrieved_count == 1
        assert result.rejected_count == 0
        conn.close()

    def test_multiple_events_persist(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [
            _make_event(title="Meeting A", external_id="e1"),
            _make_event(title="Meeting B", external_id="e2", start_time="10:00"),
            _make_event(title="Meeting C", external_id="e3", start_time="11:00"),
        ]
        result = persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        assert result.persisted_count == 3
        conn.close()

    def test_end_time_persists(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [_make_event(end_time="10:30")]
        persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        row = conn.execute(
            "SELECT end_time FROM meetings WHERE external_id = ?", ["evt-001"]
        ).fetchone()
        assert row is not None
        assert row[0] == "10:30"
        conn.close()

    def test_location_persists(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [_make_event(location="Conference Room B")]
        persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        row = conn.execute(
            "SELECT location FROM meetings WHERE external_id = ?", ["evt-001"]
        ).fetchone()
        assert row is not None
        assert row[0] == "Conference Room B"
        conn.close()

    def test_description_summary_persists(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [_make_event(description_summary="Quarterly review meeting")]
        persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        row = conn.execute(
            "SELECT description_summary FROM meetings WHERE external_id = ?", ["evt-001"]
        ).fetchone()
        assert row is not None
        assert row[0] == "Quarterly review meeting"
        conn.close()

    def test_external_id_persists(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [_make_event(external_id="google-cal-abc123")]
        persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        row = conn.execute(
            "SELECT external_id FROM meetings WHERE external_id = ?", ["google-cal-abc123"]
        ).fetchone()
        assert row is not None
        assert row[0] == "google-cal-abc123"
        conn.close()

    def test_stable_fallback_id_when_no_external_id(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [_make_event(external_id="", title="No ID Meeting")]
        result = persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        assert result.persisted_count == 1
        # Should have generated a stable ID
        row = conn.execute(
            "SELECT id FROM meetings WHERE title = ?", ["No ID Meeting"]
        ).fetchone()
        assert row is not None
        assert len(row[0]) > 0
        conn.close()

    def test_existing_event_replacement(self, tmp_path):
        """Re-syncing the same external_id should replace, not duplicate."""
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events1 = [_make_event(title="Original Title", external_id="dup-1")]
        persist_calendar_events(
            conn, TARGET_DATE, events1, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        events2 = [_make_event(title="Updated Title", external_id="dup-1")]
        result = persist_calendar_events(
            conn, TARGET_DATE, events2, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        assert result.persisted_count == 1
        assert result.replaced_count == 1
        count = conn.execute(
            "SELECT COUNT(*) FROM meetings WHERE external_id = ?", ["dup-1"]
        ).fetchone()[0]
        assert count == 1
        title = conn.execute(
            "SELECT title FROM meetings WHERE external_id = ?", ["dup-1"]
        ).fetchone()[0]
        assert title == "Updated Title"
        conn.close()

    def test_attendees_json_serialized_correctly(self, tmp_path):
        """Attendees must be json.dumps(), not str()."""
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [_make_event(attendees=["alice@example.com", "bob@example.com"])]
        persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        row = conn.execute(
            "SELECT attendees FROM meetings WHERE external_id = ?", ["evt-001"]
        ).fetchone()
        raw = row[0]
        # Must be valid JSON, not Python repr
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        assert isinstance(parsed, list)
        assert "alice@example.com" in parsed
        # Must NOT contain Python-style single quotes
        assert "'" not in raw or raw.startswith("[")
        conn.close()

    def test_linked_entities_json_serialized_correctly(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [_make_event()]
        events[0]["linked_entities"] = [{"type": "deal", "name": "Acme Deal"}]
        persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        row = conn.execute(
            "SELECT linked_entities FROM meetings WHERE external_id = ?", ["evt-001"]
        ).fetchone()
        raw = row[0]
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        assert isinstance(parsed, list)
        assert parsed[0]["name"] == "Acme Deal"
        conn.close()

    def test_individual_event_rejection(self, tmp_path):
        """An event missing required fields should be rejected, not crash the batch."""
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [
            _make_event(title="Good Meeting", external_id="good-1"),
            {"start_time": "10:00", "attendees": ["x"]},  # missing title
        ]
        result = persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        assert result.persisted_count == 1
        assert result.rejected_count == 1
        assert len(result.errors) > 0
        conn.close()

    def test_partial_persistence(self, tmp_path):
        """Some events persist, some fail — result should reflect both."""
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        events = [
            _make_event(title="OK 1", external_id="ok-1"),
            {"start_time": "10:00", "attendees": ["x"]},  # missing title → rejected
            _make_event(title="OK 2", external_id="ok-2", start_time="11:00"),
        ]
        result = persist_calendar_events(
            conn, TARGET_DATE, events, source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        assert result.persisted_count == 2
        assert result.rejected_count == 1
        conn.close()

    def test_zero_events_is_not_failure(self, tmp_path):
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        result = persist_calendar_events(
            conn, TARGET_DATE, [], source="calendar_sync",
            retrieved_at=datetime.utcnow().isoformat(),
        )
        assert result.persisted_count == 0
        assert result.retrieved_count == 0
        assert result.rejected_count == 0
        conn.close()


# ---------------------------------------------------------------------------
# Phase 4: API sync contract tests
# ---------------------------------------------------------------------------


def _mock_retrieval_result(events: list[dict], ok: bool = True, error: str = ""):
    """Build a mock RetrievalResult."""
    from manager_os.ingest.workspace_gemini import RetrievalResult
    return RetrievalResult(
        ok=ok,
        error=error,
        items=events,
        retrieved_at=datetime.utcnow().isoformat(),
        source_title="calendar",
    )


class TestCalendarSyncAPI:
    def test_sync_success_returns_meetings_and_counts(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        events = [_make_event(), _make_event(title="Second", external_id="e2", start_time="10:00")]
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
            return_value=_mock_retrieval_result(events),
        ):
            client = TestClient(create_app())
            resp = client.post("/api/meetings/sync-calendar", json={"date": TARGET_DATE_STR})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["retrieved_count"] == 2
        assert body["persisted_count"] == 2
        assert body["rejected_count"] == 0
        assert len(body["meetings"]) == 2

    def test_sync_persisted_meetings_survive_reload(self, tmp_path, monkeypatch):
        """GET /api/meetings after sync must return the same meetings."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        events = [_make_event()]
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
            return_value=_mock_retrieval_result(events),
        ):
            client = TestClient(create_app())
            client.post("/api/meetings/sync-calendar", json={"date": TARGET_DATE_STR})
        # Now GET without any mock — reads from DB
        client2 = TestClient(create_app())
        resp = client2.get("/api/meetings", params={"date": TARGET_DATE_STR})
        assert resp.status_code == 200
        body = resp.json()
        titles = {m["title"] for m in body["meetings"]}
        assert "Team Standup" in titles

    def test_sync_total_persistence_failure_returns_ok_false(self, tmp_path, monkeypatch):
        """If retrieval succeeds but ALL persistence fails, ok must be False."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        events = [{"start_time": "10:00", "attendees": ["x"]}]  # missing title
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
            return_value=_mock_retrieval_result(events),
        ):
            client = TestClient(create_app())
            resp = client.post("/api/meetings/sync-calendar", json={"date": TARGET_DATE_STR})
        body = resp.json()
        assert body["ok"] is False
        assert body["persisted_count"] == 0
        assert body["retrieved_count"] == 1

    def test_sync_partial_success(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        events = [
            _make_event(title="Good", external_id="good-1"),
            {"start_time": "10:00", "attendees": ["x"]},  # missing title
        ]
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
            return_value=_mock_retrieval_result(events),
        ):
            client = TestClient(create_app())
            resp = client.post("/api/meetings/sync-calendar", json={"date": TARGET_DATE_STR})
        body = resp.json()
        assert body["ok"] is True
        assert body.get("partial") is True
        assert body["persisted_count"] == 1
        assert body["rejected_count"] == 1

    def test_sync_retrieval_failure_returns_ok_false(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
            return_value=_mock_retrieval_result([], ok=False, error="Gemini CLI failed"),
        ):
            client = TestClient(create_app())
            resp = client.post("/api/meetings/sync-calendar", json={"date": TARGET_DATE_STR})
        body = resp.json()
        assert body["ok"] is False
        assert len(body["errors"]) > 0

    def test_sync_zero_events_returns_ok_true(self, tmp_path, monkeypatch):
        """Legitimate zero events is not a failure."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
            return_value=_mock_retrieval_result([]),
        ):
            client = TestClient(create_app())
            resp = client.post("/api/meetings/sync-calendar", json={"date": TARGET_DATE_STR})
        body = resp.json()
        assert body["ok"] is True
        assert body["retrieved_count"] == 0
        assert body["persisted_count"] == 0

    def test_sync_response_meetings_equal_persisted(self, tmp_path, monkeypatch):
        """The meetings in the response must match what was persisted."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        events = [_make_event(title="Sync Test", external_id="sync-1")]
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
            return_value=_mock_retrieval_result(events),
        ):
            client = TestClient(create_app())
            resp = client.post("/api/meetings/sync-calendar", json={"date": TARGET_DATE_STR})
        body = resp.json()
        assert len(body["meetings"]) == body["persisted_count"]
        meeting = body["meetings"][0]
        assert meeting["title"] == "Sync Test"
        assert meeting["end_time"] == "09:30"
        assert meeting["location"] == "Room A"

    def test_no_automatic_calendar_retrieval_on_page_load(self, tmp_path, monkeypatch):
        """GET /api/meetings must NOT trigger any calendar retrieval."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        _seed_legacy_meeting(conn)
        conn.close()
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
        ) as mock_retrieve:
            client = TestClient(create_app())
            resp = client.get("/api/meetings", params={"date": TARGET_DATE_STR})
            assert resp.status_code == 200
            mock_retrieve.assert_not_called()

    def test_no_swallowed_exceptions(self, tmp_path, monkeypatch):
        """If persistence raises, the error must be in the response, not swallowed."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        events = [_make_event()]
        with patch(
            "manager_os.ingest.workspace_gemini.retrieve_calendar",
            return_value=_mock_retrieval_result(events),
        ):
            client = TestClient(create_app())
            # Corrupt the DB by dropping the meetings table
            conn = get_connection(db_path)
            conn.execute("DROP TABLE meetings")
            conn.close()
            resp = client.post("/api/meetings/sync-calendar", json={"date": TARGET_DATE_STR})
        body = resp.json()
        # Must not be ok:true with empty meetings and no errors
        if body["ok"]:
            assert body["persisted_count"] > 0 or body["retrieved_count"] == 0
        else:
            assert len(body["errors"]) > 0


# ---------------------------------------------------------------------------
# Phase 5: Meeting read path tests
# ---------------------------------------------------------------------------


class TestMeetingReadPath:
    def test_read_returns_end_time(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        now = datetime.now(timezone.utc)
        conn.execute(
            """INSERT INTO meetings (id, meeting_date, start_time, end_time, title,
               attendees, linked_entities, source, external_id, location,
               description_summary, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ["m1", TARGET_DATE, "09:00", "10:00", "Meeting with End Time",
             '["Alice"]', "[]", "calendar", "ext1", "Room A", "Desc", now],
        )
        conn.close()
        client = TestClient(create_app())
        resp = client.get("/api/meetings", params={"date": TARGET_DATE_STR})
        body = resp.json()
        meeting = body["meetings"][0]
        assert meeting["end_time"] == "10:00"
        assert meeting["location"] == "Room A"
        assert meeting["description_summary"] == "Desc"

    def test_malformed_legacy_json_does_not_crash(self, tmp_path, monkeypatch):
        """Malformed attendees JSON must not crash the read path.

        DuckDB's JSON column rejects Python-repr strings at insert time,
        so we test with a NULL attendees value (edge case from legacy data)
        and verify the read path handles it gracefully.
        """
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        now = datetime.now(timezone.utc)
        # Insert with NULL attendees (possible from legacy migration)
        conn.execute(
            """INSERT INTO meetings (id, meeting_date, start_time, title,
               attendees, linked_entities, source, external_id, updated_at)
               VALUES (?, ?, ?, ?, NULL, NULL, 'calendar', 'ext1', ?)""",
            ["m1", TARGET_DATE, "09:00", "Null Attendees", now],
        )
        conn.close()
        client = TestClient(create_app())
        resp = client.get("/api/meetings", params={"date": TARGET_DATE_STR})
        assert resp.status_code == 200
        body = resp.json()
        # Should not crash — meeting with null attendees is skipped by the
        # no-attendee filter, so it won't appear in results
        assert isinstance(body["meetings"], list)
