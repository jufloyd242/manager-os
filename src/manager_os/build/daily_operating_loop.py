"""Deterministic, local-DB-only Daily Operating Loop payload builder.

Used by `manager-os daily` to answer, every morning, without any live
Gemini/Workspace/Drive/Calendar/Chat/Sheets/OpenAI calls:

  - Which people are overloaded or underused?
  - Which meetings need prep?
  - Which projects/deals have open risk signals?
  - Which projects are missing documents?
  - What feedback has changed signal behavior?
  - What should Justin do next?

Every section reads only from the local DuckDB database. If a table is
missing or a query fails, the section degrades to an empty list and a
warning is recorded instead of crashing the whole loop.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from manager_os.build.dashboard_data import (
    get_forecast_week_list,
    get_meetings_for_date,
    get_people_allocation_for_week,
    get_today_signals,
)


def _people_staffing(conn, target_date: date, settings, warnings: list[str]) -> list[dict]:
    """Return per-person allocation entries that carry a warning (over/under-allocated)."""
    try:
        weeks = get_forecast_week_list(conn, as_of=target_date, limit=1)
        if not weeks:
            return []
        allocations = get_people_allocation_for_week(conn, weeks[0], settings=settings)
        return [a for a in allocations if a.get("warning")]
    except Exception as exc:
        warnings.append(f"people_staffing: {exc}")
        return []


def _meetings_needing_prep(conn, target_date: date, warnings: list[str]) -> list[dict]:
    """Return today's meetings that have no row yet in meeting_prep."""
    try:
        meetings = get_meetings_for_date(conn, target_date)
        prepped_ids = {
            row[0] for row in conn.execute("SELECT DISTINCT meeting_id FROM meeting_prep").fetchall()
        }
        return [
            {
                "id": m["id"],
                "title": m["title"],
                "start_time": m.get("start_time") or "",
                "reason": "No meeting prep generated yet",
            }
            for m in meetings
            if m["id"] not in prepped_ids
        ]
    except Exception as exc:
        warnings.append(f"meetings: {exc}")
        return []


def _projects_deals_risk(conn, target_date: date, warnings: list[str]) -> list[dict]:
    """Return open medium+ severity signals for deal/client entities."""
    try:
        signals = get_today_signals(conn, target_date=target_date, min_severity="medium")
        return [
            {
                "entity_type": s.entity_type,
                "entity_name": s.entity_name,
                "severity": s.severity,
                "summary": s.summary,
                "why_it_matters": s.why_it_matters,
            }
            for s in signals
            if s.entity_type in ("deal", "client")
        ]
    except Exception as exc:
        warnings.append(f"projects_deals: {exc}")
        return []


def _document_gaps(conn, warnings: list[str]) -> list[dict]:
    """Return projects with zero rows in project_documents."""
    try:
        rows = conn.execute(
            """
            SELECT p.opportunity_number, p.project_name, p.client
            FROM projects p
            LEFT JOIN project_documents d ON d.project_id = p.id
            WHERE p.opportunity_number != ''
            GROUP BY p.opportunity_number, p.project_name, p.client
            HAVING COUNT(d.id) = 0
            ORDER BY p.opportunity_number
            """
        ).fetchall()
        return [
            {
                "opportunity_number": r[0],
                "project_name": r[1] or "",
                "client": r[2] or "",
                "suggested_command": (
                    f"manager-os project-docs-fetch --opportunity-number {r[0]} --dry-run"
                ),
            }
            for r in rows
        ]
    except Exception as exc:
        warnings.append(f"document_gaps: {exc}")
        return []


def _feedback_learning(conn, warnings: list[str]) -> list[dict]:
    """Return feedback-learning candidates (signals whose behavior should change)."""
    try:
        rows = conn.execute(
            """
            SELECT pattern_type, entity_name, signal_type, rating, event_count, suggested_action, status
            FROM feedback_learning_candidates
            ORDER BY event_count DESC
            """
        ).fetchall()
        return [
            {
                "pattern_type": r[0],
                "entity_name": r[1] or "",
                "signal_type": r[2] or "",
                "rating": r[3],
                "event_count": r[4],
                "suggested_action": r[5] or "",
                "status": r[6],
            }
            for r in rows
        ]
    except Exception as exc:
        warnings.append(f"feedback_learning: {exc}")
        return []


def _recommended_actions(
    people_staffing: list[dict],
    meetings: list[dict],
    document_gaps: list[dict],
) -> list[dict]:
    """Build deterministic recommended actions — no LLM."""
    actions: list[dict] = []

    for p in people_staffing:
        pct = p.get("allocation_pct", 0) or 0
        actions.append({
            "title": f"Review allocation for {p['person_name']} — {pct:.0f}% planned.",
            "reason": p.get("warning") or f"{pct:.0f}% allocation",
            "command": None,
            "priority": "high" if pct > 150 else "medium",
        })

    for m in meetings:
        when = f" at {m['start_time']}" if m.get("start_time") else ""
        actions.append({
            "title": f"Prep for {m['title']} — meeting today{when}.",
            "reason": m["reason"],
            "command": None,
            "priority": "high",
        })

    for g in document_gaps:
        actions.append({
            "title": f"Fetch docs for {g['opportunity_number']} — no project documents indexed.",
            "reason": "0 documents in project_documents",
            "command": g["suggested_command"],
            "priority": "medium",
        })

    return actions


def build_daily_operating_loop(conn, target_date: date, settings=None) -> dict[str, Any]:
    """Build the deterministic Daily Operating Loop payload from local DB data only.

    No live Gemini/Workspace/Drive/Calendar/Chat/Sheets/OpenAI calls are made.
    """
    warnings: list[str] = []

    people_staffing = _people_staffing(conn, target_date, settings, warnings)
    meetings = _meetings_needing_prep(conn, target_date, warnings)
    projects_deals = _projects_deals_risk(conn, target_date, warnings)
    document_gaps = _document_gaps(conn, warnings)
    feedback_learning = _feedback_learning(conn, warnings)
    recommended_actions = _recommended_actions(people_staffing, meetings, document_gaps)

    return {
        "date": target_date.isoformat(),
        "people_staffing": people_staffing,
        "meetings": meetings,
        "projects_deals": projects_deals,
        "document_gaps": document_gaps,
        "feedback_learning": feedback_learning,
        "recommended_actions": recommended_actions,
        "warnings": warnings,
    }
