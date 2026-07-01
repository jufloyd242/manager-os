"""Validates CommandRunRequests against the registry and builds safe argv lists.

Safety invariants enforced here:
- Blocked commands are rejected before any argument validation or argv
  construction happens at all.
- Only parameters declared on the CommandSpec may be supplied; anything else
  (e.g. a raw "shell command" string) is rejected with InvalidArgumentError.
- argv is always built as a `list[str]` from validated, typed parameters —
  never a joined/interpolated shell string, never `shell=True`.
- Bounded parameters (e.g. --limit, --limit-projects) are hard-rejected when
  they exceed the command's declared `max_scope`, never silently clamped.

This module intentionally does NOT execute anything via subprocess — it only
validates requests and constructs the argv that a caller *could* run. See
the module docstring in this package's __init__ for what's stubbed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Optional

from manager_os.command_center import registry
from manager_os.command_center.errors import (
    CommandBlockedError,
    ConfirmationRequiredError,
    DryRunRequiredError,
    InvalidArgumentError,
    ScopeExceededError,
)
from manager_os.command_center.models import CommandSpec, RiskLevel


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
    required params, wrong types, disallowed enum values, and out-of-bound
    scope. Returns a fully-populated dict (declared params only, defaults
    filled in)."""
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
