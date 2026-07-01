"""Tests for the command runner: validation, confirmation gating, bounded scope,
blocked-command rejection, and safe (non-shell) argv construction.

Invariants covered (see task spec):
5. No arbitrary shell text accepted — unknown params rejected.
7. project_docs_fetch_live_single is bounded by --limit.
8. project_docs_fetch_batch_live_bounded is bounded by --limit-projects.
9. Blocked commands can never be run (validate_request AND build_argv reject
   before doing anything).
10. Dry-run commands don't require confirmation.
11. Unknown parameter names are rejected.
12. Unknown command_id gives a clear error.
"""

from __future__ import annotations

import sys

import pytest

from manager_os.command_center import registry, runner
from manager_os.command_center.errors import (
    CommandBlockedError,
    CommandNotFoundError,
    ConfirmationRequiredError,
    DryRunRequiredError,
    InvalidArgumentError,
    ScopeExceededError,
)


def test_dry_run_allowed_without_confirmation():
    req = runner.validate_request(
        "project_docs_fetch_live_single",
        {"opportunity_number": "OPP1"},
        dry_run=True,
        confirmed=False,
    )
    assert req.dry_run is True


def test_live_without_confirmation_is_rejected():
    with pytest.raises((ConfirmationRequiredError, DryRunRequiredError)):
        runner.validate_request(
            "project_docs_fetch_live_single",
            {"opportunity_number": "OPP1"},
            dry_run=False,
            confirmed=False,
        )


@pytest.mark.parametrize(
    "dry_run,confirmed",
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_blocked_command_always_rejected(dry_run, confirmed):
    with pytest.raises(CommandBlockedError):
        runner.validate_request("retrieve_forecast", {}, dry_run=dry_run, confirmed=confirmed)


def test_blocked_command_build_argv_rejected():
    with pytest.raises(CommandBlockedError):
        runner.build_argv("retrieve_forecast", {})


def test_live_single_missing_opportunity_number_rejected():
    with pytest.raises(InvalidArgumentError):
        runner.validate_request("project_docs_fetch_live_single", {}, dry_run=False, confirmed=True)


def test_live_single_list_opportunity_number_rejected():
    with pytest.raises(InvalidArgumentError):
        runner.validate_request(
            "project_docs_fetch_live_single",
            {"opportunity_number": ["OPP1", "OPP2"]},
            dry_run=False,
            confirmed=True,
        )


def test_live_single_over_limit_rejected():
    spec = registry.get("project_docs_fetch_live_single")
    over_limit = spec.max_scope + 1
    with pytest.raises(ScopeExceededError):
        runner.validate_request(
            "project_docs_fetch_live_single",
            {"opportunity_number": "OPP1", "limit": over_limit},
            dry_run=False,
            confirmed=True,
        )


def test_batch_live_bounded_missing_limit_projects_rejected():
    with pytest.raises(InvalidArgumentError):
        runner.validate_request("project_docs_fetch_batch_live_bounded", {}, dry_run=False, confirmed=True)


def test_batch_live_bounded_over_max_scope_rejected():
    spec = registry.get("project_docs_fetch_batch_live_bounded")
    over_limit = spec.max_scope + 1
    with pytest.raises(ScopeExceededError):
        runner.validate_request(
            "project_docs_fetch_batch_live_bounded",
            {"limit_projects": over_limit},
            dry_run=False,
            confirmed=True,
        )


def test_batch_live_bounded_within_scope_accepted():
    spec = registry.get("project_docs_fetch_batch_live_bounded")
    req = runner.validate_request(
        "project_docs_fetch_batch_live_bounded",
        {"limit_projects": spec.max_scope},
        dry_run=False,
        confirmed=True,
    )
    assert req.dry_run is False


def test_unknown_command_id_raises_clear_error():
    with pytest.raises(CommandNotFoundError):
        runner.validate_request("does_not_exist", {})


def test_unknown_parameter_rejected():
    with pytest.raises(InvalidArgumentError):
        runner.validate_request("daily_dry_run", {"raw_args": "; rm -rf /"})


def test_unknown_parameter_rejected_in_build_argv_too():
    with pytest.raises(InvalidArgumentError):
        runner.build_argv("daily_dry_run", {"raw_args": "; rm -rf /"})


def test_wrong_type_parameter_rejected():
    with pytest.raises(InvalidArgumentError):
        runner.validate_request("project_memory_report", {"as_json": "yes"})


def test_build_argv_is_list_of_strings():
    argv = runner.build_argv("daily_dry_run", {})
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)
    assert len(argv) > 1


def test_build_argv_daily_dry_run_exact_shape():
    argv = runner.build_argv("daily_dry_run", {})
    assert argv == [
        sys.executable, "-m", "manager_os.cli",
        "daily", "--dry-run", "--no-workspace", "--skip-project-index",
    ]


def test_build_argv_project_docs_fetch_live_single_no_dry_run_flags():
    argv = runner.build_argv(
        "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}
    )
    assert "--opportunity-number" in argv
    assert "OPP1" in argv
    assert "--dry-run" not in argv
    assert "--print-prompt" not in argv


def test_build_argv_batch_live_bounded_shape():
    argv = runner.build_argv(
        "project_docs_fetch_batch_live_bounded", {"limit_projects": 5}
    )
    assert "--batch" in argv
    assert "--limit-projects" in argv
    assert "5" in argv
    assert "--dry-run" not in argv
    assert "--print-prompt" not in argv


def test_build_argv_never_contains_a_joined_multi_flag_string():
    argv = runner.build_argv("search_projects", {"query": "foo bar", "client": "Acme"})
    for part in argv:
        assert not part.startswith("manager-os ")
        assert "--" not in part or part.startswith("--")
