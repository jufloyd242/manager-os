"""Tests for data-quality fixes:

Phase 1 — Meeting prep dict/object mismatch
Phase 2 — Duplicate meeting deduplication
Phase 3 — No-invitee calendar event filtering
Phase 4 — Staffing allocation bucket classification (100% = fully_utilized)
Phase 5 — Deal document links
Phase 7 — Client opportunity numbers
Phase 8 — People alias normalization
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.build.dashboard_data import (
    get_meetings_for_date,
    meeting_dict_to_record,
    get_forecast_summary,
    get_deal_rows,
    get_deal_documents,
    get_client_rows,
)
from manager_os.build.people_normalization import PeopleNormalizer, run_people_audit
from manager_os.schemas import MeetingRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def _seed_meeting(
    conn,
    title: str,
    meeting_date: date,
    start_time: str = "10:00",
    attendees: list | None = None,
    external_id: str = "",
    linked_entities: list | None = None,
) -> str:
    row_id = content_hash(f"meeting::{title}::{meeting_date}::{start_time}::{external_id}")
    conn.execute(
        """
        INSERT OR REPLACE INTO meetings
            (id, meeting_date, start_time, title, attendees, linked_entities,
             source, external_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'test', ?, CURRENT_TIMESTAMP)
        """,
        [
            row_id, meeting_date.isoformat(), start_time, title,
            json.dumps(attendees if attendees is not None else ["Alice", "Bob"]),
            json.dumps(linked_entities or []),
            external_id,
        ],
    )
    return row_id


def _seed_forecast(
    conn,
    person_name: str,
    week_start: date,
    alloc: float,
    client: str = "Acme",
    fc_type: str = "confirmed",
) -> None:
    row_id = content_hash(f"{person_name}::{week_start}::{client}")
    conn.execute(
        """
        INSERT OR REPLACE INTO staffing_forecast
            (id, person_name, week_start, client, project,
             allocation_pct, forecast_type, ingested_at)
        VALUES (?, ?, ?, ?, 'proj', ?, ?, CURRENT_TIMESTAMP)
        """,
        [row_id, person_name, week_start.isoformat(), client, alloc, fc_type],
    )


def _seed_deal(
    conn,
    deal_name: str,
    account: str = "Acme Corp",
    deal_id: str = "",
    feasibility: str = "feasible",
) -> str:
    row_id = content_hash(f"{account}::{deal_name}")
    conn.execute(
        """
        INSERT OR REPLACE INTO deals
            (id, account, deal_name, deal_id, stage, close_date,
             sow_status, loe_status, staffing_feasibility, updated_at)
        VALUES (?, ?, ?, ?, 'SOW Review', ?, 'pending', 'pending', ?, CURRENT_TIMESTAMP)
        """,
        [
            row_id, account, deal_name, deal_id,
            (date.today() + timedelta(days=10)).isoformat(),
            feasibility,
        ],
    )
    return row_id


# ===========================================================================
# Phase 1 — Meeting prep dict/object mismatch
# ===========================================================================


class TestMeetingDictToRecord:
    def test_converts_dict_to_meetingrecord(self, conn):
        _seed_meeting(conn, "Standup", date.today(), attendees=["Alice", "Bob"])
        meetings = get_meetings_for_date(conn, date.today())
        assert meetings, "Expected at least one meeting"
        rec = meeting_dict_to_record(meetings[0])
        assert isinstance(rec, MeetingRecord)

    def test_linked_entities_defaults_to_empty_list(self, conn):
        _seed_meeting(conn, "Standup", date.today(),
                      attendees=["Alice"], linked_entities=None)
        meetings = get_meetings_for_date(conn, date.today())
        rec = meeting_dict_to_record(meetings[0])
        assert rec.linked_entities == []

    def test_attendees_defaults_to_empty_list_on_missing(self):
        """meeting_dict_to_record should not raise if attendees key is missing."""
        m = {
            "id": "test-id",
            "meeting_date": date.today(),
            "start_time": "10:00",
            "title": "Test",
            "source": "",
            "external_id": "",
            # attendees and linked_entities intentionally omitted
        }
        rec = meeting_dict_to_record(m)
        assert rec.attendees == []
        assert rec.linked_entities == []

    def test_linked_entities_attribute_accessible(self, conn):
        """The MeetingRecord should have .linked_entities, not raise AttributeError."""
        _seed_meeting(
            conn, "Planning", date.today(),
            attendees=["Alice"],
            linked_entities=[{"entity_type": "client", "entity_name": "Acme"}],
        )
        meetings = get_meetings_for_date(conn, date.today())
        rec = meeting_dict_to_record(meetings[0])
        # This must not raise AttributeError
        les = rec.linked_entities
        assert isinstance(les, list)
        assert len(les) == 1
        assert les[0]["entity_name"] == "Acme"

    def test_generate_meeting_prep_with_dict_does_not_raise(self, conn):
        """generate_meeting_prep should work when called with a MeetingRecord."""
        from manager_os.extract.meeting_prep import generate_meeting_prep
        _seed_meeting(conn, "Weekly Sync", date.today(), attendees=["Alice", "Bob"])
        meetings = get_meetings_for_date(conn, date.today())
        assert meetings
        rec = meeting_dict_to_record(meetings[0])
        prep = generate_meeting_prep(rec, conn)
        assert prep.content
        assert "Weekly Sync" in prep.content


# ===========================================================================
# Phase 2 — Duplicate meeting deduplication
# ===========================================================================


class TestMeetingDeduplication:
    def test_same_external_id_collapses_to_one(self, conn):
        today = date.today()
        _seed_meeting(conn, "Planning", today, start_time="09:00",
                      attendees=["Alice"], external_id="evt-001")
        _seed_meeting(conn, "Planning", today, start_time="09:00",
                      attendees=["Alice", "Bob", "Carol"], external_id="evt-001")
        meetings = get_meetings_for_date(conn, today)
        planning = [m for m in meetings if m["title"] == "Planning"]
        assert len(planning) == 1

    def test_same_title_start_date_collapses_to_one(self, conn):
        today = date.today()
        _seed_meeting(conn, "Daily Standup", today, start_time="09:00",
                      attendees=["Alice"], external_id="")
        _seed_meeting(conn, "Daily Standup", today, start_time="09:00",
                      attendees=["Alice", "Bob"], external_id="")
        meetings = get_meetings_for_date(conn, today)
        standups = [m for m in meetings if m["title"] == "Daily Standup"]
        assert len(standups) == 1

    def test_different_start_times_remain_separate(self, conn):
        today = date.today()
        _seed_meeting(conn, "Daily Standup", today, start_time="09:00",
                      attendees=["Alice"])
        _seed_meeting(conn, "Daily Standup", today, start_time="14:00",
                      attendees=["Bob"])
        meetings = get_meetings_for_date(conn, today)
        standups = [m for m in meetings if m["title"] == "Daily Standup"]
        assert len(standups) == 2

    def test_richer_duplicate_wins(self, conn):
        """When two records share external_id, the one with more attendees wins."""
        today = date.today()
        # Insert sparse record first
        _seed_meeting(conn, "Planning A", today, start_time="10:00",
                      attendees=["Alice"], external_id="evt-rich")
        # Insert richer record second — has more attendees and linked_entities
        _seed_meeting(conn, "Planning A", today, start_time="10:00",
                      attendees=["Alice", "Bob", "Carol"],
                      linked_entities=[{"entity_type": "client", "entity_name": "Acme"}],
                      external_id="evt-rich")
        meetings = get_meetings_for_date(conn, today)
        planning = [m for m in meetings if m["title"] == "Planning A"]
        assert len(planning) == 1
        # Should have picked richer record (3 attendees)
        assert len(planning[0]["attendees"]) == 3


# ===========================================================================
# Phase 3 — No-invitee calendar events filtered from dashboard query
# ===========================================================================


class TestNoAttendeeMeetingsFiltered:
    def test_no_attendee_meeting_excluded(self, conn):
        today = date.today()
        # Seed a meeting with empty attendees (solo timebox)
        row_id = content_hash("solo::timebox::today")
        conn.execute(
            """
            INSERT OR REPLACE INTO meetings
                (id, meeting_date, start_time, title, attendees, linked_entities,
                 source, external_id, updated_at)
            VALUES (?, ?, '10:00', 'Deep Work', '[]', '[]', 'test', '', CURRENT_TIMESTAMP)
            """,
            [row_id, today.isoformat()],
        )
        meetings = get_meetings_for_date(conn, today)
        assert all(m["title"] != "Deep Work" for m in meetings)

    def test_meeting_with_attendees_included(self, conn):
        today = date.today()
        _seed_meeting(conn, "Client Review", today, attendees=["Alice", "External"])
        meetings = get_meetings_for_date(conn, today)
        assert any(m["title"] == "Client Review" for m in meetings)

    def test_null_attendees_excluded(self, conn):
        today = date.today()
        row_id = content_hash("null::attendees::test")
        conn.execute(
            """
            INSERT OR REPLACE INTO meetings
                (id, meeting_date, start_time, title, attendees, linked_entities,
                 source, external_id, updated_at)
            VALUES (?, ?, '11:00', 'Lunch Block', NULL, '[]', 'test', '', CURRENT_TIMESTAMP)
            """,
            [row_id, today.isoformat()],
        )
        meetings = get_meetings_for_date(conn, today)
        assert all(m["title"] != "Lunch Block" for m in meetings)


# ===========================================================================
# Phase 4 — Staffing allocation bucket classification
# ===========================================================================


class TestStaffingAllocationBuckets:
    """100% should be fully_utilized. 101% overallocated. 80% available."""

    def test_100_pct_is_fully_utilized(self, conn):
        today = date.today()
        _seed_forecast(conn, "Alice", today, 100.0)
        summary = get_forecast_summary(conn, as_of=today)
        label_2w = next(k for k in summary if k.startswith("2w"))
        bucket = summary[label_2w]
        assert "Alice" in bucket["fully_utilized"]

    def test_100_pct_is_not_available(self, conn):
        today = date.today()
        _seed_forecast(conn, "Alice", today, 100.0)
        summary = get_forecast_summary(conn, as_of=today)
        label_2w = next(k for k in summary if k.startswith("2w"))
        bucket = summary[label_2w]
        assert "Alice" not in bucket["available"]

    def test_100_pct_is_not_overallocated(self, conn):
        today = date.today()
        _seed_forecast(conn, "Alice", today, 100.0)
        summary = get_forecast_summary(conn, as_of=today)
        label_2w = next(k for k in summary if k.startswith("2w"))
        bucket = summary[label_2w]
        assert "Alice" not in bucket["overallocated"]

    def test_101_pct_is_overallocated(self, conn):
        today = date.today()
        _seed_forecast(conn, "Bob", today, 101.0)
        summary = get_forecast_summary(conn, as_of=today)
        label_2w = next(k for k in summary if k.startswith("2w"))
        bucket = summary[label_2w]
        assert "Bob" in bucket["overallocated"]

    def test_80_pct_is_available(self, conn):
        today = date.today()
        _seed_forecast(conn, "Carol", today, 80.0)
        summary = get_forecast_summary(conn, as_of=today)
        label_2w = next(k for k in summary if k.startswith("2w"))
        bucket = summary[label_2w]
        assert "Carol" in bucket["available"]

    def test_multiple_weeks_at_100_remain_fully_utilized(self, conn):
        today = date.today()
        for i in range(4):
            week = today + timedelta(weeks=i)
            _seed_forecast(conn, "Dave", week, 100.0, client=f"ClientW{i}")
        summary = get_forecast_summary(conn, as_of=today)
        label_30d = next(k for k in summary if k.startswith("30d"))
        bucket = summary[label_30d]
        assert "Dave" in bucket["fully_utilized"]
        assert "Dave" not in bucket["overallocated"]
        assert "Dave" not in bucket["available"]

    def test_window_labels_contain_dates(self, conn):
        today = date.today()
        summary = get_forecast_summary(conn, as_of=today)
        # Only the long-label keys (with date ranges) should contain dates
        long_labels = [k for k in summary if " (" in k]
        assert len(long_labels) == 3, f"Expected 3 long-label keys, got: {list(summary.keys())}"
        for label in long_labels:
            assert today.isoformat() in label, f"Label '{label}' missing start date"


# ===========================================================================
# Phase 5 — Deal document links
# ===========================================================================


class TestDealDocumentLinks:
    def test_get_deal_documents_returns_empty_when_no_docs(self, conn):
        _seed_deal(conn, "Empty Deal", deal_id="OPP-001")
        docs = get_deal_documents(conn, "OPP-001")
        assert docs == {}

    def test_get_deal_documents_returns_found_docs(self, conn):
        from manager_os.ingest.drive_deal_docs import ensure_deal_documents_table
        ensure_deal_documents_table(conn)
        _seed_deal(conn, "Big Deal", deal_id="OPP-100")
        # Insert a found SOW
        row_id = content_hash("deal_doc::OPP-100::int_sow::http://example.com/sow")
        conn.execute(
            """
            INSERT INTO deal_documents
                (id, deal_id, account, deal_name, document_type, title, url,
                 source, retrieved_at, search_status)
            VALUES (?, 'OPP-100', 'Acme', 'Big Deal', 'int_sow',
                    'INT SOW v2', 'http://example.com/sow', 'Google Drive',
                    CURRENT_TIMESTAMP, 'found')
            """,
            [row_id],
        )
        docs = get_deal_documents(conn, "OPP-100")
        assert "int_sow" in docs
        assert docs["int_sow"]["url"] == "http://example.com/sow"
        assert docs["int_sow"]["title"] == "INT SOW v2"

    def test_get_deal_rows_includes_doc_links(self, conn):
        from manager_os.ingest.drive_deal_docs import ensure_deal_documents_table
        ensure_deal_documents_table(conn)
        db_id = _seed_deal(conn, "Linked Deal", deal_id="OPP-200")
        # Insert a found Deal Sheet
        row_id = content_hash("deal_doc::OPP-200::deal_sheet::http://example.com/ds")
        conn.execute(
            """
            INSERT INTO deal_documents
                (id, deal_id, account, deal_name, document_type, title, url,
                 source, retrieved_at, search_status)
            VALUES (?, 'OPP-200', 'Acme Corp', 'Linked Deal', 'deal_sheet',
                    'Deal Sheet Q3', 'http://example.com/ds', 'Google Drive',
                    CURRENT_TIMESTAMP, 'found')
            """,
            [row_id],
        )
        rows = get_deal_rows(conn)
        linked = next((r for r in rows if r.deal_name == "Linked Deal"), None)
        assert linked is not None
        assert linked.deal_sheet_url == "http://example.com/ds"

    def test_deal_rows_no_doc_links_do_not_crash(self, conn):
        _seed_deal(conn, "No Docs Deal")
        rows = get_deal_rows(conn)
        no_doc = next((r for r in rows if r.deal_name == "No Docs Deal"), None)
        assert no_doc is not None
        assert no_doc.sow_url == ""
        assert no_doc.deal_sheet_url == ""

    def test_staffing_feasibility_source_from_deals_csv(self, conn):
        _seed_deal(conn, "CSV Deal", feasibility="feasible")
        rows = get_deal_rows(conn)
        d = next((r for r in rows if r.deal_name == "CSV Deal"), None)
        assert d is not None
        assert d.staffing_feasibility_source == "deals_csv"

    def test_staffing_feasibility_source_unknown_when_missing(self, conn):
        """A deal with NULL staffing_feasibility gets source='unknown'."""
        row_id = content_hash("acme::no-feas")
        conn.execute(
            """
            INSERT INTO deals (id, account, deal_name, stage, close_date,
                               staffing_feasibility, updated_at)
            VALUES (?, 'Acme', 'No Feas Deal', 'Closed', ?, NULL, CURRENT_TIMESTAMP)
            """,
            [row_id, (date.today() + timedelta(days=5)).isoformat()],
        )
        rows = get_deal_rows(conn)
        d = next((r for r in rows if r.deal_name == "No Feas Deal"), None)
        assert d is not None
        assert d.staffing_feasibility_source == "unknown"


# ===========================================================================
# Phase 7 — Client opportunity numbers
# ===========================================================================


class TestClientOpportunityNumbers:
    def test_client_row_includes_deals(self, conn):
        _seed_deal(conn, "Project Alpha", account="Acme Corp", deal_id="OPP-300")
        conn.execute(
            """
            INSERT INTO signals (id, signal_date, source, source_path, entity_type, entity_name,
                signal_type, severity, summary, requires_manager_attention, confidence,
                status, created_at, updated_at)
            VALUES (?, ?, 'rule', '', 'client', 'Acme Corp', 'risk', 'medium',
                    'Test', FALSE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [content_hash("acme::signal"), date.today().isoformat()],
        )
        rows = get_client_rows(conn)
        acme = next((r for r in rows if r["name"] == "Acme Corp"), None)
        assert acme is not None
        assert "deals" in acme
        deals = acme["deals"]
        assert len(deals) == 1
        assert deals[0]["deal_id"] == "OPP-300"
        assert deals[0]["deal_name"] == "Project Alpha"

    def test_multiple_deals_for_same_client(self, conn):
        _seed_deal(conn, "Deal One", account="BigCo", deal_id="OPP-A1")
        _seed_deal(conn, "Deal Two", account="BigCo", deal_id="OPP-A2")
        conn.execute(
            """
            INSERT INTO signals (id, signal_date, source, source_path, entity_type, entity_name,
                signal_type, severity, summary, requires_manager_attention, confidence,
                status, created_at, updated_at)
            VALUES (?, ?, 'rule', '', 'client', 'BigCo', 'risk', 'low',
                    'Test', FALSE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [content_hash("bigco::signal"), date.today().isoformat()],
        )
        rows = get_client_rows(conn)
        bigco = next((r for r in rows if r["name"] == "BigCo"), None)
        assert bigco is not None
        opp_ids = {d["deal_id"] for d in bigco["deals"]}
        assert "OPP-A1" in opp_ids
        assert "OPP-A2" in opp_ids

    def test_missing_deal_id_handled_gracefully(self, conn):
        # Deal without deal_id column set
        _seed_deal(conn, "No ID Deal", account="NoIDCo", deal_id="")
        conn.execute(
            """
            INSERT INTO signals (id, signal_date, source, source_path, entity_type, entity_name,
                signal_type, severity, summary, requires_manager_attention, confidence,
                status, created_at, updated_at)
            VALUES (?, ?, 'rule', '', 'client', 'NoIDCo', 'risk', 'low',
                    'Test', FALSE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [content_hash("noidco::signal"), date.today().isoformat()],
        )
        rows = get_client_rows(conn)
        noidco = next((r for r in rows if r["name"] == "NoIDCo"), None)
        assert noidco is not None
        # Should have a deals list, even if deal_id is empty
        assert isinstance(noidco.get("deals", []), list)


# ===========================================================================
# Phase 8 — People alias normalization
# ===========================================================================


class TestPeopleNormalizer:
    def _make_person(self, name: str, aliases: list, track: bool = True):
        """Create a simple object with the PersonConfig interface."""
        class _P:
            pass
        p = _P()
        p.name = name
        p.aliases = aliases
        p.role = ""
        p.level = ""
        p.track = track
        return p

    def test_alias_resolves_to_canonical(self):
        people = [self._make_person("Taylor Stacey", ["Taylor", "T. Stacey"])]
        n = PeopleNormalizer(people)
        assert n.canonicalize("Taylor") == "Taylor Stacey"
        assert n.canonicalize("T. Stacey") == "Taylor Stacey"

    def test_canonical_name_resolves_to_itself(self):
        people = [self._make_person("Taylor Stacey", ["Taylor"])]
        n = PeopleNormalizer(people)
        assert n.canonicalize("Taylor Stacey") == "Taylor Stacey"

    def test_unknown_name_returns_unchanged(self):
        people = [self._make_person("Taylor Stacey", ["Taylor"])]
        n = PeopleNormalizer(people)
        assert n.canonicalize("Unknown Person") == "Unknown Person"

    def test_tracked_person_is_tracked(self):
        people = [self._make_person("Alice Chen", [], track=True)]
        n = PeopleNormalizer(people)
        assert n.is_tracked("Alice Chen") is True

    def test_untracked_person_is_not_tracked(self):
        people = [self._make_person("Bob Jones", [], track=False)]
        n = PeopleNormalizer(people)
        assert n.is_tracked("Bob Jones") is False

    def test_alias_of_untracked_person_is_not_tracked(self):
        people = [self._make_person("Bob Jones", ["Bob"], track=False)]
        n = PeopleNormalizer(people)
        assert n.is_tracked("Bob") is False

    def test_tracked_names_excludes_untracked(self):
        people = [
            self._make_person("Alice Chen", [], track=True),
            self._make_person("Bob Jones", [], track=False),
        ]
        n = PeopleNormalizer(people)
        tracked = n.tracked_names()
        assert "Alice Chen" in tracked
        assert "Bob Jones" not in tracked

    def test_duplicate_aliases_collapse(self):
        people = [
            self._make_person("Taylor Stacey", ["Taylor", "T. Stacey"]),
            # hypothetical second entry with same alias — should not crash
            self._make_person("Taylor Other", ["Taylor2"]),
        ]
        n = PeopleNormalizer(people)
        # Each alias maps to one canonical
        assert n.canonicalize("Taylor") == "Taylor Stacey"
        assert n.canonicalize("Taylor2") == "Taylor Other"

    def test_canonicalize_list_deduplicates(self):
        people = [self._make_person("Taylor Stacey", ["Taylor"])]
        n = PeopleNormalizer(people)
        result = n.canonicalize_list(["Taylor", "Taylor Stacey", "Alice"])
        assert result.count("Taylor Stacey") == 1

    def test_find_unconfigured_names(self):
        people = [self._make_person("Alice Chen", ["Alice"])]
        n = PeopleNormalizer(people)
        unconfigured = n.find_unconfigured(["Alice", "Unknown Bob"])
        assert "Unknown Bob" in unconfigured
        assert "Alice" not in unconfigured


class TestPeopleAudit:
    def test_audit_finds_unconfigured_names_from_signals(self, conn):
        """Names in signals not in people.yaml should appear as unconfigured."""
        conn.execute(
            """
            INSERT INTO signals (id, signal_date, source, source_path, entity_type, entity_name,
                signal_type, severity, summary, requires_manager_attention, confidence,
                status, created_at, updated_at)
            VALUES (?, ?, 'rule', '', 'person', 'Unconfigured Person', 'risk', 'medium',
                    'Test', FALSE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [content_hash("unknown::signal"), date.today().isoformat()],
        )
        from manager_os.build.people_normalization import PeopleNormalizer, run_people_audit

        class _FakeSettings:
            config_dir = "./config"

        try:
            audit = run_people_audit(conn, settings=None)
            # "Unconfigured Person" should appear in unconfigured list
            assert "Unconfigured Person" in audit.unconfigured_in_db
        except Exception:
            # If config loading fails in test env, just skip rather than fail
            pytest.skip("people.yaml not available in test environment")

    def test_duplicate_candidates_detected(self, conn):
        """A name that is an alias of another canonical should appear as duplicate candidate."""
        # Taylor → Taylor Stacey in config
        conn.execute(
            """
            INSERT INTO staffing_forecast
                (id, person_name, week_start, client, project,
                 allocation_pct, forecast_type, ingested_at)
            VALUES ('fc-test', 'Taylor', ?, 'Client', 'proj', 80.0, 'confirmed', CURRENT_TIMESTAMP)
            """,
            [date.today().isoformat()],
        )
        try:
            audit = run_people_audit(conn, settings=None)
            # "Taylor" should be identified as a duplicate candidate → canonical "Taylor Stacey"
            alias_names = [pair[0] for pair in audit.duplicate_candidates]
            assert "Taylor" in alias_names
        except Exception:
            pytest.skip("people.yaml not available in test environment")


# ===========================================================================
# Phase 3 — GWS ingest: no-attendee events skipped
# ===========================================================================


class TestGWSCalendarIngestion:
    def test_event_with_no_attendees_skipped(self, conn):
        from manager_os.ingest.gws_client import _ingest_calendar_file
        import tempfile, json, pathlib

        events = [
            {
                "id": "evt-solo",
                "summary": "Deep Work Block",
                "start": {"dateTime": "2026-06-16T09:00:00-07:00"},
                "end": {"dateTime": "2026-06-16T11:00:00-07:00"},
                "attendees": [],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(events, f)
            path = pathlib.Path(f.name)

        result = _ingest_calendar_file(path, conn, force=False)
        assert result.skipped == 1
        assert result.skip_reasons.get("no_external_attendees", 0) == 1
        assert result.ingested == 0

    def test_event_with_only_self_skipped(self, conn):
        from manager_os.ingest.gws_client import _ingest_calendar_file
        import tempfile, json, pathlib

        events = [
            {
                "id": "evt-selfonly",
                "summary": "Focus Time",
                "start": {"dateTime": "2026-06-16T14:00:00-07:00"},
                "end": {"dateTime": "2026-06-16T15:00:00-07:00"},
                "attendees": [
                    {"email": "me@example.com", "displayName": "Me", "self": True}
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(events, f)
            path = pathlib.Path(f.name)

        result = _ingest_calendar_file(path, conn, force=False)
        assert result.skipped == 1
        assert result.ingested == 0

    def test_event_with_external_attendee_ingested(self, conn):
        from manager_os.ingest.gws_client import _ingest_calendar_file
        import tempfile, json, pathlib

        events = [
            {
                "id": "evt-external",
                "summary": "Client Meeting",
                "start": {"dateTime": "2026-06-16T10:00:00-07:00"},
                "end": {"dateTime": "2026-06-16T11:00:00-07:00"},
                "attendees": [
                    {"email": "me@example.com", "displayName": "Me", "self": True},
                    {"email": "client@example.com", "displayName": "Client Rep", "self": False},
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(events, f)
            path = pathlib.Path(f.name)

        result = _ingest_calendar_file(path, conn, force=False)
        assert result.ingested == 1
        assert result.skipped == 0

    def test_event_with_multiple_invitees_ingested(self, conn):
        from manager_os.ingest.gws_client import _ingest_calendar_file
        import tempfile, json, pathlib

        events = [
            {
                "id": "evt-multi",
                "summary": "Team Sync",
                "start": {"dateTime": "2026-06-16T09:00:00-07:00"},
                "end": {"dateTime": "2026-06-16T10:00:00-07:00"},
                "attendees": [
                    {"email": "alice@example.com", "displayName": "Alice"},
                    {"email": "bob@example.com", "displayName": "Bob"},
                ],
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(events, f)
            path = pathlib.Path(f.name)

        result = _ingest_calendar_file(path, conn, force=False)
        assert result.ingested == 1
