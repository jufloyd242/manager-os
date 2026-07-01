"""Contract tests for GET /api/daily.

Mirrors the local-DB-only, no-live-call guarantees already proven for
`manager-os daily` in tests/test_cli_daily_operating_loop.py.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection

TARGET_DATE = date(2026, 6, 29)

EXPECTED_KEYS = {
    "date",
    "people_staffing",
    "meetings",
    "projects_deals",
    "document_gaps",
    "feedback_learning",
    "recommended_actions",
    "warnings",
}


def _client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    monkeypatch.setenv("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false")
    return TestClient(create_app()), db_path


def _seed_baseline_note(conn) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type, entity_name, title, body, tags, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["note1", "doc1", TARGET_DATE, "1on1", "person", "Seed Person", "Seed", "Seed body", "[]", now],
    )


DOC_GAP_OPP = "OPP031267"


def _seed_document_gap_project(conn, opportunity_number: str = DOC_GAP_OPP) -> None:
    """Seed a project with zero project_documents rows to trigger a document-gap
    recommended action. Mirrors tests/test_cli_daily_operating_loop.py's
    test_daily_shows_document_gap_with_fetch_suggestion seeding pattern."""
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [f"project::{opportunity_number}", "No Docs Project", "NoDocs Client", opportunity_number, now, now],
    )


def _find_document_gap_action(body: dict, opportunity_number: str = DOC_GAP_OPP) -> dict | None:
    for action in body["recommended_actions"]:
        if action.get("entity_id") == opportunity_number:
            return action
        if action.get("id") == f"document_gap:{opportunity_number}":
            return action
    return None


def _all_command_ids(body: dict) -> list[str]:
    """Collect every command_id referenced across primary_command/secondary_commands
    for every recommended action, so we can assert forbidden ids never leak."""
    ids: list[str] = []
    for action in body["recommended_actions"]:
        primary = action.get("primary_command")
        if primary and primary.get("command_id"):
            ids.append(primary["command_id"])
        for secondary in action.get("secondary_commands") or []:
            if secondary.get("command_id"):
                ids.append(secondary["command_id"])
    return ids


def test_daily_returns_expected_section_keys(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    assert set(body.keys()) == EXPECTED_KEYS
    assert body["date"] == TARGET_DATE.isoformat()
    for key in (
        "people_staffing",
        "meetings",
        "projects_deals",
        "document_gaps",
        "feedback_learning",
        "recommended_actions",
        "warnings",
    ):
        assert isinstance(body[key], list)


def test_daily_does_not_call_live_gemini_or_workspace(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200
    mock_run.assert_not_called()


def test_daily_missing_empty_db_does_not_crash(tmp_path, monkeypatch):
    # Point at a DB path that has never been created / has no seeded data.
    db_path = str(tmp_path / "empty.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    monkeypatch.setenv("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false")
    client = TestClient(create_app())

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    assert set(body.keys()) == EXPECTED_KEYS


def test_daily_invalid_date_returns_400(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    resp = client.get("/api/daily", params={"date": "not-a-date"})

    assert resp.status_code == 400
    body = resp.json()
    assert "detail" in body


def test_daily_defaults_to_today_when_no_date_given(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily")

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    assert set(body.keys()) == EXPECTED_KEYS


# ------------------------------------------------------------------
# Structured document-gap action contract (Agent A's primary_command /
# secondary_commands fields on recommended_actions) — API must pass these
# through build_daily_operating_loop()'s dict verbatim, with no additional
# command execution or shell-command construction in the route itself.
# ------------------------------------------------------------------


def test_daily_document_gap_action_has_primary_command_dry_run(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    _seed_document_gap_project(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    action = _find_document_gap_action(body)
    assert action is not None, body["recommended_actions"]
    assert action.get("primary_command") is not None
    assert action["primary_command"]["command_id"] == "project_docs_fetch_dry_run"


def test_daily_document_gap_action_secondary_includes_print_prompt(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    _seed_document_gap_project(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    action = _find_document_gap_action(body)
    assert action is not None, body["recommended_actions"]
    secondary_ids = {s.get("command_id") for s in action.get("secondary_commands") or []}
    assert "project_docs_fetch_print_prompt" in secondary_ids


def test_daily_document_gap_action_secondary_live_single_has_bounded_params(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    _seed_document_gap_project(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    action = _find_document_gap_action(body)
    assert action is not None, body["recommended_actions"]
    live_entries = [
        s for s in action.get("secondary_commands") or [] if s.get("command_id") == "project_docs_fetch_live_single"
    ]
    assert len(live_entries) == 1, action.get("secondary_commands")
    live = live_entries[0]
    assert live["params"]["limit"] == 3
    assert live["params"]["timeout"] == 60


def test_daily_document_gap_live_secondary_requires_confirmation_and_dry_run(tmp_path, monkeypatch):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    _seed_document_gap_project(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    action = _find_document_gap_action(body)
    assert action is not None, body["recommended_actions"]
    live = next(
        s for s in action["secondary_commands"] if s.get("command_id") == "project_docs_fetch_live_single"
    )
    assert live["requires_confirmation"] is True
    assert live["requires_successful_dry_run"] is True


def test_daily_no_action_exposes_disallowed_command_ids(tmp_path, monkeypatch):
    """Guard against ever surfacing the unbounded batch-live command or any raw
    workspace/Gemini command_id anywhere in recommended_actions."""
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    _seed_document_gap_project(conn)
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    all_ids = _all_command_ids(body)
    forbidden = {"project_docs_fetch_batch_live_bounded"}
    for command_id in all_ids:
        assert command_id not in forbidden
        assert "workspace" not in command_id
        assert "gemini" not in command_id


def test_daily_route_never_executes_subprocess(tmp_path, monkeypatch):
    """The route must be a pure passthrough of build_daily_operating_loop()'s
    dict — it must never itself invoke command_center's subprocess runner."""
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    _seed_document_gap_project(conn)
    conn.close()

    with (
        patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval"),
        patch("manager_os.command_center.runner.subprocess.run") as mock_subprocess_run,
    ):
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_subprocess_run.assert_not_called()
