"""Dashboard data query functions.

All functions return typed dicts or schema model instances for use
in the Streamlit dashboard. Cached with st.cache_data externally.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from manager_os.schemas import (
    ActionItem,
    DashboardDealRow,
    DashboardForecastRow,
    DashboardPeopleRow,
    MeetingRecord,
    Signal,
)

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _safe_json_list(raw, item_type: type) -> list:
    """Parse a JSON list field safely, handling malformed legacy values.

    Tries json.loads first. If that fails (e.g. Python-repr style strings
    from old str() serialization), attempts ast.literal_eval as a fallback.
    Returns [] on any parse failure and emits a warning.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, item_type)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, item_type)]
            return []
        except (json.JSONDecodeError, ValueError):
            # Fallback: try ast.literal_eval for legacy Python-repr strings
            try:
                import ast
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, list):
                    logger.warning("Parsed legacy Python-repr JSON field (not valid JSON)")
                    return [item for item in parsed if isinstance(item, item_type)]
            except (ValueError, SyntaxError):
                logger.warning("Could not parse JSON list field, returning empty list")
            return []
    return []


def _severity_rank(severity: str) -> int:
    return _SEVERITY_ORDER.get(severity, 4)


# ------------------------------------------------------------------
# Today tab
# ------------------------------------------------------------------


def get_today_signals(
    conn,
    target_date: date | None = None,
    min_severity: str = "medium",
    statuses: list[str] | None = None,
    include_feedback_hidden: bool = False,
) -> list[Signal]:
    """Return open signals, filtered by severity threshold.

    By default excludes signals with feedback-hidden statuses
    (noisy, stale, wrong, dismissed, acknowledged, snoozed).
    Set include_feedback_hidden=True to show all statuses.
    """
    if target_date is None:
        target_date = date.today()
    if statuses is None:
        if include_feedback_hidden:
            statuses = ["open", "needs_context", "noisy", "stale", "wrong",
                        "dismissed", "acknowledged", "snoozed"]
        else:
            statuses = ["open", "needs_context"]

    min_rank = _severity_rank(min_severity)
    status_placeholders = ", ".join("?" * len(statuses))

    rows = conn.execute(
        f"""
        SELECT id, signal_date, source, source_path, entity_type, entity_name,
               signal_type, severity, summary, why_it_matters,
               requires_manager_attention, owner, due_date, confidence, status,
               created_at, updated_at
        FROM signals
        WHERE status IN ({status_placeholders})
        ORDER BY
            CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                          WHEN 'medium' THEN 2 ELSE 3 END,
            signal_date DESC
        """,
        statuses,
    ).fetchall()

    signals = []
    for row in rows:
        try:
            s = Signal(
                id=row[0], signal_date=row[1], source=row[2], source_path=row[3] or "",
                entity_type=row[4], entity_name=row[5], signal_type=row[6],
                severity=row[7], summary=row[8], why_it_matters=row[9] or "",
                requires_manager_attention=bool(row[10]), owner=row[11] or "",
                due_date=row[12], confidence=float(row[13]), status=row[14],
                created_at=row[15], updated_at=row[16],
            )
            if _severity_rank(s.severity) <= min_rank:
                signals.append(s)
        except Exception as exc:
            logger.warning("Skipping malformed signal: %s", exc)
    return signals


def _row_to_action_item(row) -> ActionItem:
    """Convert a DB row (10-column SELECT) to an ActionItem."""
    return ActionItem(
        id=row[0], signal_id=row[1], source_note_id=row[2],
        assigned_to=row[3], description=row[4],
        due_date=row[5], status=row[6], created_at=row[7],
        feedback_rating=row[8], feedback_reason=row[9],
        snooze_until=row[10],
    )


_AI_SELECT = """
    SELECT id, signal_id, source_note_id, assigned_to, description,
           due_date, status, created_at,
           feedback_rating, feedback_reason, snooze_until
    FROM action_items
"""


def get_open_action_items(conn) -> list[ActionItem]:
    """Return action items that are currently open (excludes snoozed until future date)."""
    today = date.today()
    rows = conn.execute(
        _AI_SELECT + """
        WHERE status = 'open'
          AND (snooze_until IS NULL OR snooze_until <= ?)
        ORDER BY due_date NULLS LAST
        """,
        [today],
    ).fetchall()
    items = []
    for row in rows:
        try:
            items.append(_row_to_action_item(row))
        except Exception:
            pass
    return items


