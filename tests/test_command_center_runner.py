"""Tests for the command runner: validation, confirmation gating, bounded scope,
blocked-command rejection, safe (non-shell) argv construction, and (execution
phase) actually running allowlisted commands via subprocess with persisted
history.

Invariants covered (see task spec):
5. No arbitrary shell text accepted — unknown params rejected.
7. project_docs_fetch_live_single is bounded by --limit.
8. project_docs_fetch_batch_live_bounded is bounded by --limit-projects.
9. Blocked commands can never be run (validate_request AND build_argv reject
   before doing anything).
10. Dry-run commands don't require confirmation.
11. Unknown parameter names are rejected.
12. Unknown command_id gives a clear error.

Execution-phase invariants (execute_command):
E1. Safe command executes through subprocess.run with shell=False and a
    list[str] argv (never a joined shell string).
E2. stdout/stderr/status are captured in the returned result.
E3. A command_runs row is created for a successful run.
E4. A command_runs row is created for a failed run.
E5. A blocked command_id cannot execute (subprocess.run not called), but a
    row IS persisted recording the blocked attempt.
E6. An unknown command_id cannot execute — clear error, nothing persisted.
E7. A raw "shell text" param is rejected before argv construction.
E8. Timeout is enforced and persisted as status="timeout".
E9. A live/external command (not in the phase-1 allowlist) is rejected
    regardless of confirm, and never reaches subprocess.run.
E10. project_docs_fetch_dry_run requires opportunity_number.
E11. project_docs_fetch_batch_live_bounded is not executable in this phase
     regardless of otherwise-valid params.
E12. One real, unmocked subprocess execution proves the wiring actually works.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from manager_os.command_center import history, registry, runner
from manager_os.command_center.errors import (
    CommandBlockedError,
    CommandNotFoundError,
    ConfirmationRequiredError,
    DryRunRequiredError,
    InvalidArgumentError,
    ScopeExceededError,
)
from manager_os.command_center.runner import execute_command
from manager_os.db import get_connection

REPO_ROOT = Path(__file__).parent.parent


def test_dry_run_allowed_without_confirmation():
    # project_docs_fetch_live_single itself no longer supports_dry_run (that
    # capability lives on the separate project_docs_fetch_dry_run command_id
    # — see test_command_center_registry.py::test_live_single_does_not_support_dry_run_itself).
    req = runner.validate_request(
        "project_docs_fetch_dry_run",
        {"opportunity_number": "OPP1"},
        dry_run=True,
        confirmed=False,
    )
    assert req.dry_run is True


def test_live_single_dry_run_flag_rejected_since_it_no_longer_supports_dry_run():
    with pytest.raises(InvalidArgumentError):
        runner.validate_request(
            "project_docs_fetch_live_single",
            {"opportunity_number": "OPP1"},
            dry_run=True,
            confirmed=False,
        )


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
    over_limit = (spec.max_scope or 5) + 1
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
    over_limit = (spec.max_scope or 10) + 1
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


# ---------------------------------------------------------------------------
# execute_command — actually running allowlisted commands via subprocess,
# with persisted history for every attempted run.
# ---------------------------------------------------------------------------


def _mock_completed(returncode=0, stdout="ok\n", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


@pytest.fixture()
def cc_conn():
    conn = get_connection(":memory:")
    yield conn
    conn.close()


def test_execute_safe_command_calls_subprocess_with_list_and_shell_false(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run", return_value=_mock_completed()) as mock_run:
        execute_command(cc_conn, "daily_dry_run", {})

    assert mock_run.call_count == 1
    args, kwargs = mock_run.call_args
    called_argv = args[0]
    assert isinstance(called_argv, list)
    assert all(isinstance(part, str) for part in called_argv)
    assert kwargs.get("shell", False) is False


def test_execute_captures_stdout_stderr_status(cc_conn):
    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(returncode=0, stdout="hello\n", stderr="warn\n"),
    ):
        result = execute_command(cc_conn, "daily_dry_run", {})

    assert result["status"] == "success"
    assert result["stdout"] == "hello\n"
    assert result["stderr"] == "warn\n"


def test_execute_success_persists_command_runs_row(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run", return_value=_mock_completed()):
        result = execute_command(cc_conn, "daily_dry_run", {})

    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None
    assert row["status"] == "success"
    assert row["command_id"] == "daily_dry_run"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None


def test_execute_failure_persists_command_runs_row(cc_conn):
    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(returncode=1, stdout="", stderr="boom"),
    ):
        result = execute_command(cc_conn, "daily_dry_run", {})

    assert result["status"] == "failed"
    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None
    assert row["status"] == "failed"
    assert row["stderr"] == "boom"
    assert row["error"] is not None


def test_execute_blocked_command_never_calls_subprocess_but_is_persisted(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(cc_conn, "retrieve_forecast", {})

    mock_run.assert_not_called()
    assert result["status"] == "blocked"
    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None
    assert row["status"] == "blocked"
    assert row["command_id"] == "retrieve_forecast"


def test_execute_batch_live_bounded_blocked_regardless_of_valid_params(cc_conn):
    spec = registry.get("project_docs_fetch_batch_live_bounded")
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn,
            "project_docs_fetch_batch_live_bounded",
            {"limit_projects": spec.max_scope},
            confirm=True,
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"
    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None and row["status"] == "blocked"


def test_execute_live_single_confirm_false_blocked_regardless(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn,
            "project_docs_fetch_live_single",
            {"opportunity_number": "OPP1"},
            confirm=False,
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"


def test_execute_live_single_confirm_true_still_blocked_in_this_phase(cc_conn):
    # confirm=True alone is not sufficient: without a qualifying prior
    # successful project_docs_fetch_dry_run run for this OppID, the live
    # call is still rejected (dry-run-first guardrail).
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn,
            "project_docs_fetch_live_single",
            {"opportunity_number": "OPP1"},
            confirm=True,
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"
    assert "project_docs_fetch_dry_run" in result["error"]


def test_execute_unknown_command_raises_and_persists_nothing(cc_conn):
    before = len(history.list_command_runs(cc_conn, limit=1000))

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        with pytest.raises(CommandNotFoundError):
            execute_command(cc_conn, "does_not_exist", {})

    mock_run.assert_not_called()
    after = len(history.list_command_runs(cc_conn, limit=1000))
    assert after == before


def test_execute_raw_shell_param_rejected_before_subprocess(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        with pytest.raises(InvalidArgumentError):
            execute_command(cc_conn, "daily_dry_run", {"__raw__": "; rm -rf /"})

    mock_run.assert_not_called()


def test_execute_project_docs_fetch_dry_run_missing_opportunity_number_rejected(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        with pytest.raises(InvalidArgumentError):
            execute_command(cc_conn, "project_docs_fetch_dry_run", {})

    mock_run.assert_not_called()


def test_execute_timeout_is_enforced_and_persisted(cc_conn):
    with patch(
        "manager_os.command_center.runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=5),
    ):
        result = execute_command(cc_conn, "daily_dry_run", {}, timeout=5)

    assert result["status"] == "timeout"
    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None
    assert row["status"] == "timeout"



# ---------------------------------------------------------------------------
# project_docs_fetch_live_single — guarded live execution. This is the ONLY
# external_bounded command allowed to actually execute in this phase, and
# only when every guardrail below passes: single string opportunity_number
# (no batch/list), confirm=True, limit<=5 (default 3), timeout<=120
# (default 60), and a qualifying recent (<=30min) successful
# project_docs_fetch_dry_run run for the same normalized OppID.
# ---------------------------------------------------------------------------


def _seed_successful_dry_run(conn, opportunity_number, limit=10, timeout=120):
    """Persist a qualifying successful project_docs_fetch_dry_run row for
    opportunity_number, via the real execute_command path (subprocess
    mocked), so tests can exercise the dry-run-first guardrail honestly."""
    with patch("manager_os.command_center.runner.subprocess.run", return_value=_mock_completed()):
        result = execute_command(
            conn,
            "project_docs_fetch_dry_run",
            {"opportunity_number": opportunity_number, "limit": limit, "timeout": timeout},
        )
    assert result["status"] == "success"
    return result["run_id"]


def test_registry_live_single_risk_and_external_call_risk():
    spec = registry.get("project_docs_fetch_live_single")
    assert spec.risk_level.value == "external_bounded"
    assert spec.external_call_risk.value == "likely"


def test_execute_live_single_missing_opportunity_number_rejected(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(cc_conn, "project_docs_fetch_live_single", {}, confirm=True)

    mock_run.assert_not_called()
    assert result["status"] == "blocked"


def test_execute_live_single_confirm_false_rejected_even_with_qualifying_dry_run(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}, confirm=False
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"


def test_execute_live_single_limit_over_max_rejected(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn,
            "project_docs_fetch_live_single",
            {"opportunity_number": "OPP1", "limit": 6},
            confirm=True,
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"


def test_execute_live_single_timeout_over_max_rejected(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn,
            "project_docs_fetch_live_single",
            {"opportunity_number": "OPP1", "timeout": 121},
            confirm=True,
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"


def test_execute_live_single_batch_flag_rejected(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn,
            "project_docs_fetch_live_single",
            {"opportunity_number": "OPP1", "batch": True},
            confirm=True,
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"


def test_execute_live_single_list_opportunity_number_rejected(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn,
            "project_docs_fetch_live_single",
            {"opportunity_number": ["OPP1", "OPP2"]},
            confirm=True,
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"


def test_execute_live_single_missing_dry_run_first_rejected(cc_conn):
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}, confirm=True
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"
    assert "project_docs_fetch_dry_run" in result["error"]


def test_execute_live_single_qualifying_dry_run_allows_execution(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch(
        "manager_os.command_center.runner.subprocess.run", return_value=_mock_completed()
    ) as mock_run:
        result = execute_command(
            cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}, confirm=True
        )

    mock_run.assert_called_once()
    assert result["status"] == "success"


def test_execute_live_single_argv_shape_when_guardrails_pass(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch(
        "manager_os.command_center.runner.subprocess.run", return_value=_mock_completed()
    ) as mock_run:
        execute_command(
            cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "opp1"}, confirm=True
        )

    args, kwargs = mock_run.call_args
    argv = args[0]
    assert argv == [
        sys.executable, "-m", "manager_os.cli",
        "project-docs-fetch",
        "--opportunity-number", "OPP1",
        "--limit", "3",
        "--timeout", "60",
        "--verbose",
    ]
    assert kwargs.get("shell", False) is False


def test_execute_live_single_missing_confirm_persists_blocked_row(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}, confirm=False
        )

    mock_run.assert_not_called()
    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None
    assert row["status"] == "blocked"
    assert row["command_id"] == "project_docs_fetch_live_single"


def test_execute_live_single_subprocess_success_persists_success(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(returncode=0),
    ):
        result = execute_command(
            cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}, confirm=True
        )

    assert result["status"] == "success"
    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None
    assert row["status"] == "success"


def test_execute_live_single_subprocess_nonzero_persists_failed(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(returncode=1, stdout="", stderr="boom"),
    ):
        result = execute_command(
            cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}, confirm=True
        )

    assert result["status"] == "failed"
    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None
    assert row["status"] == "failed"


def test_execute_live_single_subprocess_timeout_persists_timeout(cc_conn):
    _seed_successful_dry_run(cc_conn, "OPP1")
    with patch(
        "manager_os.command_center.runner.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["x"], timeout=60),
    ):
        result = execute_command(
            cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}, confirm=True
        )

    assert result["status"] == "timeout"
    row = history.get_command_run(cc_conn, result["run_id"])
    assert row is not None
    assert row["status"] == "timeout"


def test_execute_batch_live_bounded_still_blocked_regardless_of_confirm_phase2(cc_conn):
    spec = registry.get("project_docs_fetch_batch_live_bounded")
    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        result = execute_command(
            cc_conn,
            "project_docs_fetch_batch_live_bounded",
            {"limit_projects": spec.max_scope},
            confirm=True,
        )

    mock_run.assert_not_called()
    assert result["status"] == "blocked"


def test_execute_workspace_and_retrieve_commands_still_blocked(cc_conn):
    for command_id in (
        "workspace_fetch_deal_docs", "retrieve_forecast", "retrieve_calendar", "retrieve_activity",
    ):
        with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
            result = execute_command(cc_conn, command_id, {}, confirm=True)
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Registry metadata <-> runner guardrail agreement (registry fix task).
#
# runner._execute_live_single hardcodes limit default=3/max=5 and timeout
# default=60/max=120 as the enforced safety net for project_docs_fetch_live_single
# — these tests confirm the registry's declared ParameterSpec values for
# that command now truthfully describe those same numbers, and that the
# runner's hardcoded checks (unchanged) still agree in practice.
# ---------------------------------------------------------------------------


def test_registry_live_single_limit_max_matches_runner_hardcoded_guardrail():
    spec = registry.get("project_docs_fetch_live_single")
    limit_param = spec.get_parameter("limit")
    assert limit_param is not None
    assert limit_param.maximum == 5
    assert limit_param.default == 3


def test_registry_live_single_timeout_max_matches_runner_hardcoded_guardrail():
    spec = registry.get("project_docs_fetch_live_single")
    timeout_param = spec.get_parameter("timeout")
    assert timeout_param is not None
    assert timeout_param.maximum == 120
    assert timeout_param.default == 60


def test_batch_live_bounded_not_in_executable_allowlist_regression():
    # Regression (unchanged from prior phases): project_docs_fetch_batch_live_bounded
    # must remain non-executable regardless of this registry fix.
    assert "project_docs_fetch_batch_live_bounded" not in runner._EXECUTABLE_COMMAND_IDS


def test_execute_live_single_never_calls_gemini_retrieval(cc_conn):
    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_gemini:
        _seed_successful_dry_run(cc_conn, "OPP1")
        with patch(
            "manager_os.command_center.runner.subprocess.run", return_value=_mock_completed()
        ):
            execute_command(
                cc_conn, "project_docs_fetch_live_single", {"opportunity_number": "OPP1"}, confirm=True
            )

    mock_gemini.assert_not_called()


def test_execute_daily_dry_run_end_to_end_real_subprocess(tmp_path, monkeypatch):
    # No mocking here: proves the wiring truly works. Uses a dedicated temp
    # DB path for the *subprocess's* own DB access (never the real DB), and
    # a separate in-memory connection for this test's own command_runs
    # bookkeeping (avoids any DuckDB file-lock contention between the two
    # separate processes touching the same file).
    sub_db_path = str(tmp_path / "subprocess_test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", sub_db_path)
    monkeypatch.setenv("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false")

    conn = get_connection(":memory:")
    start = time.monotonic()
    result = execute_command(conn, "daily_dry_run", {}, timeout=60)
    elapsed = time.monotonic() - start
    conn.close()

    assert elapsed < 60, "daily_dry_run should be fast/local (took %.1fs)" % elapsed
    assert result["status"] == "success", result["stderr"]
    assert result["argv"][0] == sys.executable
    assert "DRY RUN" in (result["stdout"] or "")
