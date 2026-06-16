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
    Signal,
)

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


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
) -> list[Signal]:
    """Return open signals, filtered by severity threshold."""
    if target_date is None:
        target_date = date.today()
    if statuses is None:
        statuses = ["open"]

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
    rows = conn.execute(
        """
        SELECT id, meeting_date, start_time, title, attendees,
               linked_entities, source, external_id, updated_at
        FROM meetings WHERE meeting_date = ?
        ORDER BY start_time NULLS LAST
        """,
        [target_date],
    ).fetchall()
    results = []
    for r in rows:
        attendees_raw = r[4]
        attendees = json.loads(attendees_raw) if isinstance(attendees_raw, str) else (attendees_raw or [])
        linked_raw = r[5]
        linked = json.loads(linked_raw) if isinstance(linked_raw, str) else (linked_raw or [])
        results.append({
            "id": r[0],
            "meeting_date": r[1],
            "start_time": r[2] or "",
            "title": r[3] or "",
            "attendees": attendees,
            "linked_entities": linked,
            "source": r[6] or "",
            "external_id": r[7] or "",
            "updated_at": r[8],
        })
    return results


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


def get_people_rows(conn, as_of: date | None = None) -> list[DashboardPeopleRow]:
    """Return one row per person from config, enriched with signal/note data."""
    if as_of is None:
        as_of = date.today()

    # All known people from the people table; also collect names from notes
    people_rows = conn.execute(
        "SELECT name, role, current_client, allocation_pct, next_availability_date, "
        "last_1on1_date, morale_signal, growth_topic, blockers FROM people"
    ).fetchall()

    # Build a map of name → row for people in the DB table
    people_map: dict[str, dict] = {}
    for r in people_rows:
        people_map[r[0]] = {
            "name": r[0], "role": r[1] or "", "current_client": r[2] or "",
            "allocation_pct": r[3] or 0.0, "next_availability_date": r[4],
            "last_1on1_date": r[5], "morale_signal": r[6] or "green",
            "growth_topic": r[7] or "", "blockers": r[8] or "",
        }

    # Pull all unique person names from notes (1on1 notes)
    note_people = conn.execute(
        "SELECT DISTINCT entity_name FROM notes WHERE note_type = '1on1' AND entity_name != ''"
    ).fetchall()
    for (name,) in note_people:
        if name and name not in people_map:
            people_map[name] = {
                "name": name, "role": "", "current_client": "", "allocation_pct": 0.0,
                "next_availability_date": None, "last_1on1_date": None,
                "morale_signal": "green", "growth_topic": "", "blockers": "",
            }

    # Also pull people from signals
    sig_people = conn.execute(
        "SELECT DISTINCT entity_name FROM signals WHERE entity_type = 'person' AND entity_name != ''"
    ).fetchall()
    for (name,) in sig_people:
        if name and name not in people_map:
            people_map[name] = {
                "name": name, "role": "", "current_client": "", "allocation_pct": 0.0,
                "next_availability_date": None, "last_1on1_date": None,
                "morale_signal": "green", "growth_topic": "", "blockers": "",
            }

    # Last 1:1 date per person from notes
    last_1on1 = conn.execute(
        "SELECT entity_name, MAX(note_date) FROM notes WHERE note_type = '1on1' GROUP BY entity_name"
    ).fetchall()
    last_1on1_map = {r[0]: r[1] for r in last_1on1 if r[0]}

    # Open signals per person
    sig_rows = conn.execute(
        "SELECT entity_name, COUNT(*), "
        "MIN(CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END) "
        "FROM signals WHERE entity_type = 'person' AND status = 'open' GROUP BY entity_name"
    ).fetchall()
    sig_map: dict[str, tuple[int, str | None]] = {}
    _rank_to_sev = {0: "critical", 1: "high", 2: "medium", 3: "low"}
    for r in sig_rows:
        sig_map[r[0]] = (r[1], _rank_to_sev.get(r[2]))

    # Current-week allocation per person.
    # Use the NEAREST forecast week on or after as_of (not a sum across weeks).
    # For wide-format rows (forecast_type='capacity'), allocation_pct is already
    # planned_hours / target_hours * 100.  For normalized rows it's already a
    # percentage.  Either way we want the single-week value for the current week.
    nearest_week_row = conn.execute(
        """
        SELECT MIN(week_start)
        FROM staffing_forecast
        WHERE week_start >= ?
        """,
        [as_of],
    ).fetchone()
    nearest_week = nearest_week_row[0] if nearest_week_row and nearest_week_row[0] else None

    fc_map: dict[str, float] = {}
    if nearest_week:
        fc_rows = conn.execute(
            """
            SELECT person_name, SUM(allocation_pct)
            FROM staffing_forecast
            WHERE week_start = ?
            GROUP BY person_name
            """,
            [nearest_week],
        ).fetchall()
        fc_map = {r[0]: float(r[1] or 0.0) for r in fc_rows if r[0]}

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