def get_action_items_filtered(
    conn,
    statuses: list[str] | None = None,
    assigned_to: str | None = None,
    include_snoozed: bool = False,
) -> list[ActionItem]:
    """Return action items filtered by status list and optional assignee."""
    today = date.today()
    if statuses is None:
        statuses = ["open"]
    placeholders = ", ".join("?" * len(statuses))
    params: list = list(statuses)
    snooze_clause = "" if include_snoozed else " AND (snooze_until IS NULL OR snooze_until <= ?)"
    if not include_snoozed:
        params.append(today)
    assignee_clause = ""
    if assigned_to:
        assignee_clause = " AND assigned_to = ?"
        params.append(assigned_to)
    rows = conn.execute(
        _AI_SELECT + f"""
        WHERE status IN ({placeholders}){snooze_clause}{assignee_clause}
        ORDER BY due_date NULLS LAST
        """,
        params,
    ).fetchall()
    items = []
    for row in rows:
        try:
            items.append(_row_to_action_item(row))
        except Exception:
            pass
    return items


def update_action_item(
    conn,
    item_id: str,
    *,
    status: str | None = None,
    feedback_rating: str | None = None,
    feedback_reason: str | None = None,
    snooze_until=None,
) -> None:
    """Update status and/or feedback on an action item."""
    from datetime import datetime as _dt
    now = _dt.utcnow()
    sets = ["updated_at = ?"]
    params: list = [now]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if feedback_rating is not None:
        sets.append("feedback_rating = ?")
        params.append(feedback_rating)
    if feedback_reason is not None:
        sets.append("feedback_reason = ?")
        params.append(feedback_reason)
    if snooze_until is not None:
        sets.append("snooze_until = ?")
        params.append(snooze_until)
    params.append(item_id)
    conn.execute(
        f"UPDATE action_items SET {', '.join(sets)} WHERE id = ?",
        params,
    )


def get_meetings_for_date(conn, target_date: date) -> list[dict]:
    """Return meetings for *target_date* as plain dicts (stable public contract).

    Each dict has keys:
        id, meeting_date, start_time, title, attendees (list[str]),
        linked_entities (list[dict]), source, external_id, updated_at

    Meetings with no attendees (solo timeblocks) are excluded.
    Duplicates are collapsed by (external_id) or (title + start_time + date)
    keeping the richest record — one with attendees > external_id > linked_entities.
    """
    from manager_os.schemas import MeetingRecord  # local import avoids circularity

    rows = conn.execute(
        """
        SELECT id, meeting_date, start_time, end_time, title, attendees,
               linked_entities, source, external_id, location,
               description_summary, updated_at
        FROM meetings WHERE meeting_date = ?
        ORDER BY start_time NULLS LAST
        """,
        [target_date],
    ).fetchall()

    parsed: list[dict] = []
    for r in rows:
        attendees_raw = r[5]
        attendees: list[str] = _safe_json_list(attendees_raw, str)
        linked_raw = r[6]
        linked: list[dict] = _safe_json_list(linked_raw, dict)
        # Skip solo / no-attendee events
        if not attendees:
            logger.debug("Skipping no-attendee meeting: %s on %s", r[4], r[1])
            continue
        parsed.append({
            "id": r[0],
            "meeting_date": r[1],
            "start_time": r[2] or "",
            "end_time": r[3] or "",
            "title": r[4] or "",
            "attendees": attendees,
            "linked_entities": linked,
            "source": r[7] or "",
            "external_id": r[8] or "",
            "location": r[9] or "",
            "description_summary": r[10] or "",
            "updated_at": r[11],
        })

    # --- Deduplicate ---
    # Key: external_id if present, else (normalised_title, start_time, date)
    def _dedup_key(m: dict) -> str:
        if m.get("external_id"):
            return f"ext:{m['external_id']}"
        norm_title = (m["title"] or "").lower().strip()
        return f"ts:{norm_title}|{m['start_time']}|{m['meeting_date']}"

    def _richness(m: dict) -> tuple:
        """Higher tuple → richer record; used for max() selection."""
        return (
            1 if m["attendees"] else 0,
            1 if m.get("external_id") else 0,
            len(m.get("linked_entities") or []),
            1 if m.get("source") else 0,
        )

    seen: dict[str, dict] = {}
    for m in parsed:
        key = _dedup_key(m)
        if key not in seen or _richness(m) > _richness(seen[key]):
            seen[key] = m

    return list(seen.values())


def meeting_dict_to_record(m: dict):
    """Convert a meeting dict (from get_meetings_for_date) to a MeetingRecord.

    Safe to call even when linked_entities or attendees are missing/None.
    """
    from manager_os.schemas import MeetingRecord
    return MeetingRecord(
        id=m["id"],
        meeting_date=m["meeting_date"],
        start_time=m.get("start_time") or "",
        title=m.get("title") or "",
        attendees=m.get("attendees") or [],
        linked_entities=m.get("linked_entities") or [],
        source=m.get("source") or "",
        external_id=m.get("external_id") or "",
    )


