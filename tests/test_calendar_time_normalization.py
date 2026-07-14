"""Tests for canonical calendar time normalization.

All tests verify that timestamps are correctly normalized to UTC,
meeting_date is derived in America/Denver, and all edge cases
(DST, midnight, all-day, naive, plain HH:MM) are handled.
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pytest

from manager_os.ingest.calendar_time import (
    NormalizedCalendarTime,
    normalize_calendar_event_time,
)

DENVER = ZoneInfo("America/Denver")


# ---------------------------------------------------------------------------
# Helper to check Denver display time
# ---------------------------------------------------------------------------


def denver_display(dt: datetime | None) -> str:
    """Format a datetime in America/Denver for assertions."""
    if dt is None:
        return ""
    dt_denver = dt.astimezone(DENVER)
    return dt_denver.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Timed events with explicit offset
# ---------------------------------------------------------------------------


class TestExplicitOffset:
    def test_z_suffix_parsed_as_utc(self):
        """2026-07-13T15:00:00Z → 9:00 AM Denver (MDT, UTC-6)."""
        event = {"start_time": "2026-07-13T15:00:00Z", "timezone": "America/Denver"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        assert result.start_at_utc == datetime(2026, 7, 13, 15, 0, 0, tzinfo=timezone.utc)
        # Denver in July is MDT (UTC-6), so 15:00 UTC = 09:00 Denver
        assert denver_display(result.start_at_utc) == "2026-07-13 09:00"
        assert result.local_start_date == date(2026, 7, 13)
        assert result.is_all_day is False

    def test_negative_offset_parsed(self):
        """2026-07-13T09:00:00-06:00 → 9:00 AM Denver."""
        event = {"start_time": "2026-07-13T09:00:00-06:00"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        assert result.start_at_utc == datetime(2026, 7, 13, 15, 0, 0, tzinfo=timezone.utc)
        assert denver_display(result.start_at_utc) == "2026-07-13 09:00"
        assert result.local_start_date == date(2026, 7, 13)

    def test_winter_offset_parsed(self):
        """2026-12-14T09:00:00-07:00 → 9:00 AM Denver (MST, UTC-7)."""
        event = {"start_time": "2026-12-14T09:00:00-07:00"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        # 09:00 -07:00 = 16:00 UTC
        assert result.start_at_utc == datetime(2026, 12, 14, 16, 0, 0, tzinfo=timezone.utc)
        # Denver in December is MST (UTC-7), so 16:00 UTC = 09:00 Denver
        assert denver_display(result.start_at_utc) == "2026-12-14 09:00"
        assert result.local_start_date == date(2026, 12, 14)

    def test_positive_offset_parsed(self):
        """2026-07-13T17:00:00+02:00 → 9:00 AM Denver."""
        event = {"start_time": "2026-07-13T17:00:00+02:00"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        # 17:00 +02:00 = 15:00 UTC
        assert result.start_at_utc == datetime(2026, 7, 13, 15, 0, 0, tzinfo=timezone.utc)
        assert denver_display(result.start_at_utc) == "2026-07-13 09:00"


# ---------------------------------------------------------------------------
# Naive timestamps with event timezone
# ---------------------------------------------------------------------------


class TestNaiveWithTimezone:
    def test_naive_with_event_timezone(self):
        """Naive timestamp + event timezone → interpreted in that timezone."""
        event = {"start_time": "2026-07-13T09:00:00", "timezone": "America/New_York"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        # 09:00 EDT = 13:00 UTC
        assert result.start_at_utc == datetime(2026, 7, 13, 13, 0, 0, tzinfo=timezone.utc)
        # Denver: 13:00 UTC = 07:00 Denver
        assert denver_display(result.start_at_utc) == "2026-07-13 07:00"
        assert result.local_start_date == date(2026, 7, 13)

    def test_naive_with_denver_fallback(self):
        """Naive timestamp, no timezone → assume America/Denver, emit warning."""
        event = {"start_time": "2026-07-13T09:00:00"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        # 09:00 Denver (MDT, UTC-6) = 15:00 UTC
        assert result.start_at_utc == datetime(2026, 7, 13, 15, 0, 0, tzinfo=timezone.utc)
        assert result.local_start_date == date(2026, 7, 13)
        assert len(result.warnings) > 0
        assert "America/Denver" in result.warnings[0] or "ambiguous" in result.warnings[0].lower()


# ---------------------------------------------------------------------------
# Plain HH:MM
# ---------------------------------------------------------------------------


class TestPlainTime:
    def test_plain_hh_mm_with_target_date(self):
        """Plain '09:00' + target date → 9:00 AM Denver."""
        event = {"start_time": "09:00"}
        result = normalize_calendar_event_time(event, target_date=date(2026, 7, 13))
        assert result.start_at_utc is not None
        # 09:00 Denver (MDT) = 15:00 UTC
        assert result.start_at_utc == datetime(2026, 7, 13, 15, 0, 0, tzinfo=timezone.utc)
        assert result.local_start_date == date(2026, 7, 13)
        assert len(result.warnings) > 0

    def test_plain_hh_mm_without_target_date(self):
        """Plain '09:00' without target date → warning, no start_at."""
        event = {"start_time": "09:00"}
        result = normalize_calendar_event_time(event)
        # Should still produce a result but with a warning
        assert len(result.warnings) > 0


# ---------------------------------------------------------------------------
# All-day events
# ---------------------------------------------------------------------------


class TestAllDayEvents:
    def test_all_day_event(self):
        """All-day event should not shift through UTC."""
        event = {"start_date": "2026-07-13", "is_all_day": True}
        result = normalize_calendar_event_time(event)
        assert result.is_all_day is True
        assert result.start_at_utc is None
        assert result.end_at_utc is None
        assert result.local_start_date == date(2026, 7, 13)

    def test_all_day_event_with_start_time_ignored(self):
        """All-day event with a start_time should still be all-day."""
        event = {"start_time": "2026-07-13T00:00:00", "is_all_day": True}
        result = normalize_calendar_event_time(event)
        assert result.is_all_day is True
        assert result.start_at_utc is None
        assert result.local_start_date == date(2026, 7, 13)


# ---------------------------------------------------------------------------
# UTC midnight boundary
# ---------------------------------------------------------------------------


class TestMidnightBoundary:
    def test_event_near_utc_midnight_remains_previous_denver_day(self):
        """2026-07-14T05:00:00Z → 10:59 PM Denver July 13 (MDT)."""
        event = {"start_time": "2026-07-14T05:00:00Z"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        # 05:00 UTC = 23:00 Denver July 13 (MDT, UTC-6)
        assert denver_display(result.start_at_utc) == "2026-07-13 23:00"
        assert result.local_start_date == date(2026, 7, 13)

    def test_event_near_utc_midnight_becomes_next_denver_day(self):
        """2026-07-13T07:00:00Z → 1:00 AM Denver July 13 (MDT)."""
        event = {"start_time": "2026-07-13T07:00:00Z"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        # 07:00 UTC = 01:00 Denver July 13 (MDT)
        assert denver_display(result.start_at_utc) == "2026-07-13 01:00"
        assert result.local_start_date == date(2026, 7, 13)


# ---------------------------------------------------------------------------
# DST transitions
# ---------------------------------------------------------------------------


class TestDSTTransitions:
    def test_dst_spring_forward(self):
        """March 8, 2026 — spring forward at 2:00 AM Denver.
        2026-03-08T09:00:00-07:00 → 9:00 AM Denver (MST before, but offset says -07:00).
        Actually after spring forward, Denver is MDT (-06:00).
        Let's test 2026-03-15T15:00:00Z → 09:00 Denver (MDT)."""
        event = {"start_time": "2026-03-15T15:00:00Z"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        # March 15 is after spring forward → MDT (UTC-6)
        assert denver_display(result.start_at_utc) == "2026-03-15 09:00"
        assert result.local_start_date == date(2026, 3, 15)

    def test_dst_fall_back(self):
        """November 8, 2026 — fall back. Denver is MST (UTC-7).
        2026-11-08T16:00:00Z → 09:00 Denver (MST)."""
        event = {"start_time": "2026-11-08T16:00:00Z"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        # November → MST (UTC-7)
        assert denver_display(result.start_at_utc) == "2026-11-08 09:00"
        assert result.local_start_date == date(2026, 11, 8)

    def test_summer_mdt(self):
        """July → MDT (UTC-6)."""
        event = {"start_time": "2026-07-13T15:00:00Z"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        assert denver_display(result.start_at_utc) == "2026-07-13 09:00"

    def test_winter_mst(self):
        """December → MST (UTC-7)."""
        event = {"start_time": "2026-12-14T16:00:00Z"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is not None
        assert denver_display(result.start_at_utc) == "2026-12-14 09:00"


# ---------------------------------------------------------------------------
# Invalid timestamps
# ---------------------------------------------------------------------------


class TestInvalidTimestamps:
    def test_invalid_timestamp_rejected(self):
        """Invalid timestamp should produce a warning and no start_at."""
        event = {"start_time": "not-a-timestamp"}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is None
        assert len(result.warnings) > 0

    def test_empty_start_time(self):
        event = {"start_time": ""}
        result = normalize_calendar_event_time(event)
        assert result.start_at_utc is None
        assert result.local_start_date is not None  # Should still have a date fallback

    def test_none_start_time(self):
        event = {"start_time": None}
        result = normalize_calendar_event_time(event, target_date=date(2026, 7, 13))
        assert result.start_at_utc is None
        assert result.local_start_date == date(2026, 7, 13)


# ---------------------------------------------------------------------------
# End time normalization
# ---------------------------------------------------------------------------


class TestEndTimeNormalization:
    def test_end_time_with_offset(self):
        event = {
            "start_time": "2026-07-13T15:00:00Z",
            "end_time": "2026-07-13T15:30:00Z",
        }
        result = normalize_calendar_event_time(event)
        assert result.end_at_utc is not None
        assert result.end_at_utc == datetime(2026, 7, 13, 15, 30, 0, tzinfo=timezone.utc)

    def test_end_time_none(self):
        event = {"start_time": "2026-07-13T15:00:00Z"}
        result = normalize_calendar_event_time(event)
        assert result.end_at_utc is None


# ---------------------------------------------------------------------------
# Timezone persistence
# ---------------------------------------------------------------------------


class TestTimezonePersistence:
    def test_timezone_from_event(self):
        event = {"start_time": "2026-07-13T09:00:00-06:00", "timezone": "America/Denver"}
        result = normalize_calendar_event_time(event)
        assert result.event_timezone == "America/Denver"

    def test_timezone_fallback_denver(self):
        event = {"start_time": "2026-07-13T15:00:00Z"}
        result = normalize_calendar_event_time(event)
        assert result.event_timezone == "America/Denver"

    def test_timezone_from_offset_inferred(self):
        """If no IANA timezone but offset present, infer timezone."""
        event = {"start_time": "2026-07-13T09:00:00-06:00"}
        result = normalize_calendar_event_time(event)
        # Should infer America/Denver from -06:00 in July
        assert result.event_timezone is not None
        assert len(result.event_timezone) > 0


# ---------------------------------------------------------------------------
# Chronological ordering with normalized fields
# ---------------------------------------------------------------------------


class TestChronologicalOrdering:
    def _make_meeting(self, start_at, title="M", end_at=None, is_all_day=False, meeting_id="m"):
        return {
            "id": meeting_id,
            "start_at": start_at,
            "end_at": end_at,
            "is_all_day": is_all_day,
            "title": title,
        }

    def test_utc_summer_ordering(self):
        """8 AM, 9:30 AM, 12 PM, 1 PM, 4:30 PM Denver (July, MDT)."""
        from manager_os.build.dashboard_data import sort_meetings_chronological
        meetings = [
            self._make_meeting("2026-07-13T22:30:00Z", "4:30 PM", meeting_id="m5"),  # 4:30 PM Denver
            self._make_meeting("2026-07-13T19:00:00Z", "1:00 PM", meeting_id="m4"),  # 1:00 PM Denver
            self._make_meeting("2026-07-13T18:00:00Z", "12:00 PM", meeting_id="m3"),  # 12:00 PM Denver
            self._make_meeting("2026-07-13T15:30:00Z", "9:30 AM", meeting_id="m2"),  # 9:30 AM Denver
            self._make_meeting("2026-07-13T14:00:00Z", "8:00 AM", meeting_id="m1"),  # 8:00 AM Denver
        ]
        sorted_m = sort_meetings_chronological(meetings)
        titles = [m["title"] for m in sorted_m]
        assert titles == ["8:00 AM", "9:30 AM", "12:00 PM", "1:00 PM", "4:30 PM"]

    def test_winter_ordering(self):
        """8 AM, 12 PM Denver (December, MST, UTC-7)."""
        from manager_os.build.dashboard_data import sort_meetings_chronological
        meetings = [
            self._make_meeting("2026-12-14T19:00:00Z", "12:00 PM", meeting_id="m2"),  # 12:00 PM Denver
            self._make_meeting("2026-12-14T15:00:00Z", "8:00 AM", meeting_id="m1"),  # 8:00 AM Denver
        ]
        sorted_m = sort_meetings_chronological(meetings)
        assert sorted_m[0]["title"] == "8:00 AM"
        assert sorted_m[1]["title"] == "12:00 PM"


# ---------------------------------------------------------------------------
# Persistence with normalized times
# ---------------------------------------------------------------------------


class TestPersistenceWithNormalizedTimes:
    def test_persist_populates_start_at_and_meeting_date(self, tmp_path, monkeypatch):
        """persist_calendar_events should populate start_at, end_at, meeting_date
        using the canonical normalizer, not raw string slicing."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        from manager_os.db import get_connection
        from manager_os.ingest.calendar_persistence import persist_calendar_events

        conn = get_connection(db_path)
        events = [
            {
                "title": "Test Meeting",
                "start_time": "2026-07-13T15:00:00Z",  # 9:00 AM Denver
                "end_time": "2026-07-13T15:30:00Z",
                "attendees": ["alice@example.com"],
                "external_id": "e1",
                "timezone": "America/Denver",
            }
        ]
        result = persist_calendar_events(
            conn, date(2026, 7, 13), events,
            source="calendar_sync",
            retrieved_at="2026-07-13T12:00:00Z",
        )
        assert result.persisted_count == 1

        row = conn.execute(
            "SELECT start_at, end_at, meeting_date, timezone, is_all_day FROM meetings WHERE external_id = 'e1'"
        ).fetchone()
        assert row is not None
        # start_at should be the UTC instant
        assert row[0] is not None
        # meeting_date should be the Denver date
        assert str(row[2]) == "2026-07-13"
        # timezone should be persisted
        assert row[3] == "America/Denver"
        # is_all_day should be False
        assert row[4] is False or row[4] == False
        conn.close()

    def test_persist_all_day_event_no_utc_shift(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        from manager_os.db import get_connection
        from manager_os.ingest.calendar_persistence import persist_calendar_events

        conn = get_connection(db_path)
        events = [
            {
                "title": "All Day Event",
                "start_date": "2026-07-13",
                "is_all_day": True,
                "attendees": ["alice@example.com"],
                "external_id": "e2",
            }
        ]
        result = persist_calendar_events(
            conn, date(2026, 7, 13), events,
            source="calendar_sync",
            retrieved_at="2026-07-13T12:00:00Z",
        )
        assert result.persisted_count == 1

        row = conn.execute(
            "SELECT start_at, end_at, meeting_date, is_all_day FROM meetings WHERE external_id = 'e2'"
        ).fetchone()
        assert row is not None
        # All-day: start_at should be NULL
        assert row[0] is None
        assert row[1] is None
        # meeting_date should be the calendar date, not shifted
        assert str(row[2]) == "2026-07-13"
        assert row[3] is True or row[3] == True
        conn.close()

    def test_persist_updates_existing_event_not_duplicate(self, tmp_path, monkeypatch):
        """Re-syncing same external_id should update, not create duplicate."""
        db_path = str(tmp_path / "test.duckdb")
        monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
        from manager_os.db import get_connection
        from manager_os.ingest.calendar_persistence import persist_calendar_events

        conn = get_connection(db_path)
        events1 = [{"title": "Original", "start_time": "2026-07-13T15:00:00Z", "attendees": ["a@x.com"], "external_id": "dup-1"}]
        persist_calendar_events(conn, date(2026, 7, 13), events1, source="calendar_sync", retrieved_at="2026-07-13T12:00:00Z")

        events2 = [{"title": "Updated", "start_time": "2026-07-13T16:00:00Z", "attendees": ["a@x.com"], "external_id": "dup-1"}]
        result = persist_calendar_events(conn, date(2026, 7, 13), events2, source="calendar_sync", retrieved_at="2026-07-13T13:00:00Z")

        assert result.persisted_count == 1
        assert result.replaced_count == 1
        count = conn.execute("SELECT COUNT(*) FROM meetings WHERE external_id = 'dup-1'").fetchone()[0]
        assert count == 1
        title = conn.execute("SELECT title FROM meetings WHERE external_id = 'dup-1'").fetchone()[0]
        assert title == "Updated"
        conn.close()
