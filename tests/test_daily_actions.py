"""Tests for structured, machine-actionable daily recommended actions.

Covers the document-gap-derived recommended actions gaining new structured
fields (id, source, entity_type, entity_id, primary_command,
secondary_commands) backed by real command_center registry command_ids,
while keeping the existing human-readable fields (title, reason, command,
priority) unchanged for backward compatibility. Also confirms
people_staffing/meeting-derived actions remain plain informational actions
(no fabricated primary_command).

No live Gemini/Workspace/Drive/Calendar/Chat/Sheets/OpenAI calls are made or
allowed in this file.
"""

from __future__ import annotations

from manager_os.build.daily_operating_loop import (
    _recommended_actions,
    build_document_gap_action,
)
from manager_os.command_center import registry

_FORBIDDEN_COMMAND_IDS = {
    "project_docs_fetch_batch_live_bounded",
    "project_docs_fetch_batch_dry_run",
    "project_docs_fetch_batch_print_prompt",
    "retrieve_forecast",
    "retrieve_calendar",
    "retrieve_activity",
    "workspace_fetch_deal_docs",
}

# Read the real registry defaults rather than hardcoding — this is the same
# source of truth build_document_gap_action must use, so a future change to
# the registry's guardrails can't silently desync from this test.
_LIVE_SINGLE_SPEC = registry.get("project_docs_fetch_live_single")
_LIVE_SINGLE_DEFAULT_LIMIT = next(p.default for p in _LIVE_SINGLE_SPEC.parameters if p.name == "limit")
_LIVE_SINGLE_DEFAULT_TIMEOUT = next(p.default for p in _LIVE_SINGLE_SPEC.parameters if p.name == "timeout")


def _sample_gap(opp: str = "OPP031267") -> dict:
    return {
        "opportunity_number": opp,
        "project_name": "Sample Project",
        "client": "Sample Client",
        "suggested_command": f"manager-os project-docs-fetch --opportunity-number {opp} --dry-run",
    }


def _collect_command_ids(action: dict) -> list[str]:
    ids = []
    if action.get("primary_command"):
        ids.append(action["primary_command"]["command_id"])
    for c in action.get("secondary_commands") or []:
        ids.append(c["command_id"])
    return ids


class TestBuildDocumentGapAction:
    def test_id_field(self):
        action = build_document_gap_action(_sample_gap("OPP031267"))
        assert action["id"] == "document_gap:OPP031267"

    def test_entity_fields(self):
        action = build_document_gap_action(_sample_gap("OPP031267"))
        assert action["entity_id"] == "OPP031267"
        assert action["entity_type"] == "project"
        assert action["source"] == "document_gaps"

    def test_primary_command_is_dry_run(self):
        action = build_document_gap_action(_sample_gap("OPP031267"))
        assert action["primary_command"]["command_id"] == "project_docs_fetch_dry_run"
        assert action["primary_command"]["params"]["opportunity_number"] == "OPP031267"

    def test_secondary_print_prompt_command(self):
        action = build_document_gap_action(_sample_gap("OPP031267"))
        print_prompt = next(
            c for c in action["secondary_commands"] if c["command_id"] == "project_docs_fetch_print_prompt"
        )
        assert print_prompt["params"]["opportunity_number"] == "OPP031267"

    def test_secondary_live_single_command(self):
        action = build_document_gap_action(_sample_gap("OPP031267"))
        live = next(
            c for c in action["secondary_commands"] if c["command_id"] == "project_docs_fetch_live_single"
        )
        assert live["params"]["opportunity_number"] == "OPP031267"
        assert live["params"]["limit"] == _LIVE_SINGLE_DEFAULT_LIMIT == 3
        assert live["params"]["timeout"] == _LIVE_SINGLE_DEFAULT_TIMEOUT == 60
        assert live["requires_confirmation"] is True
        assert live["requires_successful_dry_run"] is True

    def test_no_forbidden_command_ids(self):
        action = build_document_gap_action(_sample_gap("OPP031267"))
        assert _FORBIDDEN_COMMAND_IDS.isdisjoint(_collect_command_ids(action))

    def test_backward_compatible_fields_preserved(self):
        gap = _sample_gap("OPP031267")
        action = build_document_gap_action(gap)
        assert action["title"] == "Fetch docs for OPP031267 — no project documents indexed."
        assert action["reason"] == "0 documents in project_documents"
        assert action["priority"] == "medium"
        assert action["command"] == gap["suggested_command"]


class TestRecommendedActionsWiring:
    def test_document_gap_action_wired_through(self):
        gap = _sample_gap("OPP9")
        actions = _recommended_actions([], [], [gap])
        assert len(actions) == 1
        assert actions[0]["id"] == "document_gap:OPP9"
        assert actions[0]["primary_command"]["command_id"] == "project_docs_fetch_dry_run"

    def test_people_staffing_action_has_no_primary_command(self):
        person = {"person_name": "Alice Chen", "allocation_pct": 120.0, "warning": "overallocated"}
        actions = _recommended_actions([person], [], [])
        assert len(actions) == 1
        assert actions[0].get("primary_command") is None

    def test_meeting_action_has_no_primary_command(self):
        meeting = {
            "id": "mtg1",
            "title": "Client Sync",
            "start_time": "10:00",
            "reason": "No meeting prep generated yet",
        }
        actions = _recommended_actions([], [meeting], [])
        assert len(actions) == 1
        assert actions[0].get("primary_command") is None

    def test_no_forbidden_command_ids_anywhere(self):
        person = {"person_name": "Alice", "allocation_pct": 120.0, "warning": "overallocated"}
        meeting = {"id": "mtg1", "title": "Sync", "start_time": "10:00", "reason": "x"}
        gap = _sample_gap("OPP1")
        actions = _recommended_actions([person], [meeting], [gap])
        for a in actions:
            assert _FORBIDDEN_COMMAND_IDS.isdisjoint(_collect_command_ids(a))

    def test_existing_backward_compatible_fields_all_present(self):
        person = {"person_name": "Alice", "allocation_pct": 120.0, "warning": "overallocated"}
        meeting = {"id": "mtg1", "title": "Sync", "start_time": "10:00", "reason": "x"}
        gap = _sample_gap("OPP1")
        actions = _recommended_actions([person], [meeting], [gap])
        for a in actions:
            assert "title" in a
            assert "reason" in a
            assert "priority" in a
            assert "command" in a
