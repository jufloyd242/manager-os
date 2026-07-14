"""Meeting-specific context retrieval planner.

The LLM must not independently roam through all available data.
This module determines:
- Allowed source types
- Date range
- Entity scope
- Record limits
- Excluded context
- Whether targeted live retrieval is permitted

The LLM reasons only over the supplied context bundle.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context limits (default maximums)
# ---------------------------------------------------------------------------

CONTEXT_LIMITS: dict[str, int] = {
    "highlights": 12,
    "commitments": 8,
    "risks": 5,
    "decisions": 5,
    "actions": 8,
    "prior_meetings": 3,
    "project_documents": 8,
    "sources": 20,
}


# ---------------------------------------------------------------------------
# Profile retrieval plans
# ---------------------------------------------------------------------------

PROFILE_RETRIEVAL_PLANS: dict[str, dict[str, Any]] = {
    "upward_daily_status": {
        "includes": [
            "today_priorities",
            "current_work_priorities",
            "progress_since_previous",
            "commitments_to_manager",
            "items_waiting_on_manager",
            "decisions_needed",
            "active_blockers",
            "staffing_exceptions",
            "client_escalations",
            "deal_presales_issues",
            "meaningful_wins",
            "recent_notes",
            "prior_standup_commitments",
        ],
        "excludes": [
            "attendee_biographies",
            "unrelated_project_history",
            "completed_low_priority_work",
            "broad_staffing_without_exception",
            "full_project_document_dumps",
            "old_unchanged_risks",
            "technical_rule_metadata",
            "personal_hr_context",
        ],
        "time_horizon": "since_previous_occurrence",
        "fallback_horizon_hours": 48,
    },
    "direct_report_1on1": {
        "includes": [
            "previous_1on1s",
            "notes_involving_employee",
            "open_commitments",
            "open_actions",
            "recent_wins",
            "risks_blockers",
            "current_project_client",
            "allocation",
            "upcoming_roll_off",
            "growth_topic",
            "morale_signal",
            "support_owed",
            "unresolved_decisions",
        ],
        "excludes": [
            "unrelated_team_context",
            "broad_staffing_data",
            "historical_project_summaries",
        ],
        "time_horizon": "last_3_1on1s",
    },
    "manager_1on1": {
        "includes": [
            "progress_against_goals",
            "team_performance",
            "staffing_hiring",
            "client_escalations",
            "deal_escalations",
            "decisions_needed",
            "organizational_problems",
            "career_scope_topics",
            "prior_1on1_commitments",
            "strategic_topics",
        ],
        "excludes": [
            "daily_standup_detail",
            "individual_contributor_detail",
        ],
        "time_horizon": "since_last_1on1",
    },
    "client_project": {
        "includes": [
            "project_record",
            "opportunity_number",
            "sow",
            "deal_sheet",
            "loe",
            "project_plan",
            "milestones",
            "risks",
            "actions",
            "decisions",
            "recent_project_notes",
            "previous_meetings",
            "workspace_context",
            "staffing",
            "scope_concerns",
        ],
        "excludes": [
            "unrelated_client_data",
            "personal_context",
        ],
        "time_horizon": "project_lifetime",
    },
    "deal_presales": {
        "includes": [
            "deal_stage",
            "close_date",
            "amount",
            "probability_category",
            "customer_need",
            "proposed_solution",
            "sow",
            "loe",
            "staffing_feasibility",
            "requested_roles",
            "technical_risks",
            "blockers",
            "next_step",
            "delivery_comments",
            "relevant_drive_documents",
            "previous_deal_discussions",
        ],
        "excludes": [
            "unrelated_deals",
            "completed_project_history",
        ],
        "time_horizon": "deal_lifetime",
    },
    "team_standup": {
        "includes": [
            "current_blockers",
            "dependencies",
            "staffing_issues",
            "commitments_due",
            "decisions_needed",
            "announcements",
            "high_priority_actions",
            "meaningful_wins",
        ],
        "excludes": [
            "individual_1on1_detail",
            "strategic_topics",
        ],
        "time_horizon": "since_previous_standup",
    },
    "interview": {
        "includes": [
            "candidate_role",
            "interview_stage",
            "interview_focus",
            "required_competencies",
            "prior_interviewer_feedback",
            "assigned_questions",
        ],
        "excludes": [
            "employee_performance_context",
            "unrelated_team_data",
        ],
        "time_horizon": "interview_cycle",
    },
    "generic": {
        "includes": [
            "recent_notes_involving_attendees",
            "prior_recurring_meetings",
            "open_actions_involving_attendees",
            "entities_in_title_description",
            "relevant_recent_signals",
            "calendar_links_attachments",
        ],
        "excludes": [
            "broad_data_dumps",
        ],
        "time_horizon": "recent_week",
    },
    "no_prep": {
        "includes": [],
        "excludes": ["all"],
        "time_horizon": "none",
    },
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ContextItem:
    """A single context item for meeting prep."""
    source_id: str
    source_type: str  # signal, action_item, note, decision, meeting, project_doc
    title: str
    date: date | None
    entity: str
    excerpt_or_fact: str
    relevance_reason: str
    confidence: float = 0.5
    severity: str = ""  # for signals
    metadata: dict = field(default_factory=dict)


@dataclass
class ContextBundle:
    """A bundle of context items for LLM prep generation."""
    sources: list[dict] = field(default_factory=list)
    items: list[ContextItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    profile_id: str = ""
    time_horizon: str = ""


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def rank_context_items(
    items: list[ContextItem],
    attendees: list[str] | None = None,
) -> list[ContextItem]:
    """Rank context items deterministically.

    Ranking signals (higher = better):
    1. Exact attendee match
    2. Severity (critical > high > medium > low)
    3. Recency (more recent = better)
    4. Confidence
    """
    if attendees is None:
        attendees = []

    def sort_key(item: ContextItem) -> tuple:
        # Attendee match (1 if match, 0 if not) — reversed for descending
        attendee_match = 0
        for a in attendees:
            if a.lower() in (item.entity or "").lower():
                attendee_match = 1
                break

        # Severity (lower number = higher priority, so negate)
        severity_rank = _SEVERITY_ORDER.get(item.severity, 4) if item.severity else 4

        # Recency (more recent = higher date, so negate for descending)
        date_val = item.date or date.min

        # Confidence
        conf = item.confidence

        # Return tuple for sorting (descending: negate positive values)
        return (-attendee_match, severity_rank, -date_val.toordinal() if date_val else 0, -conf)

    return sorted(items, key=sort_key)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def retrieve_meeting_context(
    conn,
    meeting: dict[str, Any],
    meeting_type: str,
    *,
    target_date: date | None = None,
) -> ContextBundle:
    """Retrieve meeting-specific context based on the meeting's profile.

    Args:
        conn: DuckDB connection.
        meeting: Meeting dict with title, attendees, meeting_date, etc.
        meeting_type: Classified meeting type (e.g. "upward_daily_status").
        target_date: Optional date for time horizon calculation.

    Returns:
        ContextBundle with sources and ranked items.
    """
    plan = PROFILE_RETRIEVAL_PLANS.get(meeting_type, PROFILE_RETRIEVAL_PLANS["generic"])

    # No-prep meetings get empty context
    if meeting_type == "no_prep" or not plan.get("includes"):
        return ContextBundle(
            sources=[],
            items=[],
            profile_id=meeting_type,
            time_horizon=plan.get("time_horizon", "none"),
        )

    attendees = meeting.get("attendees", [])
    if not isinstance(attendees, list):
        attendees = []
    meeting_date_str = meeting.get("meeting_date", "")
    try:
        meeting_date = date.fromisoformat(str(meeting_date_str)) if meeting_date_str else date.today()
    except (ValueError, TypeError):
        meeting_date = date.today()

    # Calculate time horizon
    lookback_days = 7  # Default
    if plan.get("time_horizon") == "since_previous_occurrence":
        lookback_days = 2  # Previous occurrence + today
    elif plan.get("time_horizon") == "recent_week":
        lookback_days = 7
    elif plan.get("time_horizon") == "project_lifetime":
        lookback_days = 365
    elif plan.get("time_horizon") == "deal_lifetime":
        lookback_days = 365

    horizon_start = meeting_date - timedelta(days=lookback_days)

    items: list[ContextItem] = []
    sources: list[dict] = []

    # 1. Retrieve signals for attendees
    try:
        rows = conn.execute(
            """SELECT id, signal_date, entity_name, signal_type, severity,
                      summary, why_it_matters, requires_manager_attention, confidence
               FROM signals
               WHERE signal_date >= ? AND status = 'open'
               ORDER BY signal_date DESC
               LIMIT ?""",
            [horizon_start, CONTEXT_LIMITS["risks"] * 2],
        ).fetchall()
        for r in rows:
            sig_id = r[0]
            sig_date = r[1] if r[1] else None
            entity = r[2] or ""
            severity = r[4] or "medium"
            summary = r[5] or ""
            item = ContextItem(
                source_id=f"signal:{sig_id}",
                source_type="signal",
                title=summary[:100],
                date=sig_date,
                entity=entity,
                excerpt_or_fact=summary[:500],
                relevance_reason="Open signal" + (f" ({severity})" if severity else ""),
                confidence=float(r[8]) if r[8] else 0.5,
                severity=severity,
            )
            items.append(item)
            sources.append({
                "source_id": f"signal:{sig_id}",
                "source_type": "signal",
                "title": summary[:100],
                "date": str(sig_date) if sig_date else "",
                "entity": entity,
                "excerpt_or_fact": summary[:500],
                "relevance_reason": item.relevance_reason,
                "confidence": item.confidence,
            })
    except Exception as e:
        logger.warning("Failed to retrieve signals: %s", e)

    # 2. Retrieve action items
    try:
        rows = conn.execute(
            """SELECT id, assigned_to, description, due_date, status
               FROM action_items
               WHERE status = 'open'
               ORDER BY due_date NULLS LAST
               LIMIT ?""",
            [CONTEXT_LIMITS["actions"]],
        ).fetchall()
        for r in rows:
            ai_id = r[0]
            assigned_to = r[1] or ""
            desc = r[2] or ""
            due = r[3] if r[3] else None
            item = ContextItem(
                source_id=f"action:{ai_id}",
                source_type="action_item",
                title=desc[:100],
                date=due,
                entity=assigned_to,
                excerpt_or_fact=desc[:500],
                relevance_reason="Open action item" + (f" (due {due})" if due else ""),
                confidence=0.7,
            )
            items.append(item)
            sources.append({
                "source_id": f"action:{ai_id}",
                "source_type": "action_item",
                "title": desc[:100],
                "date": str(due) if due else "",
                "entity": assigned_to,
                "excerpt_or_fact": desc[:500],
                "relevance_reason": item.relevance_reason,
                "confidence": 0.7,
            })
    except Exception as e:
        logger.warning("Failed to retrieve action items: %s", e)

    # 3. Retrieve notes for attendees
    try:
        # Build attendee filter
        attendee_names = [a.split("@")[0] if "@" in a else a for a in attendees]
        placeholders = ", ".join("?" * len(attendee_names)) if attendee_names else "''"
        rows = conn.execute(
            f"""SELECT id, note_date, entity_name, title, body, note_type
               FROM notes
               WHERE note_date >= ?
               AND (entity_name IN ({placeholders}) OR title LIKE ? OR body LIKE ?)
               ORDER BY note_date DESC
               LIMIT ?""",
            [horizon_start] + attendee_names + [f"%{attendees[0]}%" if attendees else "%%",
                                                f"%{attendees[0]}%" if attendees else "%%",
                                                CONTEXT_LIMITS["prior_meetings"] * 2],
        ).fetchall()
        for r in rows:
            note_id = r[0]
            note_date = r[1] if r[1] else None
            entity = r[2] or ""
            title = r[3] or ""
            body = r[4] or ""
            excerpt = body[:500] + ("..." if len(body) > 500 else "")
            item = ContextItem(
                source_id=f"note:{note_id}",
                source_type="note",
                title=title[:100],
                date=note_date,
                entity=entity,
                excerpt_or_fact=excerpt,
                relevance_reason="Recent note involving attendee",
                confidence=0.6,
            )
            items.append(item)
            sources.append({
                "source_id": f"note:{note_id}",
                "source_type": "note",
                "title": title[:100],
                "date": str(note_date) if note_date else "",
                "entity": entity,
                "excerpt_or_fact": excerpt,
                "relevance_reason": item.relevance_reason,
                "confidence": 0.6,
            })
    except Exception as e:
        logger.warning("Failed to retrieve notes: %s", e)

    # Rank items
    ranked_items = rank_context_items(items, attendees)

    # Apply total limit
    max_items = CONTEXT_LIMITS["highlights"] + CONTEXT_LIMITS["risks"] + CONTEXT_LIMITS["actions"]
    ranked_items = ranked_items[:max_items]
    sources = sources[:CONTEXT_LIMITS["sources"]]

    return ContextBundle(
        sources=sources,
        items=ranked_items,
        profile_id=meeting_type,
        time_horizon=plan.get("time_horizon", "recent_week"),
    )