def get_people_allocation_for_week(
    conn,
    week_start: date,
) -> list[dict]:
    """Return per-person allocation detail for a single forecast week.

    Each entry has:
        person_name   str
        planned_hours float   (0.0 if not set)
        target_hours  float | None
        allocation_pct float  (planned/target*100, or 0 when no target)
        projects      list[str]
        warning       str | None   (set when allocation_pct > 150% or target missing)
    """
    rows = conn.execute(
        """
        SELECT person_name,
               SUM(COALESCE(planned_hours, allocation_pct)) AS planned,
               MAX(target_hours)                            AS target,
               SUM(allocation_pct)                         AS alloc_pct_sum,
               STRING_AGG(DISTINCT project, ', ')          AS projects
        FROM staffing_forecast
        WHERE week_start = ?
          AND forecast_type IN ('capacity', 'confirmed', 'likely')
        GROUP BY person_name
        ORDER BY person_name
        """,
        [week_start],
    ).fetchall()

    result = []
    for person_name, planned, target, alloc_pct_sum, projects in rows:
        planned = float(planned or 0.0)
        target_val = float(target) if target is not None else None

        # Prefer explicit planned_hours / target_hours ratio;
        # fall back to stored allocation_pct sum for normalized rows.
        if target_val is not None and target_val > 0:
            alloc_pct = (planned / target_val) * 100.0
        elif alloc_pct_sum is not None:
            alloc_pct = float(alloc_pct_sum)
        else:
            alloc_pct = 0.0

        warning = None
        if target_val is None or target_val == 0:
            warning = "no capacity target"
        elif alloc_pct > 150:
            warning = f"{alloc_pct:.0f}% — dangerously overallocated"
        elif alloc_pct > 100:
            warning = f"{alloc_pct:.0f}% — overallocated"

        result.append({
            "person_name":    person_name,
            "planned_hours":  planned,
            "target_hours":   target_val,
            "allocation_pct": round(alloc_pct, 1),
            "projects":       [p.strip() for p in (projects or "").split(",") if p.strip()],
            "warning":        warning,
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


def get_deal_rows(conn, as_of: date | None = None) -> list[DashboardDealRow]:
    """Return all deals enriched with signal counts."""
    if as_of is None:
        as_of = date.today()

    rows = conn.execute(
        "SELECT id, account, deal_name, stage, close_date, technical_owner, "
        "ae_name, loe_status, sow_status, staffing_feasibility, blockers, next_action "
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
        _, account, deal_name, stage, close_date, tech_owner, ae, loe, sow, feasibility, blockers, next_action = row
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

        result.append(DashboardDealRow(
            account=account,
            deal_name=deal_name,
            stage=stage or "",
            close_date=cd,
            days_to_close=days_to_close,
            technical_owner=tech_owner or "",
            ae_name=ae or "",
            loe_status=loe or "",
            sow_status=sow or "",
            staffing_feasibility=feasibility or "feasible",  # type: ignore[arg-type]
            blockers=blockers or "",
            next_action=next_action or "",
            open_signal_count=open_count,
            highest_severity=highest_sev,  # type: ignore[arg-type]
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
    - overallocated: person has any week > 100.01%
    - underallocated: person has any week < 99.99%
    - available: person has all weeks between 99.99% and 100.01%
    """
    from datetime import timedelta
    if as_of is None:
        as_of = date.today()

    all_rows = get_forecast_rows(conn, as_of=as_of)
    buckets = {
        "2w": as_of + timedelta(days=14),
        "30d": as_of + timedelta(days=30),
        "60d": as_of + timedelta(days=60),
    }

    summary: dict[str, dict] = {}
    for label, end_date in buckets.items():
        in_window = [r for r in all_rows if as_of <= r.week_start <= end_date]

        # Per-person-week classification; aggregate into per-person status sets
        person_statuses: dict[str, set[str]] = {}
        for r in in_window:
            name = r.person_name
            if name not in person_statuses:
                person_statuses[name] = set()
            if r.is_overallocated:
                person_statuses[name].add("over")
            elif r.is_underallocated:
                person_statuses[name].add("under")
            else:
                person_statuses[name].add("ok")

        overallocated = sorted(p for p, s in person_statuses.items() if "over" in s)
        underallocated = sorted(p for p, s in person_statuses.items() if "under" in s)
        available = sorted(
            p for p, s in person_statuses.items()
            if "over" not in s and "under" not in s
        )

        # Roll-offs: last confirmed week for any person within window
        rolloffs = conn.execute(
            "SELECT person_name, MAX(week_start) as last_week FROM staffing_forecast "
            "WHERE forecast_type = 'confirmed' AND week_start BETWEEN ? AND ? "
            "GROUP BY person_name",
            [as_of, end_date],
        ).fetchall()

        summary[label] = {
            "overallocated": overallocated,
            "underallocated": underallocated,
            "available": available,
            "rolloffs": [(r[0], r[1]) for r in rolloffs],
        }
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
