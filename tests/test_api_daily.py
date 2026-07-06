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
    "action_summary",
    "action_groups",
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


def _all_group_command_ids(body: dict) -> list[str]:
    """Collect every command_id referenced anywhere inside action_groups[*].actions."""
    ids: list[str] = []
    for group in body.get("action_groups") or []:
        for action in group.get("actions") or []:
            primary = action.get("primary_command")
            if primary and primary.get("command_id"):
                ids.append(primary["command_id"])
            for secondary in action.get("secondary_commands") or []:
                if secondary.get("command_id"):
                    ids.append(secondary["command_id"])
    return ids


# ------------------------------------------------------------------
# action_summary / action_groups passthrough contract (Agent B, API layer).
#
# build_daily_operating_loop() is mocked here (patched at its import site in
# manager_os.api.app) so these tests exercise ONLY the API's passthrough
# faithfulness against the agreed contract shape, independent of whether
# Agent A's build/daily_operating_loop.py changes have landed yet. This
# keeps the API test suite green regardless of landing order. A real
# end-to-end cross-check (using genuine document_gaps -> action_groups data
# from the real pipeline) is Agent A's test responsibility per the task
# split; once that lands, these mocked tests still guard the API contract
# independently.
# ------------------------------------------------------------------


def _build_mock_loop_with_action_groups(conn, target_date: date, settings=None) -> dict:
    doc_gap_actions = [
        {
            "id": f"document_gap:OPP{i}",
            "source": "document_gaps",
            "entity_type": "project",
            "entity_id": f"OPP{i}",
            "title": f"Fetch docs for OPP{i}",
            "reason": "0 documents in project_documents",
            "command": f"manager-os project-docs-fetch --opportunity-number OPP{i} --dry-run",
            "priority": "medium",
            "primary_command": {
                "command_id": "project_docs_fetch_dry_run",
                "params": {"opportunity_number": f"OPP{i}"},
            },
            "secondary_commands": [
                {
                    "label": "Print Prompt",
                    "command_id": "project_docs_fetch_print_prompt",
                    "params": {"opportunity_number": f"OPP{i}"},
                },
                {
                    "label": "Run Live Fetch",
                    "command_id": "project_docs_fetch_live_single",
                    "params": {"opportunity_number": f"OPP{i}", "limit": 3, "timeout": 60},
                    "requires_confirmation": True,
                    "requires_successful_dry_run": True,
                },
            ],
        }
        for i in (1, 2, 3)
    ]
    people_action = {
        "title": "Review allocation for Alice — 160% planned.",
        "reason": "160% allocation",
        "command": None,
        "priority": "high",
        "source": "people_staffing",
    }
    recommended_actions = [*doc_gap_actions, people_action]

    action_summary = {
        "total": len(recommended_actions),
        "by_source": {"document_gaps": 3, "people_staffing": 1},
        "by_priority": {"high": 1, "medium": 3, "low": 0},
        "executable": 3,
        "informational": 1,
    }
    action_groups = [
        {
            "id": "document_gaps",
            "title": "Document Gaps",
            "source": "document_gaps",
            "count": 3,
            "priority": "medium",
            "summary": "3 projects missing documents",
            "default_visible_count": 5,
            "actions": doc_gap_actions,
        },
        {
            "id": "people_staffing",
            "title": "Staffing Reviews",
            "source": "people_staffing",
            "count": 1,
            "priority": "high",
            "summary": "1 staffing review",
            "default_visible_count": 5,
            "actions": [people_action],
        },
    ]

    return {
        "date": target_date.isoformat(),
        "people_staffing": [],
        "meetings": [],
        "projects_deals": [],
        "document_gaps": [],
        "feedback_learning": [],
        "recommended_actions": recommended_actions,
        "warnings": [],
        "action_summary": action_summary,
        "action_groups": action_groups,
    }


