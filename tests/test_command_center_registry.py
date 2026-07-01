"""Tests for the command center registry: known commands, risk classification.

Invariants covered (see task spec):
2. Every command has a risk_level.
3. Every command declares external_call_risk explicitly.
4. Commands with external_call_risk in (likely, high) require_confirmation=True.
6. Token estimate exists for any command where external_call_risk is not "none"
   (also checked for the print-prompt variants which are local_safe but still
   have a real prompt).
9. Blocked commands are flagged risk_level=blocked (actual rejection is
   tested in test_command_center_runner.py).
12. Unknown command_id gives a clear error.
"""

from __future__ import annotations

import pytest

from manager_os.command_center import registry
from manager_os.command_center.errors import CommandNotFoundError
from manager_os.command_center.models import ExternalCallRisk, RiskLevel

EXPECTED_COMMAND_IDS = {
    "daily_dry_run",
    "project_memory_report",
    "feedback_summary",
    "feedback_candidates",
    "people_audit",
    "search_projects",
    "llm_doctor_no_smoke",
    "project_docs_fetch_dry_run",
    "project_docs_fetch_print_prompt",
    "project_docs_fetch_live_single",
    "project_docs_fetch_batch_dry_run",
    "project_docs_fetch_batch_print_prompt",
    "project_docs_fetch_batch_live_bounded",
    "retrieve_forecast",
    "retrieve_calendar",
    "retrieve_activity",
    "workspace_fetch_deal_docs",
}

BLOCKED_COMMAND_IDS = {
    "retrieve_forecast",
    "retrieve_calendar",
    "retrieve_activity",
    "workspace_fetch_deal_docs",
}


def test_registry_lists_all_known_commands():
    assert set(registry.list_command_ids()) == EXPECTED_COMMAND_IDS


def test_every_command_has_a_risk_level():
    for spec in registry.all_specs():
        assert spec.risk_level is not None
        assert isinstance(spec.risk_level, RiskLevel)


def test_every_command_declares_external_call_risk_explicitly():
    for spec in registry.all_specs():
        assert spec.external_call_risk is not None
        assert isinstance(spec.external_call_risk, ExternalCallRisk)


def test_likely_or_high_risk_commands_require_confirmation():
    for spec in registry.all_specs():
        if spec.external_call_risk in (ExternalCallRisk.likely, ExternalCallRisk.high):
            assert spec.requires_confirmation is True, spec.command_id


def test_no_command_exposes_a_raw_shell_parameter():
    forbidden = {"raw_command", "shell", "cmd", "command_line", "shell_command", "raw_args"}
    for spec in registry.all_specs():
        for param in spec.parameters:
            assert param.name not in forbidden, (spec.command_id, param.name)
            assert "shell" not in param.name.lower()


@pytest.mark.parametrize(
    "command_id",
    [
        "project_docs_fetch_live_single",
        "project_docs_fetch_batch_live_bounded",
        "project_docs_fetch_print_prompt",
        "project_docs_fetch_batch_print_prompt",
    ],
)
def test_token_estimate_present_for_project_docs_commands(command_id):
    spec = registry.get(command_id)
    assert spec.estimated_input_tokens is not None
    assert spec.estimated_prompt_chars is not None


def test_no_prompt_command_has_no_token_estimate():
    spec = registry.get("project_memory_report")
    assert spec.estimated_prompt_chars is None
    assert spec.estimated_input_tokens is None


def test_blocked_commands_have_blocked_risk_level():
    for command_id in BLOCKED_COMMAND_IDS:
        spec = registry.get(command_id)
        assert spec.risk_level == RiskLevel.blocked


def test_non_blocked_commands_have_none_or_bounded_risk():
    for spec in registry.all_specs():
        if spec.command_id not in BLOCKED_COMMAND_IDS:
            assert spec.risk_level != RiskLevel.blocked


def test_live_single_and_batch_are_bounded():
    single = registry.get("project_docs_fetch_live_single")
    batch = registry.get("project_docs_fetch_batch_live_bounded")
    assert single.max_scope is not None
    assert batch.max_scope is not None


