"""Forecast dashboard query functions.

Provides enriched forecast data for the Forecast API and React view.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from manager_os.build.dashboard_data import (
    get_forecast_rows,
    get_forecast_week_list,
    get_people_allocation_for_week,
)


def get_forecast_data(
    conn,
    *,
    week_start: str | None = None,
    person: str | None = None,
    client: str | None = None,
    exceptions_only: bool = False,
    limit: int = 200,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return forecast data with classification.

    Args:
        conn: Open DuckDB connection.
        week_start: Optional week to query (ISO date). Default selects best week.
        person: Optional person name filter.
        client: Optional client name filter.
        exceptions_only: Only return exception rows.
        limit: Max results (default 200).
        as_of: Reference date (default today).

    Returns:
        Dict with keys: selected_week, selection_explanation, available_weeks,
        people, row_count, person_count, exception_count, status_counts,
        freshness, last_ingestion, warnings.
    """
    if as_of is None:
        as_of = date.today()

    warnings: list[str] = []
    available_weeks = get_forecast_week_list(conn, as_of=as_of, limit=52)

    # Determine selected week
    if week_start:
        try:
            selected = date.fromisoformat(week_start)
        except ValueError:
            warnings.append(f"Invalid week_start: {week_start}")
            selected = _best_week(available_weeks, as_of)
    else:
        selected = _best_week(available_weeks, as_of)

    if selected is None:
        return {
            "selected_week": None,
            "selection_explanation": "No forecast data available",
            "available_weeks": [d.isoformat() for d in available_weeks],
            "people": [],
            "row_count": 0,
            "person_count": 0,
            "exception_count": 0,
            "status_counts": {"overallocated": 0, "underutilized": 0, "available": 0, "unknown": 0},
            "freshness": "missing",
            "last_ingestion": None,
            "warnings": warnings + ["No forecast data found"],
        }

    selection_explanation = _week_explanation(selected, available_weeks, as_of)

    # Get allocation for the week
    try:
        allocations = get_people_allocation_for_week(conn, selected)
    except Exception as exc:
        warnings.append(f"allocation: {exc}")
        allocations = []

    # Get forecast rows for detail
    try:
        forecast_rows = get_forecast_rows(conn, as_of=selected)
        detail_rows = [r.model_dump(mode="json") for r in forecast_rows]
    except Exception as exc:
        warnings.append(f"forecast_rows: {exc}")
        detail_rows = []

    # Build people list with classification
    people = []
    for alloc in allocations:
        p = dict(alloc)
        p["classification"] = _classify_allocation(alloc, selected, conn)
        p["roll_off"] = _detect_roll_off(conn, alloc["person_name"], selected)
        people.append(p)

    # Apply filters
    if person:
        person_lower = person.lower()
        people = [p for p in people if person_lower in (p.get("person_name") or "").lower()]

    if client:
        client_lower = client.lower()
        people = [
            p for p in people
            if any(client_lower in (proj or "").lower() for proj in p.get("projects", []))
        ]

    if exceptions_only:
        people = [p for p in people if p.get("classification") in ("overallocated", "underutilized")]
        detail_rows = [r for r in detail_rows if r.get("is_overallocated") or r.get("is_underallocated")]

    # Classify status counts
    status_counts = {"overallocated": 0, "underutilized": 0, "available": 0, "unknown": 0}
    for p in people:
        cls = p.get("classification", "unknown")
        if cls in status_counts:
            status_counts[cls] += 1

    # Freshness
    freshness, last_ingestion = _forecast_freshness(conn)

    return {
        "selected_week": selected.isoformat(),
        "selection_explanation": selection_explanation,
        "available_weeks": [d.isoformat() for d in available_weeks],
        "people": people,
        "detail_rows": detail_rows[:limit],
        "row_count": len(detail_rows),
        "person_count": len(people),
        "exception_count": sum(1 for p in people if p.get("warning")),
        "status_counts": status_counts,
        "freshness": freshness,
        "last_ingestion": last_ingestion,
        "warnings": warnings,
    }


def _best_week(available_weeks: list[date], as_of: date) -> date | None:
    """Select best week: current if present, nearest future, most recent past."""
    if not available_weeks:
        return None

    # Current week
    for w in available_weeks:
        if w <= as_of < w + timedelta(days=7):
            return w

    # Nearest future
    for w in available_weeks:
        if w >= as_of:
            return w

    # Most recent past
    return available_weeks[-1]


def _week_explanation(selected: date, available: list[date], as_of: date) -> str:
    """Explain why this week was selected."""
    if selected <= as_of < selected + timedelta(days=7):
        return "Current week"
    if selected >= as_of:
        days = (selected - as_of).days
        return f"Nearest future week ({days}d ahead)"
    if selected < as_of:
        days = (as_of - selected).days
        return f"Most recent past week ({days}d ago)"
    return "Default week"


def _classify_allocation(alloc: dict, week: date, conn) -> str:
    """Classify a person-week allocation."""
    alloc_pct = alloc.get("allocation_pct", 0.0)

    # Overallocated
    if alloc_pct > 100.01:
        return "overallocated"

    # Available/bench: valid capacity with zero planned
    planned = alloc.get("planned_hours", 0.0)
    target = alloc.get("target_hours")
    if target and target > 0 and planned == 0.0:
        return "available"

    # Underutilized: below 80%
    if target and target > 0 and alloc_pct < 80.0:
        return "underutilized"

    # Unknown: no row or no valid capacity
    if not target or target == 0:
        return "unknown"

    return "normal"


def _detect_roll_off(conn, person_name: str, current_week: date) -> dict | None:
    """Detect upcoming roll-off for a person using future weeks."""
    try:
        rows = conn.execute(
            """
            SELECT week_start, SUM(COALESCE(planned_hours, 0)) as planned
            FROM staffing_forecast
            WHERE person_name = ?
              AND week_start > ?
              AND forecast_type IN ('confirmed', 'likely')
            GROUP BY week_start
            ORDER BY week_start
            LIMIT 8
            """,
            [person_name, current_week],
        ).fetchall()

        if not rows:
            return None

        # Find meaningful decrease
        for i in range(1, len(rows)):
            if rows[i][1] is not None and rows[0][1] is not None:
                if rows[i][1] < rows[0][1] * 0.3 and rows[i][1] < 5:
                    ws = rows[i][0]
                    if isinstance(ws, date):
                        return {
                            "week": ws.isoformat(),
                            "reason": f"Work drops to {rows[i][1]:.0f}h from {rows[0][1]:.0f}h",
                        }
        return None
    except Exception:
        return None


def _forecast_freshness(conn) -> tuple[str, str | None]:
    """Determine forecast source freshness from DB."""
    try:
        row = conn.execute(
            "SELECT MAX(ingested_at) FROM raw_documents WHERE source_type = 'forecast'"
        ).fetchone()
        if row and row[0]:
            ts = row[0]
            if isinstance(ts, datetime):
                return "fresh", ts.isoformat()
            return "fresh", str(ts)
        return "missing", None
    except Exception:
        return "missing", None