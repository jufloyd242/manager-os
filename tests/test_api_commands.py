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
# registry.py's declared metadata for this command now truthfully matches
# the runtime guardrails enforced in runner.py's _execute_live_single: limit
# default=3/maximum=5, timeout default=60/maximum=120, plus
# related_dry_run_command/related_print_prompt_command and
# supports_dry_run=False (dry-run is a separate command_id, not a mode
# toggle on this one). See the final report for the exact fields changed.
# ---------------------------------------------------------------------------


def test_get_live_single_spec_has_truthful_limit_and_timeout_bounds(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/commands/project_docs_fetch_live_single")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    params = {p["name"]: p for p in body["parameters"]}
    assert params["limit"]["default"] == 3
    assert params["limit"]["maximum"] == 5
    assert params["timeout"]["default"] == 60
    assert params["timeout"]["maximum"] == 120


def test_get_live_single_spec_has_related_commands_and_supports_dry_run_false(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/commands/project_docs_fetch_live_single")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["related_dry_run_command"] == "project_docs_fetch_dry_run"
    assert body["related_print_prompt_command"] == "project_docs_fetch_print_prompt"
    assert body["supports_dry_run"] is False
    assert body["requires_confirmation"] is True
    assert body["dry_run_required_before_live"] is True
    assert body["risk_level"] == "external_bounded"
    assert body["external_call_risk"] == "likely"


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


def test_validate_live_single_omitted_limit_timeout_argv_shows_defaults(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"opportunity_number": "OPP031267"}},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    argv_preview = body["argv_preview"]
    assert "--limit" in argv_preview and "--timeout" in argv_preview
    assert argv_preview[argv_preview.index("--limit") + 1] == "3"
    assert argv_preview[argv_preview.index("--timeout") + 1] == "60"


def test_validate_live_single_limit_5_ok(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"opportunity_number": "OPP031267", "limit": 5, "timeout": 60}},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_validate_live_single_limit_6_rejected(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"opportunity_number": "OPP031267", "limit": 6, "timeout": 60}},
    )

    assert resp.status_code == 400, resp.text


def test_validate_live_single_timeout_120_ok(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"opportunity_number": "OPP031267", "limit": 3, "timeout": 120}},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


def test_validate_live_single_timeout_121_rejected(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.post(
        "/api/commands/project_docs_fetch_live_single/validate",
        json={"params": {"opportunity_number": "OPP031267", "limit": 3, "timeout": 121}},
    )

    assert resp.status_code == 400, resp.text


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
    # project_docs_fetch_live_single IS executable (allowlisted, dispatched
    # to runner._execute_live_single), but with no prior successful
    # project_docs_fetch_dry_run run for this OppID on record, the
    # dry-run-first guardrail rejects it before any argv is built.
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


def test_run_live_single_confirm_true_with_bogus_dry_run_id_still_blocked(tmp_path, monkeypatch):
    # A dry_run_run_id that doesn't correspond to any real, qualifying,
    # successful project_docs_fetch_dry_run run is rejected — same as
    # omitting it entirely.
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


def test_run_live_single_succeeds_after_qualifying_dry_run(tmp_path, monkeypatch):
    """Full guardrail happy path: a real successful project_docs_fetch_dry_run
    for an OppID, followed by project_docs_fetch_live_single/run with
    confirm=true and no dry_run_run_id, picks up that recent dry run
    automatically (history.find_recent_successful_dry_run) and executes."""
    client, db_path = _client(tmp_path, monkeypatch)

    from manager_os.db import get_connection
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ["project::OPP031267", "Project OPP031267", "Client OPP031267", "OPP031267", now, now],
    )
    conn.close()

    with patch(
        "manager_os.command_center.runner.subprocess.run",
        return_value=_mock_completed(stdout="dry run ok\n"),
    ), patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval"):
        dry_resp = client.post(
            "/api/commands/project_docs_fetch_dry_run/run",
            json={"params": {"opportunity_number": "OPP031267"}, "confirm": False},
        )
    assert dry_resp.json()["status"] == "success", dry_resp.text

    with patch(
        "manager_os.command_center.runner.search_drive_for_project_docs"
    ) as mock_search, patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_gemini:
        mock_search.return_value = {
            "status": "success",
            "raw_count": 5,
            "parsed_count": 4,
            "inserted": 3,
            "updated": 1,
            "skipped": 0,
            "errors": []
        }
        live_resp = client.post(
            "/api/commands/project_docs_fetch_live_single/run",
            json={
                "params": {"opportunity_number": "OPP031267", "limit": 3, "timeout": 60},
                "confirm": True,
            },
        )

    mock_search.assert_called_once()
    mock_gemini.assert_not_called()
    assert live_resp.status_code == 200, live_resp.text
    body = live_resp.json()
    assert body["ok"] is True
    assert body["status"] == "success"


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
