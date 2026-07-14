"""Tests for meeting schema, chronological ordering, and calendar range sync.

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


# ---------------------------------------------------------------------------
# Phase 1: Schema migration tests
# ---------------------------------------------------------------------------


class TestMeetingSchemaMigration:
    def test_fresh_db_has_normalized_timestamp_columns(self):
        """Fresh database must include start_at, end_at, is_all_day, organizer,
        conference_url, recurring_event_id."""
        conn = get_connection(":memory:")
        rows = conn.execute("DESCRIBE meetings").fetchall()
        col_names = {row[0] for row in rows}
        expected = {
            "start_at", "end_at", "is_all_day", "organizer",
            "conference_url", "recurring_event_id",
        }
        assert expected.issubset(col_names), f"Missing: {expected - col_names}"

    def test_fresh_db_has_extended_meeting_prep_columns(self):
        """Fresh database must include meeting_fingerprint, classification,
        profile_id, source_fingerprint, structured_prep_json, etc."""
        conn = get_connection(":memory:")
        rows = conn.execute("DESCRIBE meeting_prep").fetchall()
        col_names = {row[0] for row in rows}
        expected = {
            "meeting_fingerprint", "classification", "profile_id",
            "source_fingerprint", "structured_prep_json",
            "source_references_json", "generator_version",
            "llm_provider", "llm_model", "live_enrichment_used",
            "generation_status", "safe_error",
        }
        assert expected.issubset(col_names), f"Missing: {expected - col_names}"

    def test_legacy_meetings_schema_migrates(self, tmp_path):
        """A database with old meetings schema (no start_at) must gain columns."""
        db_path = str(tmp_path / "legacy.duckdb")
        conn = get_connection(db_path)
        conn.execute("DROP TABLE IF EXISTS meetings")
        conn.execute("""
            CREATE TABLE meetings (
                id VARCHAR PRIMARY KEY,
                meeting_date DATE NOT NULL,
                start_time VARCHAR,
                end_time VARCHAR,
                title VARCHAR NOT NULL,
                attendees JSON,
                linked_entities JSON,
                source VARCHAR,
                external_id VARCHAR,
                location VARCHAR,
                description_summary VARCHAR,
                updated_at TIMESTAMP NOT NULL
            )
        """)
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["m1", date(2026, 7, 10), "09:00", "Test", '["Alice"]', "[]", "cal", "e1", now],
        )
        conn.close()

        conn2 = get_connection(db_path)
        rows = conn2.execute("DESCRIBE meetings").fetchall()
        col_names = {row[0] for row in rows}
        assert "start_at" in col_names
        assert "is_all_day" in col_names
        assert "organizer" in col_names
        # Existing data survives
        row = conn2.execute("SELECT title FROM meetings WHERE id = 'm1'").fetchone()
        assert row is not None
        assert row[0] == "Test"
        conn2.close()

    def test_legacy_meeting_prep_schema_migrates(self, tmp_path):
        """A database with old meeting_prep schema must gain new columns."""
        db_path = str(tmp_path / "legacy.duckdb")
        conn = get_connection(db_path)
        conn.execute("DROP TABLE IF EXISTS meeting_prep")
        conn.execute("""
            CREATE TABLE meeting_prep (
                id VARCHAR PRIMARY KEY,
                meeting_id VARCHAR NOT NULL,
                content VARCHAR NOT NULL,
                generated_at TIMESTAMP NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO meeting_prep (id, meeting_id, content, generated_at) VALUES (?, ?, ?, ?)",
            ["p1", "m1", "old prep", datetime.utcnow()],
        )
        conn.close()

        conn2 = get_connection(db_path)
        rows = conn2.execute("DESCRIBE meeting_prep").fetchall()
        col_names = {row[0] for row in rows}
        assert "meeting_fingerprint" in col_names
        assert "classification" in col_names
        assert "generation_status" in col_names
        # Existing data survives
        row = conn2.execute("SELECT content FROM meeting_prep WHERE id = 'p1'").fetchone()
        assert row is not None
        assert row[0] == "old prep"
        conn2.close()

    def test_migration_idempotent(self, tmp_path):
        """Running init_schema multiple times must not error or duplicate columns."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        from manager_os.db import init_schema
        init_schema(conn)
        init_schema(conn)
        rows = conn.execute("DESCRIBE meetings").fetchall()
        col_names = [row[0] for row in rows]
        assert len(col_names) == len(set(col_names))
        conn.close()


# ---------------------------------------------------------------------------
# Phase 2: Chronological ordering tests
# ---------------------------------------------------------------------------


class TestChronologicalOrdering:
    """Test the canonical chronological sort function."""

    def _make_meeting(self, start_at, title="M", end_at=None, is_all_day=False, meeting_id="m"):
        return {
            "id": meeting_id,
            "start_at": start_at,
            "end_at": end_at,
            "is_all_day": is_all_day,
            "title": title,
        }

    def test_12h_times_ordered(self):
        """8 AM, 9:30 AM, 12 PM, 1 PM, 4:30 PM must be in that order."""
        from manager_os.build.dashboard_data import sort_meetings_chronological
        meetings = [
            self._make_meeting("2026-07-13T16:30:00", "4:30 PM", meeting_id="m5"),
            self._make_meeting("2026-07-13T13:00:00", "1:00 PM", meeting_id="m4"),
            self._make_meeting("2026-07-13T12:00:00", "12:00 PM", meeting_id="m3"),
            self._make_meeting("2026-07-13T09:30:00", "9:30 AM", meeting_id="m2"),
            self._make_meeting("2026-07-13T08:00:00", "8:00 AM", meeting_id="m1"),
        ]
        sorted_m = sort_meetings_chronological(meetings)
        titles = [m["title"] for m in sorted_m]
        assert titles == ["8:00 AM", "9:30 AM", "12:00 PM", "1:00 PM", "4:30 PM"]

    def test_reverse_provider_order_corrected(self):
        """Provider returning reverse order must be corrected."""
        from manager_os.build.dashboard_data import sort_meetings_chronological
        meetings = [
            self._make_meeting("2026-07-13T16:00:00", "Late", meeting_id="m3"),
            self._make_meeting("2026-07-13T09:00:00", "Early", meeting_id="m1"),
            self._make_meeting("2026-07-13T12:00:00", "Mid", meeting_id="m2"),
        ]
        sorted_m = sort_meetings_chronological(meetings)
        assert [m["title"] for m in sorted_m] == ["Early", "Mid", "Late"]

    def test_all_day_events_grouped(self):
        """All-day events should be in a clearly labeled group, not mixed with timed."""
        from manager_os.build.dashboard_data import sort_meetings_chronological
        meetings = [
            self._make_meeting("2026-07-13T14:00:00", "Afternoon", meeting_id="m2"),
            self._make_meeting(None, "All Day", is_all_day=True, meeting_id="m1"),
            self._make_meeting("2026-07-13T09:00:00", "Morning", meeting_id="m3"),
        ]
        sorted_m = sort_meetings_chronological(meetings)
        # All-day should be separated (either first or last, consistently)
        titles = [m["title"] for m in sorted_m]
        assert "All Day" in titles
        # All-day should not be between timed events
        all_day_idx = titles.index("All Day")
        timed_indices = [titles.index("Morning"), titles.index("Afternoon")]
        assert all_day_idx < min(timed_indices) or all_day_idx > max(timed_indices)

    def test_missing_times_last(self):
        """Meetings with missing start_at should be placed last."""
        from manager_os.build.dashboard_data import sort_meetings_chronological
        meetings = [
            self._make_meeting(None, "No Time", meeting_id="m1"),
            self._make_meeting("2026-07-13T10:00:00", "Has Time", meeting_id="m2"),
        ]
        sorted_m = sort_meetings_chronological(meetings)
        assert sorted_m[-1]["title"] == "No Time"

    def test_same_start_times_ordered_by_end(self):
        """Same start time should be ordered by end time."""
        from manager_os.build.dashboard_data import sort_meetings_chronological
        meetings = [
            self._make_meeting("2026-07-13T09:00:00", "Long", end_at="2026-07-13T10:30:00", meeting_id="m1"),
            self._make_meeting("2026-07-13T09:00:00", "Short", end_at="2026-07-13T09:15:00", meeting_id="m2"),
        ]
        sorted_m = sort_meetings_chronological(meetings)
        assert sorted_m[0]["title"] == "Short"
        assert sorted_m[1]["title"] == "Long"

    def test_same_start_and_end_ordered_by_title(self):
        """Same start and end should use normalized title as tie-breaker."""
        from manager_os.build.dashboard_data import sort_meetings_chronological
        meetings = [
            self._make_meeting("2026-07-13T09:00:00", "Zebra", end_at="2026-07-13T10:00:00", meeting_id="m1"),
            self._make_meeting("2026-07-13T09:00:00", "Alpha", end_at="2026-07-13T10:00:00", meeting_id="m2"),
        ]
        sorted_m = sort_meetings_chronological(meetings)
        assert sorted_m[0]["title"] == "Alpha"
        assert sorted_m[1]["title"] == "Zebra"


# ---------------------------------------------------------------------------
# Phase 3: Calendar range sync tests
# ---------------------------------------------------------------------------


def _mock_retrieval_result(events, ok=True, error=""):
    from manager_os.ingest.workspace_gemini import RetrievalResult
    return RetrievalResult(
        ok=ok,
        error=error,
        items=events,
        retrieved_at=datetime.utcnow().isoformat(),
        source_title="calendar",
    )


class TestCalendarRangeSync:
    def test_retrieve_calendar_range_calls_gemini_once(self):
        """retrieve_calendar_range should make one Gemini call for the full range."""
        from manager_os.ingest.workspace_gemini import retrieve_calendar_range
        events = [
            {"title": "Meeting A", "start_time": "2026-07-13T09:00:00", "attendees": ["alice@example.com"]},
            {"title": "Meeting B", "start_time": "2026-07-14T10:00:00", "attendees": ["bob@example.com"]},
        ]
        with patch(
            "manager_os.ingest.workspace_gemini._run_gemini_retrieval",
            return_value=(json.dumps({"ok": True, "events": events, "retrieved_at": datetime.utcnow().isoformat()}), ["gemini"]),
        ) as mock:
            result = retrieve_calendar_range(date(2026, 7, 13), date(2026, 7, 19))
            mock.assert_called_once()
            assert result.ok
            assert len(result.items) == 2

    def test_range_sync_persists_meetings_for_week(self, tmp_path, monkeypatch):
        """Syncing a week should persist meetings across multiple dates."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        events = [
            {"title": "Mon Meeting", "start_time": "2026-07-13T09:00:00", "attendees": ["alice@example.com"], "external_id": "e1"},
            {"title": "Wed Meeting", "start_time": "2026-07-15T14:00:00", "attendees": ["bob@example.com"], "external_id": "e2"},
        ]
        from manager_os.ingest.workspace_gemini import retrieve_calendar_range
        with patch(
            "manager_os.ingest.workspace_gemini._run_gemini_retrieval",
            return_value=(json.dumps({"ok": True, "events": events, "retrieved_at": datetime.utcnow().isoformat()}), ["gemini"]),
        ):
            result = retrieve_calendar_range(date(2026, 7, 13), date(2026, 7, 19))
            assert result.ok

        conn = get_connection(db_path)
        from manager_os.ingest.calendar_persistence import persist_calendar_events
        # Persist events for each date
        for event in events:
            event_date = date.fromisoformat(event["start_time"][:10])
            persist_calendar_events(
                conn, event_date, [event],
                source="calendar_sync",
                retrieved_at=datetime.utcnow().isoformat(),
            )
        count = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        assert count == 2
        conn.close()


