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
