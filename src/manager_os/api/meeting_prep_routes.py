"""Meeting Prep API routes.

Provides deterministic meeting preparation without external calls.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import yaml
from fastapi import APIRouter, Depends, HTTPException

from manager_os.api.deps import get_db_connection, get_fresh_settings
from manager_os.build.workspace_context import get_context_for_entities
from manager_os.config import Settings, load_clients, load_deal_aliases, load_people
from manager_os.extract.entities import EntityResolver
from manager_os.extract.meeting_prep import get_relevant_meeting_context
from manager_os.schemas import MeetingRecord

router = APIRouter(prefix="/api", tags=["meetings"])

_PREP_RULES_CACHE: list[dict] | None = None
_CONFIG_DIR_CACHE: str | None = None


def _load_prep_rules(settings: Settings) -> list[dict]:
    """Load meeting prep rules from YAML config."""
    global _PREP_RULES_CACHE, _CONFIG_DIR_CACHE
    config_dir_str = str(settings.config_dir)
    if _PREP_RULES_CACHE is not None and config_dir_str == _CONFIG_DIR_CACHE:
        return _PREP_RULES_CACHE

    rules_path = Path(settings.config_dir) / "meeting_prep_rules.yaml"
    if not rules_path.exists():
        _PREP_RULES_CACHE = []
        _CONFIG_DIR_CACHE = config_dir_str
        return _PREP_RULES_CACHE

    with open(rules_path) as f:
        data = yaml.safe_load(f)
    _PREP_RULES_CACHE = data.get("rules", [])
    _CONFIG_DIR_CACHE = config_dir_str
    return _PREP_RULES_CACHE


def _match_rule(meeting: MeetingRecord, rules: list[dict], resolver: EntityResolver | None) -> dict | None:
    """Match a meeting to the first applicable rule."""
    for rule in rules:
        match = rule.get("match", {})
        if not match:
            return rule  # Empty match = fallback

        # Check title patterns
        title_patterns = match.get("title_patterns", [])
        if title_patterns:
            title_lower = (meeting.title or "").lower()
            if not any(p.lower() in title_lower for p in title_patterns):
                continue

        # Check exact title
        exact_title = match.get("exact_title")
        if exact_title:
            if (meeting.title or "").lower() != exact_title.lower():
                continue

        # Check relationship
        relationships = match.get("relationship")
        if relationships:
            if isinstance(relationships, str):
                relationships = [relationships]
            if resolver:
                matched = False
                for attendee in meeting.attendees:
                    resolved = resolver.resolve_any(attendee)
                    if resolved and hasattr(resolved, "relationship"):
                        if resolved.relationship in relationships:
                            matched = True
                            break
                    # Check if this person is a direct report (from people config)
                    resolved_name = resolver.resolve_any(attendee)
                    if resolved_name:
                        canon = resolved_name.canonical_name
                        rel = _get_person_relationship(canon, resolver)
                        if rel in relationships:
                            matched = True
                            break
                if not matched:
                    continue

        # Check attendee names
        attendee_names = match.get("attendee_names", [])
        if attendee_names:
            attendee_lower = [a.lower() for a in meeting.attendees]
            if not any(n.lower() in attendee_lower for n in attendee_names):
                continue

        # Check attendee count
        min_count = match.get("attendee_count_min")
        max_count = match.get("attendee_count_max")
        if min_count is not None and len(meeting.attendees) < min_count:
            continue
        if max_count is not None and len(meeting.attendees) > max_count:
            continue

        return rule

    return None


def _get_person_relationship(canonical_name: str, resolver: EntityResolver | None) -> str | None:
    """Determine relationship for a person from config."""
    if resolver is None:
        return None
    # Try to infer from the resolver's known data
    try:
        from manager_os.config import get_settings
        settings = get_settings()
        people = settings.load_people() if hasattr(settings, 'load_people') else []
        for p in people:
            if p.get("name", "").lower() == canonical_name.lower():
                return p.get("relationship")
    except Exception:
        pass
    return None


def _build_prep_sections(
    meeting: MeetingRecord,
    rule: dict,
    conn,
    resolver: EntityResolver | None,
    settings: Settings,
) -> dict[str, Any]:
    """Build preparation sections for a meeting."""
    prep = rule.get("preparation", {})
    sections = prep.get("sections", [])
    sources = prep.get("sources", [])

    # Gather context
    context = get_relevant_meeting_context(meeting, conn, resolver)

    # Gather workspace context for linked entities
    entities = []
    for le in meeting.linked_entities:
        et = le.get("entity_type", "")
        en = le.get("entity_name", "")
        if et and en:
            entities.append((et, en))
    for attendee in meeting.attendees:
        if resolver:
            match = resolver.resolve_any(attendee)
            if match:
                entities.append((match.entity_type, match.canonical_name))

    ws_context = []
    if entities:
        try:
            ws_result = get_context_for_entities(conn, meeting.meeting_date, entities, lookback_days=7)
            ws_context = [c for c in ws_result if c.is_attention or c.is_action]
        except Exception:
            pass

    result_sections = {}
    for section in sections:
        result_sections[section] = _build_section(section, context, ws_context, meeting, conn, resolver)

    return {
        "sections": result_sections,
        "sources_consulted": sources,
        "context_candidates": [c.to_dict() for c in context[:15]],
        "workspace_context_items": len(ws_context),
    }


def _build_section(
    section: str,
    context: list,
    ws_context: list,
    meeting: MeetingRecord,
    conn,
    resolver: EntityResolver | None,
) -> list[dict]:
    """Build a single prep section from context."""
    items = []

    if section == "changes":
        for c in context:
            if c.source_type == "note" and c.score > 50:
                items.append({
                    "type": "note",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                    "date": c.date.isoformat() if c.date else None,
                })

    elif section == "risks":
        for c in context:
            if c.source_type == "signal" and c.metadata.get("severity") in ("critical", "high"):
                items.append({
                    "type": "risk",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                    "severity": c.metadata.get("severity"),
                })

    elif section == "actions":
        for c in context:
            if c.source_type == "action_item":
                items.append({
                    "type": "action",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                    "assigned_to": c.metadata.get("assigned_to", ""),
                })

    elif section == "decisions":
        for c in context:
            if c.source_type == "note" and "decision" in c.excerpt.lower():
                items.append({
                    "type": "decision",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                })

    elif section == "wins":
        for c in context:
            if "win" in c.excerpt.lower() or "accomplish" in c.excerpt.lower():
                items.append({
                    "type": "win",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                })

    elif section == "asks":
        for c in context:
            if "ask" in c.excerpt.lower() or "need" in c.excerpt.lower() or "help" in c.excerpt.lower():
                items.append({
                    "type": "ask",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                    "date": c.date.isoformat() if c.date else None,
                })

    elif section == "talking_points":
        for c in context[:5]:
            items.append({
                "type": "talking_point",
                "title": c.title,
                "detail": c.excerpt[:200],
                "entity": c.entity_name,
            })

    elif section == "questions":
        for c in context[:3]:
            items.append({
                "type": "question",
                "title": c.title,
                "detail": c.excerpt[:200],
                "entity": c.entity_name,
            })

    elif section == "blockers":
        for c in context:
            if "blocker" in c.excerpt.lower() or "blocked" in c.excerpt.lower():
                items.append({
                    "type": "blocker",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                })

    elif section == "commitments":
        for c in context:
            if c.source_type == "action_item":
                items.append({
                    "type": "commitment",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                    "assigned_to": c.metadata.get("assigned_to", ""),
                })

    elif section == "dependencies":
        for c in context:
            if "depend" in c.excerpt.lower() or "block" in c.excerpt.lower():
                items.append({
                    "type": "dependency",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                })

    elif section == "announcements":
        for c in context[:3]:
            if c.source_type == "note":
                items.append({
                    "type": "announcement",
                    "title": c.title,
                    "detail": c.excerpt[:200],
                    "entity": c.entity_name,
                })

    elif section == "milestones":
        try:
            rows = conn.execute(
                """SELECT id, name, client, end_date, status FROM engagements
                   WHERE status = 'active' ORDER BY end_date LIMIT 5"""
            ).fetchall()
            for row in rows:
                items.append({
                    "type": "milestone",
                    "title": row[1],
                    "detail": f"{row[2]} — {row[4]}",
                    "entity": row[2],
                    "date": row[3].isoformat() if row[3] else None,
                })
        except Exception:
            pass

    elif section == "deals_context":
        try:
            rows = conn.execute(
                """SELECT deal_name, stage, close_date, next_action
                   FROM deals WHERE close_date IS NOT NULL
                   ORDER BY close_date LIMIT 5"""
            ).fetchall()
            for row in rows:
                items.append({
                    "type": "deal",
                    "title": row[0],
                    "detail": f"{row[1]} — {row[3] or ''}",
                    "date": row[2].isoformat() if row[2] else None,
                })
        except Exception:
            pass

    elif section == "prior_meetings":
        try:
            rows = conn.execute(
                """SELECT id, meeting_date, title FROM meetings
                   WHERE meeting_date < ? AND meeting_date >= ?
                   ORDER BY meeting_date DESC LIMIT 5""",
                [meeting.meeting_date, meeting.meeting_date.isoformat()],
            ).fetchall()
            for row in rows:
                items.append({
                    "type": "prior_meeting",
                    "title": row[2],
                    "date": row[1].isoformat() if row[1] else None,
                })
        except Exception:
            pass

    elif section == "staffing":
        try:
            from manager_os.build.dashboard_data import get_people_allocation_for_week
            weeks = conn.execute(
                "SELECT DISTINCT week_start FROM staffing_forecast ORDER BY week_start LIMIT 1"
            ).fetchone()
            if weeks and weeks[0]:
                ws = weeks[0] if isinstance(weeks[0], date) else date.fromisoformat(str(weeks[0]))
                allocations = get_people_allocation_for_week(conn, ws)
                for alloc in allocations:
                    if alloc.get("warning"):
                        items.append({
                            "type": "staffing",
                            "title": alloc["person_name"],
                            "detail": f"{alloc['allocation_pct']:.0f}% — {alloc.get('warning', '')}",
                        })
        except Exception:
            pass

    # Add workspace context items
    for wc in ws_context:
        items.append({
            "type": "workspace_context",
            "title": wc.title,
            "detail": wc.excerpt[:200],
            "entity": wc.entity_name,
            "date": wc.source_date.isoformat() if wc.source_date else None,
            "is_attention": wc.is_attention,
        })

    return items


@router.get("/meetings/{meeting_id}/prep")
def get_meeting_prep(
    meeting_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    settings: Settings = Depends(get_fresh_settings),
) -> dict:
    """Get deterministic meeting preparation. No external calls."""
    # Fetch meeting
    row = conn.execute(
        "SELECT id, meeting_date, start_time, title, attendees, linked_entities, source, external_id "
        "FROM meetings WHERE id = ?",
        [meeting_id],
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")

    # Parse JSON fields
    attendees_raw = row[4]
    attendees = json.loads(attendees_raw) if isinstance(attendees_raw, str) else (attendees_raw or [])
    linked_raw = row[5]
    linked_entities = json.loads(linked_raw) if isinstance(linked_raw, str) else (linked_raw or [])

    meeting = MeetingRecord(
        id=row[0],
        meeting_date=row[1],
        start_time=row[2] or "",
        title=row[3] or "",
        attendees=attendees,
        linked_entities=linked_entities,
        source=row[6] or "",
        external_id=row[7] or "",
    )

    # Load rules
    rules = _load_prep_rules(settings)

    # Build resolver
    people = load_people(settings)
    clients = load_clients(settings)
    deal_aliases = load_deal_aliases(settings)
    resolver = EntityResolver(people, clients, deal_aliases)

    # Match rule
    matched_rule = _match_rule(meeting, rules, resolver)
    if matched_rule is None:
        matched_rule = rules[-1] if rules else {"id": "generic_fallback", "name": "Generic Meeting", "prep_required": True, "preparation": {"sections": [], "sources": []}}

    rule_id = matched_rule.get("id", "unknown")
    rule_name = matched_rule.get("name", "Unknown")
    prep_required = matched_rule.get("prep_required", True)

    # Build prep sections
    prep_data = _build_prep_sections(meeting, matched_rule, conn, resolver, settings)

    # Resolve attendees
    resolved_attendees = []
    for attendee in meeting.attendees:
        match = resolver.resolve_any(attendee) if resolver else None
        if match:
            resolved_attendees.append({
                "person_name": match.canonical_name,
                "relationship": getattr(match, "relationship", None),
                "evidence_source": "entity_resolver",
                "evidence_path": attendee,
                "warnings": [],
            })
        else:
            resolved_attendees.append({
                "person_name": attendee,
                "relationship": None,
                "evidence_source": "raw_attendee",
                "evidence_path": attendee,
                "warnings": ["Could not resolve to canonical person"],
            })

    # Check for project-document gaps
    missing_context = []
    if prep_required:
        if not prep_data.get("sections") or all(len(v) == 0 for v in prep_data["sections"].values()):
            missing_context.append("No relevant context found for this meeting")
        if not prep_data.get("workspace_context_items"):
            missing_context.append("No workspace context available for this date")

    return {
        "meeting_id": meeting_id,
        "meeting_title": meeting.title,
        "meeting_date": meeting.meeting_date.isoformat() if meeting.meeting_date else None,
        "meeting_time": meeting.start_time or "",
        "attendees": meeting.attendees,
        "resolved_attendees": resolved_attendees,
        "matched_rule_id": rule_id,
        "matched_rule_name": rule_name,
        "meeting_type": rule_name,
        "prep_required": prep_required,
        "why_this_rule_matched": f"Matched rule '{rule_name}' ({rule_id})",
        "sections": prep_data.get("sections", {}),
        "sources_consulted": prep_data.get("sources_consulted", []),
        "sources_selected": prep_data.get("sources_consulted", []),
        "sources_excluded": [],
        "missing_context_warnings": missing_context,
        "llm_enriched": False,
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.post("/meetings/{meeting_id}/prep")
def regenerate_meeting_prep(
    meeting_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    settings: Settings = Depends(get_fresh_settings),
) -> dict:
    """Regenerate deterministic meeting preparation. No external calls."""
    return get_meeting_prep(meeting_id, conn=conn, settings=settings)


@router.post("/meetings/sync-calendar")
def sync_calendar(
    body: dict,
    conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    settings: Settings = Depends(get_fresh_settings),
) -> dict:
    """Sync calendar events for a specific date via Gemini CLI.

    This is an explicit external operation — only called when the user
    clicks the Sync Calendar button.

    Returns honest diagnostics: retrieved_count, persisted_count,
    rejected_count, replaced_count, warnings, errors. Never returns
    ok:true with zero meetings when retrieval succeeded but all
    persistence failed.
    """
    from datetime import datetime as _dt
    from manager_os.ingest.workspace_gemini import retrieve_calendar
    from manager_os.ingest.calendar_persistence import persist_calendar_events

    target_date_str = body.get("date", "")
    if not target_date_str:
        raise HTTPException(status_code=400, detail="Missing 'date' field")

    try:
        target_date = date.fromisoformat(target_date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {target_date_str!r}")

    try:
        result = retrieve_calendar(
            target_date,
            use_yolo=True,
            timeout=180,
        )
    except Exception as exc:
        return {
            "ok": False,
            "date": target_date_str,
            "meetings": [],
            "retrieved_count": 0,
            "persisted_count": 0,
            "rejected_count": 0,
            "replaced_count": 0,
            "retrieved_at": _dt.utcnow().isoformat(),
            "source": "gemini_cli",
            "warnings": [],
            "errors": [f"Calendar retrieval failed: {exc}"],
        }

    if not result.ok:
        return {
            "ok": False,
            "date": target_date_str,
            "meetings": [],
            "retrieved_count": 0,
            "persisted_count": 0,
            "rejected_count": 0,
            "replaced_count": 0,
            "retrieved_at": _dt.utcnow().isoformat(),
            "source": "gemini_cli",
            "warnings": [],
            "errors": [result.error or "Calendar retrieval returned failure status"],
        }

    # Persist events using the canonical path
    retrieved_at = result.retrieved_at or _dt.utcnow().isoformat()
    persist_result = persist_calendar_events(
        conn,
        target_date,
        result.items,
        source="calendar_sync",
        retrieved_at=retrieved_at,
    )

    # Determine ok/partial based on persistence results
    retrieved_count = persist_result.retrieved_count
    persisted_count = persist_result.persisted_count

    if retrieved_count > 0 and persisted_count == 0:
        # All persistence failed — not ok
        ok = False
        partial = False
    elif persist_result.rejected_count > 0 and persisted_count > 0:
        ok = True
        partial = True
    else:
        ok = True
        partial = False

    return {
        "ok": ok,
        "partial": partial,
        "date": target_date_str,
        "meetings": persist_result.persisted_meetings,
        "retrieved_count": retrieved_count,
        "persisted_count": persisted_count,
        "rejected_count": persist_result.rejected_count,
        "replaced_count": persist_result.replaced_count,
        "retrieved_at": retrieved_at,
        "source": "gemini_cli",
        "warnings": persist_result.warnings,
        "errors": persist_result.errors,
    }


@router.post("/meetings/{meeting_id}/generate-prep")
def generate_meeting_prep(
    meeting_id: str,
    conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    settings: Settings = Depends(get_fresh_settings),
) -> dict:
    """Generate LLM-driven meeting preparation.

    This is an explicit operation — only called when the user clicks
    Generate Prep. Uses the LLM to produce structured, grounded prep.
    """
    from manager_os.extract.meeting_profiles import classify_meeting
    from manager_os.extract.llm_meeting_prep import (
        generate_prep, persist_prep, get_prep_freshness,
        get_persisted_prep, PrepGenerationError,
    )

    # Fetch meeting
    row = conn.execute(
        "SELECT id, meeting_date, start_time, end_time, title, attendees, "
        "linked_entities, source, external_id, location, description_summary "
        "FROM meetings WHERE id = ?",
        [meeting_id],
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Meeting {meeting_id} not found")

    meeting = {
        "id": row[0],
        "meeting_date": str(row[1]) if row[1] else "",
        "start_time": row[2] or "",
        "end_time": row[3] or "",
        "title": row[4] or "",
        "attendees": _safe_json_list(row[5], str),
        "linked_entities": _safe_json_list(row[6], dict),
        "source": row[7] or "",
        "external_id": row[8] or "",
        "location": row[9] or "",
        "description_summary": row[10] or "",
    }

    # Classify meeting
    classification = classify_meeting(meeting)

    # If no-prep, return immediately
    if not classification.get("prep_required", True):
        return {
            "ok": True,
            "meeting_id": meeting_id,
            "prep_state": "no_prep",
            "classification": classification,
            "prep": None,
            "message": "No preparation needed for this meeting type.",
        }

    # Build meeting fingerprint
    meeting_fingerprint = str(hash((
        meeting.get("title", ""),
        meeting.get("meeting_date", ""),
        meeting.get("start_time", ""),
        tuple(meeting.get("attendees", [])),
        meeting.get("description_summary", ""),
    )))

    # Build context bundle using the profile-driven retrieval planner
    from manager_os.extract.meeting_context import retrieve_meeting_context
    meeting_type_for_context = classification.get("meeting_type", "generic")
    context_result = retrieve_meeting_context(conn, meeting, meeting_type_for_context)
    context_bundle = {
        "sources": context_result.sources,
        "items": [{"source_id": i.source_id, "source_type": i.source_type,
                    "title": i.title, "date": str(i.date) if i.date else "",
                    "entity": i.entity, "excerpt_or_fact": i.excerpt_or_fact,
                    "relevance_reason": i.relevance_reason, "confidence": i.confidence}
                  for i in context_result.items],
    }
    source_fingerprint = str(hash(tuple(s.get("source_id", "") for s in context_result.sources)))

    # Check freshness
    freshness = get_prep_freshness(conn, meeting_id, meeting_fingerprint, source_fingerprint)
    if freshness == "current":
        existing = get_persisted_prep(conn, meeting_id)
        if existing:
            return {
                "ok": True,
                "meeting_id": meeting_id,
                "prep_state": "current",
                "classification": classification,
                "prep": existing["prep_data"],
                "generated_at": existing["generated_at"],
                "message": "Prep is current. Use Regenerate to create a new one.",
            }

    # Generate prep via LLM
    profile = {
        "meeting_type": classification.get("meeting_type", "generic"),
        "objective": classification.get("objective", "Prepare for this meeting."),
        "output_schema": classification.get("output_schema", "generic"),
    }

    try:
        prep_data = generate_prep(
            meeting,
            classification.get("meeting_type", "generic"),
            context_bundle,
            profile=profile,
        )
    except PrepGenerationError as e:
        # Persist failure
        persist_prep(
            conn, meeting_id, {}, classification.get("meeting_type", "generic"),
            classification.get("profile_id", ""), meeting_fingerprint,
            source_fingerprint, [], "gemini_cli", "",
            generation_status="failed",
            safe_error=str(e),
        )
        return {
            "ok": False,
            "meeting_id": meeting_id,
            "prep_state": "failed",
            "classification": classification,
            "prep": None,
            "error": str(e),
            "message": "Context gathered, but preparation generation failed.",
        }

    # Persist prep
    persist_prep(
        conn, meeting_id, prep_data,
        classification.get("meeting_type", "generic"),
        classification.get("profile_id", ""),
        meeting_fingerprint, source_fingerprint,
        [], "gemini_cli", "",
    )

    return {
        "ok": True,
        "meeting_id": meeting_id,
        "prep_state": "generated",
        "classification": classification,
        "prep": prep_data,
        "message": "Prep generated successfully.",
    }


def _safe_json_list(raw, item_type: type) -> list:
    """Parse a JSON list field safely."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, item_type)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, item_type)]
        except (json.JSONDecodeError, ValueError):
            pass
    return []