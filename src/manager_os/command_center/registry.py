"""The command registry: the single allowlist of Manager OS commands that the
command center is permitted to validate/run.

Nothing outside this module's `_COMMANDS` tuple is runnable. There is no way
to run a command that is not registered here, and no way to pass free-text
shell arguments through any registered command's parameters.
"""

from __future__ import annotations

import math

from manager_os.command_center.errors import CommandNotFoundError
from manager_os.command_center.models import CommandSpec, ExternalCallRisk, ParameterSpec, RiskLevel
from manager_os.ingest.project_drive_docs import (
    _build_batch_drive_search_prompt,
    _build_drive_search_prompt,
)


def _chars_and_tokens(text: str) -> tuple[int, int]:
    return len(text), math.ceil(len(text) / 4)


# Sample prompts used only to compute a representative default token/char
# estimate at registry build time (used when a caller hasn't yet supplied
# real args to estimate against — see token_estimator.estimate_for_command).
_SAMPLE_SINGLE_PROMPT = _build_drive_search_prompt("SAMPLE001", "Sample Client", "Sample Project")
_SAMPLE_BATCH_PROMPT = _build_batch_drive_search_prompt(
    [
        {"opportunity_number": f"SAMPLE{i}", "client": "Sample Client", "project_name": "Sample Project"}
        for i in range(5)
    ]
)

_SINGLE_CHARS, _SINGLE_TOKENS = _chars_and_tokens(_SAMPLE_SINGLE_PROMPT)
_BATCH_CHARS, _BATCH_TOKENS = _chars_and_tokens(_SAMPLE_BATCH_PROMPT)

# Bounds are deliberately conservative and hard-enforced by the runner (see
# runner._validate_args), not just documented here.
_BATCH_MAX_LIMIT_PROJECTS = 10

# project_docs_fetch_live_single-specific bounds. These MUST match the
# private _LIVE_SINGLE_* guardrail constants enforced at execution time in
# command_center/runner.py's _execute_live_single — this tuple exists so the
# declared metadata (surfaced via the API/UI) is truthful about what a live
# run will actually accept, instead of the much looser dry_run/print_prompt
# bounds below (which only ever preview, never call subprocess).
_LIVE_SINGLE_DEFAULT_LIMIT = 3
_LIVE_SINGLE_MAX_LIMIT = 5
_LIVE_SINGLE_DEFAULT_TIMEOUT = 60
_LIVE_SINGLE_MAX_TIMEOUT = 120

_PROJECT_DOCS_SINGLE_PARAMS = (
    ParameterSpec(name="opportunity_number", type="str", required=True, help="Opportunity number to fetch docs for."),
    ParameterSpec(name="client", type="str", required=False, default="", help="Client name (for prompt context)."),
    ParameterSpec(name="project_name", type="str", required=False, default="", help="Project name (for prompt context)."),
    ParameterSpec(name="limit", type="int", required=False, default=10, help="Max documents to fetch per project."),
    ParameterSpec(name="timeout", type="int", required=False, default=120, help="Timeout in seconds for Gemini CLI."),
    # Declared (but not yet enforced by the runner) so API callers can pass a
    # dry-run-first attestation without tripping "unknown parameter"
    # validation. Real dry-run-first enforcement (checking this references a
    # prior successful project_docs_fetch_dry_run run for the same OppID) is
    # not yet implemented — see command_center/runner.py's execute_command
    # docstring and the API layer's report for this delta.
    ParameterSpec(
        name="dry_run_run_id",
        type="str",
        required=False,
        default=None,
        help="Optional run_id of a prior successful dry-run for the same OppID (not yet enforced).",
    ),
)