def test_unknown_command_id_raises_clear_error():
    with pytest.raises(CommandNotFoundError):
        registry.get("nonexistent_command")


# ---------------------------------------------------------------------------
# project_docs_fetch_live_single metadata must truthfully match the runtime
# guardrails already enforced in runner.py's _execute_live_single (limit
# default=3/max=5, timeout default=60/max=120, required opportunity_number,
# requires_confirmation, dry_run_required_before_live, supports_dry_run=False,
# related_dry_run_command/related_print_prompt_command) — previously this
# command shared a param spec with project_docs_fetch_dry_run/print_prompt
# and declared the wrong (looser) limit/timeout defaults and max.
# ---------------------------------------------------------------------------


def test_live_single_limit_param_default_and_max():
    spec = registry.get("project_docs_fetch_live_single")
    limit_param = spec.get_parameter("limit")
    assert limit_param.default == 3
    assert limit_param.maximum == 5


def test_live_single_timeout_param_default_and_max():
    spec = registry.get("project_docs_fetch_live_single")
    timeout_param = spec.get_parameter("timeout")
    assert timeout_param.default == 60
    assert timeout_param.maximum == 120


def test_live_single_requires_opportunity_number():
    spec = registry.get("project_docs_fetch_live_single")
    opp_param = spec.get_parameter("opportunity_number")
    assert opp_param.required is True
    assert opp_param.type == "str"


def test_live_single_requires_confirmation():
    spec = registry.get("project_docs_fetch_live_single")
    assert spec.requires_confirmation is True


def test_live_single_requires_dry_run_before_live():
    spec = registry.get("project_docs_fetch_live_single")
    assert spec.dry_run_required_before_live is True


def test_live_single_related_dry_run_command():
    spec = registry.get("project_docs_fetch_live_single")
    assert spec.related_dry_run_command == "project_docs_fetch_dry_run"


def test_live_single_related_print_prompt_command():
    spec = registry.get("project_docs_fetch_live_single")
    assert spec.related_print_prompt_command == "project_docs_fetch_print_prompt"


def test_live_single_does_not_support_dry_run_itself():
    # project_docs_fetch_live_single IS the live command; a dry-run preview
    # is represented by the separate project_docs_fetch_dry_run command_id,
    # not by dry-running this one. Don't confuse "requires a dry run first"
    # (dry_run_required_before_live=True) with "supports being dry-run".
    spec = registry.get("project_docs_fetch_live_single")
    assert spec.supports_dry_run is False


def test_live_single_has_no_batch_params():
    spec = registry.get("project_docs_fetch_live_single")
    names = spec.parameter_names()
    assert "limit_projects" not in names
    assert "projects" not in names


# --- Regression: sibling commands' own metadata must be unaffected. ---


def test_project_docs_fetch_dry_run_metadata_unchanged():
    spec = registry.get("project_docs_fetch_dry_run")
    limit_param = spec.get_parameter("limit")
    timeout_param = spec.get_parameter("timeout")
    assert limit_param.default == 10
    assert timeout_param.default == 120
    assert spec.supports_dry_run is True
    assert spec.risk_level == RiskLevel.local_safe
    assert spec.external_call_risk == ExternalCallRisk.none


def test_project_docs_fetch_print_prompt_metadata_unchanged():
    spec = registry.get("project_docs_fetch_print_prompt")
    limit_param = spec.get_parameter("limit")
    timeout_param = spec.get_parameter("timeout")
    assert limit_param.default == 10
    assert timeout_param.default == 120
    assert spec.supports_print_prompt is True
    assert spec.risk_level == RiskLevel.local_safe
    assert spec.external_call_risk == ExternalCallRisk.none


def test_batch_live_bounded_metadata_unaffected_regression():
    spec = registry.get("project_docs_fetch_batch_live_bounded")
    assert spec.risk_level == RiskLevel.external_bounded
    assert spec.external_call_risk == ExternalCallRisk.likely
    assert spec.max_scope == 10
    assert spec.bounded_param == "limit_projects"
