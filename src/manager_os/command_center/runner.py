"""Validates CommandRunRequests against the registry, builds safe argv lists,
and (for a narrow phase-1 allowlist of local, read-only commands) actually
executes them via subprocess — persisting a `command_runs` row for every
attempted run.

Safety invariants enforced here:
- Blocked commands are rejected before any argument validation or argv
  construction happens at all.
- Only parameters declared on the CommandSpec may be supplied; anything else
  (e.g. a raw "shell command" string) is rejected with InvalidArgumentError.
- argv is always built as a `list[str]` from validated, typed parameters —
  never a joined/interpolated shell string, never `shell=True`.
- Bounded parameters (e.g. --limit, --limit-projects) are hard-rejected when
  they exceed the command's declared `max_scope`, never silently clamped.
- `execute_command` only ever calls `subprocess.run` for command_ids in the
  phase-1 `_EXECUTABLE_COMMAND_IDS` allowlist below — every other registered
  command (blocked risk_level, or external_bounded/external_high_risk not in
  the allowlist) is rejected with status="blocked", regardless of
  `confirm`, and never reaches subprocess.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

from manager_os.command_center import history, registry, token_estimator
from manager_os.command_center.errors import (
    CommandBlockedError,
    ConfirmationRequiredError,
    DryRunRequiredError,
    InvalidArgumentError,
    ScopeExceededError,
)
from manager_os.command_center.models import CommandSpec, RiskLevel
from manager_os.utils import normalize_opp_id
from manager_os.ingest.project_drive_docs import search_drive_for_project_docs


@dataclass(frozen=True)
class ValidatedRequest:
    command_id: str
    args: dict[str, Any]
    dry_run: bool
    confirmed: bool
    spec: CommandSpec


def _type_ok(value: Any, type_name: str) -> bool:
    if type_name == "bool":
        return isinstance(value, bool)
    if type_name == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "str":
        return isinstance(value, str)
    if type_name == "list":
        return isinstance(value, list)
    return False


def _validate_args(spec: CommandSpec, args: dict[str, Any]) -> dict[str, Any]:
    """Validate args against spec.parameters: reject unknown keys, missing
    required params, wrong types, disallowed enum values, out-of-bound
    per-parameter minimum/maximum, and out-of-bound `bounded_param`/
    `max_scope` scope. Returns a fully-populated dict (declared params only,
    defaults filled in)."""
    allowed = spec.parameter_names()
    unknown = set(args) - allowed
    if unknown:
        raise InvalidArgumentError(
            f"Unknown parameter(s) {sorted(unknown)} for command {spec.command_id!r}. "
            f"Allowed parameters: {sorted(allowed)}."
        )

    validated: dict[str, Any] = {}
    for param in spec.parameters:
        if param.name in args:
            value = args[param.name]
            if value is not None and not _type_ok(value, param.type):
                raise InvalidArgumentError(
                    f"Parameter {param.name!r} for command {spec.command_id!r} must be "
                    f"of type {param.type!r}, got {type(value).__name__}."
                )
            if value is not None and param.allowed_values is not None and value not in param.allowed_values:
                raise InvalidArgumentError(
                    f"Parameter {param.name!r} value {value!r} for command "
                    f"{spec.command_id!r} is not in allowed values {param.allowed_values!r}."
                )
            if value is not None and param.maximum is not None and value > param.maximum:
                raise ScopeExceededError(
                    f"Parameter {param.name!r}={value} for command {spec.command_id!r} "
                    f"exceeds maximum allowed {param.maximum}."
                )
            if value is not None and param.minimum is not None and value < param.minimum:
                raise ScopeExceededError(
                    f"Parameter {param.name!r}={value} for command {spec.command_id!r} "
                    f"is below minimum allowed {param.minimum}."
                )
            validated[param.name] = value
        elif param.required:
            raise InvalidArgumentError(
                f"Missing required parameter {param.name!r} for command {spec.command_id!r}."
            )
        else:
            validated[param.name] = param.default

    if spec.max_scope is not None and spec.bounded_param:
        value = validated.get(spec.bounded_param)
        if value is not None and value > spec.max_scope:
            raise ScopeExceededError(
                f"Parameter {spec.bounded_param!r}={value} exceeds max allowed "
                f"{spec.max_scope} for command {spec.command_id!r}."
            )

    return validated


def validate_request(
    command_id: str,
    args: Optional[dict[str, Any]] = None,
    *,
    dry_run: bool = False,
    confirmed: bool = False,
) -> ValidatedRequest:
    """Validate a proposed command run. Raises before doing anything unsafe.

    Order of checks:
    1. Command must exist (CommandNotFoundError).
    2. Blocked commands are rejected unconditionally, before any argument
       validation (CommandBlockedError) — this holds for every combination
       of dry_run/confirmed.
    3. Arguments are validated against the command's declared parameters
       (unknown keys, missing required, wrong type, out-of-scope bound).
    4. If dry_run=True: allowed to proceed without confirmation, as long as
       the command supports dry-run at all.
    5. If dry_run=False (live): requires_confirmation and
       dry_run_required_before_live are both gated on `confirmed`.
    """
    spec = registry.get(command_id)

    if spec.risk_level == RiskLevel.blocked:
        raise CommandBlockedError(command_id)

    validated_args = _validate_args(spec, args or {})

    if dry_run:
        if not spec.supports_dry_run:
            raise InvalidArgumentError(f"Command {command_id!r} does not support --dry-run.")
        return ValidatedRequest(
            command_id=command_id, args=validated_args, dry_run=True, confirmed=confirmed, spec=spec
        )

    if spec.requires_confirmation and not confirmed:
        raise ConfirmationRequiredError(command_id)
    if spec.dry_run_required_before_live and not confirmed:
        raise DryRunRequiredError(command_id)

    return ValidatedRequest(
        command_id=command_id, args=validated_args, dry_run=False, confirmed=confirmed, spec=spec
    )


# ---------------------------------------------------------------------------
# argv construction — one builder per command_id, list[str] only, never a
# joined/interpolated shell string.
# ---------------------------------------------------------------------------


def _daily_dry_run_argv(a: dict) -> list[str]:
    argv = ["daily", "--dry-run", "--no-workspace", "--skip-project-index"]
    if a.get("target_date"):
        argv += ["--date", a["target_date"]]
    return argv


def _project_memory_report_argv(a: dict) -> list[str]:
    argv = ["project-memory-report"]
    if a.get("as_json"):
        argv.append("--json")
    return argv


def _feedback_summary_argv(a: dict) -> list[str]:
    return ["feedback", "summary"]


def _feedback_candidates_argv(a: dict) -> list[str]:
    argv = ["feedback", "candidates"]
    if a.get("limit") is not None:
        argv += ["--limit", str(a["limit"])]
    return argv


def _people_audit_argv(a: dict) -> list[str]:
    argv = ["people-audit"]
    if a.get("verbose"):
        argv.append("--verbose")
    return argv


def _search_projects_argv(a: dict) -> list[str]:
    argv = ["search-projects"]
    if a.get("query"):
        argv.append(a["query"])
    flag_map = [
        ("client", "--client"), ("person", "--person"), ("technology", "--technology"),
        ("project_type", "--type"), ("industry", "--industry"), ("sales_rep", "--sales-rep"),
        ("status", "--status"), ("year", "--year"), ("close_after", "--close-after"),
        ("close_before", "--close-before"), ("opportunity_number", "--opportunity-number"),
        ("document_type", "--document-type"), ("limit", "--limit"),
    ]
    for key, flag in flag_map:
        value = a.get(key)
        if value:
            argv += [flag, str(value)]
    if a.get("as_json"):
        argv.append("--json")
    return argv


def _llm_doctor_no_smoke_argv(a: dict) -> list[str]:
    # --no-smoke-test is always hardcoded here; there is no param that can
    # override it back to a live smoke test.
    argv = ["llm-doctor", "--no-smoke-test"]
    if a.get("timeout") is not None:
        argv += ["--timeout", str(a["timeout"])]
    return argv


def _project_docs_fetch_single_argv(a: dict, mode: str) -> list[str]:
    argv = ["project-docs-fetch", "--opportunity-number", str(a["opportunity_number"])]
    if a.get("limit") is not None:
        argv += ["--limit", str(a["limit"])]
    if a.get("timeout") is not None:
        argv += ["--timeout", str(a["timeout"])]
    if mode == "dry_run":
        argv.append("--dry-run")
    elif mode == "print_prompt":
        argv.append("--print-prompt")
    return argv


def _project_docs_fetch_batch_argv(a: dict, mode: str) -> list[str]:
    argv = ["project-docs-fetch", "--batch", "--limit-projects", str(a["limit_projects"])]
    if a.get("timeout") is not None:
        argv += ["--timeout", str(a["timeout"])]
    if a.get("force"):
        argv.append("--force")
    if mode == "dry_run":
        argv.append("--dry-run")
    elif mode == "print_prompt":
        argv.append("--print-prompt")
    return argv


# "projects" is a param on the batch commands used only to compute an
# accurate token estimate — it must never be forwarded to argv.
_ARGV_BUILDERS: dict[str, Callable[[dict], list[str]]] = {
    "daily_dry_run": _daily_dry_run_argv,
    "project_memory_report": _project_memory_report_argv,
    "feedback_summary": _feedback_summary_argv,
    "feedback_candidates": _feedback_candidates_argv,
    "people_audit": _people_audit_argv,
    "search_projects": _search_projects_argv,
    "llm_doctor_no_smoke": _llm_doctor_no_smoke_argv,
    "project_docs_fetch_dry_run": lambda a: _project_docs_fetch_single_argv(a, "dry_run"),
    "project_docs_fetch_print_prompt": lambda a: _project_docs_fetch_single_argv(a, "print_prompt"),
    "project_docs_fetch_live_single": lambda a: _project_docs_fetch_single_argv(a, "live"),
    "project_docs_fetch_batch_dry_run": lambda a: _project_docs_fetch_batch_argv(a, "dry_run"),
    "project_docs_fetch_batch_print_prompt": lambda a: _project_docs_fetch_batch_argv(a, "print_prompt"),
    "project_docs_fetch_batch_live_bounded": lambda a: _project_docs_fetch_batch_argv(a, "live"),
}


def build_argv(
    command_id: str, args: Optional[dict[str, Any]] = None, *, dry_run: bool = False
) -> list[str]:
    """Build a safe argv list for a registered command.

    Returns `[sys.executable, "-m", "manager_os.cli", <subcommand>, ...]` —
    always a list[str], never a shell string, never `shell=True`. Blocked
    commands raise before any argv is built. Unknown/invalid/out-of-scope
    arguments raise before any argv is built.

    Note: `dry_run` here only affects nothing yet (argv shape for dry-run
    variants is baked into the specific command_id, e.g.
    "project_docs_fetch_dry_run" vs "project_docs_fetch_live_single") — the
    parameter exists for API symmetry with validate_request and reserved for
    future single-command dry-run toggling.
    """
    spec = registry.get(command_id)

    if spec.risk_level == RiskLevel.blocked:
        raise CommandBlockedError(command_id)

    validated = _validate_args(spec, args or {})

    builder = _ARGV_BUILDERS.get(command_id)
    if builder is None:
        raise InvalidArgumentError(
            f"No argv builder registered for command {command_id!r}."
        )

    return [sys.executable, "-m", "manager_os.cli", *builder(validated)]


# ---------------------------------------------------------------------------
# execute_command — phase-1 execution: actually run the narrow allowlist of
# local, read-only commands via subprocess, persisting a command_runs row
# for every attempted run.
# ---------------------------------------------------------------------------

# Only these command_ids may ever reach subprocess.run in this phase. Every
# other registered command_id — including risk_level=blocked commands AND
# any external_bounded/external_high_risk command not listed here (e.g.
# project_docs_fetch_batch_live_bounded) — is rejected with
# status="blocked", regardless of `confirm`.
#
# project_docs_fetch_live_single is the ONE exception: it's allowlisted here,
# but execute_command dispatches it to `_execute_live_single` BEFORE this
# allowlist check, which enforces its own strict guardrails (single OppID,
# confirm=True, bounded limit/timeout, dry-run-first) before ever building
# argv or calling subprocess — see `_execute_live_single` below.
_EXECUTABLE_COMMAND_IDS: frozenset[str] = frozenset(
    {
        "daily_dry_run",
        "project_docs_fetch_dry_run",
        "project_docs_fetch_print_prompt",
        "project_docs_fetch_live_single",
        "project_memory_report",
        "feedback_summary",
        "people_audit",
        "search_projects",
    }
)

# project_docs_fetch_live_single-specific guardrails (stricter than the
# shared _PROJECT_DOCS_SINGLE_PARAMS registry defaults, which are also used
# by the always-safe dry_run/print_prompt variants of this command).
_LIVE_SINGLE_ALLOWED_PARAMS: frozenset[str] = frozenset(
    {"opportunity_number", "client", "project_name", "limit", "timeout", "dry_run_run_id", "force"}
)
_LIVE_SINGLE_DEFAULT_LIMIT = 3
_LIVE_SINGLE_MAX_LIMIT = 5
_LIVE_SINGLE_DEFAULT_TIMEOUT = 60
_LIVE_SINGLE_MAX_TIMEOUT = 120
_LIVE_SINGLE_DRY_RUN_WINDOW_MINUTES = 30


def _decode(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _run_result(row: dict[str, Any], *, argv: Optional[list[str]]) -> dict[str, Any]:
    return {
        "run_id": row["id"],
        "command_id": row["command_id"],
        "status": row["status"],
        "argv": argv,
        "stdout": row["stdout"],
        "stderr": row["stderr"],
        "error": row["error"],
        "estimated_input_tokens": row["estimated_input_tokens"],
        "estimated_output_tokens": row["estimated_output_tokens"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _reject_live_single(conn: Any, spec: CommandSpec, message: str) -> dict[str, Any]:
    """Persist a blocked command_runs row for project_docs_fetch_live_single
    and return the standard result dict, without ever building argv or
    calling subprocess."""
    run_id = history.insert_command_run_started(
        conn,
        command_id=spec.command_id,
        risk_level=spec.risk_level.value,
        external_call_risk=spec.external_call_risk.value,
        dry_run=False,
        argv=None,
        estimated_input_tokens=spec.estimated_input_tokens,
    )
    history.update_command_run_finished(conn, run_id, status="blocked", error=message)
    row = history.get_command_run(conn, run_id)
    return _run_result(row, argv=None)


def _execute_live_single(
    conn: Any,
    spec: CommandSpec,
    params: dict[str, Any],
    *,
    confirm: bool,
    timeout: Optional[int],
) -> dict[str, Any]:
    """Guarded execution path for project_docs_fetch_live_single: the ONE
    external_bounded command allowed to actually execute in this phase.

    Guardrails, checked in order, every one of them BEFORE any argv is built
    or subprocess is invoked:
    1. Only opportunity_number/client/project_name/limit/timeout/
       dry_run_run_id are accepted \u2014 any other key (e.g. a "batch" flag)
       is rejected. There is no way to pass more than one OppID.
    2. opportunity_number is required and must be a single string
       (normalized via manager_os.utils.normalize_opp_id) \u2014 a list/tuple
       is rejected.
    3. confirm=True is required.
    4. limit: default 3, hard max 5 \u2014 rejected (never clamped) if exceeded.
    5. timeout: default 60, hard max 120 \u2014 rejected (never clamped) if
       exceeded.
    6. dry-run-first: there must be a qualifying prior successful
       project_docs_fetch_dry_run run for the SAME normalized
       opportunity_number within the last 30 minutes \u2014 verified via the
       caller-supplied `dry_run_run_id` (checked against
       history.get_command_run) if given, else via
       history.find_recent_successful_dry_run.

    Every attempt (rejected or executed) is persisted to command_runs.
    """
    params = params or {}

    unknown = set(params) - _LIVE_SINGLE_ALLOWED_PARAMS
    if unknown:
        return _reject_live_single(
            conn,
            spec,
            f"Unknown parameter(s) {sorted(unknown)} for project_docs_fetch_live_single. "
            f"Allowed parameters: {sorted(_LIVE_SINGLE_ALLOWED_PARAMS)}. Batch/list "
            "execution is never accepted for this command \u2014 exactly one "
            "opportunity_number is required.",
        )

    opportunity_number = params.get("opportunity_number")
    if not opportunity_number or not isinstance(opportunity_number, str):
        return _reject_live_single(
            conn,
            spec,
            "opportunity_number is required and must be a single string for "
            "project_docs_fetch_live_single (batch/list values are rejected).",
        )
    normalized_opp = normalize_opp_id(opportunity_number)

    if not confirm:
        return _reject_live_single(
            conn,
            spec,
            "Confirmation required (confirm=True) to execute "
            "project_docs_fetch_live_single.",
        )

    limit = params.get("limit")
    if limit is None:
        limit = _LIVE_SINGLE_DEFAULT_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool):
        return _reject_live_single(
            conn, spec, f"limit must be an int, got {type(limit).__name__}."
        )
    if limit > _LIVE_SINGLE_MAX_LIMIT:
        return _reject_live_single(
            conn,
            spec,
            f"limit={limit} exceeds max allowed {_LIVE_SINGLE_MAX_LIMIT} for "
            "project_docs_fetch_live_single.",
        )

    run_timeout_flag = params.get("timeout")
    if run_timeout_flag is None:
        run_timeout_flag = _LIVE_SINGLE_DEFAULT_TIMEOUT
    if not isinstance(run_timeout_flag, int) or isinstance(run_timeout_flag, bool):
        return _reject_live_single(
            conn, spec, f"timeout must be an int, got {type(run_timeout_flag).__name__}."
        )
    if run_timeout_flag > _LIVE_SINGLE_MAX_TIMEOUT:
        return _reject_live_single(
            conn,
            spec,
            f"timeout={run_timeout_flag} exceeds max allowed {_LIVE_SINGLE_MAX_TIMEOUT} "
            "for project_docs_fetch_live_single.",
        )

    dry_run_run_id = params.get("dry_run_run_id")
    if dry_run_run_id:
        dry_row = history.get_command_run(conn, dry_run_run_id)
        if not history.is_qualifying_dry_run(
            dry_row, normalized_opp, within_minutes=_LIVE_SINGLE_DRY_RUN_WINDOW_MINUTES
        ):
            return _reject_live_single(
                conn,
                spec,
                f"Provided dry_run_run_id={dry_run_run_id!r} does not correspond to a "
                "qualifying recent successful project_docs_fetch_dry_run run for "
                f"{normalized_opp}. Run project_docs_fetch_dry_run successfully "
                "before live execution.",
            )
    else:
        found_run_id = history.find_recent_successful_dry_run(
            conn, normalized_opp, within_minutes=_LIVE_SINGLE_DRY_RUN_WINDOW_MINUTES
        )
        if not found_run_id:
            return _reject_live_single(
                conn,
                spec,
                "Run project_docs_fetch_dry_run successfully before live execution.",
            )

    argv = [
        sys.executable, "-m", "manager_os.cli",
        "project-docs-fetch",
        "--opportunity-number", normalized_opp,
        "--limit", str(limit),
        "--timeout", str(run_timeout_flag),
        "--verbose",
    ]

    _, estimated_input_tokens = token_estimator.estimate_for_command(
        "project_docs_fetch_live_single",
        {
            "opportunity_number": normalized_opp,
            "client": params.get("client", ""),
            "project_name": params.get("project_name", ""),
        },
    )

    run_id = history.insert_command_run_started(
        conn,
        command_id=spec.command_id,
        risk_level=spec.risk_level.value,
        external_call_risk=spec.external_call_risk.value,
        dry_run=False,
        argv=argv,
        estimated_input_tokens=estimated_input_tokens,
    )

    try:
        # Run the drive search engine natively, passing the active database handle
        stats = search_drive_for_project_docs(
            opportunity_number=normalized_opp,
            client=params.get("client", ""),
            project_name=params.get("project_name", ""),
            conn=conn,
            force=params.get("force", False),
            limit=limit,
            timeout=run_timeout_flag
        )

        # Convert the returned dictionary statistics into standard CLI log response structures
        if stats.get("status") == "error":
            status = "failed"
            error = "; ".join(stats.get("errors", ["Unknown error"]))
            stdout = ""
            stderr = error
        else:
            status = "success"
            error = None
            stdout = f"Fetch Diagnostics:\n  Raw: {stats.get('raw_count')}\n  Parsed: {stats.get('parsed_count')}\n  Inserted: {stats.get('inserted')}\n  Updated: {stats.get('updated')}\n  Skipped: {stats.get('skipped')}"
            stderr = ""

    except Exception as exc:
        status = "failed"
        error = str(exc)
        stdout = ""
        stderr = error

    history.update_command_run_finished(
        conn, run_id, status=status, stdout=stdout, stderr=stderr, error=error
    )
    row = history.get_command_run(conn, run_id)
    return _run_result(row, argv=argv)


def execute_command(
    conn: Any,
    command_id: str,
    params: Optional[dict[str, Any]] = None,
    *,
    confirm: bool = False,
    timeout: Optional[int] = None,
) -> dict[str, Any]:
    """Execute a registered command via subprocess and persist a
    `command_runs` row recording the attempt.

    Returns a JSON-serializable dict:
        {
          "run_id", "command_id", "status" ("success"|"failed"|"blocked"|"timeout"),
          "argv", "stdout", "stderr", "error",
          "estimated_input_tokens", "estimated_output_tokens",
          "started_at", "finished_at",
        }

    Raises:
    - CommandNotFoundError if command_id isn't registered (nothing is
      persisted — there's no valid spec to record against).
    - InvalidArgumentError / ScopeExceededError if the command is in the
      phase-1 allowlist but its arguments fail validation (e.g. missing a
      required parameter, or an unregistered/raw parameter) — a "failed"
      row is persisted first so there's still an audit trail, then the
      original exception is re-raised.

    Never raises for a command that's simply not executable in this phase
    (blocked risk_level, or any external_bounded/external_high_risk command
    not in `_EXECUTABLE_COMMAND_IDS`, or a confirm=False live command) —
    those return status="blocked" instead, with a persisted row, and
    subprocess.run is never called. `confirm` has no effect on this
    allowlist in this phase — it exists for forward-compatibility with a
    future phase that permits confirmed external_bounded execution.
    """
    params = params or {}
    spec = registry.get(command_id)  # raises CommandNotFoundError; nothing persisted yet

    history.ensure_command_runs_table(conn)

    if command_id == "project_docs_fetch_live_single":
        # Special-cased: this command has its own strict guardrail gate
        # (single OppID, confirm=True, bounded limit/timeout, dry-run-first)
        # instead of the generic build_argv/_validate_args path below.
        return _execute_live_single(conn, spec, params, confirm=confirm, timeout=timeout)

    if command_id not in _EXECUTABLE_COMMAND_IDS:
        error = (
            f"Command {command_id!r} (risk_level={spec.risk_level.value}) is not "
            "executable in this phase. Only these command_ids may execute: "
            f"{sorted(_EXECUTABLE_COMMAND_IDS)}."
        )
        run_id = history.insert_command_run_started(
            conn,
            command_id=command_id,
            risk_level=spec.risk_level.value,
            external_call_risk=spec.external_call_risk.value,
            dry_run=spec.supports_dry_run,
            argv=None,
            estimated_input_tokens=spec.estimated_input_tokens,
        )
        history.update_command_run_finished(conn, run_id, status="blocked", error=error)
        row = history.get_command_run(conn, run_id)
        return _run_result(row, argv=None)

    try:
        argv = build_argv(command_id, params)
    except (InvalidArgumentError, ScopeExceededError) as exc:
        run_id = history.insert_command_run_started(
            conn,
            command_id=command_id,
            risk_level=spec.risk_level.value,
            external_call_risk=spec.external_call_risk.value,
            dry_run=spec.supports_dry_run,
            argv=None,
            estimated_input_tokens=spec.estimated_input_tokens,
        )
        history.update_command_run_finished(conn, run_id, status="failed", error=str(exc))
        raise

    run_timeout = timeout if timeout is not None else spec.default_timeout_seconds
    run_id = history.insert_command_run_started(
        conn,
        command_id=command_id,
        risk_level=spec.risk_level.value,
        external_call_risk=spec.external_call_risk.value,
        dry_run=spec.supports_dry_run,
        argv=argv,
        estimated_input_tokens=spec.estimated_input_tokens,
    )

    try:
        proc = subprocess.run(
            argv, shell=False, capture_output=True, text=True, timeout=run_timeout
        )
    except subprocess.TimeoutExpired as exc:
        history.update_command_run_finished(
            conn,
            run_id,
            status="timeout",
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
            error=f"Command {command_id!r} timed out after {run_timeout}s.",
        )
        row = history.get_command_run(conn, run_id)
        return _run_result(row, argv=argv)
    except OSError as exc:
        history.update_command_run_finished(conn, run_id, status="failed", error=str(exc))
        row = history.get_command_run(conn, run_id)
        return _run_result(row, argv=argv)

    status = "success" if proc.returncode == 0 else "failed"
    error = None if status == "success" else f"Command exited with code {proc.returncode}."
    history.update_command_run_finished(
        conn, run_id, status=status, stdout=proc.stdout, stderr=proc.stderr, error=error
    )
    row = history.get_command_run(conn, run_id)
    return _run_result(row, argv=argv)
