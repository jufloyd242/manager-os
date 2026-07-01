"""Command center: a safe, allowlisted command registry + runner foundation.

This package lets a future dashboard/API run Manager OS actions without
becoming an arbitrary shell terminal:

- `registry` — the allowlist of runnable commands (`CommandSpec`s), each with
  a declared risk_level, external_call_risk, typed parameters, and bounds.
- `runner` — validates a run request against the registry and builds a safe
  `argv` list (`[sys.executable, "-m", "manager_os.cli", ...]`). Rejects
  blocked commands, unconfirmed high-risk commands, unknown parameters, and
  out-of-bound scope. `execute_command` actually runs a narrow phase-1
  allowlist of local, read-only commands via `subprocess.run` (shell=False),
  persisting a `command_runs` row for every attempted run; every other
  registered command (blocked, or external_bounded/external_high_risk) is
  rejected with status="blocked" and never reaches subprocess.
- `token_estimator` — cheap `chars/4` token estimates, reusing the real
  project-docs prompt builders rather than duplicating prompt text.
- `history` — in-memory run recording, plus DB persistence via a
  `command_runs` table (`ensure_command_runs_table`,
  `insert_command_run_started`, `update_command_run_finished`,
  `list_command_runs`, `get_command_run`), used by `runner.execute_command`.
- `models` — CommandSpec/ParameterSpec/enums/request-response shapes.
- `errors` — all exception types raised by registry/runner.
"""

from __future__ import annotations

from manager_os.command_center.errors import (
    CommandBlockedError,
    CommandCenterError,
    CommandNotFoundError,
    ConfirmationRequiredError,
    DryRunRequiredError,
    InvalidArgumentError,
    ScopeExceededError,
)
from manager_os.command_center.history import (
    CommandHistory,
    CommandRunRecord,
    ensure_command_runs_table,
    get_command_run,
    insert_command_run_started,
    list_command_runs,
    load_recent_runs,
    persist_run,
    update_command_run_finished,
)
from manager_os.command_center.models import (
    CommandRunRequest,
    CommandRunResult,
    CommandSpec,
    ExternalCallRisk,
    ParameterSpec,
    RiskLevel,
)
from manager_os.command_center.runner import ValidatedRequest, build_argv, execute_command, validate_request
from manager_os.command_center.token_estimator import estimate_for_command, estimate_tokens

__all__ = [
    "CommandBlockedError",
    "CommandCenterError",
    "CommandNotFoundError",
    "ConfirmationRequiredError",
    "DryRunRequiredError",
    "InvalidArgumentError",
    "ScopeExceededError",
    "CommandHistory",
    "CommandRunRecord",
    "ensure_command_runs_table",
    "get_command_run",
    "insert_command_run_started",
    "list_command_runs",
    "load_recent_runs",
    "persist_run",
    "update_command_run_finished",
    "CommandRunRequest",
    "CommandRunResult",
    "CommandSpec",
    "ExternalCallRisk",
    "ParameterSpec",
    "RiskLevel",
    "ValidatedRequest",
    "build_argv",
    "execute_command",
    "validate_request",
    "estimate_for_command",
    "estimate_tokens",
]