def update_signal_status(
    conn,
    signal_id: str,
    new_status: str,
    changed_by: str = "dashboard",
    note: str = "",
) -> None:
    """Update a signal's status and write an audit log entry."""
    from datetime import datetime
    from manager_os.db import content_hash

    # Fetch current status for the log
    row = conn.execute("SELECT status FROM signals WHERE id = ?", [signal_id]).fetchone()
    old_status = row[0] if row else "unknown"

    now = datetime.utcnow()
    conn.execute(
        "UPDATE signals SET status = ?, updated_at = ? WHERE id = ?",
        [new_status, now, signal_id],
    )

    # Write audit log entry
    log_id = content_hash(f"status_log::{signal_id}::{new_status}::{now.isoformat()}")
    conn.execute(
        """
        INSERT OR IGNORE INTO signal_status_log
            (id, signal_id, old_status, new_status, changed_at, changed_by, note)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [log_id, signal_id, old_status, new_status, now, changed_by, note],
    )


def get_signal_status_history(conn, signal_id: str) -> list[dict]:
    """Return chronological status history for a signal."""
    rows = conn.execute(
        "SELECT old_status, new_status, changed_at, changed_by, note "
        "FROM signal_status_log WHERE signal_id = ? ORDER BY changed_at",
        [signal_id],
    ).fetchall()
    return [
        {
            "old_status": r[0], "new_status": r[1],
            "changed_at": r[2], "changed_by": r[3], "note": r[4] or "",
        }
        for r in rows
    ]


# ------------------------------------------------------------------
# People tab (#14)
# ------------------------------------------------------------------


def get_people_rows(conn, as_of: date | None = None, settings=None) -> list[DashboardPeopleRow]:
    """Return one row per tracked person, canonicalized and enriched with signal/note data.

    People with ``track: false`` in people.yaml are excluded from the result.
    Aliases are canonicalized before display.
    """
    if as_of is None:
        as_of = date.today()

    # Build normalizer for alias resolution and track filtering
    normalizer = _get_people_normalizer(settings)

    def _canon(name: str) -> str:
        return normalizer.canonicalize(name) if normalizer else (name or "")

    def _is_tracked(name: str) -> bool:
        return normalizer.is_tracked(name) if normalizer else True

    # All known people from the people table; also collect names from notes
    people_rows = conn.execute(
        "SELECT name, role, current_client, allocation_pct, next_availability_date, "
        "last_1on1_date, morale_signal, growth_topic, blockers FROM people"
    ).fetchall()

    # Build a map of canonical_name → row for people in the DB table
    people_map: dict[str, dict] = {}
    for r in people_rows:
        canon = _canon(r[0])
        if not _is_tracked(canon):
            continue
        if canon not in people_map:
            people_map[canon] = {
                "name": canon, "role": r[1] or "", "current_client": r[2] or "",
                "allocation_pct": r[3] or 0.0, "next_availability_date": r[4],
                "last_1on1_date": r[5], "morale_signal": r[6] or "green",
                "growth_topic": r[7] or "", "blockers": r[8] or "",
            }

    # Pull all unique person names from notes (1on1 notes), canonicalize
    note_people = conn.execute(
        "SELECT DISTINCT entity_name FROM notes WHERE note_type = '1on1' AND entity_name != ''"
    ).fetchall()
    for (name,) in note_people:
        if not name:
            continue
        canon = _canon(name)
        if not _is_tracked(canon):
            continue
        if canon not in people_map:
            people_map[canon] = {
                "name": canon, "role": "", "current_client": "", "allocation_pct": 0.0,
                "next_availability_date": None, "last_1on1_date": None,
                "morale_signal": "green", "growth_topic": "", "blockers": "",
            }

    # Also pull people from signals, canonicalize
    sig_people = conn.execute(
        "SELECT DISTINCT entity_name FROM signals WHERE entity_type = 'person' AND entity_name != ''"
    ).fetchall()
    for (name,) in sig_people:
        if not name:
            continue
        canon = _canon(name)
        if not _is_tracked(canon):
            continue
        if canon not in people_map:
            people_map[canon] = {
                "name": canon, "role": "", "current_client": "", "allocation_pct": 0.0,
                "next_availability_date": None, "last_1on1_date": None,
                "morale_signal": "green", "growth_topic": "", "blockers": "",
            }

    # Last 1:1 date per person from notes — canonicalize names for grouping
    last_1on1_raw = conn.execute(
        "SELECT entity_name, note_date FROM notes WHERE note_type = '1on1' ORDER BY note_date DESC"
    ).fetchall()
    last_1on1_map: dict[str, Any] = {}
    for r in last_1on1_raw:
        if not r[0]:
            continue
        canon = _canon(r[0])
        if canon not in last_1on1_map:  # keep most recent
            last_1on1_map[canon] = r[1]

    # Open signals per person — canonicalize names
    sig_rows_raw = conn.execute(
        "SELECT entity_name, severity FROM signals WHERE entity_type = 'person' AND status = 'open'"
    ).fetchall()
    _rank_to_sev = {0: "critical", 1: "high", 2: "medium", 3: "low"}
    _sev_to_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sig_map_counts: dict[str, int] = {}
    sig_map_min_rank: dict[str, int] = {}
    for r in sig_rows_raw:
        if not r[0]:
            continue
        canon = _canon(r[0])
        sig_map_counts[canon] = sig_map_counts.get(canon, 0) + 1
        rank = _sev_to_rank.get(r[1], 4)
        sig_map_min_rank[canon] = min(sig_map_min_rank.get(canon, 99), rank)
    sig_map: dict[str, tuple[int, str | None]] = {
        name: (sig_map_counts[name], _rank_to_sev.get(sig_map_min_rank[name]))
        for name in sig_map_counts
    }

    # Current-week allocation per person.
    # Use the NEAREST forecast week on or after as_of (not a sum across weeks).
    # Reuse the canonical allocation helper so People tab and Forecast tab agree.
    nearest_week = _nearest_forecast_week(conn, as_of)

    fc_map: dict[str, float] = {}
    if nearest_week:
        for entry in get_people_allocation_for_week(conn, nearest_week, settings=settings):
            fc_map[entry["person_name"]] = entry["allocation_pct"]

    result = []
    for name, p in sorted(people_map.items()):
        last_1on1_raw = last_1on1_map.get(name) or p["last_1on1_date"]
        if last_1on1_raw:
            try:
                last_1on1_date = last_1on1_raw if isinstance(last_1on1_raw, date) else date.fromisoformat(str(last_1on1_raw))
                days_since = (as_of - last_1on1_date).days
            except Exception:
                last_1on1_date = None
                days_since = None
        else:
            last_1on1_date = None
            days_since = None

        open_count, highest_sev = sig_map.get(name, (0, None))
        alloc = fc_map.get(name, p["allocation_pct"] or 0.0)

        morale = p["morale_signal"] or "green"
        if highest_sev == "critical":
            morale = "red"
        elif highest_sev == "high" and morale == "green":
            morale = "yellow"

        result.append(DashboardPeopleRow(
            name=name,
            role=p["role"],
            current_client=p["current_client"],
            allocation_pct=alloc,
            next_availability_date=p["next_availability_date"],
            last_1on1_date=last_1on1_date,
            days_since_1on1=days_since,
            morale=morale,  # type: ignore[arg-type]
            blockers=p["blockers"],
            open_signal_count=open_count,
            highest_severity=highest_sev,  # type: ignore[arg-type]
            growth_topic=p["growth_topic"],
        ))
    return result


def get_signals_for_person(conn, person_name: str) -> list[Signal]:
    """Return all open signals for a specific person."""
    return [
        s for s in get_today_signals(conn, min_severity="low")
        if s.entity_name == person_name and s.entity_type == "person"
    ]


def get_forecast_week_list(conn, as_of: date | None = None, limit: int = 12) -> list[date]:
    """Return the list of distinct forecast weeks on or after *as_of*, up to *limit*."""
    if as_of is None:
        as_of = date.today()
    rows = conn.execute(
        """
        SELECT DISTINCT week_start
        FROM staffing_forecast
        WHERE week_start >= ?
        ORDER BY week_start
        LIMIT ?
        """,
        [as_of, limit],
    ).fetchall()
    result = []
    for (ws,) in rows:
        try:
            result.append(ws if isinstance(ws, date) else date.fromisoformat(str(ws)))
        except Exception:
            pass
    return result


def _get_people_normalizer(settings=None):
    """Build a PeopleNormalizer from config, or return None on failure."""
    try:
        from manager_os.build.people_normalization import PeopleNormalizer
        return PeopleNormalizer.from_config(settings)
    except Exception:
        return None


def _nearest_forecast_week(conn, as_of: date) -> date | None:
    """Return the earliest forecast week on or after *as_of*, or None."""
    row = conn.execute(
        "SELECT MIN(week_start) FROM staffing_forecast WHERE week_start >= ?",
        [as_of],
    ).fetchone()
    if not row or not row[0]:
        return None
    ws = row[0]
    return ws if isinstance(ws, date) else date.fromisoformat(str(ws))


def get_people_allocation_for_week(
    conn,
    week_start: date,
    settings=None,
) -> list[dict]:
    """Return per-person allocation detail for a single forecast week.

    Canonical allocation math (single source of truth for People + Forecast tabs):

        allocation_pct = total_planned_hours / max(target_hours) * 100

    Rules:
      * Person names are canonicalized via PeopleNormalizer.from_config(settings).
      * planned_hours are SUMmed across project rows for the same canonical person.
      * target_hours use MAX per canonical person/week (never summed — capacity rows
        are often duplicated per project).
      * When planned_hours AND target_hours are present, the stored allocation_pct
        is ignored.
      * Legacy rows with no planned/target hours fall back to SUM(allocation_pct).
      * Hours and percentages are never mixed in one SUM.

    Each entry has:
        person_name   str           (canonical name)
        planned_hours float         (0.0 if not set)
        target_hours  float | None
        allocation_pct float        (planned/target*100, or legacy fallback)
        projects      list[str]     ("Client / Project" entries)
        warning       str | None
        raw_names     list[str]     (distinct raw names that mapped to this person)
    """
    normalizer = _get_people_normalizer(settings)

    def _canon(name: str) -> str:
        return normalizer.canonicalize(name) if normalizer else (name or "")

    # Pull raw rows for the week. We aggregate in Python so we can canonicalize
    # names and keep hours/percentages separate.
    rows = conn.execute(
        """
        SELECT person_name, client, project,
               allocation_pct, planned_hours, target_hours
        FROM staffing_forecast
        WHERE week_start = ?
          AND forecast_type IN ('capacity', 'confirmed', 'likely')
        """,
        [week_start],
    ).fetchall()

    # Per canonical person accumulators
    agg: dict[str, dict] = {}
    for person_name, client, project, alloc_pct, planned_h, target_h in rows:
        if not person_name:
            continue
        canon = _canon(person_name)
        bucket = agg.setdefault(canon, {
            "planned_hours_sum": 0.0,
            "has_planned": False,
            "target_hours_max": None,
            "has_target": False,
            "alloc_pct_sum": 0.0,
            "has_alloc_pct": False,
            "projects": [],
            "raw_names": set(),
        })
        # planned_hours
        if planned_h is not None:
            bucket["planned_hours_sum"] += float(planned_h)
            bucket["has_planned"] = True
        # target_hours — MAX, never sum (capacity rows duplicate target per project)
        if target_h is not None:
            th = float(target_h)
            if not bucket["has_target"] or th > bucket["target_hours_max"]:
                bucket["target_hours_max"] = th
            bucket["has_target"] = True
        # stored allocation_pct — only used as legacy fallback
        if alloc_pct is not None:
            bucket["alloc_pct_sum"] += float(alloc_pct)
            bucket["has_alloc_pct"] = True
        # project label
        client_s = (client or "").strip()
        project_s = (project or "").strip()
        if project_s and client_s:
            label = f"{client_s} / {project_s}"
        elif project_s:
            label = project_s
        elif client_s:
            label = client_s
        else:
            label = ""
        if label and label not in bucket["projects"]:
            bucket["projects"].append(label)
        # raw name
        if person_name and person_name != canon:
            bucket["raw_names"].add(person_name)
        bucket["raw_names"].add(person_name)

    result = []
    for canon, b in sorted(agg.items()):
        planned = b["planned_hours_sum"] if b["has_planned"] else 0.0
        target_val = b["target_hours_max"] if b["has_target"] else None

        # Canonical math: prefer planned/target ratio when both present.
        if b["has_planned"] and b["has_target"] and target_val and target_val > 0:
            alloc_pct = (planned / target_val) * 100.0
        elif b["has_alloc_pct"]:
            # Legacy fallback: rows with no planned/target hours.
            alloc_pct = b["alloc_pct_sum"]
        else:
            alloc_pct = 0.0

        warning = None
        if target_val is None or target_val == 0:
            warning = "no capacity target"
        elif alloc_pct > 150:
            warning = f"{alloc_pct:.0f}% — dangerously overallocated"
        elif alloc_pct > 100:
            warning = f"{alloc_pct:.0f}% — overallocated"
        elif alloc_pct < 50:
            warning = f"{alloc_pct:.0f}% — underallocated"

        result.append({
            "person_name":    canon,
            "planned_hours":  round(planned, 2),
            "target_hours":   round(target_val, 2) if target_val is not None else None,
            "allocation_pct": round(alloc_pct, 1),
            "projects":       b["projects"],
            "warning":        warning,
            "raw_names":      sorted(n for n in b["raw_names"] if n),
        })
    return result


# ------------------------------------------------------------------
# Clients tab (#15)
# ------------------------------------------------------------------


def get_client_rows(conn, as_of: date | None = None) -> list[dict]:
    """Return one row per client, derived from signals and notes."""
    if as_of is None:
        as_of = date.today()

    # Collect all known client names
    client_names: set[str] = set()
    for (name,) in conn.execute(
        "SELECT DISTINCT entity_name FROM signals WHERE entity_type = 'client' AND entity_name != ''"
    ).fetchall():
        client_names.add(name)
    for (name,) in conn.execute(
        "SELECT DISTINCT entity_name FROM notes WHERE entity_type = 'client' AND entity_name != ''"
    ).fetchall():
        client_names.add(name)
    for row in conn.execute(
        "SELECT DISTINCT name FROM clients WHERE name != ''"
    ).fetchall():
        client_names.add(row[0])

    # Signals per client
    sig_rows = conn.execute(
        "SELECT entity_name, COUNT(*), signal_type, "
        "MIN(CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END) "
        "FROM signals WHERE entity_type = 'client' AND status = 'open' GROUP BY entity_name, signal_type"
    ).fetchall()
    client_sig_counts: dict[str, int] = {}
    client_min_rank: dict[str, int] = {}
    client_risk_count: dict[str, int] = {}
    for r in sig_rows:
        name = r[0]
        client_sig_counts[name] = client_sig_counts.get(name, 0) + r[1]
        client_min_rank[name] = min(client_min_rank.get(name, 99), r[3])
        if r[2] in ("risk", "blocker"):
            client_risk_count[name] = client_risk_count.get(name, 0) + r[1]

    # Last update date per client
    last_update = conn.execute(
        "SELECT entity_name, MAX(note_date) FROM notes WHERE entity_type = 'client' GROUP BY entity_name"
    ).fetchall()
    last_update_map = {r[0]: r[1] for r in last_update if r[0]}

    _rank_to_sev = {0: "critical", 1: "high", 2: "medium", 3: "low"}

    # Active deals per client (account) — for opportunity number display
    deal_opp_rows = conn.execute(
        "SELECT account, deal_id, deal_name, stage, close_date "
        "FROM deals WHERE deal_name != '' ORDER BY account, close_date NULLS LAST"
    ).fetchall()
    client_deals: dict[str, list[dict]] = {}
    for r in deal_opp_rows:
        acct = r[0] or ""
        if acct and acct in client_names:
            if acct not in client_deals:
                client_deals[acct] = []
            client_deals[acct].append({
                "deal_id": r[1] or "",
                "deal_name": r[2] or "",
                "stage": r[3] or "",
                "close_date": r[4],
            })

    result = []
    for name in sorted(client_names):
        rank = client_min_rank.get(name, 99)
        health = "red" if rank == 0 else ("yellow" if rank == 1 else "green")

        last_raw = last_update_map.get(name)
        last_date = None
        if last_raw:
            try:
                last_date = last_raw if isinstance(last_raw, date) else date.fromisoformat(str(last_raw))
            except Exception:
                pass

        result.append({
            "name": name,
            "health": health,
            "open_signal_count": client_sig_counts.get(name, 0),
            "open_risk_count": client_risk_count.get(name, 0),
            "last_update_date": last_date,
            "highest_severity": _rank_to_sev.get(rank),
            "deals": client_deals.get(name, []),
        })

    # Sort: red first, then yellow, then green
    _health_order = {"red": 0, "yellow": 1, "green": 2}
    result.sort(key=lambda r: (_health_order.get(r["health"], 3), r["name"]))
    return result


def get_signals_for_client(conn, client_name: str) -> list[Signal]:
    """Return all open signals for a specific client."""
    return [
        s for s in get_today_signals(conn, min_severity="low")
        if s.entity_name == client_name and s.entity_type == "client"
    ]


# ------------------------------------------------------------------
# Deals tab (#16)
# ------------------------------------------------------------------


def get_deal_documents(conn, deal_id: str) -> dict[str, dict]:
    """Return SOW and Deal Sheet metadata for a deal from deal_documents table.

    Returns a dict keyed by document_type ('int_sow', 'deal_sheet'), each containing
    title, url, search_status.  Missing document types return empty dicts.
    """
    try:
        rows = conn.execute(
            """
            SELECT document_type, title, url, search_status
            FROM deal_documents
            WHERE deal_id = ?
              AND search_status = 'found'
            ORDER BY retrieved_at DESC
            """,
            [deal_id],
        ).fetchall()
    except Exception:
        return {}

    docs: dict[str, dict] = {}
    for doc_type, title, url, status in rows:
        if doc_type not in docs:  # keep most recent per type
            docs[doc_type] = {"title": title or "", "url": url or "", "status": status or ""}
    return docs


def get_deal_rows(conn, as_of: date | None = None) -> list[DashboardDealRow]:
    """Return all deals enriched with signal counts, document links, and feasibility provenance."""
    if as_of is None:
        as_of = date.today()

    rows = conn.execute(
        "SELECT id, account, deal_name, stage, close_date, technical_owner, "
        "ae_name, loe_status, sow_status, staffing_feasibility, blockers, next_action, "
        "COALESCE(deal_id, '') "
        "FROM deals ORDER BY close_date NULLS LAST"
    ).fetchall()

    # Signal counts per deal
    sig_rows = conn.execute(
        "SELECT entity_name, COUNT(*), "
        "MIN(CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END) "
        "FROM signals WHERE entity_type = 'deal' AND status = 'open' GROUP BY entity_name"
    ).fetchall()
    _rank_to_sev = {0: "critical", 1: "high", 2: "medium", 3: "low"}
    sig_map = {r[0]: (r[1], _rank_to_sev.get(r[2])) for r in sig_rows}

    result = []
    for row in rows:
        db_id, account, deal_name, stage, close_date, tech_owner, ae, loe, sow, feasibility, blockers, next_action, deal_id_val = row
        if close_date:
            try:
                cd = close_date if isinstance(close_date, date) else date.fromisoformat(str(close_date))
                days_to_close = (cd - as_of).days
            except Exception:
                cd = None
                days_to_close = None
        else:
            cd = None
            days_to_close = None

        open_count, highest_sev = sig_map.get(deal_name, (0, None))

        # Determine staffing feasibility provenance
        if feasibility:
            feasibility_source = "deals_csv"
        else:
            feasibility_source = "unknown"
            feasibility = "feasible"

        # Look up document links
        effective_deal_id = deal_id_val or db_id
        docs = get_deal_documents(conn, effective_deal_id)
        sow_doc = docs.get("int_sow", {})
        ds_doc = docs.get("deal_sheet", {})

        result.append(DashboardDealRow(
            account=account,
            deal_name=deal_name,
            deal_id=effective_deal_id,
            stage=stage or "",
            close_date=cd,
            days_to_close=days_to_close,
            technical_owner=tech_owner or "",
            ae_name=ae or "",
            loe_status=loe or "",
            sow_status=sow or "",
            staffing_feasibility=feasibility,  # type: ignore[arg-type]
            staffing_feasibility_source=feasibility_source,
            blockers=blockers or "",
            next_action=next_action or "",
            open_signal_count=open_count,
            highest_severity=highest_sev,  # type: ignore[arg-type]
            sow_title=sow_doc.get("title", ""),
            sow_url=sow_doc.get("url", ""),
            deal_sheet_title=ds_doc.get("title", ""),
            deal_sheet_url=ds_doc.get("url", ""),
        ))
    return result


# ------------------------------------------------------------------
# Forecast tab (#17)
# ------------------------------------------------------------------


def get_forecast_rows(conn, as_of: date | None = None) -> list[DashboardForecastRow]:
    """Return all forecast rows for the next 60 days."""
    if as_of is None:
        as_of = date.today()

    from datetime import timedelta
    horizon = as_of + timedelta(days=60)

    rows = conn.execute(
        """
        SELECT person_name, week_start, client, project,
               SUM(allocation_pct)              AS alloc_pct,
               forecast_type,
               SUM(COALESCE(planned_hours, 0))  AS planned,
               MAX(target_hours)                AS target
        FROM staffing_forecast
        WHERE week_start >= ? AND week_start <= ?
          AND forecast_type IN ('capacity', 'confirmed', 'likely')
        GROUP BY person_name, week_start, client, project, forecast_type
        ORDER BY week_start, person_name
        """,
        [as_of, horizon],
    ).fetchall()

    result = []
    for row in rows:
        person_name, week_start, client, project, alloc, fc_type, planned, target = row
        try:
            ws = week_start if isinstance(week_start, date) else date.fromisoformat(str(week_start))
        except Exception:
            continue
        planned_h = float(planned or 0.0)
        target_h  = float(target) if target is not None else None
        # Use real percentage when target is available (wide-format rows)
        if target_h and target_h > 0:
            alloc_pct = (planned_h / target_h) * 100.0
        else:
            alloc_pct = float(alloc or 0)
        result.append(DashboardForecastRow(
            person_name=person_name,
            week_start=ws,
            client=client or "",
            project=project or "",
            allocation_pct=round(alloc_pct, 1),
            forecast_type=fc_type or "confirmed",  # type: ignore[arg-type]
            is_overallocated=alloc_pct > 100.01,
            is_underallocated=alloc_pct < 99.99,
        ))
    return result


def get_forecast_summary(conn, as_of: date | None = None) -> dict:
    """Return grouped forecast stats for the 3 time buckets.

    Classification is per person-week, then summarized by person:
    - overallocated:    person has ANY week > 100.01%
    - fully_utilized:   all of person's weeks are within [99.99, 100.01]
    - available:        person has ANY week < 99.99% and NO overallocated week

    Window labels include explicit date ranges, e.g. "2w (2026-06-16 → 2026-06-30)".
    """
    from datetime import timedelta
    if as_of is None:
        as_of = date.today()

    all_rows = get_forecast_rows(conn, as_of=as_of)
    raw_buckets = {
        "2w": as_of + timedelta(days=14),
        "30d": as_of + timedelta(days=30),
        "60d": as_of + timedelta(days=60),
    }

    summary: dict[str, dict] = {}
    for short_label, end_date in raw_buckets.items():
        # Build display label with real date range
        label = f"{short_label} ({as_of.isoformat()} → {end_date.isoformat()})"

        in_window = [r for r in all_rows if as_of <= r.week_start <= end_date]

        # Per-person-week classification; aggregate into per-person status sets
        person_statuses: dict[str, set[str]] = {}
        for r in in_window:
            name = r.person_name
            if name not in person_statuses:
                person_statuses[name] = set()
            if r.is_overallocated:           # > 100.01
                person_statuses[name].add("over")
            elif not r.is_underallocated:    # 99.99 <= x <= 100.01
                person_statuses[name].add("ok")
            else:                            # < 99.99
                person_statuses[name].add("under")

        overallocated = sorted(p for p, s in person_statuses.items() if "over" in s)
        # Fully utilized: no over, no under weeks (all "ok")
        fully_utilized = sorted(
            p for p, s in person_statuses.items()
            if "over" not in s and "under" not in s
        )
        # Available: no over, but has at least one under week
        available = sorted(
            p for p, s in person_statuses.items()
            if "over" not in s and "under" in s
        )

        # Roll-offs: last confirmed week for any person within window
        rolloffs = conn.execute(
            "SELECT person_name, MAX(week_start) as last_week FROM staffing_forecast "
            "WHERE forecast_type = 'confirmed' AND week_start BETWEEN ? AND ? "
            "GROUP BY person_name",
            [as_of, end_date],
        ).fetchall()

        bucket_data = {
            "overallocated": overallocated,
            "fully_utilized": fully_utilized,
            "available": available,
            "rolloffs": [(r[0], r[1]) for r in rolloffs],
            # Legacy key (backward compat): "underallocated" = same as "available"
            "underallocated": available,
            "start_date": as_of.isoformat(),
            "end_date": end_date.isoformat(),
        }
        # Full label (with dates) is the primary key
        summary[label] = bucket_data
        # Short-key alias for backward compat (e.g. summary["2w"])
        summary[short_label] = bucket_data
    return summary


# ------------------------------------------------------------------
# Metrics helpers
# ------------------------------------------------------------------


def get_signal_counts(conn, statuses: list[str] | None = None) -> dict[str, int]:
    """Return counts by severity for signals in the given statuses."""
    if statuses is None:
        statuses = ["open"]
    status_placeholders = ", ".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT severity, COUNT(*) FROM signals WHERE status IN ({status_placeholders}) GROUP BY severity",
        statuses,
    ).fetchall()
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for row in rows:
        counts[row[0]] = row[1]
    return counts


def get_deals_list(conn) -> list[DashboardDealRow]:
    """Return a list of deals for the dashboard."""
    rows = conn.execute(
        """
        SELECT id, account, deal_name, stage, close_date, technical_owner,
               ae_name, requested_roles, loe_status, sow_status,
               staffing_feasibility, blockers, next_action, updated_at,
               deal_id, next_steps, delivery_comment, forecast_category,
               probability, services_amount, last_status_changed_date, source_format
        FROM deals
        ORDER BY close_date DESC
        """
    ).fetchall()
    
    deals = []
    for row in rows:
        try:
            deal = DashboardDealRow(
                id=row[0] or "",
                account=row[1] or "",
                deal_name=row[2] or "",
                stage=row[3] or "",
                close_date=row[4],
                technical_owner=row[5] or "",
                ae_name=row[6] or "",
                requested_roles=json.loads(row[7]) if row[7] else [],
                loe_status=row[8] or "",
                sow_status=row[9] or "",
                staffing_feasibility=row[10] or "feasible",
                blockers=row[11] or "",
                next_action=row[12] or "",
                updated_at=row[13],
                deal_id=row[14] or "",
                next_steps=row[15] or "",
                delivery_comment=row[16] or "",
                forecast_category=row[17] or "",
                probability=float(row[18]) if row[18] else 0.0,
                services_amount=float(row[19]) if row[19] else 0.0,
                last_status_changed_date=row[20],
                source_format=row[21] or "Deals CSV",
            )
            deals.append(deal)
        except Exception as exc:
            logger.warning("Skipping malformed deal: %s", exc)
    return deals


def get_clients_list(conn) -> list:
    """Return a list of clients for the dashboard."""
    rows = conn.execute(
        """
        SELECT id, name, aliases, health, current_team, last_update_date,
               open_risks, client_sentiment, next_milestone, unresolved_decisions, updated_at
        FROM clients
        ORDER BY name
        """
    ).fetchall()
    
    clients = []
    for row in rows:
        try:
            client = {
                "id": row[0],
                "name": row[1],
                "aliases": json.loads(row[2]) if row[2] else [],
                "health": row[3],
                "current_team": json.loads(row[4]) if row[4] else [],
                "last_update_date": row[5],
                "open_risks": json.loads(row[6]) if row[6] else [],
                "client_sentiment": row[7],
                "next_milestone": row[8],
                "unresolved_decisions": json.loads(row[9]) if row[9] else [],
                "updated_at": row[10],
            }
            clients.append(client)
        except Exception as exc:
            logger.warning("Skipping malformed client: %s", exc)
    return clients
