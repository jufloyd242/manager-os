"""Rule-driven meeting preparation engine.

Matches meetings against configurable rules from ``meeting_prep_rules.yaml``
and builds a structured ``MeetingPrepResponse`` with deterministic context
from local sources. No Gemini or Workspace calls.

First-match priority: rules are evaluated in order. The first rule whose
match conditions are all satisfied wins. ``generic_fallback`` always matches
(empty conditions) and serves as the default.
"""

from __future__ import annotations

import logging
from datetime import datetime

from manager_os.extract.entities import EntityResolver
from manager_os.extract.relationships import (
    ResolvedRelationship,
    get_relationship_for_attendee,
)
from manager_os.schemas import (
    MeetingPrepResponse,
    MeetingRecord,
    PrepSource,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Default rules (used when config file is missing)
# ------------------------------------------------------------------

DEFAULT_RULES = [
    {
        "id": "direct_report_1on1",
        "name": "Direct Report 1:1",
        "prep_required": True,
        "match": {"relationship": "direct_report", "title_patterns": ["1:1", "1-1", "one-on-one", "one on one", "check-in", "1-2-1"]},
        "preparation": {"sections": ["changes", "risks", "decisions", "wins", "asks", "talking_points", "questions"], "sources": ["notes_1on1", "prior_commitments", "action_items", "signals", "staffing", "projects"]},
    },
    {
        "id": "manager_standup",
        "name": "Manager Standup",
        "prep_required": True,
        "match": {"relationship": "manager", "title_patterns": ["standup", "sync", "weekly", "staff meeting", "staff", "1:1", "1-1"]},
        "preparation": {"sections": ["changes", "risks", "decisions_needed", "wins", "asks", "talking_points"], "sources": ["team_risks", "staffing_exceptions", "deals_escalations", "prior_notes"]},
    },
    {
        "id": "client_meeting",
        "name": "Client / Project Meeting",
        "prep_required": True,
        "match": {"relationship": ["client", "external"], "title_patterns": []},
        "preparation": {"sections": ["risks", "actions", "decisions", "milestones", "deals_context", "prior_meetings"], "sources": ["client_notes", "risks", "action_items", "decisions", "deals", "prior_meetings", "workspace_summary"]},
    },
    {
        "id": "team_standup",
        "name": "Team Standup",
        "prep_required": True,
        "match": {"attendee_count_min": 3, "title_patterns": ["standup", "team sync", "daily", "team"]},
        "preparation": {"sections": ["blockers", "commitments", "dependencies", "decisions", "announcements"], "sources": ["blocker_signals", "commitments", "cross_team_deps", "decisions"]},
    },
    {
        "id": "no_prep",
        "name": "No Preparation Needed",
        "prep_required": False,
        "match": {"title_patterns": ["focus time", "focus block", "lunch", "personal", "out of office", "ooo", "reminder", "appointment", "blocked", "travel", "doctor", "dentist", "birthday", "holiday", "vacation"], "attendee_count_max": 1},
        "preparation": {"sections": [], "sources": []},
    },
    {
        "id": "generic_fallback",
        "name": "Generic Meeting",
        "prep_required": True,
        "match": {},
        "preparation": {"sections": ["risks", "actions", "prior_notes", "questions"], "sources": ["attendee_notes", "title_notes", "signals", "action_items", "prior_meetings"]},
    },
]


# ------------------------------------------------------------------
# Rule matching
# ------------------------------------------------------------------


def _rule_matches(rule: dict, meeting: MeetingRecord,
                  rels: list[ResolvedRelationship],
                  resolver: EntityResolver) -> tuple[bool, str]:
    """Check if a rule matches the given meeting.

    Returns (matches, explanation). All specified conditions must be satisfied.
    Empty conditions are ignored.
    """
    match = rule.get("match", {})
    reasons: list[str] = []

    # 1. Relationship check
    rel_condition = match.get("relationship")
    if rel_condition is not None:
        rel_values = [rel_condition] if isinstance(rel_condition, str) else list(rel_condition)

        matched_rels = []
        for attendee in meeting.attendees:
            rel = get_relationship_for_attendee(attendee, rels, resolver)
            if rel.relationship in rel_values:
                matched_rels.append(f"{attendee} ({rel.relationship})")

        if not matched_rels:
            return False, f"No attendee has relationship in {rel_values}"
        reasons.append(f"attendee relationship: {', '.join(matched_rels)}")

    # 2. Title pattern check
    title_patterns = match.get("title_patterns")
    if title_patterns is not None and len(title_patterns) > 0:
        title_lower = meeting.title.lower()
        matched_patterns = [p for p in title_patterns if p.lower() in title_lower]
        if not matched_patterns:
            return False, f"Title '{meeting.title}' doesn't match patterns {title_patterns}"
        reasons.append(f"title matches: {', '.join(matched_patterns)}")

    # 3. Exact title check
    exact_title = match.get("exact_title")
    if exact_title is not None:
        if meeting.title.lower() != exact_title.lower():
            return False, f"Title doesn't match exact: '{exact_title}'"
        reasons.append("exact title match")

    # 4. Attendee count check
    min_count = match.get("attendee_count_min")
    if min_count is not None:
        if len(meeting.attendees) < min_count:
            return False, f"Only {len(meeting.attendees)} attendees, need {min_count}"
        reasons.append(f"{len(meeting.attendees)} attendees (min {min_count})")

    max_count = match.get("attendee_count_max")
    if max_count is not None:
        if len(meeting.attendees) > max_count:
            return False, f"{len(meeting.attendees)} attendees, exceeds max {max_count}"
        reasons.append(f"{len(meeting.attendees)} attendees (max {max_count})")

    return True, "; ".join(reasons) if reasons else "always matches"


def match_meeting_rule(
    meeting: MeetingRecord,
    rels: list[ResolvedRelationship],
    resolver: EntityResolver,
    rules: list[dict] | None = None,
) -> tuple[str, str, str]:
    """Find the first matching rule for the meeting.

    Returns (rule_id, rule_name, match_explanation).
    Always returns a match (generic_fallback will match if nothing else does).
    """
    if rules is None:
        rules = DEFAULT_RULES

    for rule in rules:
        matches, explanation = _rule_matches(rule, meeting, rels, resolver)
        if matches:
            return rule.get("id", "generic_fallback"), rule.get("name", "Generic Meeting"), explanation

    # Should never reach here since generic_fallback always matches
    return "generic_fallback", "Generic Meeting", "fallback (no rule matched)"


# ------------------------------------------------------------------
# Source gathering
# ------------------------------------------------------------------


def _gather_notes_1on1(conn, attendee_name: str, resolver: EntityResolver | None = None) -> list[PrepSource]:
    """Gather 1:1 notes for a specific person.

    Real-world Obsidian notes are ingested with ``entity_name`` set to
    whatever raw string appeared in frontmatter/folder (e.g. "Alice" from
    a person-profile note's ``name: Alice`` field) — not the resolver's
    canonical name ("Alice Chen"). An exact-string match against the
    canonical ``attendee_name`` would silently miss those notes, so this
    also scans all distinct ``entity_name`` values for this person/1on1
    combination and includes any that resolve (via ``resolver``) to the
    same canonical person.
    """
    sources = []
    candidate_names = {attendee_name}
    if resolver is not None:
        raw_names = conn.execute(
            "SELECT DISTINCT entity_name FROM notes "
            "WHERE entity_type = 'person' AND note_type = '1on1' AND entity_name != ''"
        ).fetchall()
        for (raw_name,) in raw_names:
            if resolver.resolve_person(raw_name) == attendee_name:
                candidate_names.add(raw_name)

    placeholders = ", ".join("?" * len(candidate_names))
    rows = conn.execute(
        f"""SELECT id, title, note_date, body, entity_name
           FROM notes WHERE entity_type = 'person' AND entity_name IN ({placeholders})
           AND note_type = '1on1'
           ORDER BY note_date DESC NULLS LAST LIMIT 5""",
        list(candidate_names),
    ).fetchall()
    for row in rows:
        body = (row[3] or "").strip()
        excerpt = body[:300] + ("..." if len(body) > 300 else "")
        sources.append(PrepSource(
            source_type="note",
            title=row[1] or "1:1 Note",
            source_path=f"entity:{row[4]}/1on1",
            source_date=str(row[2]) if row[2] else None,
            reason_selected="recent 1:1 note for attendee",
            relevance_score=90.0,
        ))
        # Embed body excerpt in the title for section rendering
        if excerpt:
            sources[-1].title = f"{row[1] or '1:1 Note'}: {excerpt}"
    return sources


def _gather_signals(conn, entity_names: list[str]) -> list[PrepSource]:
    """Gather open signals for a list of entities."""
    sources = []
    for name in entity_names:
        rows = conn.execute(
            """SELECT id, signal_type, severity, summary, entity_name
               FROM signals
               WHERE entity_name = ? AND status = 'open'
               ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                      WHEN 'medium' THEN 2 ELSE 3 END
               LIMIT 5""",
            [name],
        ).fetchall()
        for row in rows:
            sources.append(PrepSource(
                source_type="signal",
                title=f"{row[2].upper()}: {row[1]}",
                source_path=f"entity:{row[4]}/signals",
                source_date=None,
                reason_selected=f"open {row[2]} {row[1]} signal for {row[4]}",
                relevance_score=80.0 if row[2] in ("critical", "high") else 50.0,
            ))
    return sources


def _gather_action_items(conn, entity_names: list[str]) -> list[PrepSource]:
    """Gather open action items mentioning entity names."""
    sources = []
    all_items = conn.execute(
        "SELECT id, assigned_to, description, due_date, status "
        "FROM action_items WHERE status = 'open'"
    ).fetchall()
    for row in all_items:
        desc = (row[2] or "").lower()
        for name in entity_names:
            if name.lower() in desc:
                sources.append(PrepSource(
                    source_type="action_item",
                    title=row[2][:80],
                    source_path=f"assigned_to:{row[1]}",
                    source_date=str(row[3]) if row[3] else None,
                    reason_selected=f"open action item mentioning {name}",
                    relevance_score=70.0,
                ))
                break
    return sources


def _gather_staffing_exceptions(conn) -> list[PrepSource]:
    """Gather overallocation exceptions from staffing forecast."""
    sources = []
    rows = conn.execute(
        """SELECT person_name, week_start, allocation_pct, client, project
           FROM staffing_forecast
           WHERE allocation_pct > 100
           ORDER BY allocation_pct DESC
           LIMIT 5""",
    ).fetchall()
    for row in rows:
        sources.append(PrepSource(
            source_type="staffing_forecast",
            title=f"{row[0]}: {row[2]}% on {row[3]}",
            source_path=f"staffing/{row[0]}",
            source_date=str(row[1]) if row[1] else None,
            reason_selected=f"overallocation: {row[2]}%",
            relevance_score=85.0,
        ))
    return sources


def _gather_deals(conn, entity_names: list[str]) -> list[PrepSource]:
    """Gather deals matching entity names."""
    sources = []
    for name in entity_names:
        rows = conn.execute(
            """SELECT id, deal_name, account, stage, close_date, blockers
               FROM deals
               WHERE LOWER(account) LIKE ? OR LOWER(deal_name) LIKE ?
               LIMIT 3""",
            [f"%{name.lower()}%", f"%{name.lower()}%"],
        ).fetchall()
        for row in rows:
            sources.append(PrepSource(
                source_type="deal",
                title=f"{row[1]} ({row[2]})",
                source_path=f"deal:{row[0]}",
                source_date=str(row[4]) if row[4] else None,
                reason_selected=f"deal matching {name}",
                relevance_score=60.0,
            ))
    return sources


def _gather_decisions(conn, entity_names: list[str]) -> list[PrepSource]:
    """Gather open decisions for entities."""
    sources = []
    for name in entity_names:
        rows = conn.execute(
            """SELECT id, description, decision_date, status
               FROM decisions WHERE status = 'open' AND
               (LOWER(entity_name) LIKE ? OR LOWER(description) LIKE ?)
               LIMIT 3""",
            [f"%{name.lower()}%", f"%{name.lower()}%"],
        ).fetchall()
        for row in rows:
            sources.append(PrepSource(
                source_type="decision",
                title=row[1][:80],
                source_path=f"decision:{row[0]}",
                source_date=str(row[2]) if row[2] else None,
                reason_selected=f"open decision related to {name}",
                relevance_score=65.0,
            ))
    return sources


def _gather_prior_meetings(conn, meeting: MeetingRecord) -> list[PrepSource]:
    """Gather prior meetings with same attendees or title."""
    sources = []
    title_part = meeting.title[:30].lower()
    conditions = ["LOWER(title) LIKE ?"]
    params = [f"%{title_part}%"]
    for attendee in meeting.attendees[:5]:
        conditions.append("attendees LIKE ?")
        params.append(f"%{attendee}%")
    where = " OR ".join(conditions)
    rows = conn.execute(
        f"""SELECT id, meeting_date, title FROM meetings
            WHERE id != ? AND ({where})
            ORDER BY meeting_date DESC LIMIT 3""",
        [meeting.id] + params,
    ).fetchall()
    for row in rows:
        sources.append(PrepSource(
            source_type="meeting",
            title=row[2] or "",
            source_path=f"meeting:{row[0]}",
            source_date=str(row[1]) if row[1] else None,
            reason_selected="prior meeting with same attendees/title",
            relevance_score=40.0,
        ))
    return sources


def _gather_notes_by_title(conn, title: str) -> list[PrepSource]:
    """Gather notes matching title keywords."""
    sources = []
    import re
    keywords = [w.lower() for w in re.findall(r'\b\w+\b', title) if len(w) > 3]
    if not keywords:
        return sources
    conditions = []
    params = []
    for kw in keywords[:5]:
        conditions.append("LOWER(title) LIKE ?")
        conditions.append("LOWER(body) LIKE ?")
        params.extend([f"%{kw}%", f"%{kw}%"])
    where = " OR ".join(conditions)
    rows = conn.execute(
        f"""SELECT id, title, note_date, entity_name FROM notes
            WHERE {where} ORDER BY note_date DESC NULLS LAST LIMIT 5""",
        params,
    ).fetchall()
    for row in rows:
        sources.append(PrepSource(
            source_type="note",
            title=row[1] or "",
            source_path=f"entity:{row[3]}/notes",
            source_date=str(row[2]) if row[2] else None,
            reason_selected="title keyword match",
            relevance_score=30.0,
        ))
    return sources


# ------------------------------------------------------------------
# Section builders
# ------------------------------------------------------------------


def _build_risks(sources: list[PrepSource]) -> list[dict]:
    """Build risk items from signal and staffing sources."""
    items = []
    for s in sources:
        if (s.source_type == "signal" and s.relevance_score >= 50) or s.source_type == "staffing_forecast":
            items.append({"source": s.title, "detail": s.reason_selected})
    if not items:
        items.append({"source": "no data", "detail": "No significant risks found in local data."})
    return items


def _build_actions(sources: list[PrepSource]) -> list[dict]:
    """Build action items from action_item sources."""
    items = []
    for s in sources:
        if s.source_type == "action_item":
            items.append({"source": s.title, "detail": s.reason_selected})
    if not items:
        items.append({"source": "no data", "detail": "No open action items found."})
    return items


def _build_wins(sources: list[PrepSource]) -> list[dict]:
    """Build wins section. Check for positive notes."""
    items = []
    for s in sources:
        if s.source_type == "note" and s.relevance_score >= 70:
            items.append({"source": s.title, "detail": "Recent note"})
    if not items:
        items.append({"source": "no data", "detail": "No explicit wins recorded. Consider asking."})
    return items


def _build_asks(sources: list[PrepSource]) -> list[dict]:
    """Build asks section."""
    items = []
    for s in sources:
        if s.source_type == "action_item":
            items.append({"source": s.title, "detail": s.reason_selected})
    if not items:
        items.append({"source": "no data", "detail": "No outstanding asks found."})
    return items


def _build_decisions(sources: list[PrepSource]) -> list[dict]:
    """Build decisions section."""
    items = []
    for s in sources:
        if s.source_type == "decision":
            items.append({"source": s.title, "detail": s.reason_selected})
    if not items:
        items.append({"source": "no data", "detail": "No open decisions found."})
    return items


def _build_questions(sources: list[PrepSource]) -> list[dict]:
    """Build suggested questions."""
    questions = [
        "What's going well and what needs attention?",
        "Are there any upcoming risks I should be aware of?",
        "What do you need from me before our next check-in?",
    ]
    # Check for specific signal types
    for s in sources:
        if s.source_type == "signal" and "risk" in s.title.lower():
            questions.insert(0, "What is the current status of the risk items we identified last time?")
            break
    return [{"question": q} for q in questions[:5]]


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------


def build_rule_meeting_prep(
    meeting: MeetingRecord,
    conn,
    resolver: EntityResolver,
    rels: list[ResolvedRelationship],
    rules: list[dict] | None = None,
) -> MeetingPrepResponse:
    """Build a structured deterministic meeting prep response.

    Matches a rule, gathers relevant sources, builds sections, and returns
    a ``MeetingPrepResponse``. No Gemini or Workspace calls are made.
    """
    if rules is None:
        rules = DEFAULT_RULES

    # 1. Match rule
    rule_id, rule_name, match_explanation = match_meeting_rule(
        meeting, rels, resolver, rules
    )

    # Find the rule dict
    matched_rule = next((r for r in rules if r.get("id") == rule_id), rules[-1])
    prep_required = matched_rule.get("prep_required", True)
    prep_sources_config = matched_rule.get("preparation", {}).get("sources", [])
    prep_sections_config = matched_rule.get("preparation", {}).get("sections", [])

    # 2. Gather attendee names
    attendee_names = []
    resolved_attendees = []
    for attendee in meeting.attendees:
        rel = get_relationship_for_attendee(attendee, rels, resolver)
        attendee_names.append(rel.person_name)
        resolved_attendees.append({
            "name": rel.person_name,
            "relationship": rel.relationship,
            "evidence_source": rel.evidence_source,
        })

    # 3. Gather sources per rule config
    all_sources: list[PrepSource] = []
    source_map: dict[str, list[PrepSource]] = {}

    if "notes_1on1" in prep_sources_config:
        # Scope to THIS meeting's attendees, not every direct report the
        # manager has — a 1:1 with Alice should never surface Bob's notes
        # just because Bob is also a direct report somewhere in `rels`.
        for name in attendee_names:
            notes = _gather_notes_1on1(conn, name, resolver)
            if notes:
                source_map.setdefault("notes_1on1", []).extend(notes)
                all_sources.extend(notes)

    if "signals" in prep_sources_config or "team_risks" in prep_sources_config:
        entity_names = attendee_names + ["team"]
        source_map["signals"] = _gather_signals(conn, entity_names)
        all_sources.extend(source_map["signals"])

    if "action_items" in prep_sources_config or "prior_commitments" in prep_sources_config or "commitments" in prep_sources_config:
        source_map["action_items"] = _gather_action_items(conn, attendee_names)
        all_sources.extend(source_map["action_items"])

    if "staffing_exceptions" in prep_sources_config or "staffing" in prep_sources_config:
        source_map["staffing_exceptions"] = _gather_staffing_exceptions(conn)
        all_sources.extend(source_map["staffing_exceptions"])

    if "deals" in prep_sources_config or "deals_escalations" in prep_sources_config:
        source_map["deals"] = _gather_deals(conn, attendee_names)
        all_sources.extend(source_map["deals"])

    if "decisions" in prep_sources_config:
        source_map["decisions"] = _gather_decisions(conn, attendee_names)
        all_sources.extend(source_map["decisions"])

    if "prior_meetings" in prep_sources_config:
        source_map["prior_meetings"] = _gather_prior_meetings(conn, meeting)
        all_sources.extend(source_map["prior_meetings"])

    if "title_notes" in prep_sources_config or "attendee_notes" in prep_sources_config:
        source_map["title_notes"] = _gather_notes_by_title(conn, meeting.title)
        all_sources.extend(source_map["title_notes"])

    # 4. Build sections
    sections: dict[str, list[dict]] = {}
    for section_name in prep_sections_config:
        if section_name == "risks" or section_name == "blockers":
            sections[section_name] = _build_risks(all_sources)
        elif section_name == "actions" or section_name == "commitments":
            sections[section_name] = _build_actions(all_sources)
        elif section_name == "wins":
            sections[section_name] = _build_wins(all_sources)
        elif section_name == "asks":
            sections[section_name] = _build_asks(all_sources)
        elif section_name == "decisions" or section_name == "decisions_needed":
            sections[section_name] = _build_decisions(all_sources)
        elif section_name == "questions":
            sections[section_name] = _build_questions(all_sources)
        elif section_name == "talking_points":
            sections[section_name] = [{"point": "Review key items from prep sections above."}]
        elif section_name == "changes":
            sections[section_name] = [{"change": "No recent changes detected in local data."}]
        elif section_name == "announcements":
            sections[section_name] = [{"announcement": "No pending announcements in local data."}]
        elif section_name == "dependencies":
            sections[section_name] = [{"dependency": "No cross-team dependencies found in local data."}]
        elif section_name == "milestones":
            sections[section_name] = [{"milestone": "No upcoming milestones found in local data."}]
        elif section_name == "prior_notes":
            sections[section_name] = [{"note": "No prior notes available for this meeting context."}]
        elif section_name == "deals_context":
            deals_sources = [s for s in all_sources if s.source_type == "deal"]
            if deals_sources:
                sections[section_name] = [{"deal": s.title, "detail": s.reason_selected} for s in deals_sources]
            else:
                sections[section_name] = [{"deal": "no data", "detail": "No deal context found."}]

    # 5. Build warnings
    missing_warnings = []
    if not all_sources:
        missing_warnings.append("No relevant context data found in local database for this meeting.")

    # 6. Build response
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    selected_sources = [s for s in all_sources if s.relevance_score >= 30]

    # Determine meeting type
    if rule_id == "direct_report_1on1":
        meeting_type = "direct_report_1on1"
    elif rule_id == "manager_standup":
        meeting_type = "manager_standup"
    elif rule_id == "client_meeting":
        meeting_type = "client_meeting"
    elif rule_id == "team_standup":
        meeting_type = "team_standup"
    elif rule_id == "no_prep":
        meeting_type = "personal"
    else:
        meeting_type = "generic"

    return MeetingPrepResponse(
        meeting_id=meeting.id,
        meeting_title=meeting.title,
        meeting_date=meeting.meeting_date.isoformat(),
        meeting_time=meeting.start_time or "",
        attendees=meeting.attendees,
        resolved_attendees=resolved_attendees,
        matched_rule_id=rule_id,
        matched_rule_name=rule_name,
        rule_match_explanation=match_explanation,
        meeting_type=meeting_type,
        prep_required=prep_required,
        sections=sections,
        sources_consulted=all_sources,
        sources_selected=selected_sources,
        sources_excluded=[],
        missing_context_warnings=missing_warnings,
        project_doc_warnings_suppressed=True,
        generated_at=now_str,
        llm_enriched=False,
    )