"""Command run history: in-memory recording of command runs, plus durable
DB persistence via the `command_runs` table.

`CommandRunRecord` + `CommandHistory` (in-memory) remain available for
lightweight, non-persisted tracking. `persist_run`/`load_recent_runs` and the
newer `ensure_command_runs_table`/`insert_command_run_started`/
`update_command_run_finished`/`list_command_runs`/`get_command_run` helpers
are used by `runner.execute_command` to durably record every attempted run
(success, failure, blocked, timeout) as it happens.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional


@dataclass
class CommandRunRecord:
    command_id: str
    status: str
    risk_level: str
    external_call_risk: str
    dry_run: bool
    argv_json: str
    estimated_input_tokens: Optional[int] = None
    estimated_output_tokens: Optional[int] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    error: Optional[str] = None
    affected_tables_json: Optional[str] = None

    @classmethod
    def create(
        cls,
        *,
        command_id: str,
        risk_level: str,
        external_call_risk: str,
        dry_run: bool,
        argv: list[str],
        estimated_input_tokens: Optional[int] = None,
        affected_tables: Optional[list[str]] = None,
    ) -> "CommandRunRecord":
        return cls(
            command_id=command_id,
            status="running",
            risk_level=risk_level,
            external_call_risk=external_call_risk,
            dry_run=dry_run,
            argv_json=json.dumps(argv),
            estimated_input_tokens=estimated_input_tokens,
            affected_tables_json=json.dumps(affected_tables or []),
        )

    def mark_finished(
        self,
        status: str,
        *,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.finished_at = datetime.utcnow()


class CommandHistory:
    """Simple in-memory recorder for command runs (per-process; not persisted
    across restarts unless/until persist_run is wired in by a caller)."""

    def __init__(self) -> None:
        self._records: list[CommandRunRecord] = []

    def add(self, record: CommandRunRecord) -> None:
        self._records.append(record)

    def list(
        self, command_id: Optional[str] = None, limit: Optional[int] = None
    ) -> list[CommandRunRecord]:
        records = sorted(self._records, key=lambda r: r.started_at, reverse=True)
        if command_id is not None:
            records = [r for r in records if r.command_id == command_id]
        if limit is not None:
            records = records[:limit]
        return records

    def get(self, run_id: str) -> Optional[CommandRunRecord]:
        for r in self._records:
            if r.id == run_id:
                return r
        return None


_RUN_RECORD_COLUMNS = [
    "id", "command_id", "status", "risk_level", "external_call_risk", "dry_run",
    "argv_json", "estimated_input_tokens", "estimated_output_tokens",
    "started_at", "finished_at", "stdout", "stderr", "error", "affected_tables_json",
]


def persist_run(conn: Any, record: CommandRunRecord) -> None:
    """Insert a CommandRunRecord into the `command_runs` DB table.

    Not called by anything in this pass — provided for the next integration
    step (wiring an actual executor/API endpoint to durable history).
    """
    conn.execute(
        f"""
        INSERT INTO command_runs ({", ".join(_RUN_RECORD_COLUMNS)})
        VALUES ({", ".join("?" for _ in _RUN_RECORD_COLUMNS)})
        """,
        [getattr(record, col) for col in _RUN_RECORD_COLUMNS],
    )


def load_recent_runs(
    conn: Any, command_id: Optional[str] = None, limit: int = 50
) -> list[dict]:
    """Load recent command_runs rows as plain dicts, most recent first."""
    col_list = ", ".join(_RUN_RECORD_COLUMNS)
    if command_id is not None:
        rows = conn.execute(
            f"SELECT {col_list} FROM command_runs WHERE command_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            [command_id, limit],
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {col_list} FROM command_runs ORDER BY started_at DESC LIMIT ?",
            [limit],
        ).fetchall()
    return [dict(zip(_RUN_RECORD_COLUMNS, row)) for row in rows]


# ---------------------------------------------------------------------------
# Execution-phase helpers: used by runner.execute_command to record every
# attempted run (success, failure, blocked, timeout) as it happens.
# ---------------------------------------------------------------------------


def ensure_command_runs_table(conn: Any) -> None:
    """Idempotently ensure the command_runs table (and the rest of the
    schema) exists on `conn`. Safe to call on any DuckDB connection, not
    just ones opened via `manager_os.db.get_connection` (which already runs
    this as part of its own init_schema call) — re-running is a no-op
    thanks to `CREATE TABLE IF NOT EXISTS`.
    """
    from manager_os.db import init_schema

    init_schema(conn)


def insert_command_run_started(
    conn: Any,
    *,
    command_id: str,
    risk_level: str,
    external_call_risk: str,
    dry_run: bool,
    argv: Optional[list[str]],
    estimated_input_tokens: Optional[int] = None,
    affected_tables: Optional[list[str]] = None,
) -> str:
    """Insert a new command_runs row with status="running" and return its id.

    `argv` may be None (e.g. for blocked commands, or commands whose args
    failed validation before argv could be built) — persisted as an empty
    JSON array in that case.
    """
    record = CommandRunRecord.create(
        command_id=command_id,
        risk_level=risk_level,
        external_call_risk=external_call_risk,
        dry_run=dry_run,
        argv=argv or [],
        estimated_input_tokens=estimated_input_tokens,
        affected_tables=affected_tables,
    )
    persist_run(conn, record)
    return record.id


def update_command_run_finished(
    conn: Any,
    run_id: str,
    *,
    status: str,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    error: Optional[str] = None,
) -> datetime:
    """Update an existing command_runs row in place with its final status,
    captured output, and finished_at timestamp. Returns the finished_at
    timestamp that was written."""
    finished_at = datetime.utcnow()
    conn.execute(
        """
        UPDATE command_runs
        SET status = ?, stdout = ?, stderr = ?, error = ?, finished_at = ?
        WHERE id = ?
        """,
        [status, stdout, stderr, error, finished_at, run_id],
    )
    return finished_at


def list_command_runs(conn: Any, limit: int = 50) -> list[dict]:
    """List the most recent command_runs rows, across all command_ids."""
    return load_recent_runs(conn, limit=limit)


def get_command_run(conn: Any, run_id: str) -> Optional[dict]:
    """Fetch a single command_runs row by id, or None if not found."""
    col_list = ", ".join(_RUN_RECORD_COLUMNS)
    row = conn.execute(
        f"SELECT {col_list} FROM command_runs WHERE id = ?", [run_id]
    ).fetchone()
    if row is None:
        return None
    return dict(zip(_RUN_RECORD_COLUMNS, row))


# ---------------------------------------------------------------------------
# Dry-run-first guardrail helpers (used by runner._execute_live_single to
# enforce that project_docs_fetch_live_single is only ever executed after a
# qualifying, recent, successful project_docs_fetch_dry_run run for the same
# opportunity_number).
# ---------------------------------------------------------------------------


def _extract_opportunity_number_from_argv(argv: Optional[list]) -> Optional[str]:
    """Pull the value following a `--opportunity-number` flag out of a
    persisted argv list. Returns None if not present."""
    if not argv:
        return None
    for i, part in enumerate(argv):
        if part == "--opportunity-number" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def is_qualifying_dry_run(
    row: Optional[dict], opportunity_number: str, *, within_minutes: int = 30
) -> bool:
    """Return True if `row` (a command_runs row dict, e.g. from
    get_command_run) is a successful project_docs_fetch_dry_run run for the
    given (already normalized) opportunity_number, completed within the last
    `within_minutes` minutes."""
    if row is None:
        return False
    if row.get("command_id") != "project_docs_fetch_dry_run":
        return False
    if row.get("status") != "success":
        return False
    started_at = row.get("started_at")
    if started_at is None:
        return False
    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at)
    if datetime.utcnow() - started_at > timedelta(minutes=within_minutes):
        return False
    argv = row.get("argv_json")
    if isinstance(argv, str):
        argv = json.loads(argv or "[]")
    run_opp = _extract_opportunity_number_from_argv(argv)
    if not run_opp:
        return False
    return run_opp.strip().upper() == opportunity_number.strip().upper()


def find_recent_successful_dry_run(
    conn: Any, opportunity_number: str, *, within_minutes: int = 30
) -> Optional[str]:
    """Search command_runs for the most recent successful
    project_docs_fetch_dry_run run for `opportunity_number` (already
    normalized) within the last `within_minutes` minutes. Returns its
    run_id, or None if no qualifying run is found."""
    cutoff = datetime.utcnow() - timedelta(minutes=within_minutes)
    col_list = ", ".join(_RUN_RECORD_COLUMNS)
    rows = conn.execute(
        f"""
        SELECT {col_list} FROM command_runs
        WHERE command_id = ? AND status = ? AND started_at >= ?
        ORDER BY started_at DESC
        """,
        ["project_docs_fetch_dry_run", "success", cutoff],
    ).fetchall()
    for raw in rows:
        row = dict(zip(_RUN_RECORD_COLUMNS, raw))
        if is_qualifying_dry_run(row, opportunity_number, within_minutes=within_minutes):
            return row["id"]
    return None
