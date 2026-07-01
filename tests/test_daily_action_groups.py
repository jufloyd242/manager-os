"""Tests for `action_summary` / `action_groups` on the Daily Operating Loop payload.

Covers `build_action_summary` / `build_action_groups` (grouping/summarizing
`recommended_actions` for the frontend "command tower" UI) without touching
or truncating `recommended_actions` itself.

No live Gemini/Workspace/Drive/Calendar/Chat/Sheets/OpenAI calls are made or
allowed in any test here.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from manager_os.build.daily_action_groups import build_action_groups, build_action_summary
from manager_os.build.daily_operating_loop import build_daily_operating_loop
from manager_os.db import get_connection

TARGET_DATE = date(2026, 6, 29)

_FORBIDDEN_COMMAND_IDS = {
    "project_docs_fetch_batch_live_bounded",
    "project_docs_fetch_batch_dry_run",
    "project_docs_fetch_batch_print_prompt",
    "retrieve_forecast",
    "retrieve_calendar",
    "retrieve_activity",
    "workspace_fetch_deal_docs",
}


def _collect_command_ids(action: dict) -> list[str]:
    ids = []
    if action.get("primary_command"):
        ids.append(action["primary_command"]["command_id"])
    for c in action.get("secondary_commands") or []:
        ids.append(c["command_id"])
    return ids


def _seed_project_with_doc_gap(conn, opp: str) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO projects (id, project_name, client, opportunity_number, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [f"project::{opp}", f"Project {opp}", "NoDocs Client", opp, now, now],
    )


def _seed_overallocated_person(conn, name: str = "Alice Chen", pct: float = 120.0) -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO staffing_forecast
            (id, person_id, person_name, week_start, client, project, allocation_pct, forecast_type, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [f"fc-{name}", "", name, TARGET_DATE, "Acme Corp", "Platform", pct, "confirmed", now],
    )


def _seed_meeting_without_prep(conn, meeting_id: str = "mtg1") -> None:
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [meeting_id, TARGET_DATE, "10:00", "Client Sync", '["alice@example.com"]', "[]", "test", "", now],
    )


def _realistic_loop(tmp_path, num_doc_gaps: int = 1):
    db_path = str(tmp_path / "test.duckdb")
    conn = get_connection(db_path)
    _seed_overallocated_person(conn)
    _seed_meeting_without_prep(conn)
    for i in range(num_doc_gaps):
        _seed_project_with_doc_gap(conn, f"OPP{i}")
    conn.close()

    conn = get_connection(db_path)
    try:
        loop = build_daily_operating_loop(conn, TARGET_DATE, settings=None)
    finally:
        conn.close()
    return loop


# ------------------------------------------------------------------
# 1-5: action_summary
# ------------------------------------------------------------------


def test_action_summary_total_matches_recommended_actions(tmp_path):
    loop = _realistic_loop(tmp_path)
    assert loop["action_summary"]["total"] == len(loop["recommended_actions"])


def test_action_summary_by_source_counts_document_gaps(tmp_path):
    loop = _realistic_loop(tmp_path, num_doc_gaps=3)
    gap_actions = [a for a in loop["recommended_actions"] if a.get("source") == "document_gaps"]
    assert loop["action_summary"]["by_source"]["document_gaps"] == len(gap_actions) == 3


def test_action_summary_by_priority_counts(tmp_path):
    loop = _realistic_loop(tmp_path)
    by_priority = loop["action_summary"]["by_priority"]
    expected = {"high": 0, "medium": 0, "low": 0}
    for a in loop["recommended_actions"]:
        expected[a["priority"]] += 1
    assert by_priority == expected


def test_action_summary_executable_count(tmp_path):
    loop = _realistic_loop(tmp_path)
    actions = loop["recommended_actions"]
    expected_executable = sum(
        1 for a in actions if a.get("primary_command") or a.get("secondary_commands")
    )
    assert loop["action_summary"]["executable"] == expected_executable
    assert expected_executable > 0


def test_action_summary_informational_count(tmp_path):
    loop = _realistic_loop(tmp_path)
    actions = loop["recommended_actions"]
    expected_informational = sum(
        1 for a in actions if not a.get("primary_command") and not a.get("secondary_commands")
    )
    assert loop["action_summary"]["informational"] == expected_informational
    assert expected_informational > 0
    total = loop["action_summary"]["executable"] + loop["action_summary"]["informational"]
    assert total == loop["action_summary"]["total"]


# ------------------------------------------------------------------
# 6-9: action_groups — document_gaps group shape
# ------------------------------------------------------------------


def test_action_groups_includes_document_gaps_group(tmp_path):
    loop = _realistic_loop(tmp_path)
    ids = [g["id"] for g in loop["action_groups"]]
    assert "document_gaps" in ids


def test_document_gaps_group_count_matches_actions(tmp_path):
    loop = _realistic_loop(tmp_path, num_doc_gaps=4)
    gap_group = next(g for g in loop["action_groups"] if g["id"] == "document_gaps")
    gap_actions = [a for a in loop["recommended_actions"] if a.get("source") == "document_gaps"]
    assert gap_group["count"] == len(gap_actions) == 4
    assert len(gap_group["actions"]) == 4


def test_document_gaps_default_visible_count_capped_at_5(tmp_path):
    loop = _realistic_loop(tmp_path, num_doc_gaps=8)
    gap_group = next(g for g in loop["action_groups"] if g["id"] == "document_gaps")
    assert gap_group["count"] == 8
    assert gap_group["default_visible_count"] == 5


def test_document_gaps_default_visible_count_equals_count_when_small(tmp_path):
    loop = _realistic_loop(tmp_path, num_doc_gaps=2)
    gap_group = next(g for g in loop["action_groups"] if g["id"] == "document_gaps")
    assert gap_group["count"] == 2
    assert gap_group["default_visible_count"] == 2


# ------------------------------------------------------------------
# 10: regression — recommended_actions unchanged/still present
# ------------------------------------------------------------------


def test_recommended_actions_still_present_and_populated(tmp_path):
    loop = _realistic_loop(tmp_path)
    assert "recommended_actions" in loop
    assert len(loop["recommended_actions"]) == 3  # 1 person + 1 meeting + 1 doc gap
    for key in ("title", "reason", "priority", "command"):
        for a in loop["recommended_actions"]:
            assert key in a


# ------------------------------------------------------------------
# 11: no forbidden command ids anywhere in any group
# ------------------------------------------------------------------


def test_no_forbidden_command_ids_in_any_group(tmp_path):
    loop = _realistic_loop(tmp_path, num_doc_gaps=6)
    for group in loop["action_groups"]:
        for action in group["actions"]:
            assert _FORBIDDEN_COMMAND_IDS.isdisjoint(_collect_command_ids(action))


# ------------------------------------------------------------------
# 12: group order matches specified priority
# ------------------------------------------------------------------


def test_group_order_people_staffing_before_meetings_before_document_gaps(tmp_path):
    loop = _realistic_loop(tmp_path)
    ids = [g["id"] for g in loop["action_groups"]]
    assert ids.index("people_staffing") < ids.index("meetings")
    assert ids.index("meetings") < ids.index("document_gaps")


# ------------------------------------------------------------------
# Unit-level tests for build_action_summary / build_action_groups directly
# ------------------------------------------------------------------


def test_build_action_summary_empty():
    summary = build_action_summary([])
    assert summary == {
        "total": 0,
        "by_source": {},
        "by_priority": {"high": 0, "medium": 0, "low": 0},
        "executable": 0,
        "informational": 0,
    }


def test_build_action_summary_buckets_missing_source_as_other():
    actions = [{"priority": "low"}]
    summary = build_action_summary(actions)
    assert summary["by_source"] == {"other": 1}


def test_build_action_groups_empty_groups_omitted():
    groups = build_action_groups([])
    assert groups == []


def test_build_action_groups_unknown_source_grouped_under_other():
    actions = [
        {"source": "mystery_source", "priority": "low"},
        {"source": "another_unknown", "priority": "medium"},
    ]
    groups = build_action_groups(actions)
    assert len(groups) == 1
    assert groups[0]["id"] == "other"
    assert groups[0]["title"] == "Other"
    assert groups[0]["count"] == 2


def test_build_action_groups_document_gaps_singular_summary():
    actions = [{"source": "document_gaps", "priority": "medium"}]
    groups = build_action_groups(actions)
    gap_group = next(g for g in groups if g["id"] == "document_gaps")
    assert "1 project is missing" in gap_group["summary"]


def test_build_action_groups_full_order_all_sources_present():
    actions = [
        {"source": "other_thing", "priority": "low"},
        {"source": "feedback_learning", "priority": "medium"},
        {"source": "document_gaps", "priority": "medium"},
        {"source": "projects_deals", "priority": "medium"},
        {"source": "meetings", "priority": "high"},
        {"source": "people_staffing", "priority": "high"},
    ]
    groups = build_action_groups(actions)
    ids = [g["id"] for g in groups]
    assert ids == [
        "people_staffing",
        "meetings",
        "projects_deals",
        "document_gaps",
        "feedback_learning",
        "other",
    ]
