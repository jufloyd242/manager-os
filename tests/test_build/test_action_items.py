"""Tests for action item controls, feedback, and brief integration."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.build.daily_brief import generate_daily_brief, _brief_item_id
from manager_os.build.dashboard_data import (
    get_action_items_filtered,
    get_open_action_items,
    update_action_item,
)
from manager_os.build.feedback import mark as fb_mark, list_feedback


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn():
    return get_connection(":memory:")


def _seed_ai(
    conn,
    description: str,
    assigned_to: str = "manager",
    status: str = "open",
    due_date: date | None = None,
) -> str:
    ai_id = content_hash(f"ai_test::{description}::{assigned_to}")
    conn.execute(
        """
        INSERT INTO action_items
            (id, assigned_to, description, due_date, status, created_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [ai_id, assigned_to, description, due_date, status],
    )
    return ai_id


def _seed_signal(conn, entity_name: str = "Acme", severity: str = "high",
                 summary: str = "Pipeline at risk") -> str:
    sig_id = content_hash(f"sig::{entity_name}::{summary}")
    conn.execute(
        """
        INSERT INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, confidence, status, created_at, updated_at)
        VALUES (?, ?, 'rule', '', 'client', ?, 'risk', ?, ?, '',
                TRUE, 1.0, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [sig_id, date.today().isoformat(), entity_name, severity, summary],
    )
    return sig_id


# ===========================================================================
# 1. Stable IDs in the brief
# ===========================================================================

class TestActionItemStableIds:
    """Action items render with stable [action:…] and [waiting:…] IDs."""

    def test_manager_ai_renders_action_id(self, conn) -> None:
        _seed_ai(conn, "Follow up with Alice about SOW contract renewal")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert re.search(r'\[action:[0-9a-f]+\]', brief.content), \
            "No [action:…] tag found in brief"

    def test_waiting_on_renders_waiting_id(self, conn) -> None:
        _seed_ai(conn, "Waiting on Legal to review the contract document",
                 assigned_to="Legal")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert re.search(r'\[waiting:[0-9a-f]+\]', brief.content), \
            "No [waiting:…] tag found"

    def test_stable_id_same_across_reruns(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Bob about contract renewal details")
        b1 = generate_daily_brief(conn, target_date=date.today())
        b2 = generate_daily_brief(conn, target_date=date.today())
        ids1 = set(re.findall(r'\[action:([0-9a-f]+)\]', b1.content))
        ids2 = set(re.findall(r'\[action:([0-9a-f]+)\]', b2.content))
        assert ids1 == ids2, "Action IDs changed across reruns"

    def test_brief_item_id_prefix_format(self) -> None:
        fake_id = "abcdef1234567890abcdef1234567890"
        assert _brief_item_id("action", fake_id) == f"action:{fake_id[:16]}"

    def test_brief_item_id_waiting_prefix(self) -> None:
        fake_id = "1122334455667788aabbccdd"
        assert _brief_item_id("waiting", fake_id).startswith("waiting:")


# ===========================================================================
# 2. update_action_item persists state
# ===========================================================================

class TestUpdateActionItem:
    """update_action_item correctly persists status and feedback."""

    def test_complete_removes_from_open_list(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Jason about headcount backfill")
        update_action_item(conn, ai_id, status="completed")
        items = get_open_action_items(conn)
        assert not any(i.id == ai_id for i in items), \
            "Completed item must not appear in open list"

    def test_stale_removes_from_open_list(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Dennis about the Google update")
        update_action_item(conn, ai_id, status="stale")
        items = get_open_action_items(conn)
        assert not any(i.id == ai_id for i in items)

    def test_dismissed_removes_from_open_list(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Sarah about Q3 planning session")
        update_action_item(conn, ai_id, status="dismissed")
        items = get_open_action_items(conn)
        assert not any(i.id == ai_id for i in items)

    def test_snoozed_until_future_hides_item(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with team about capacity planning")
        future = date.today() + timedelta(days=7)
        update_action_item(conn, ai_id, status="snoozed", snooze_until=future)
        items = get_open_action_items(conn)
        assert not any(i.id == ai_id for i in items), \
            "Snoozed item must not appear until snooze_until"

    def test_snoozed_until_past_shows_item(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Alice about the contract")
        past = date.today() - timedelta(days=1)
        update_action_item(conn, ai_id, status="open", snooze_until=past)
        items = get_open_action_items(conn)
        assert any(i.id == ai_id for i in items), \
            "Item with expired snooze should re-appear"

    def test_not_mine_removes_from_open(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with procurement about vendor contract")
        update_action_item(conn, ai_id, status="not_mine")
        items = get_open_action_items(conn)
        assert not any(i.id == ai_id for i in items)

    def test_feedback_rating_and_reason_persisted(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Bob about renewal details contract")
        update_action_item(conn, ai_id, feedback_rating="noisy", feedback_reason="old")
        rows = conn.execute(
            "SELECT feedback_rating, feedback_reason FROM action_items WHERE id = ?",
            [ai_id],
        ).fetchone()
        assert rows[0] == "noisy"
        assert rows[1] == "old"

    def test_reopen_completed_item(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Carol about project handoff notes")
        update_action_item(conn, ai_id, status="completed")
        assert not any(i.id == ai_id for i in get_open_action_items(conn))
        update_action_item(conn, ai_id, status="open")
        assert any(i.id == ai_id for i in get_open_action_items(conn))


# ===========================================================================
# 3. Brief filters out non-open action items
# ===========================================================================

class TestBriefActionItemFiltering:
    """Brief must not include completed/stale/snoozed action items."""

    def test_completed_ai_not_in_brief(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Alice about contract renewal status")
        update_action_item(conn, ai_id, status="completed")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Alice about contract renewal" not in brief.content, \
            "Completed item must not appear in brief"

    def test_stale_ai_not_in_brief(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Dennis Wednesday if no word by then")
        update_action_item(conn, ai_id, status="stale")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Dennis Wednesday" not in brief.content

    def test_snoozed_ai_not_in_brief(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Jason about the staffing gap decision")
        future = date.today() + timedelta(days=3)
        update_action_item(conn, ai_id, status="snoozed", snooze_until=future)
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Jason about the staffing gap" not in brief.content

    def test_noisy_feedback_suppresses_from_brief(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Carol about project staffing allocation")
        update_action_item(conn, ai_id, feedback_rating="noisy")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Carol about project staffing" not in brief.content

    def test_open_ai_still_appears_in_brief(self, conn) -> None:
        _seed_ai(conn, "Follow up with Jason to confirm backfill or new headcount")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Jason" in brief.content or "backfill" in brief.content


# ===========================================================================
# 4. get_action_items_filtered
# ===========================================================================

class TestGetActionItemsFiltered:
    """Filtered query returns correct items based on status."""

    def test_completed_items_in_completed_filter(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with legal on contract review finalization")
        update_action_item(conn, ai_id, status="completed")
        items = get_action_items_filtered(conn, statuses=["completed"])
        assert any(i.id == ai_id for i in items)

    def test_stale_in_stale_filter(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with David about onboarding checklist items")
        update_action_item(conn, ai_id, status="stale")
        items = get_action_items_filtered(conn, statuses=["stale"])
        assert any(i.id == ai_id for i in items)

    def test_snoozed_excluded_by_default(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with vendor about invoice approval process")
        future = date.today() + timedelta(days=7)
        update_action_item(conn, ai_id, status="snoozed", snooze_until=future)
        items = get_action_items_filtered(conn, statuses=["snoozed"])
        # snooze_until is in the future → excluded unless include_snoozed=True
        assert not any(i.id == ai_id for i in items)

    def test_snoozed_included_with_flag(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with procurement about the new SOW terms")
        future = date.today() + timedelta(days=7)
        update_action_item(conn, ai_id, status="snoozed", snooze_until=future)
        items = get_action_items_filtered(
            conn, statuses=["snoozed"], include_snoozed=True
        )
        assert any(i.id == ai_id for i in items)


# ===========================================================================
# 5. feedback mark action:<id>
# ===========================================================================

class TestFeedbackMarkAction:
    """manager-os feedback mark action:<id> writes to feedback table and action_items."""

    def test_feedback_mark_noisy_stores_record(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Bob about the contract renewal terms")
        brief_id = f"action:{ai_id[:16]}"
        fb_mark(conn, brief_id, "noisy", reason="not manager-actionable")
        entries = list_feedback(conn)
        assert any(e["item_id"] == brief_id for e in entries)
        entry = next(e for e in entries if e["item_id"] == brief_id)
        assert entry["rating"] == "noisy"
        assert entry["reason"] == "not manager-actionable"

    def test_feedback_mark_updates_action_item_row(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Alice about resource allocation plans")
        brief_id = f"action:{ai_id[:16]}"
        fb_mark(conn, brief_id, "stale")
        row = conn.execute(
            "SELECT feedback_rating FROM action_items WHERE id = ?", [ai_id]
        ).fetchone()
        assert row[0] == "stale"

    def test_feedback_mark_wrong_suppresses_from_brief(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Bob about the budget approval cycle")
        brief_id = f"action:{ai_id[:16]}"
        fb_mark(conn, brief_id, "wrong")
        brief = generate_daily_brief(conn, target_date=date.today())
        # feedback_rating='wrong' → filtered from brief
        assert "Bob about the budget approval" not in brief.content

    def test_feedback_mark_useful_keeps_in_brief(self, conn) -> None:
        ai_id = _seed_ai(conn, "Follow up with Carol about the SOW contract extension")
        brief_id = f"action:{ai_id[:16]}"
        fb_mark(conn, brief_id, "useful")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Carol" in brief.content or "SOW contract extension" in brief.content

    def test_feedback_list_shows_action_entries(self, conn) -> None:
        ai_id1 = _seed_ai(conn, "Follow up with team on Q3 planning allocation review")
        ai_id2 = _seed_ai(conn, "Follow up with manager about resource gap assessment")
        fb_mark(conn, f"action:{ai_id1[:16]}", "noisy")
        fb_mark(conn, f"action:{ai_id2[:16]}", "useful")
        entries = list_feedback(conn)
        item_ids = {e["item_id"] for e in entries}
        assert f"action:{ai_id1[:16]}" in item_ids
        assert f"action:{ai_id2[:16]}" in item_ids
