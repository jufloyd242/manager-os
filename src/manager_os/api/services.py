"""Data-shaping helpers for the API routes.

Route handlers stay thin: call one of these, catch exceptions, return.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

import duckdb

from manager_os.build.dashboard_data import get_meetings_for_date, get_people_rows
from manager_os.build.project_index import search_projects
from manager_os.command_center import history, registry
from manager_os.command_center.errors import CommandBlockedError
from manager_os.command_center.runner import build_argv, execute_command
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


# ---------------------------------------------------------------------------
# Command Center: registry, validation/preview, execution, run history.
# ---------------------------------------------------------------------------


def list_commands() -> list[dict]:
    """Return every registered command spec as a plain dict (registry order)."""
    return [spec.model_dump(mode="json") for spec in registry.all_specs()]


def get_command_spec(command_id: str) -> dict:
    """Return one command spec as a plain dict. Raises CommandNotFoundError."""
    spec = registry.get(command_id)
    return spec.model_dump(mode="json")


def validate_command(command_id: str, params: dict) -> dict:
    """Validate params for command_id and return a preview, without executing
    anything. Reuses `runner.build_argv` for the actual validation/argv
    construction logic (does not reimplement it).

    Raises CommandNotFoundError for an unknown command_id, and
    InvalidArgumentError/ScopeExceededError for invalid params (both left
    to the caller/route to translate into HTTP status codes). A genuinely
    risk_level=blocked command_id does NOT raise here — it returns
    ok=False with a warning, since that's a structural property of the
    command, not a caller mistake.
    """
    spec = registry.get(command_id)
    warnings: list[str] = []
    try:
        argv = build_argv(command_id, params)
        ok = True
    except CommandBlockedError as exc:
        argv = None
        ok = False
        warnings.append(str(exc))

    return {
        "ok": ok,
        "argv_preview": argv,
        "risk_level": spec.risk_level.value,
        "external_call_risk": spec.external_call_risk.value,
        "estimated_input_tokens": spec.estimated_input_tokens,
        "warnings": warnings,
        "requires_confirmation": spec.requires_confirmation,
    }


def run_command(
    conn: duckdb.DuckDBPyConnection, command_id: str, params: dict, *, confirm: bool = False
) -> dict:
    """Execute a registered command and return an API-shaped result dict.

    Delegates entirely to `runner.execute_command` — never runs subprocess
    itself. Raises CommandNotFoundError / InvalidArgumentError /
    ScopeExceededError exactly as `execute_command` does; a blocked command
    is returned (not raised) with status="blocked", matching
    `execute_command`'s own non-raising signal for that case.
    """
    result = execute_command(conn, command_id, params, confirm=confirm)
    return {
        "ok": result["status"] == "success",
        "run_id": result["run_id"],
        "status": result["status"],
        "command_id": result["command_id"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "error": result["error"],
        "estimated_input_tokens": result["estimated_input_tokens"],
        "estimated_output_tokens": result["estimated_output_tokens"],
    }


def _serialize_run(row: dict) -> dict:
    argv_json = row.get("argv_json")
    affected_tables_json = row.get("affected_tables_json")
    started_at = row.get("started_at")
    finished_at = row.get("finished_at")
    return {
        "run_id": row["id"],
        "command_id": row["command_id"],
        "status": row["status"],
        "risk_level": row["risk_level"],
        "external_call_risk": row["external_call_risk"],
        "dry_run": row["dry_run"],
        "argv": json.loads(argv_json) if argv_json else [],
        "estimated_input_tokens": row.get("estimated_input_tokens"),
        "estimated_output_tokens": row.get("estimated_output_tokens"),
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else None,
        "stdout": row.get("stdout"),
        "stderr": row.get("stderr"),
        "error": row.get("error"),
        "affected_tables": json.loads(affected_tables_json) if affected_tables_json else [],
    }


def list_runs(conn: duckdb.DuckDBPyConnection, limit: int = 50) -> list[dict]:
    """Return recent command_runs rows (most recent first), API-shaped."""
    rows = history.list_command_runs(conn, limit=limit)
    return [_serialize_run(row) for row in rows]


def get_run(conn: duckdb.DuckDBPyConnection, run_id: str) -> Optional[dict]:
    """Return one command_runs row, API-shaped, or None if not found."""
    row = history.get_command_run(conn, run_id)
    if row is None:
        return None
    return _serialize_run(row)
