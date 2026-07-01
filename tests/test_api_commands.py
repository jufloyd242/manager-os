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
