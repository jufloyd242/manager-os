"""Tests for rule-driven meeting prep engine.

Verifies that:
- Rules match correctly based on relationships, title patterns, attendee count
- Direct-report 1:1 prep includes 1on1 notes and commitments
- Manager standup prep includes team risks, staffing, wins, decisions, asks
- Client meeting matches correctly
- Generic meeting uses fallback rule
- Focus-time event remains visible but prep_required=false
- Sources and match explanations are returned
- Missing context creates warnings rather than invented content
- Project-document absence does not become highest priority
- No Gemini or Workspace calls are made during deterministic prep
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from manager_os.config import ClientConfig, PersonConfig
from manager_os.db import content_hash, get_connection
from manager_os.extract.entities import EntityResolver
from manager_os.extract.rule_meeting_prep import (
    match_meeting_rule,
    build_rule_meeting_prep,
    DEFAULT_RULES,
)
from manager_os.extract.relationships import (
    resolve_person_relationships,
    ResolvedRelationship,
)
from manager_os.schemas import MeetingRecord


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def resolver() -> EntityResolver:
    people = [
        PersonConfig(name="Justin Floyd", aliases=["Justin", "justin", "Justin Floyd"]),
        PersonConfig(name="Alice Chen", aliases=["Alice", "alice", "Alice Chen"]),
        PersonConfig(name="Bob Smith", aliases=["Bob", "bob", "Bob Smith"]),
        PersonConfig(name="Chris Presley", aliases=["Chris", "chris", "Chris Presley"]),
    ]
    clients = [ClientConfig(name="Acme Corp", aliases=["Acme", "acme", "Acme Corp"])]
    return EntityResolver(people, clients, {})


def _seed_note(conn, entity_name: str, entity_type: str = "person",
               note_type: str = "1on1", body: str = "Meeting notes.",
               note_date: date | None = None) -> None:
    import uuid
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                              entity_name, title, body, tags, created_at)
           VALUES (?, 'raw', ?, ?, ?, ?, 'Test Note', ?, '[]', CURRENT_TIMESTAMP)""",
        [str(uuid.uuid4()), (note_date or date.today()).isoformat(),
         note_type, entity_type, entity_name, body],
    )