# ---------------------------------------------------------------------------
# Phase 4: Startup weekly sync tests
# ---------------------------------------------------------------------------


class TestStartupWeeklySync:
    def test_calculate_week_range_monday_to_sunday(self):
        """Week range should be Monday 00:00 to Sunday 23:59 in America/Denver."""
        from manager_os.startup_calendar import calculate_week_range
        # 2026-07-13 is a Monday
        week_start, week_end = calculate_week_range(date(2026, 7, 13))
        assert week_start == date(2026, 7, 13)  # Monday
        assert week_end == date(2026, 7, 19)    # Sunday

    def test_calculate_week_range_from_wednesday(self):
        """Calculating from a Wednesday should still give that week's Mon-Sun."""
        from manager_os.startup_calendar import calculate_week_range
        # 2026-07-15 is a Wednesday
        week_start, week_end = calculate_week_range(date(2026, 7, 15))
        assert week_start == date(2026, 7, 13)  # Monday
        assert week_end == date(2026, 7, 19)    # Sunday

    def test_freshness_skip_when_recent_sync(self, tmp_path, monkeypatch):
        """If a recent successful sync exists, should not sync again."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        # Seed a recent sync record
        now = datetime.utcnow()
        conn.execute(
            "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["recent-sync", date(2026, 7, 13), "09:00", "Recent", '["Alice"]', "[]", "calendar_sync", "e1", now],
        )
        conn.close()

        from manager_os.startup_calendar import should_sync_week
        assert not should_sync_week(db_path, date(2026, 7, 13))

    def test_freshness_sync_when_stale(self, tmp_path, monkeypatch):
        """If last sync is older than 6 hours, should sync."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        old_time = datetime(2026, 7, 10, 12, 0, 0)
        conn.execute(
            "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["old-sync", date(2026, 7, 10), "09:00", "Old", '["Alice"]', "[]", "calendar_sync", "e1", old_time],
        )
        conn.close()

        from manager_os.startup_calendar import should_sync_week
        assert should_sync_week(db_path, date(2026, 7, 13))

    def test_freshness_sync_when_no_meetings(self, tmp_path, monkeypatch):
        """If no meetings exist for the current week, should sync."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        conn.close()

        from manager_os.startup_calendar import should_sync_week
        assert should_sync_week(db_path, date(2026, 7, 13))


# ---------------------------------------------------------------------------
# Phase 5: Meeting classification tests
# ---------------------------------------------------------------------------


class TestMeetingClassification:
    def test_data_leaders_standup_exact_match(self):
        """Data Leaders Standup should match the exact profile."""
        from manager_os.extract.meeting_profiles import match_exact_profile
        meeting = {
            "title": "Data Leaders Standup",
            "attendees": ["manager@example.com"],
            "external_id": "evt-123",
        }
        profile = match_exact_profile(meeting)
        assert profile is not None
        assert profile["profile_id"] == "data_leaders_standup"
        assert profile["meeting_type"] == "upward_daily_status"

    def test_data_leaders_variant_match(self):
        """Variant titles should also match."""
        from manager_os.extract.meeting_profiles import match_exact_profile
        meeting = {"title": "Data Leadership Standup", "attendees": ["mgr@example.com"]}
        profile = match_exact_profile(meeting)
        assert profile is not None
        assert profile["profile_id"] == "data_leaders_standup"

    def test_no_prep_focus_time_detected(self):
        """Focus time should be classified as no_prep before broad rules."""
        from manager_os.extract.meeting_profiles import classify_meeting
        meeting = {
            "title": "Focus Time",
            "attendees": [],
            "description_summary": "",
        }
        result = classify_meeting(meeting)
        assert result["meeting_type"] == "no_prep"
        assert result["prep_required"] is False

    def test_no_prep_lunch_detected(self):
        from manager_os.extract.meeting_profiles import classify_meeting
        meeting = {"title": "Lunch", "attendees": [], "description_summary": ""}
        result = classify_meeting(meeting)
        assert result["meeting_type"] == "no_prep"

    def test_no_prep_ooo_detected(self):
        from manager_os.extract.meeting_profiles import classify_meeting
        meeting = {"title": "Out of Office", "attendees": [], "description_summary": ""}
        result = classify_meeting(meeting)
        assert result["meeting_type"] == "no_prep"

    def test_exact_profile_overrides_classification(self):
        """Exact profile match should take precedence over LLM classification."""
        from manager_os.extract.meeting_profiles import classify_meeting
        meeting = {
            "title": "Data Leaders Standup",
            "attendees": ["mgr@example.com"],
            "description_summary": "Daily sync",
        }
        result = classify_meeting(meeting)
        assert result["profile_id"] == "data_leaders_standup"
        assert result["meeting_type"] == "upward_daily_status"
        assert result["classification_source"] == "exact_profile"

    def test_generic_meeting_gets_classification(self):
        """A meeting without an exact profile should get a classification."""
        from manager_os.extract.meeting_profiles import classify_meeting
        meeting = {
            "title": "Project Review",
            "attendees": ["alice@example.com", "bob@example.com"],
            "description_summary": "Review project status",
        }
        result = classify_meeting(meeting)
        assert "meeting_type" in result
        assert result["classification_source"] in ("llm", "heuristic")


# ---------------------------------------------------------------------------
# Phase 6: LLM prep generation tests (mocked)
# ---------------------------------------------------------------------------


class TestLLMPrepGeneration:
    def test_strict_json_parsing(self):
        """Valid JSON response should parse successfully."""
        from manager_os.extract.llm_meeting_prep import parse_llm_prep_response
        raw = json.dumps({
            "meeting_type": "upward_daily_status",
            "objective": "Give my manager a concise daily leadership update.",
            "today_priorities": [
                {"text": "Priority 1", "why_it_matters": "Important", "source_ids": ["s1"]}
            ],
            "progress_since_last_meeting": [],
            "help_needed": [],
            "decisions_needed": [],
            "risks_to_flag": [],
            "commitments": [],
            "likely_follow_up_questions": [],
            "talk_track": "Today I'm working on...",
            "missing_context": [],
            "source_ids": ["s1"],
        })
        result = parse_llm_prep_response(raw, "upward_daily_status")
        assert result is not None
        assert result["meeting_type"] == "upward_daily_status"
        assert len(result["today_priorities"]) == 1

    def test_invalid_json_rejected(self):
        """Invalid JSON should raise a parse error."""
        from manager_os.extract.llm_meeting_prep import parse_llm_prep_response, PrepParseError
        with pytest.raises(PrepParseError):
            parse_llm_prep_response("not json {{{", "upward_daily_status")

    def test_schema_validation_missing_required_field(self):
        """Missing required field should fail validation."""
        from manager_os.extract.llm_meeting_prep import parse_llm_prep_response, PrepValidationError
        raw = json.dumps({
            "meeting_type": "upward_daily_status",
            # Missing objective, today_priorities, etc.
        })
        with pytest.raises(PrepValidationError):
            parse_llm_prep_response(raw, "upward_daily_status")

    def test_citation_validation_rejects_unsupported_source(self):
        """An item citing a non-existent source_id should be flagged."""
        from manager_os.extract.llm_meeting_prep import validate_citations
        prep = {
            "today_priorities": [
                {"text": "Priority", "why_it_matters": "Because", "source_ids": ["nonexistent"]}
            ],
            "source_ids": ["s1", "s2"],
        }
        issues = validate_citations(prep, ["s1", "s2"])
        assert len(issues) > 0
        assert "nonexistent" in issues[0]

    def test_citation_validation_accepts_valid_sources(self):
        from manager_os.extract.llm_meeting_prep import validate_citations
        prep = {
            "today_priorities": [
                {"text": "Priority", "why_it_matters": "Because", "source_ids": ["s1"]}
            ],
            "source_ids": ["s1"],
        }
        issues = validate_citations(prep, ["s1"])
        assert len(issues) == 0

    def test_llm_timeout_handled(self):
        """LLM timeout should produce a distinct failure state."""
        from manager_os.extract.llm_meeting_prep import generate_prep, PrepGenerationError
        import subprocess
        meeting = {"id": "m1", "title": "Test Meeting", "attendees": ["alice@example.com"]}
        context_bundle = {"sources": [], "items": []}
        with patch(
            "manager_os.llm.gemini_cli.generate",
            side_effect=subprocess.TimeoutExpired(cmd="gemini", timeout=60),
        ):
            with pytest.raises(PrepGenerationError) as exc_info:
                generate_prep(meeting, "upward_daily_status", context_bundle)
            assert "timeout" in str(exc_info.value).lower()

    def test_llm_unavailable_handled(self):
        from manager_os.extract.llm_meeting_prep import generate_prep, PrepGenerationError
        from manager_os.llm.gemini_cli import GeminiUnavailable
        meeting = {"id": "m1", "title": "Test", "attendees": ["a@example.com"]}
        context_bundle = {"sources": [], "items": []}
        with patch(
            "manager_os.llm.gemini_cli.generate",
            side_effect=GeminiUnavailable("Gemini not found"),
        ):
            with pytest.raises(PrepGenerationError) as exc_info:
                generate_prep(meeting, "upward_daily_status", context_bundle)
            assert "unavailable" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Phase 7: Prep persistence + freshness tests
# ---------------------------------------------------------------------------


class TestPrepPersistence:
    def test_prep_persists_to_db(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        from manager_os.extract.llm_meeting_prep import persist_prep
        prep_data = {
            "meeting_type": "upward_daily_status",
            "objective": "Test objective",
            "today_priorities": [],
            "talk_track": "Test talk track",
            "source_ids": ["s1"],
        }
        result = persist_prep(
            conn,
            meeting_id="m1",
            prep_data=prep_data,
            classification="upward_daily_status",
            profile_id="data_leaders_standup",
            meeting_fingerprint="fp1",
            source_fingerprint="sfp1",
            source_references=["s1"],
            llm_provider="gemini_cli",
            llm_model="gemini-2.5-flash",
        )
        assert result is not None
        # Verify persisted
        row = conn.execute(
            "SELECT meeting_id, classification, generation_status FROM meeting_prep WHERE meeting_id = ?",
            ["m1"],
        ).fetchone()
        assert row is not None
        assert row[0] == "m1"
        assert row[1] == "upward_daily_status"
        conn.close()

    def test_current_prep_returned_when_fingerprints_match(self, tmp_path, monkeypatch):
        """When meeting + source fingerprints unchanged, prep is current."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        from manager_os.extract.llm_meeting_prep import persist_prep, get_prep_freshness
        prep_data = {"meeting_type": "test", "talk_track": "test"}
        persist_prep(
            conn, "m1", prep_data, "test", "test_profile",
            "fp1", "sfp1", ["s1"], "gemini_cli", "model",
        )
        freshness = get_prep_freshness(conn, "m1", "fp1", "sfp1")
        assert freshness == "current"

    def test_stale_prep_when_meeting_fingerprint_changes(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        from manager_os.extract.llm_meeting_prep import persist_prep, get_prep_freshness
        prep_data = {"meeting_type": "test", "talk_track": "test"}
        persist_prep(
            conn, "m1", prep_data, "test", "test_profile",
            "fp1", "sfp1", ["s1"], "gemini_cli", "model",
        )
        # Different meeting fingerprint → stale
        freshness = get_prep_freshness(conn, "m1", "different_fp", "sfp1")
        assert freshness == "stale"

    def test_stale_prep_when_source_fingerprint_changes(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        from manager_os.extract.llm_meeting_prep import persist_prep, get_prep_freshness
        prep_data = {"meeting_type": "test", "talk_track": "test"}
        persist_prep(
            conn, "m1", prep_data, "test", "test_profile",
            "fp1", "sfp1", ["s1"], "gemini_cli", "model",
        )
        freshness = get_prep_freshness(conn, "m1", "fp1", "different_sfp")
        assert freshness == "stale"

    def test_not_generated_when_no_prep(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        from manager_os.extract.llm_meeting_prep import get_prep_freshness
        freshness = get_prep_freshness(conn, "m1", "fp1", "sfp1")
        assert freshness == "not_generated"


# ---------------------------------------------------------------------------
# Phase 8: No automatic prep on startup / no external calls on page load
# ---------------------------------------------------------------------------


class TestNoAutomaticPrep:
    def test_no_prep_generation_on_page_load(self, tmp_path, monkeypatch):
        """GET /api/meetings must NOT trigger prep generation."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        conn = get_connection(db_path)
        now = datetime.now(timezone.utc)
        conn.execute(
            "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["m1", date(2026, 7, 13), "09:00", "Standup", '["Alice"]', "[]", "calendar_sync", "e1", now],
        )
        conn.close()
        with patch("manager_os.llm.gemini_cli.generate") as mock_gen:
            client = TestClient(create_app())
            resp = client.get("/api/meetings", params={"date": "2026-07-13"})
            assert resp.status_code == 200
            mock_gen.assert_not_called()
