"""Command run history: in-memory recording of command runs, plus (designed
and implemented, but not yet wired into any caller) DB persistence.

Status: `CommandRunRecord` + `CommandHistory` (in-memory) are fully
implemented and usable today. `persist_run`/`load_recent_runs` against the
`command_runs` DB table are also implemented, but nothing in this pass calls
them yet — no CLI/API/runner code writes to the DB. That wiring is the next
integration step once a caller (e.g. the API layer) actually executes
commands and wants durable history.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
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