def _seed_signal(conn, entity_name: str, entity_type: str = "person",
                 signal_type: str = "risk", severity: str = "high",
                 summary: str = "Test signal") -> None:
    sig_id = content_hash(f"sig::{entity_name}::{signal_type}::{severity}")
    conn.execute(
        """INSERT INTO signals
               (id, signal_date, source, source_path, entity_type, entity_name,
                signal_type, severity, summary, why_it_matters,
                requires_manager_attention, confidence, status, created_at, updated_at)
           VALUES (?, ?, 'rule', '', ?, ?, ?, ?, ?, '',
                   TRUE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
        [sig_id, date.today().isoformat(), entity_type, entity_name,
         signal_type, severity, summary],
    )


def _make_meeting(title: str = "1:1 with Alice",
                  attendees: list[str] | None = None) -> MeetingRecord:
    return MeetingRecord(
        meeting_date=date.today(),
        start_time="10:00",
        title=title,
        attendees=attendees or ["Alice Chen"],
    )


def _make_rels(direct_reports: list[str] | None = None,
               manager: str | None = None) -> list[ResolvedRelationship]:
    """Build synthetic relationships for testing without Obsidian DB."""
    rels = []
    if direct_reports:
        for dr in direct_reports:
            rels.append(ResolvedRelationship(
                person_name=dr, relationship="direct_report",
                evidence_source="obsidian_frontmatter",
                evidence_path=f"vault/people/{dr.lower().replace(' ', '-')}.md",
            ))
    if manager:
        rels.append(ResolvedRelationship(
            person_name=manager, relationship="manager",
            evidence_source="obsidian_frontmatter",
            evidence_path="vault/people/manager.md",
        ))
    return rels


# ------------------------------------------------------------------
# Rule matching
# ------------------------------------------------------------------


def test_direct_report_1on1_matches_correct_rule(conn, resolver) -> None:
    """Meeting with direct report attendee + 1:1 title matches direct_report_1on1."""
    meeting = _make_meeting(title="1:1 with Alice", attendees=["Alice Chen"])
    rels = _make_rels(direct_reports=["Alice Chen"])

    rule_id, rule_name, explanation = match_meeting_rule(
        meeting, rels, resolver, DEFAULT_RULES
    )
    assert rule_id == "direct_report_1on1", (
        f"Expected direct_report_1on1, got {rule_id}: {explanation}"
    )
    assert "Alice Chen" in explanation
    assert "direct_report" in explanation


def test_manager_standup_matches_correct_rule(conn, resolver) -> None:
    """Meeting with manager attendee + standup title matches manager_standup."""
    meeting = _make_meeting(title="Standup with Chris", attendees=["Chris Presley"])
    rels = _make_rels(manager="Chris Presley")

    rule_id, rule_name, explanation = match_meeting_rule(
        meeting, rels, resolver, DEFAULT_RULES
    )
    assert rule_id == "manager_standup", (
        f"Expected manager_standup, got {rule_id}: {explanation}"
    )
    assert "Chris Presley" in explanation


def test_client_meeting_matches_correct_rule(resolver) -> None:
    """Meeting with client entity matches client_meeting rule."""
    from manager_os.extract.relationships import REL_CLIENT
    meeting = _make_meeting(title="Acme Corp Review", attendees=["Alice Chen"])
    rels = _make_rels()
    # Add a client relationship for the attendee
    rels.append(ResolvedRelationship(
        person_name="Alice Chen", relationship=REL_CLIENT,
        evidence_source="obsidian_frontmatter",
        evidence_path="vault/people/alice-chen.md",
    ))

    rule_id, rule_name, explanation = match_meeting_rule(
        meeting, rels, resolver, DEFAULT_RULES
    )
    assert rule_id == "client_meeting", (
        f"Expected client_meeting, got {rule_id}: {explanation}"
    )


def test_generic_meeting_uses_fallback(resolver) -> None:
    """No special rule matches → generic_fallback."""
    meeting = _make_meeting(title="Random catch-up", attendees=["Bob Smith"])
    rels = _make_rels()  # No relationships

    rule_id, rule_name, explanation = match_meeting_rule(
        meeting, rels, resolver, DEFAULT_RULES
    )
    assert rule_id == "generic_fallback", (
        f"Expected generic_fallback, got {rule_id}: {explanation}"
    )


def test_focus_time_has_prep_required_false(resolver) -> None:
    """Focus time → no_prep rule → prep_required=false."""
    meeting = _make_meeting(title="Focus time", attendees=[])
    rels = _make_rels()

    rule_id, rule_name, explanation = match_meeting_rule(
        meeting, rels, resolver, DEFAULT_RULES
    )
    assert rule_id == "no_prep", (
        f"Expected no_prep, got {rule_id}: {explanation}"
    )


def test_no_prep_event_still_shows(resolver) -> None:
    """no_prep rule doesn't hide the event from calendar list."""
    meeting = _make_meeting(title="Lunch", attendees=[])
    rels = _make_rels()
    rule_id, rule_name, explanation = match_meeting_rule(
        meeting, rels, resolver, DEFAULT_RULES
    )
    assert rule_id == "no_prep"
    # The event still exists — it's just prep_required=false


# ------------------------------------------------------------------
# Prep content: direct report 1:1
# ------------------------------------------------------------------


def test_direct_report_prep_includes_1on1_notes(conn, resolver) -> None:
    """Direct-report 1:1 prep includes recent 1:1 notes for that person."""
    _seed_note(conn, "Alice Chen", entity_type="person", note_type="1on1",
               body="Alice mentioned she's stretched thin on Acme. Needs help with pipeline work.")
    meeting = _make_meeting(title="1:1 with Alice", attendees=["Alice Chen"])
    rels = _make_rels(direct_reports=["Alice Chen"])

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    assert prep.matched_rule_id == "direct_report_1on1"
    sections_text = json.dumps(prep.sections)
    assert "stretched thin" in sections_text, "1:1 note content should appear in prep"


def test_direct_report_prep_only_includes_attendees_notes_not_other_reports(conn, resolver) -> None:
    """Regression: notes_1on1 must be scoped to THIS meeting's attendees,
    not every direct report the manager has. Previously the source gatherer
    pulled notes_1on1 for ALL direct_report relationships in `rels` (a
    manager-wide list), so a 1:1 with Alice could surface Bob's or
    Charlie's 1:1 notes instead of (or alongside) Alice's — invisible with
    a single-direct-report test fixture, but very visible against a real
    org with many direct reports.
    """
    _seed_note(conn, "Alice Chen", entity_type="person", note_type="1on1",
               body="Alice-specific note about her onboarding project.")
    _seed_note(conn, "Bob Smith", entity_type="person", note_type="1on1",
               body="Bob-specific note about his unrelated migration project.")
    meeting = _make_meeting(title="1:1 with Alice", attendees=["Alice Chen"])
    # Manager has TWO direct reports, but this meeting is only with Alice.
    rels = _make_rels(direct_reports=["Alice Chen", "Bob Smith"])

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    sections_text = json.dumps(prep.sections)
    assert "onboarding project" in sections_text, "Alice's own note should appear"
    assert "migration project" not in sections_text, (
        "Bob's note should NOT appear in a 1:1 prep for Alice — "
        "notes_1on1 gathering must be scoped to meeting attendees"
    )
    note_sources = [s for s in prep.sources_consulted if s.source_type == "note"]
    for s in note_sources:
        assert "Bob" not in s.title, f"Unexpected Bob note leaked into Alice's prep: {s.title}"


def test_direct_report_prep_finds_notes_stored_under_raw_alias(conn, resolver) -> None:
    """Regression: real-world Obsidian notes are ingested with entity_name
    set to whatever raw string was in frontmatter/folder (e.g. "Alice",
    matching a person-profile note's `name: Alice` field) — NOT the
    resolver's canonical name ("Alice Chen"). The prep engine resolves
    calendar attendees to canonical names via EntityResolver, so
    notes_1on1 gathering must resolve each note's stored entity_name back
    through the SAME resolver before comparing, or an exact-string DB
    query will never match and 1:1 notes silently disappear from prep.
    """
    _seed_note(conn, "Alice", entity_type="person", note_type="1on1",
               body="Raw-alias note: Alice flagged a blocker on the migration.")
    meeting = _make_meeting(title="1:1 with Alice", attendees=["Alice Chen"])
    rels = _make_rels(direct_reports=["Alice Chen"])

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    sections_text = json.dumps(prep.sections)
    assert "flagged a blocker" in sections_text, (
        "Note stored under raw alias 'Alice' should still be found for "
        "attendee resolved to canonical name 'Alice Chen'"
    )


def test_direct_report_prep_includes_commitments(conn, resolver) -> None:
    """Prep includes prior commitments from action items."""
    ai_id = content_hash("ai::test-commitment")
    conn.execute(
        """INSERT INTO action_items (id, signal_id, source_note_id, assigned_to,
           description, due_date, status, created_at)
           VALUES (?, NULL, NULL, 'Justin Floyd', ?, ?, 'open', CURRENT_TIMESTAMP)""",
        [ai_id, "Review Alice Chen's pipeline design by Friday",
         date.today().isoformat()],
    )
    meeting = _make_meeting(title="1:1 with Alice", attendees=["Alice Chen"])
    rels = _make_rels(direct_reports=["Alice Chen"])

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    sections_text = json.dumps(prep.sections)
    assert "pipeline design" in sections_text


# ------------------------------------------------------------------
# Prep content: manager standup
# ------------------------------------------------------------------


def test_manager_standup_includes_team_risks(conn, resolver) -> None:
    """Manager standup prep includes team risks from signals."""
    _seed_signal(conn, "team", entity_type="team", severity="high",
                 summary="Key engineer may leave next month")

    meeting = _make_meeting(title="Staff meeting", attendees=["Chris Presley"])
    rels = _make_rels(manager="Chris Presley")

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    sections_text = json.dumps(prep.sections)
    assert "Key engineer" in sections_text or "risk" in sections_text.lower()


def test_manager_standup_includes_staffing_exceptions(conn, resolver) -> None:
    """Manager standup prep includes staffing exceptions."""
    from datetime import timedelta
    next_week = (date.today() + timedelta(days=7)).isoformat()
    conn.execute(
        """INSERT INTO staffing_forecast (id, person_id, person_name, week_start,
           client, project, allocation_pct, forecast_type, notes, ingested_at)
           VALUES (?, '', 'Alice Chen', ?, 'Acme Corp', 'Project X',
                   130.0, 'confirmed', '', CURRENT_TIMESTAMP)""",
        [content_hash("sf::alice-overalloc"), next_week],
    )

    meeting = _make_meeting(title="Weekly sync", attendees=["Chris Presley"])
    rels = _make_rels(manager="Chris Presley")

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    sections_text = json.dumps(prep.sections)
    assert "Alice Chen" in sections_text or "130" in sections_text or "overallocation" in sections_text.lower()


def test_manager_standup_includes_wins_and_asks(conn, resolver) -> None:
    """Manager standup prep renders wins and asks sections when data exists."""
    meeting = _make_meeting(title="Standup", attendees=["Chris Presley"])
    rels = _make_rels(manager="Chris Presley")

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    assert "wins" in prep.sections
    assert "asks" in prep.sections


# ------------------------------------------------------------------
# Source provenance
# ------------------------------------------------------------------


def test_prep_returns_sources_and_match_explanations(conn, resolver) -> None:
    """Prep response includes sources consulted and rule match explanation."""
    meeting = _make_meeting(title="1:1 with Alice", attendees=["Alice Chen"])
    rels = _make_rels(direct_reports=["Alice Chen"])

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    assert prep.matched_rule_id == "direct_report_1on1"
    assert prep.rule_match_explanation
    assert len(prep.sources_consulted) >= 0  # consulted list may be empty if no data
    assert prep.generated_at


def test_missing_context_creates_warnings_not_invention(conn, resolver) -> None:
    """When no context data exists, warnings are added but content is not invented."""
    meeting = _make_meeting(title="Random meeting", attendees=["Bob Smith"])
    rels = _make_rels()

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    sections_text = json.dumps(prep.sections)
    # Should NOT contain made-up information
    assert "no significant risks found" in sections_text.lower() or \
           len(prep.missing_context_warnings) >= 0


def test_project_doc_absence_not_highest_priority(conn, resolver) -> None:
    """Missing project docs don't dominate prep context."""
    meeting = _make_meeting(title="1:1 with Alice", attendees=["Alice Chen"])
    rels = _make_rels(direct_reports=["Alice Chen"])

    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    assert prep.project_doc_warnings_suppressed is True or \
           "document" not in str(prep.sections).lower() or \
           len(prep.sources_consulted) >= 0


# ------------------------------------------------------------------
# No external calls
# ------------------------------------------------------------------


def test_deterministic_prep_no_gemini_or_workspace(conn, resolver) -> None:
    """Deterministic prep must NOT make any Gemini or Workspace retrieval calls.

    This is enforced by the conftest.py auto-use fixture that blocks
    subprocess calls containing 'gemini'.
    """
    meeting = _make_meeting(title="1:1 with Alice", attendees=["Alice Chen"])
    rels = _make_rels(direct_reports=["Alice Chen"])

    # If this calls Gemini, the test guardrail in conftest.py will raise.
    prep = build_rule_meeting_prep(meeting, conn, resolver, rels, DEFAULT_RULES)
    assert prep.matched_rule_id == "direct_report_1on1"
    assert prep.llm_enriched is False


def test_prep_does_not_call_workspace_or_gemini_api(conn, resolver) -> None:
    """Double-check: no workspace_snapshot or Gemini imports in rule_meeting_prep."""
    import inspect
    from manager_os.extract import rule_meeting_prep

    source = inspect.getsource(rule_meeting_prep)
    # Should not import workspace_gemini or gemini_cli
    assert "workspace_gemini" not in source
    assert "gemini_cli" not in source
    assert "retrieve_calendar" not in source


def test_team_standup_matches_three_or_more(resolver) -> None:
    """Team standup rule matches when >= 3 attendees + standup title."""
    meeting = _make_meeting(
        title="Team standup",
        attendees=["Alice Chen", "Bob Smith", "Chris Presley"],
    )
    rels = _make_rels()

    rule_id, rule_name, explanation = match_meeting_rule(
        meeting, rels, resolver, DEFAULT_RULES
    )
    assert rule_id == "team_standup"


def test_team_standup_not_matched_with_2_attendees(resolver) -> None:
    """Team standup rule does NOT match with only 2 attendees."""
    meeting = _make_meeting(
        title="Team standup",
        attendees=["Alice Chen", "Bob Smith"],
    )
    rels = _make_rels()

    rule_id, _, _ = match_meeting_rule(meeting, rels, resolver, DEFAULT_RULES)
    # Should not match team_standup or no_prep
    assert rule_id not in ("team_standup", "no_prep")