# Parameters for project_docs_fetch_live_single specifically: same shape as
# _PROJECT_DOCS_SINGLE_PARAMS (used by the always-safe dry_run/print_prompt
# variants of this command), but with limit/timeout tightened to the actual
# guardrails _execute_live_single enforces at run time, and dry_run_run_id's
# help text updated since it IS enforced for this command (see runner.py).
_PROJECT_DOCS_LIVE_SINGLE_PARAMS = (
    ParameterSpec(name="opportunity_number", type="str", required=True, help="Opportunity number to fetch docs for."),
    ParameterSpec(name="client", type="str", required=False, default="", help="Client name (for prompt context)."),
    ParameterSpec(name="project_name", type="str", required=False, default="", help="Project name (for prompt context)."),
    ParameterSpec(
        name="limit",
        type="int",
        required=False,
        default=_LIVE_SINGLE_DEFAULT_LIMIT,
        minimum=1,
        maximum=_LIVE_SINGLE_MAX_LIMIT,
        help="Max documents to fetch per project (guardrail-bounded).",
    ),
    ParameterSpec(
        name="timeout",
        type="int",
        required=False,
        default=_LIVE_SINGLE_DEFAULT_TIMEOUT,
        minimum=1,
        maximum=_LIVE_SINGLE_MAX_TIMEOUT,
        help="Timeout in seconds for Gemini CLI (guardrail-bounded).",
    ),
    ParameterSpec(
        name="dry_run_run_id",
        type="str",
        required=False,
        default=None,
        help=(
            "Optional run_id of a prior successful project_docs_fetch_dry_run "
            "run for the same OppID. If omitted, the most recent qualifying "
            "dry run within 30 minutes is used instead; if none exists, the "
            "live run is blocked."
        ),
    ),
)

