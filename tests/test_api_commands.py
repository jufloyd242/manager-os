"""Contract tests for the Command Center API endpoints.

Covers:
- GET /api/commands
- GET /api/commands/{command_id}
- POST /api/commands/{command_id}/validate
- POST /api/commands/{command_id}/run
- GET /api/runs
- GET /api/runs/{run_id}
- GET /api/runs/{run_id}/logs

Every /run test mocks `manager_os.command_center.runner.subprocess.run` (the
same patch target used in tests/test_command_center_runner.py) so nothing
real ever executes, and `_run_gemini_retrieval` is asserted un-called as an
extra live-call guardrail.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from manager_os.api.app import create_app


def _client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    monkeypatch.setenv("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false")
    return TestClient(create_app()), db_path


def _mock_completed(returncode=0, stdout="ok\n", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# GET /api/commands
# ---------------------------------------------------------------------------


def test_list_commands_returns_registry(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/commands")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0
    ids = {c["command_id"] for c in body}
    assert "daily_dry_run" in ids
    for spec in body:
        assert "command_id" in spec
        assert "risk_level" in spec
        assert "external_call_risk" in spec


def test_command_spec_includes_risk_and_token_fields(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/commands")

    body = resp.json()
    spec = next(c for c in body if c["command_id"] == "project_docs_fetch_dry_run")
    assert spec["risk_level"] == "local_safe"
    assert spec["external_call_risk"] == "none"
    assert spec["estimated_input_tokens"] is not None
    assert spec["supports_dry_run"] is True
    assert isinstance(spec["parameters"], list)


def test_get_unknown_command_returns_404(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/commands/does_not_exist")

    assert resp.status_code == 404


def test_get_known_command_returns_spec(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/commands/daily_dry_run")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["command_id"] == "daily_dry_run"


# ---------------------------------------------------------------------------
# POST /api/commands/{command_id}/validate
# ---------------------------------------------------------------------------


def test_validate_safe_dry_run_command_returns_ok_and_argv_preview(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_dry_run/validate",
        json={"params": {"opportunity_number": "OPP031267"}},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["argv_preview"], list)
    assert "--dry-run" in body["argv_preview"]
    assert body["risk_level"] == "local_safe"
    assert body["external_call_risk"] == "none"
    assert body["requires_confirmation"] is False


def test_validate_invalid_params_returns_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_dry_run/validate",
        json={"params": {}},
    )

    assert resp.status_code in (400, 422)


def test_validate_unknown_command_returns_404(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post("/api/commands/does_not_exist/validate", json={"params": {}})

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/commands/{command_id}/run
# ---------------------------------------------------------------------------


def test_run_safe_command_persists_and_returns_run_id(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(),
    ) as mock_run, patch(
        "manager_os.ingest.workspace_gemini._run_gemini_retrieval"
    ) as mock_gemini:
        resp = client.post(
            "/api/commands/daily_dry_run/run",
            json={"params": {}, "confirm": False},
        )

    assert resp.status_code == 200, resp.text
    mock_run.assert_called_once()
    mock_gemini.assert_not_called()
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "success"
    assert body["command_id"] == "daily_dry_run"
    assert body["run_id"]


def test_run_blocked_command_does_not_execute(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        resp = client.post(
            "/api/commands/project_docs_fetch_batch_live_bounded/run",
            json={"params": {"limit_projects": 3}, "confirm": True},
        )

    mock_run.assert_not_called()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "blocked"


def test_run_unknown_command_returns_404(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        resp = client.post(
            "/api/commands/does_not_exist/run",
            json={"params": {}, "confirm": False},
        )

    mock_run.assert_not_called()
    assert resp.status_code == 404


def test_run_invalid_params_returns_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run:
        resp = client.post(
            "/api/commands/project_docs_fetch_dry_run/run",
            json={"params": {}, "confirm": False},
        )

    mock_run.assert_not_called()
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Guarded live execution: project_docs_fetch_live_single
#
# NOTE on scope: `command_center` internals (registry.py) received one tiny,
# purely-additive change as part of this work (see report): an optional
# `dry_run_run_id` str parameter was declared on the shared project-docs-
# fetch parameter tuple so the API can accept it on a request body without
# tripping "unknown parameter" validation. The contract's other new
# guardrails (tightening `limit`'s max_scope to 5, adding a timeout ceiling,
# real dry-run-first enforcement, adding this command to the executable
# allowlist) are Agent A's concurrent command_center work and had not
# landed as of this session — tightening them here would require non-tiny,
# cross-cutting changes (e.g. lowering the shared default `limit` so it
# still fits under a new bound) that risk breaking other committed tests
# (see test_command_center_runner.py::test_execute_live_single_confirm_true_still_blocked_in_this_phase).
# Tests below assert TODAY'S actual behavior and are clearly annotated with
# the delta versus the target contract; see the final report for a full list.
# ---------------------------------------------------------------------------


def test_validate_live_single_valid_params_returns_guardrail_fields(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"opportunity_number": "OPP031267", "limit": 3, "timeout": 60}},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["command_id"] == "project_docs_fetch_live_single"
    assert body["risk_level"] == "external_bounded"
    assert body["external_call_risk"] == "likely"
    assert body["requires_confirmation"] is True
    assert body["dry_run_required_before_live"] is True
    assert isinstance(body["estimated_input_tokens"], int)
    assert "estimated_output_tokens" in body
    assert isinstance(body["argv_preview"], list)
    assert body["warnings"] == []


def test_validate_live_single_missing_opportunity_number_rejected(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"limit": 3, "timeout": 60}},
    )

    assert resp.status_code in (400, 422)


def test_validate_live_single_limit_6_not_yet_bounded_to_5(tmp_path, monkeypatch):
    # DELTA (see report): the registry's current max_scope for this command
    # is 20 (bounded_param="limit"), not the contract's target of 5 — that
    # tightening is part of Agent A's guardrail work and had not landed as
    # of this session. Lowering the shared default (currently 10, used by
    # the dry_run/print_prompt variants too) to fit under a new max_scope=5
    # is itself a non-trivial, cross-command change, not a tiny one, so it's
    # left out of scope here. This test documents ACTUAL current behavior
    # (limit=6 is within max_scope=20, so it's accepted) rather than the
    # eventually-desired rejection, per "match whatever build_argv/validation
    # actually does" guidance. Once Agent A's tighter bound lands, flip this
    # assertion to expect 400/422.
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"opportunity_number": "OPP031267", "limit": 6, "timeout": 60}},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_validate_live_single_timeout_121_not_yet_bounded(tmp_path, monkeypatch):
    # DELTA (see report): command_center has no timeout ceiling mechanism
    # today — only one bounded_param per command is supported, already used
    # for `limit`. Adding a second bound is real command_center feature work
    # (Agent A's guardrail scope), not a tiny fix, so it's out of scope here.
    # This test documents the ACTUAL current behavior (timeout=121 accepted)
    # per "match whatever build_argv/validation actually does" guidance.
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"opportunity_number": "OPP031267", "limit": 3, "timeout": 121}},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_run_live_single_without_confirm_blocked(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run, patch(
        "manager_os.ingest.workspace_gemini._run_gemini_retrieval"
    ) as mock_gemini:
        resp = client.post(
            "/api/commands/project_docs_fetch_live_single/run",
            json={
                "params": {"opportunity_number": "OPP031267", "limit": 3, "timeout": 60},
                "confirm": False,
            },
        )

    mock_run.assert_not_called()
    mock_gemini.assert_not_called()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "blocked"


def test_run_live_single_confirm_true_no_dry_run_id_blocked(tmp_path, monkeypatch):
    # As of this session, command_center's execute_command allowlist does
    # not yet include project_docs_fetch_live_single (see
    # test_execute_live_single_confirm_true_still_blocked_in_this_phase) —
    # it is unconditionally blocked regardless of confirm, which also covers
    # the "no qualifying prior dry-run" case for now.
    client, _ = _client(tmp_path, monkeypatch)

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run, patch(
        "manager_os.ingest.workspace_gemini._run_gemini_retrieval"
    ) as mock_gemini:
        resp = client.post(
            "/api/commands/project_docs_fetch_live_single/run",
            json={
                "params": {"opportunity_number": "OPP031267", "limit": 3, "timeout": 60},
                "confirm": True,
            },
        )

    mock_run.assert_not_called()
    mock_gemini.assert_not_called()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "blocked"


def test_run_live_single_confirm_true_with_dry_run_id_still_blocked_pending_guardrail_landing(
    tmp_path, monkeypatch
):
    # CONTRACT (not yet implemented in command_center as of this session):
    # confirm=true + a valid dry_run_run_id referencing a prior successful
    # project_docs_fetch_dry_run run for the same OppID should allow this to
    # execute and return status="success" when the mocked subprocess layer
    # succeeds. Today, project_docs_fetch_live_single is still unconditionally
    # blocked (not in runner._EXECUTABLE_COMMAND_IDS, no dry-run-first check
    # exists yet) — asserting that real, current, safe-by-default behavior
    # here rather than reimplementing the guardrail in the API layer. Update
    # this test to assert status=="success" once Agent A's mechanism lands.
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(),
    ) as mock_run, patch(
        "manager_os.ingest.workspace_gemini._run_gemini_retrieval"
    ) as mock_gemini:
        resp = client.post(
            "/api/commands/project_docs_fetch_live_single/run",
            json={
                "params": {
                    "opportunity_number": "OPP031267",
                    "limit": 3,
                    "timeout": 60,
                    "dry_run_run_id": "some-prior-dry-run-id",
                },
                "confirm": True,
            },
        )

    mock_run.assert_not_called()
    mock_gemini.assert_not_called()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "blocked"


def test_run_live_single_blocked_attempt_is_persisted(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run, patch(
        "manager_os.ingest.workspace_gemini._run_gemini_retrieval"
    ):
        resp = client.post(
            "/api/commands/project_docs_fetch_live_single/run",
            json={"params": {"opportunity_number": "OPP031267"}, "confirm": True},
        )
    mock_run.assert_not_called()
    run_id = resp.json()["run_id"]

    list_resp = client.get("/api/runs", params={"limit": 50})

    assert list_resp.status_code == 200, list_resp.text
    ids = {r["run_id"] for r in list_resp.json()["runs"]}
    assert run_id in ids


def test_run_batch_live_bounded_regression_still_blocked(tmp_path, monkeypatch):
    """Regression guard: project_docs_fetch_batch_live_bounded must remain
    blocked, unaffected by the project_docs_fetch_live_single guardrail work
    added in this change."""
    client, _ = _client(tmp_path, monkeypatch)

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run, patch(
        "manager_os.ingest.workspace_gemini._run_gemini_retrieval"
    ) as mock_gemini:
        resp = client.post(
            "/api/commands/project_docs_fetch_batch_live_bounded/run",
            json={"params": {"limit_projects": 3}, "confirm": True},
        )

    mock_run.assert_not_called()
    mock_gemini.assert_not_called()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "blocked"


def test_run_workspace_fetch_deal_docs_regression_still_blocked(tmp_path, monkeypatch):
    """Regression guard: any risk_level=blocked command (e.g.
    workspace_fetch_deal_docs) must remain blocked."""
    client, _ = _client(tmp_path, monkeypatch)

    with patch("manager_os.command_center.runner.subprocess.run") as mock_run, patch(
        "manager_os.ingest.workspace_gemini._run_gemini_retrieval"
    ) as mock_gemini:
        resp = client.post(
            "/api/commands/workspace_fetch_deal_docs/run",
            json={"params": {}, "confirm": True},
        )

    mock_run.assert_not_called()
    mock_gemini.assert_not_called()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "blocked"


# ---------------------------------------------------------------------------
# GET /api/runs, GET /api/runs/{run_id}, GET /api/runs/{run_id}/logs
# ---------------------------------------------------------------------------


def test_list_runs_returns_persisted_runs(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(stdout="hello\n"),
    ):
        run_resp = client.post(
            "/api/commands/daily_dry_run/run",
            json={"params": {}, "confirm": False},
        )
    run_id = run_resp.json()["run_id"]

    resp = client.get("/api/runs", params={"limit": 50})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["runs"], list)
    ids = {r["run_id"] for r in body["runs"]}
    assert run_id in ids


def test_get_run_returns_single_run(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(),
    ):
        run_resp = client.post(
            "/api/commands/daily_dry_run/run",
            json={"params": {}, "confirm": False},
        )
    run_id = run_resp.json()["run_id"]

    resp = client.get(f"/api/runs/{run_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["command_id"] == "daily_dry_run"


def test_get_unknown_run_returns_404(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/runs/does-not-exist")

    assert resp.status_code == 404


def test_get_run_logs_returns_stdout_stderr(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(stdout="out text\n", stderr="err text\n"),
    ):
        run_resp = client.post(
            "/api/commands/daily_dry_run/run",
            json={"params": {}, "confirm": False},
        )
    run_id = run_resp.json()["run_id"]

    resp = client.get(f"/api/runs/{run_id}/logs")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stdout"] == "out text\n"
    assert body["stderr"] == "err text\n"
    assert "error" in body


def test_get_unknown_run_logs_returns_404(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/runs/does-not-exist/logs")

    assert resp.status_code == 404
