"""Data-shaping helpers for the API routes.

Route handlers stay thin: call one of these, catch exceptions, return.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Optional

import duckdb

from manager_os.build.dashboard_data import get_meetings_for_date, get_people_rows
from manager_os.build.project_index import search_projects
from manager_os.command_center import history, registry, token_estimator
from manager_os.command_center.errors import CommandBlockedError
from manager_os.command_center.runner import build_argv, execute_command
from manager_os.config import Settings

_SOURCE_TABLES = ["projects", "people", "meetings", "signals", "staffing_forecast"]


def _format_age(dt: datetime) -> str:
    now = datetime.now()
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return "less than a minute ago"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} minute{'s' if int(minutes) != 1 else ''} ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)} hour{'s' if int(hours) != 1 else ''} ago"
    days = hours / 24
    return f"{int(days)} day{'s' if int(days) != 1 else ''} ago"


def _get_last_successful_fetch(conn: duckdb.DuckDBPyConnection, command_id: str) -> Optional[datetime]:
    try:
        res = conn.execute(
            "SELECT finished_at FROM command_runs WHERE command_id = ? AND status = 'success' ORDER BY finished_at DESC LIMIT 1",
            [command_id]
        ).fetchone()
        if res and res[0]:
            return res[0]
    except Exception:
        pass
    return None


def _get_last_successful_ingest(conn: duckdb.DuckDBPyConnection, table_name: str) -> Optional[datetime]:
    col = "ingested_at" if table_name == "staffing_forecast" else "updated_at"
    try:
        res = conn.execute(f"SELECT MAX({col}) FROM {table_name}").fetchone()
        if res and res[0]:
            return res[0]
    except Exception:
        pass
    return None


def _get_last_source_date(conn: duckdb.DuckDBPyConnection, table_name: str) -> Optional[date]:
    col = None
    if table_name == "meetings":
        col = "meeting_date"
    elif table_name == "signals":
        col = "signal_date"
    elif table_name == "staffing_forecast":
        col = "week_start"
    elif table_name == "people":
        col = "last_1on1_date"
    elif table_name == "projects":
        col = "close_date"
    
    if not col:
        return None
        
    try:
        res = conn.execute(f"SELECT MAX({col}) FROM {table_name}").fetchone()
        if res and res[0]:
            val = res[0]
            if isinstance(val, (datetime, date)):
                return val
            if isinstance(val, str):
                return date.fromisoformat(val.split(" ")[0])
    except Exception:
        pass
    return None


def build_status(conn: duckdb.DuckDBPyConnection, settings: Settings) -> dict:
    """Return a local system/data freshness summary, one entry per key table."""
    warnings: list[str] = []
    sources = []
    
    command_id_map = {
        "meetings": "retrieve_calendar",
        "staffing_forecast": "retrieve_forecast",
        "projects": "search_projects",
        "people": "people_audit",
    }

    for name in _SOURCE_TABLES:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            
            if count == 0:
                sources.append({
                    "name": name,
                    "status": "empty",
                    "count": 0,
                    "last_updated": None,
                    "warnings": [],
                    "last_source_date": None,
                    "last_successful_fetch": None,
                    "last_successful_ingest": None,
                    "calculated_age": None,
                    "freshness": "missing",
                    "explanation": "No records found in database.",
                })
                continue
                
            last_source_date = _get_last_source_date(conn, name)
            last_ingest = _get_last_successful_ingest(conn, name)
            
            last_fetch = None
            if name in command_id_map:
                last_fetch = _get_last_successful_fetch(conn, command_id_map[name])
                
            ref_time = last_ingest or last_fetch
            calculated_age = None
            freshness = "unknown"
            explanation = "No ingest/fetch timestamp metadata found to verify freshness."
            
            if ref_time:
                calculated_age = _format_age(ref_time)
                now = datetime.now()
                # Check 24 hours
                if (now - ref_time) <= timedelta(hours=24):
                    freshness = "fresh"
                    explanation = f"Data was updated {calculated_age}."
                else:
                    freshness = "stale"
                    explanation = f"Data is stale (updated {calculated_age})."
            
            sources.append({
                "name": name,
                "status": "available",
                "count": count,
                "last_updated": last_ingest.isoformat() if last_ingest else None,
                "warnings": [],
                "last_source_date": last_source_date.isoformat() if last_source_date else None,
                "last_successful_fetch": last_fetch.isoformat() if last_fetch else None,
                "last_successful_ingest": last_ingest.isoformat() if last_ingest else None,
                "calculated_age": calculated_age,
                "freshness": freshness,
                "explanation": explanation,
            })
            
        except Exception as exc:
            msg = f"{name}: {exc}"
            warnings.append(msg)
            sources.append({
                "name": name,
                "status": "missing",
                "count": 0,
                "last_updated": None,
                "warnings": [msg],
                "last_source_date": None,
                "last_successful_fetch": None,
                "last_successful_ingest": None,
                "calculated_age": None,
                "freshness": "missing",
                "explanation": f"Failed to inspect source: {exc}",
            })

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

    `estimated_input_tokens` uses `token_estimator.estimate_for_command`
    (real prompt built from supplied params when possible) rather than the
    spec's static placeholder, so the preview reflects the actual call.
    `estimated_output_tokens` is always None today — no output-token
    estimator exists yet.
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

    _, estimated_input_tokens = token_estimator.estimate_for_command(command_id, params)

    return {
        "ok": ok,
        "command_id": command_id,
        "argv_preview": argv,
        "risk_level": spec.risk_level.value,
        "external_call_risk": spec.external_call_risk.value,
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": None,
        "warnings": warnings,
        "requires_confirmation": spec.requires_confirmation,
        "dry_run_required_before_live": spec.dry_run_required_before_live,
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