_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        command_id="daily_dry_run",
        label="Daily (dry run)",
        description="Preview the full daily operating loop without writing to the DB or contacting Workspace.",
        category="daily",
        cli_command="daily",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=(
            ParameterSpec(name="target_date", type="str", required=False, default=None, help="Date YYYY-MM-DD, defaults to today."),
        ),
        supports_dry_run=True,
        reads_tables=("projects", "signals", "action_items", "deals", "people", "clients"),
    ),
    CommandSpec(
        command_id="project_memory_report",
        label="Project Memory Report",
        description="Report on project memory quality and completeness. No Gemini calls.",
        category="reporting",
        cli_command="project-memory-report",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=(
            ParameterSpec(name="as_json", type="bool", required=False, default=False),
        ),
        reads_tables=("projects", "project_documents"),
    ),
    CommandSpec(
        command_id="feedback_summary",
        label="Feedback Summary",
        description="Show a summary of all recorded feedback.",
        category="reporting",
        cli_command="feedback summary",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        reads_tables=("feedback", "feedback_events"),
    ),
    CommandSpec(
        command_id="feedback_candidates",
        label="Feedback Learning Candidates",
        description=(
            "List feedback-learning candidate patterns from the "
            "feedback_learning_candidates table. NOTE: no `manager-os feedback "
            "candidates` CLI subcommand exists yet as of this pass — this is a "
            "registry-only placeholder (cli_command is documentation-only) "
            "until that CLI command is actually wired up."
        ),
        category="reporting",
        cli_command="feedback candidates",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=(
            ParameterSpec(name="limit", type="int", required=False, default=20),
        ),
        reads_tables=("feedback_learning_candidates",),
    ),
    CommandSpec(
        command_id="people_audit",
        label="People Audit",
        description="Audit people config: canonical names, aliases, untracked, unconfigured.",
        category="reporting",
        cli_command="people-audit",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=(
            ParameterSpec(name="verbose", type="bool", required=False, default=False),
        ),
        reads_tables=("people",),
    ),
    CommandSpec(
        command_id="search_projects",
        label="Search Projects",
        description="Search the project knowledge index (NetSuite Closed-Won Opportunities).",
        category="reporting",
        cli_command="search-projects",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=(
            ParameterSpec(name="query", type="str", required=False, default=""),
            ParameterSpec(name="client", type="str", required=False, default=""),
            ParameterSpec(name="person", type="str", required=False, default=""),
            ParameterSpec(name="technology", type="str", required=False, default=""),
            ParameterSpec(name="project_type", type="str", required=False, default=""),
            ParameterSpec(name="industry", type="str", required=False, default=""),
            ParameterSpec(name="sales_rep", type="str", required=False, default=""),
            ParameterSpec(name="status", type="str", required=False, default=""),
            ParameterSpec(name="year", type="int", required=False, default=None),
            ParameterSpec(name="close_after", type="str", required=False, default=""),
            ParameterSpec(name="close_before", type="str", required=False, default=""),
            ParameterSpec(name="opportunity_number", type="str", required=False, default=""),
            ParameterSpec(name="document_type", type="str", required=False, default=""),
            ParameterSpec(name="limit", type="int", required=False, default=20),
            ParameterSpec(name="as_json", type="bool", required=False, default=False),
        ),
        reads_tables=("projects", "project_documents"),
    ),
    CommandSpec(
        command_id="llm_doctor_no_smoke",
        label="LLM Doctor (no smoke test)",
        description=(
            "Diagnose the Gemini CLI binary/config WITHOUT sending a live "
            "smoke-test prompt. `manager-os llm-doctor` defaults --smoke-test "
            "to True (a real live call); this command hardcodes "
            "--no-smoke-test in build_argv so it can never make that live "
            "call regardless of caller-supplied params."
        ),
        category="diagnostics",
        cli_command="llm-doctor --no-smoke-test",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=(
            ParameterSpec(name="timeout", type="int", required=False, default=60),
        ),
    ),
    CommandSpec(
        command_id="project_docs_fetch_dry_run",
        label="Project Docs Fetch (dry run)",
        description="Preview a single-project Drive document search without calling Gemini.",
        category="project_docs",
        cli_command="project-docs-fetch",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=_PROJECT_DOCS_SINGLE_PARAMS,
        supports_dry_run=True,
        estimated_prompt_chars=_SINGLE_CHARS,
        estimated_input_tokens=_SINGLE_TOKENS,
        reads_tables=("projects",),
    ),
    CommandSpec(
        command_id="project_docs_fetch_print_prompt",
        label="Project Docs Fetch (print prompt)",
        description="Print the exact Gemini prompt that would be sent for a single project, without calling Gemini.",
        category="project_docs",
        cli_command="project-docs-fetch",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=_PROJECT_DOCS_SINGLE_PARAMS,
        supports_print_prompt=True,
        estimated_prompt_chars=_SINGLE_CHARS,
        estimated_input_tokens=_SINGLE_TOKENS,
        reads_tables=("projects",),
    ),
    CommandSpec(
        command_id="project_docs_fetch_live_single",
        label="Project Docs Fetch (live, single project)",
        description="Search Google Drive (via Gemini CLI) for a single project's documents and write results to project_documents.",
        category="project_docs",
        cli_command="project-docs-fetch",
        risk_level=RiskLevel.external_bounded,
        external_call_risk=ExternalCallRisk.likely,
        parameters=_PROJECT_DOCS_LIVE_SINGLE_PARAMS,
        supports_dry_run=False,
        requires_confirmation=True,
        dry_run_required_before_live=True,
        related_dry_run_command="project_docs_fetch_dry_run",
        related_print_prompt_command="project_docs_fetch_print_prompt",
        max_scope=_LIVE_SINGLE_MAX_LIMIT,
        bounded_param="limit",
        estimated_prompt_chars=_SINGLE_CHARS,
        estimated_input_tokens=_SINGLE_TOKENS,
        reads_tables=("projects",),
        writes_tables=("project_documents",),
    ),
    CommandSpec(
        command_id="project_docs_fetch_batch_dry_run",
        label="Project Docs Fetch — Batch (dry run)",
        description="Preview a multi-project Drive document search without calling Gemini.",
        category="project_docs",
        cli_command="project-docs-fetch --batch",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=(
            ParameterSpec(name="limit_projects", type="int", required=False, default=5, help="Max projects to include."),
            ParameterSpec(name="projects", type="list", required=False, default=None, help="Optional real project dicts (for accurate estimation only; never passed to argv)."),
            ParameterSpec(name="timeout", type="int", required=False, default=120),
            ParameterSpec(name="force", type="bool", required=False, default=False),
        ),
        supports_dry_run=True,
        estimated_prompt_chars=_BATCH_CHARS,
        estimated_input_tokens=_BATCH_TOKENS,
        reads_tables=("projects",),
    ),
    CommandSpec(
        command_id="project_docs_fetch_batch_print_prompt",
        label="Project Docs Fetch — Batch (print prompt)",
        description="Print the exact batch Gemini prompt without calling Gemini.",
        category="project_docs",
        cli_command="project-docs-fetch --batch",
        risk_level=RiskLevel.local_safe,
        external_call_risk=ExternalCallRisk.none,
        parameters=(
            ParameterSpec(name="limit_projects", type="int", required=False, default=5),
            ParameterSpec(name="projects", type="list", required=False, default=None, help="Optional real project dicts (for accurate estimation only; never passed to argv)."),
            ParameterSpec(name="timeout", type="int", required=False, default=120),
            ParameterSpec(name="force", type="bool", required=False, default=False),
        ),
        supports_print_prompt=True,
        estimated_prompt_chars=_BATCH_CHARS,
        estimated_input_tokens=_BATCH_TOKENS,
        reads_tables=("projects",),
    ),
    CommandSpec(
        command_id="project_docs_fetch_batch_live_bounded",
        label="Project Docs Fetch — Batch (live, bounded)",
        description="Search Google Drive (via Gemini CLI) for up to max_scope projects' documents in one call and write results to project_documents.",
        category="project_docs",
        cli_command="project-docs-fetch --batch",
        risk_level=RiskLevel.external_bounded,
        external_call_risk=ExternalCallRisk.likely,
        parameters=(
            ParameterSpec(name="limit_projects", type="int", required=True, help="Max projects to include (hard-bounded)."),
            ParameterSpec(name="projects", type="list", required=False, default=None, help="Optional real project dicts (for accurate estimation only; never passed to argv)."),
            ParameterSpec(name="timeout", type="int", required=False, default=120),
            ParameterSpec(name="force", type="bool", required=False, default=False),
        ),
        supports_dry_run=True,
        requires_confirmation=True,
        dry_run_required_before_live=True,
        max_scope=_BATCH_MAX_LIMIT_PROJECTS,
        bounded_param="limit_projects",
        estimated_prompt_chars=_BATCH_CHARS,
        estimated_input_tokens=_BATCH_TOKENS,
        reads_tables=("projects",),
        writes_tables=("project_documents",),
    ),
    # -----------------------------------------------------------------
    # Blocked (not runnable). Registered for visibility only. The runner
    # rejects validate_request/build_argv for these before doing anything.
    # -----------------------------------------------------------------
    CommandSpec(
        command_id="retrieve_forecast",
        label="Retrieve Forecast (Workspace)",
        description=(
            "Broad live Google Sheets retrieval via Gemini CLI, invoked "
            "internally by `daily`/`ingest --workspace-fetch` (not a "
            "standalone CLI command). Not runnable from the command center."
        ),
        category="workspace",
        cli_command="(internal) manager_os.ingest.workspace_gemini.retrieve_forecast — not a CLI command",
        risk_level=RiskLevel.blocked,
        external_call_risk=ExternalCallRisk.high,
        requires_confirmation=True,
        writes_tables=("staffing_forecast",),
    ),
    CommandSpec(
        command_id="retrieve_calendar",
        label="Retrieve Calendar (Workspace)",
        description=(
            "Broad live Google Calendar retrieval via Gemini CLI, invoked "
            "internally by `daily`/`ingest --workspace-fetch` (not a "
            "standalone CLI command). Not runnable from the command center."
        ),
        category="workspace",
        cli_command="(internal) manager_os.ingest.workspace_gemini.retrieve_calendar — not a CLI command",
        risk_level=RiskLevel.blocked,
        external_call_risk=ExternalCallRisk.high,
        requires_confirmation=True,
        writes_tables=("meetings",),
    ),
    CommandSpec(
        command_id="retrieve_activity",
        label="Retrieve Activity (Workspace)",
        description=(
            "Broad live Google Chat activity retrieval via Gemini CLI, "
            "invoked internally by `daily`/`ingest --workspace-fetch` (not a "
            "standalone CLI command). Not runnable from the command center."
        ),
        category="workspace",
        cli_command="(internal) manager_os.ingest.workspace_gemini.retrieve_activity — not a CLI command",
        risk_level=RiskLevel.blocked,
        external_call_risk=ExternalCallRisk.high,
        requires_confirmation=True,
    ),
    CommandSpec(
        command_id="workspace_fetch_deal_docs",
        label="Workspace Fetch Deal Docs",
        description=(
            "Broad live Google Drive retrieval across all deals via Gemini "
            "CLI. Not runnable from the command center."
        ),
        category="workspace",
        cli_command="workspace-fetch-deal-docs",
        risk_level=RiskLevel.blocked,
        external_call_risk=ExternalCallRisk.high,
        requires_confirmation=True,
        writes_tables=("deal_documents",),
    ),
)

_REGISTRY: dict[str, CommandSpec] = {c.command_id: c for c in _COMMANDS}


def get(command_id: str) -> CommandSpec:
    """Return the CommandSpec for command_id, or raise CommandNotFoundError."""
    try:
        return _REGISTRY[command_id]
    except KeyError:
        raise CommandNotFoundError(command_id) from None


def list_command_ids() -> list[str]:
    return list(_REGISTRY.keys())


def all_specs() -> list[CommandSpec]:
    return list(_REGISTRY.values())
