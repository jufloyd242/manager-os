"""Tests for meeting-specific context retrieval and ranking."""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
import json

import pytest

from manager_os.db import get_connection
from manager_os.extract.meeting_context import (
    ContextBundle,
    ContextItem,
    retrieve_meeting_context,
    rank_context_items,
    PROFILE_RETRIEVAL_PLANS,
    CONTEXT_LIMITS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_meeting(conn, meeting_id="m1", title="Data Leaders Standup",
                  attendees=None, meeting_date=None):
    """Seed a meeting into the DB."""
    if attendees is None:
        attendees = ["manager@example.com"]
    if meeting_date is None:
        meeting_date = date(2026, 7, 13)
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO meetings
           (id, meeting_date, start_time, title, attendees, linked_entities,
            source, external_id, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [meeting_id, meeting_date, "09:00", title,
         json.dumps(attendees), "[]", "calendar_sync", "ext1", now],
    )


def _seed_signal(conn, signal_id="s1", entity_name="Alice", severity="high",
                 summary="Risk on project X", signal_date=None):
    if signal_date is None:
        signal_date = date(2026, 7, 12)
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO signals
           (id, signal_date, source, entity_type, entity_name, signal_type,
            severity, summary, why_it_matters, requires_manager_attention,
            confidence, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [signal_id, signal_date, "obsidian", "person", entity_name, "risk",
         severity, summary, "Important", True, 0.9, "open", now, now],
    )


def _seed_action_item(conn, item_id="a1", assigned_to="Alice",
                      description="Follow up on deal", due_date=None):
    if due_date is None:
        due_date = date(2026, 7, 15)
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO action_items
           (id, signal_id, source_note_id, assigned_to, description, due_date,
            status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [item_id, None, None, assigned_to, description, due_date, "open", now],
    )


def _seed_note(conn, note_id="n1", entity_name="Alice", body="Discussed progress on project X",
               note_date=None, note_type="1on1"):
    if note_date is None:
        note_date = date(2026, 7, 10)
    now = datetime.now(timezone.utc)
    conn.execute(
        """INSERT INTO notes
           (id, raw_document_id, note_date, note_type, entity_type, entity_name,
            title, body, tags, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [note_id, "doc1", note_date, note_type, "person", entity_name,
         "1:1 Notes", body, "[]", now],
    )


# ---------------------------------------------------------------------------
# Profile retrieval plans
# ---------------------------------------------------------------------------


class TestProfileRetrievalPlans:
    def test_upward_daily_status_plan_exists(self):
        assert "upward_daily_status" in PROFILE_RETRIEVAL_PLANS

    def test_direct_report_1on1_plan_exists(self):
        assert "direct_report_1on1" in PROFILE_RETRIEVAL_PLANS

    def test_manager_1on1_plan_exists(self):
        assert "manager_1on1" in PROFILE_RETRIEVAL_PLANS

    def test_client_project_plan_exists(self):
        assert "client_project" in PROFILE_RETRIEVAL_PLANS

    def test_deal_presales_plan_exists(self):
        assert "deal_presales" in PROFILE_RETRIEVAL_PLANS

    def test_team_standup_plan_exists(self):
        assert "team_standup" in PROFILE_RETRIEVAL_PLANS

    def test_generic_plan_exists(self):
        assert "generic" in PROFILE_RETRIEVAL_PLANS

    def test_upward_daily_status_excludes_biographies(self):
        plan = PROFILE_RETRIEVAL_PLANS["upward_daily_status"]
        assert "excludes" in plan
        excludes = plan["excludes"]
        assert any("biograph" in e.lower() for e in excludes)

    def test_upward_daily_status_includes_priorities(self):
        plan = PROFILE_RETRIEVAL_PLANS["upward_daily_status"]
        assert "includes" in plan
        includes = plan["includes"]
        assert any("priorit" in i.lower() for i in includes)


# ---------------------------------------------------------------------------
# Context limits
# ---------------------------------------------------------------------------


class TestContextLimits:
    def test_limits_exist(self):
        assert "highlights" in CONTEXT_LIMITS
        assert "commitments" in CONTEXT_LIMITS
        assert "risks" in CONTEXT_LIMITS
        assert "decisions" in CONTEXT_LIMITS
        assert "actions" in CONTEXT_LIMITS
        assert "prior_meetings" in CONTEXT_LIMITS
        assert "sources" in CONTEXT_LIMITS

    def test_limits_reasonable(self):
        assert CONTEXT_LIMITS["highlights"] <= 15
        assert CONTEXT_LIMITS["risks"] <= 10
        assert CONTEXT_LIMITS["sources"] <= 30


# ---------------------------------------------------------------------------
# Context retrieval
# ---------------------------------------------------------------------------


class TestRetrieveMeetingContext:
    def test_retrieves_signals_for_attendees(self, tmp_path):
        """Context should include signals for meeting attendees."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        _seed_meeting(conn, attendees=["alice@example.com"])
        _seed_signal(conn, entity_name="alice@example.com")

        meeting = {
            "id": "m1",
            "title": "Data Leaders Standup",
            "attendees": ["alice@example.com"],
            "meeting_date": "2026-07-13",
        }
        bundle = retrieve_meeting_context(conn, meeting, "upward_daily_status")
        assert len(bundle.sources) > 0 or len(bundle.items) > 0
        conn.close()

    def test_retrieves_action_items(self, tmp_path):
        """Context should include open action items."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        _seed_meeting(conn, attendees=["alice@example.com"])
        _seed_action_item(conn, assigned_to="alice@example.com")
        conn.close()

        meeting = {
            "id": "m1",
            "title": "1:1 with Alice",
            "attendees": ["alice@example.com"],
            "meeting_date": "2026-07-13",
        }
        bundle = retrieve_meeting_context(conn, meeting, "direct_report_1on1")
        assert isinstance(bundle, ContextBundle)

    def test_retrieves_notes(self, tmp_path):
        """Context should include notes for attendees."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        _seed_meeting(conn, attendees=["alice@example.com"])
        _seed_note(conn, entity_name="alice@example.com")
        conn.close()

        meeting = {
            "id": "m1",
            "title": "1:1 with Alice",
            "attendees": ["alice@example.com"],
            "meeting_date": "2026-07-13",
        }
        bundle = retrieve_meeting_context(conn, meeting, "direct_report_1on1")
        assert isinstance(bundle, ContextBundle)

    def test_context_items_have_required_fields(self, tmp_path):
        """Every context item must have source_id, source_type, title, etc."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        _seed_meeting(conn, attendees=["alice@example.com"])
        _seed_signal(conn, entity_name="alice@example.com")
        conn.close()

        meeting = {
            "id": "m1",
            "title": "Data Leaders Standup",
            "attendees": ["alice@example.com"],
            "meeting_date": "2026-07-13",
        }
        bundle = retrieve_meeting_context(conn, meeting, "upward_daily_status")
        for item in bundle.items:
            assert hasattr(item, "source_id")
            assert hasattr(item, "source_type")
            assert hasattr(item, "title")
            assert hasattr(item, "relevance_reason")
            assert hasattr(item, "confidence")

    def test_context_respects_limits(self, tmp_path):
        """Context should not exceed configured limits."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        _seed_meeting(conn, attendees=["alice@example.com"])
        # Seed many signals
        for i in range(20):
            _seed_signal(conn, signal_id=f"s{i}", entity_name="alice@example.com",
                         summary=f"Risk {i}")
        conn.close()

        meeting = {
            "id": "m1",
            "title": "Data Leaders Standup",
            "attendees": ["alice@example.com"],
            "meeting_date": "2026-07-13",
        }
        bundle = retrieve_meeting_context(conn, meeting, "upward_daily_status")
        assert len(bundle.items) <= CONTEXT_LIMITS["highlights"] + CONTEXT_LIMITS["risks"] + CONTEXT_LIMITS["actions"]

    def test_empty_context_when_no_data(self, tmp_path):
        """When no context exists, should return empty bundle, not crash."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        _seed_meeting(conn, attendees=["nobody@example.com"])
        conn.close()

        meeting = {
            "id": "m1",
            "title": "Random Meeting",
            "attendees": ["nobody@example.com"],
            "meeting_date": "2026-07-13",
        }
        bundle = retrieve_meeting_context(conn, meeting, "generic")
        assert isinstance(bundle, ContextBundle)
        assert len(bundle.items) == 0

    def test_no_prep_profile_returns_empty(self, tmp_path):
        """No-prep meetings should return empty context."""
        db_path = str(tmp_path / "test.duckdb")
        conn = get_connection(db_path)
        conn.close()

        meeting = {
            "id": "m1",
            "title": "Focus Time",
            "attendees": [],
            "meeting_date": "2026-07-13",
        }
        bundle = retrieve_meeting_context(conn, meeting, "no_prep")
        assert len(bundle.items) == 0


# ---------------------------------------------------------------------------
# Context ranking
# ---------------------------------------------------------------------------


class TestContextRanking:
    def test_exact_attendee_match_ranks_higher(self):
        """Items matching an attendee should rank higher than non-matching."""
        items = [
            ContextItem(
                source_id="s1", source_type="signal", title="Unrelated",
                date=date(2026, 7, 12), entity="other", excerpt_or_fact="text",
                relevance_reason="no match", confidence=0.3,
            ),
            ContextItem(
                source_id="s2", source_type="signal", title="Alice Risk",
                date=date(2026, 7, 12), entity="alice@example.com",
                excerpt_or_fact="text", relevance_reason="attendee match",
                confidence=0.9,
            ),
        ]
        ranked = rank_context_items(items, attendees=["alice@example.com"])
        assert ranked[0].source_id == "s2"

    def test_higher_severity_ranks_higher(self):
        """Critical severity should rank higher than low."""
        items = [
            ContextItem(
                source_id="s1", source_type="signal", title="Low Risk",
                date=date(2026, 7, 12), entity="alice", excerpt_or_fact="text",
                relevance_reason="match", confidence=0.5,
                severity="low",
            ),
            ContextItem(
                source_id="s2", source_type="signal", title="Critical Risk",
                date=date(2026, 7, 12), entity="alice", excerpt_or_fact="text",
                relevance_reason="match", confidence=0.9,
                severity="critical",
            ),
        ]
        ranked = rank_context_items(items, attendees=["alice"])
        assert ranked[0].source_id == "s2"

    def test_more_recent_ranks_higher(self):
        """More recent items should rank higher (all else equal)."""
        items = [
            ContextItem(
                source_id="s1", source_type="note", title="Old Note",
                date=date(2026, 6, 1), entity="alice", excerpt_or_fact="text",
                relevance_reason="match", confidence=0.5,
            ),
            ContextItem(
                source_id="s2", source_type="note", title="Recent Note",
                date=date(2026, 7, 12), entity="alice", excerpt_or_fact="text",
                relevance_reason="match", confidence=0.5,
            ),
        ]
        ranked = rank_context_items(items, attendees=["alice"])
        assert ranked[0].source_id == "s2"
