"""Data-shaping helpers for the API routes.

Route handlers stay thin: call one of these, catch exceptions, return.
"""

from __future__ import annotations

from datetime import date

import duckdb

from manager_os.build.dashboard_data import get_meetings_for_date, get_people_rows
from manager_os.build.project_index import search_projects
from manager_os.config import Settings

_SOURCE_TABLES = ["projects", "people", "meetings", "signals", "staffing_forecast"]


def build_status(conn: duckdb.DuckDBPyConnection, settings: Settings) -> dict:
    """Return a local system/data freshness summary, one entry per key table."""
    warnings: list[str] = []
    sources = []
    for name in _SOURCE_TABLES:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            sources.append(
                {
                    "name": name,
                    "status": "available" if count else "empty",
                    "count": count,
                    "last_updated": None,
                    "warnings": [],
                }
            )
        except Exception as exc:
            msg = f"{name}: {exc}"
            warnings.append(msg)
            sources.append(
                {"name": name, "status": "missing", "count": 0, "last_updated": None, "warnings": [msg]}
            )

    return {
        "ok": True,
        "db_path": settings.db_path,
        "workspace_enabled": bool(settings.workspace_retrieval_enabled),
        "sources": sources,
        "warnings": warnings,
    }


def build_people(conn: duckdb.DuckDBPyConnection, settings: Settings) -> dict:
    """Return people dashboard rows, degrading to an empty list on failure."""
    warnings: list[str] = []
    try:
        rows = get_people_rows(conn, settings=settings)
        people = [r.model_dump(mode="json") for r in rows]
    except Exception as exc:
        warnings.append(f"people: {exc}")
        people = []
    return {"people": people, "warnings": warnings}


def build_meetings(conn: duckdb.DuckDBPyConnection, target_date: date) -> dict:
    """Return local meetings for target_date, degrading to an empty list on failure."""
    warnings: list[str] = []
    try:
        meetings = get_meetings_for_date(conn, target_date)
    except Exception as exc:
        warnings.append(f"meetings: {exc}")
        meetings = []
    return {"date": target_date.isoformat(), "meetings": meetings, "warnings": warnings}


def build_projects(conn: duckdb.DuckDBPyConnection, limit: int = 200) -> dict:
    """Return project index records, degrading to an empty list on failure."""
    warnings: list[str] = []
    try:
        projects = search_projects(conn, limit=limit)
    except Exception as exc:
        warnings.append(f"projects: {exc}")
        projects = []
    return {"projects": projects, "warnings": warnings}


def build_feedback(conn: duckdb.DuckDBPyConnection) -> dict:
    """Return feedback_learning_candidates rows, degrading gracefully if absent."""
    warnings: list[str] = []
    candidates: list[dict] = []
    try:
        rows = conn.execute(
            """
            SELECT pattern_type, entity_name, signal_type, rating, event_count, suggested_action, status
            FROM feedback_learning_candidates
            ORDER BY event_count DESC
            """
        ).fetchall()
        candidates = [
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
        warnings.append(f"feedback_learning_candidates: {exc}")
    return {"candidates": candidates, "warnings": warnings}