def test_daily_response_includes_action_summary_and_action_groups_keys(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with (
        patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run,
        patch(
            "manager_os.api.app.build_daily_operating_loop",
            side_effect=_build_mock_loop_with_action_groups,
        ),
    ):
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()
    assert "action_summary" in body
    assert "action_groups" in body


def test_action_summary_total_matches_recommended_actions_count(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.api.app.build_daily_operating_loop",
        side_effect=_build_mock_loop_with_action_groups,
    ):
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action_summary"]["total"] == len(body["recommended_actions"])


def test_action_groups_document_gaps_count_matches_flat_list(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.api.app.build_daily_operating_loop",
        side_effect=_build_mock_loop_with_action_groups,
    ):
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    group = next(g for g in body["action_groups"] if g["id"] == "document_gaps")
    flat_count = len([a for a in body["recommended_actions"] if a.get("source") == "document_gaps"])
    assert group["count"] == flat_count


def test_action_groups_preserve_primary_command_for_document_gap_action(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.api.app.build_daily_operating_loop",
        side_effect=_build_mock_loop_with_action_groups,
    ):
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    group = next(g for g in body["action_groups"] if g["id"] == "document_gaps")
    grouped_action = group["actions"][0]
    flat_action = next(a for a in body["recommended_actions"] if a["id"] == grouped_action["id"])
    assert grouped_action["primary_command"] == flat_action["primary_command"]


def test_action_groups_never_expose_forbidden_command_ids(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)

    with patch(
        "manager_os.api.app.build_daily_operating_loop",
        side_effect=_build_mock_loop_with_action_groups,
    ):
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    forbidden = {"project_docs_fetch_batch_live_bounded"}
    for command_id in _all_group_command_ids(body):
        assert command_id not in forbidden
        assert "workspace" not in command_id
        assert "gemini" not in command_id


def test_daily_response_backward_compatible_when_loop_omits_new_fields(tmp_path, monkeypatch):
    """If build_daily_operating_loop() returns a dict without action_summary/
    action_groups (e.g. an older build), the response must still succeed with
    sensible empty defaults rather than raising a validation error."""
    client, _ = _client(tmp_path, monkeypatch)

    def _old_shape_loop(conn, target_date, settings=None):
        return {
            "date": target_date.isoformat(),
            "people_staffing": [],
            "meetings": [],
            "projects_deals": [],
            "document_gaps": [],
            "feedback_learning": [],
            "recommended_actions": [],
            "warnings": [],
        }

    with patch("manager_os.api.app.build_daily_operating_loop", side_effect=_old_shape_loop):
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action_summary"] == {}
    assert body["action_groups"] == []


# ------------------------------------------------------------------
# Real end-to-end passthrough (no mocking of build_daily_operating_loop) —
# exercises the actual, now-landed build/daily_action_groups.py grouping
# logic through the real HTTP route, cross-checking the flat
# recommended_actions list against action_summary/action_groups.
# ------------------------------------------------------------------


def test_daily_real_pipeline_action_summary_total_matches_recommended_actions(tmp_path, monkeypatch):
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
    assert body["action_summary"]["total"] == len(body["recommended_actions"])


def test_daily_real_pipeline_document_gaps_group_matches_flat_list_and_preserves_commands(
    tmp_path, monkeypatch
):
    client, db_path = _client(tmp_path, monkeypatch)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    # Seed 6 document-gap projects to exercise the default_visible_count=5 hint
    # (backend must still return the FULL action list per group).
    for i in range(6):
        _seed_document_gap_project(conn, opportunity_number=f"OPP0{i}")
    conn.close()

    with patch("manager_os.ingest.workspace_gemini._run_gemini_retrieval") as mock_run:
        resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    mock_run.assert_not_called()
    body = resp.json()

    flat_doc_gap_actions = [a for a in body["recommended_actions"] if a.get("source") == "document_gaps"]
    assert len(flat_doc_gap_actions) == 6

    group = next(g for g in body["action_groups"] if g["id"] == "document_gaps")
    assert group["count"] == 6
    assert len(group["actions"]) == 6
    assert group["default_visible_count"] == 5

    # Spot-check: a document-gap action inside the group has the same
    # primary_command as its flat-list counterpart (matched by id).
    grouped_action = group["actions"][0]
    flat_action = next(a for a in flat_doc_gap_actions if a["id"] == grouped_action["id"])
    assert grouped_action["primary_command"] == flat_action["primary_command"]


def test_daily_real_pipeline_no_forbidden_command_ids_in_action_groups(tmp_path, monkeypatch):
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
    forbidden = {"project_docs_fetch_batch_live_bounded"}
    for command_id in _all_group_command_ids(body):
        assert command_id not in forbidden
        assert "workspace" not in command_id
        assert "gemini" not in command_id


def test_daily_real_pipeline_route_never_executes_subprocess(tmp_path, monkeypatch):
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


def test_daily_gaps_filters_legacy_empty(tmp_path, monkeypatch):
    """The /api/daily endpoint must exclude any projects with document_status='LEGACY_EMPTY' from document gaps."""
    from manager_os.api.app import create_app
    from manager_os.db import get_connection
    
    db_path = str(tmp_path / "legacy_empty_test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    _seed_baseline_note(conn)
    
    now = datetime.now(timezone.utc)
    # 1. Normal project with 0 docs (should be a gap)
    conn.execute(
        """
        INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ["project::OPP_GAP_1", "Gap Project 1", "Gap Client 1", "OPP_GAP_1", now, now],
    )
    # 2. Legacy Empty project (should NOT be a gap)
    conn.execute(
        """
        INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at, document_status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ["project::OPP_LEGACY_1", "Legacy Empty Project", "Legacy Client", "OPP_LEGACY_1", now, now, "LEGACY_EMPTY"],
    )
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/daily", params={"date": TARGET_DATE.isoformat()})
    assert resp.status_code == 200
    body = resp.json()
    
    gaps = body["document_gaps"]
    opps = {g["opportunity_number"] for g in gaps}
    
    # OPP_GAP_1 should be in the gaps, but OPP_LEGACY_1 should be filtered out
    assert "OPP_GAP_1" in opps
    assert "OPP_LEGACY_1" not in opps

