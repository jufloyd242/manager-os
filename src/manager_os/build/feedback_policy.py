"""Feedback policy layer — turns append-only feedback_events into behavior.

Deterministic, DB-backed. No LLM calls.

Responsibilities:
- Apply immediate signal status changes on feedback click
- Derive suppression/demotion rules for future ranking
- Explain why an item was hidden/demoted/boosted
- Find repeated feedback patterns → learning candidates
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from manager_os.db import content_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rating semantics
# ---------------------------------------------------------------------------

# Immediate status to set on signal when feedback is clicked
RATING_TO_SIGNAL_STATUS: dict[str, str | None] = {
    "useful":           None,             # keep visible
    "noisy":            "noisy",
    "stale":            "stale",
    "wrong":            "wrong",
    "missing-context":  "needs_context",
}

# Statuses hidden from default Today view
HIDDEN_STATUSES: frozenset[str] = frozenset({
    "noisy", "stale", "wrong", "dismissed", "acknowledged", "snoozed"
})

# Statuses shown in default Today view
VISIBLE_STATUSES: frozenset[str] = frozenset({"open", "needs_context"})

# Score deltas — exact item match
_EXACT_DELTA: dict[str, float] = {
    "useful":          +25.0,
    "noisy":           -30.0,
    "stale":           -40.0,
    "wrong":           -100.0,
    "missing-context": -10.0,
}

# Score deltas — same source_path + signal_type
_SOURCE_TYPE_DELTA: dict[str, float] = {
    "useful":          +10.0,
    "noisy":           -15.0,
    "stale":           -25.0,
    "wrong":           -60.0,
    "missing-context": 0.0,
}

# Score deltas — same entity + signal_type
_ENTITY_TYPE_DELTA: dict[str, float] = {
    "useful":          +5.0,
    "noisy":           -10.0,
    "stale":           0.0,
    "wrong":           -30.0,
    "missing-context": -5.0,
}

# Score delta — same source_path (any type)
_SOURCE_ONLY_DELTA: dict[str, float] = {
    "stale": -15.0,
}

# Learning candidate thresholds
LEARNING_THRESHOLDS: dict[str, int] = {
    "wrong":           1,
    "noisy":           2,
    "stale":           2,
    "missing-context": 3,
}

# Suggested actions for learning candidates
LEARNING_SUGGESTED_ACTIONS: dict[str, str] = {
    "wrong":           "suppress this source_path + signal_type; review extractor rule",
    "noisy":           "lower priority for this source_path + signal_type",
    "stale":           "suppress this source unless file modified after feedback",
    "missing-context": "require stronger evidence for this entity + signal_type",
}


# ---------------------------------------------------------------------------
# Phase 2 — Apply immediate feedback effect
# ---------------------------------------------------------------------------

def apply_signal_feedback_effect(
    conn,
    *,
    signal_id: str,
    item_id: str,
    rating: str,
    source_path: str | None = None,
    entity_name: str | None = None,
    signal_type: str | None = None,
) -> dict[str, Any]:
    """Apply immediate effect of feedback on a signal.

    1. Sets signal status if rating warrants it (noisy/stale/wrong/needs_context).
    2. Creates/updates learning candidate if threshold met.
    3. Returns dict describing what happened.

    Does NOT insert the feedback event — caller should call feedback.mark() first.
    """
    result: dict[str, Any] = {
        "signal_id": signal_id,
        "rating": rating,
        "status_changed": False,
        "old_status": None,
        "new_status": None,
        "learning_candidate_created": False,
        "explanation": "",
    }

    # Get current signal status
    row = conn.execute(
        "SELECT status FROM signals WHERE id = ?", [signal_id]
    ).fetchone()
    if row:
        result["old_status"] = row[0]

    # Apply immediate status change
    new_status = RATING_TO_SIGNAL_STATUS.get(rating)
    if new_status and new_status != result["old_status"]:
        from manager_os.build.dashboard_data import update_signal_status
        update_signal_status(
            conn, signal_id, new_status,
            changed_by="feedback_policy",
            note=f"feedback:{rating}",
        )
        result["status_changed"] = True
        result["new_status"] = new_status

    # Check for learning candidate
    candidate_created = _maybe_create_learning_candidate(
        conn,
        rating=rating,
        source_path=source_path,
        entity_name=entity_name,
        signal_type=signal_type,
        item_id=item_id,
    )
    result["learning_candidate_created"] = candidate_created

    # Build explanation
    parts: list[str] = []
    if result["status_changed"]:
        parts.append(f"signal status → {new_status}")
    if candidate_created:
        parts.append("learning candidate created")
    if rating == "useful":
        parts.append("boosted for future ranking")
    elif rating == "missing-context":
        parts.append("kept visible; demoted slightly")
    result["explanation"] = "; ".join(parts) if parts else "no immediate effect"

    return result


# ---------------------------------------------------------------------------
# Phase 5 — Query helpers for dashboard
# ---------------------------------------------------------------------------

def get_feedback_effects_for_signal(conn, signal) -> dict[str, Any]:
    """Return feedback effect metadata for a signal.

    Includes latest rating badge and whether it should be hidden.
    """
    item_id = f"signal:{signal.id[:16]}"
    hidden, reason = should_hide_signal_by_default(conn, signal)
    delta, explanations = score_delta_for_signal(conn, signal)

    # Get latest feedback rating for badge
    latest_rating = None
    try:
        row = conn.execute(
            """
            SELECT rating FROM (
                SELECT rating, ROW_NUMBER() OVER (ORDER BY created_at DESC) AS rn
                FROM feedback_events WHERE item_id = ?
            ) WHERE rn = 1
            """,
            [item_id],
        ).fetchone()
        if row:
            latest_rating = row[0]
    except Exception:
        pass

    return {
        "item_id": item_id,
        "latest_rating": latest_rating,
        "hidden_by_default": hidden,
        "hide_reason": reason,
        "score_delta": delta,
        "explanations": explanations,
    }


def should_hide_signal_by_default(conn, signal) -> tuple[bool, str]:
    """Return (should_hide, reason) based on signal status and feedback."""
    if signal.status in HIDDEN_STATUSES:
        return True, f"status={signal.status}"
    return False, ""


def score_delta_for_signal(conn, signal) -> tuple[float, list[str]]:
    """Return (total_delta, explanations) for feedback-based scoring.

    Checks:
    - Exact item match (latest rating)
    - Same source_path + signal_type
    - Same entity + signal_type
    - Same source_path (stale only)
    """
    item_id = f"signal:{signal.id[:16]}"
    total_delta = 0.0
    explanations: list[str] = []

    # Exact item match
    try:
        row = conn.execute(
            """
            SELECT rating FROM (
                SELECT rating, ROW_NUMBER() OVER (ORDER BY created_at DESC) AS rn
                FROM feedback_events WHERE item_id = ?
            ) WHERE rn = 1
            """,
            [item_id],
        ).fetchone()
        if row:
            rating = row[0]
            delta = _EXACT_DELTA.get(rating, 0.0)
            if delta:
                total_delta += delta
                explanations.append(f"{delta:+.0f} feedback: {rating} exact item")
    except Exception:
        pass

    # Same source_path + signal_type
    if signal.source_path and signal.signal_type:
        try:
            rows = conn.execute(
                """
                SELECT rating, COUNT(*) as n
                FROM feedback_events
                WHERE source_path = ? AND signal_type = ?
                GROUP BY rating
                """,
                [signal.source_path, signal.signal_type],
            ).fetchall()
            for rating, n in rows:
                delta = _SOURCE_TYPE_DELTA.get(rating, 0.0) * min(n, 3)
                if delta:
                    total_delta += delta
                    explanations.append(f"{delta:+.0f} feedback: {rating} same source+type ({n}x)")
        except Exception:
            pass

    # Same entity + signal_type
    if signal.entity_name and signal.signal_type:
        try:
            rows = conn.execute(
                """
                SELECT rating, COUNT(*) as n
                FROM feedback_events
                WHERE entity_name = ? AND signal_type = ?
                GROUP BY rating
                """,
                [signal.entity_name, signal.signal_type],
            ).fetchall()
            for rating, n in rows:
                delta = _ENTITY_TYPE_DELTA.get(rating, 0.0) * min(n, 3)
                if delta:
                    total_delta += delta
                    explanations.append(f"{delta:+.0f} feedback: {rating} same entity+type ({n}x)")
        except Exception:
            pass

    # Same source_path (stale only)
    if signal.source_path:
        try:
            rows = conn.execute(
                """
                SELECT COUNT(*) FROM feedback_events
                WHERE source_path = ? AND rating = 'stale'
                """,
                [signal.source_path],
            ).fetchall()
            n = rows[0][0] if rows else 0
            if n:
                delta = _SOURCE_ONLY_DELTA.get("stale", 0.0) * min(n, 3)
                if delta:
                    total_delta += delta
                    explanations.append(f"{delta:+.0f} feedback: stale same source ({n}x)")
        except Exception:
            pass

    return total_delta, explanations


# ---------------------------------------------------------------------------
# Phase 6 — Learning candidates
# ---------------------------------------------------------------------------

def _maybe_create_learning_candidate(
    conn,
    *,
    rating: str,
    source_path: str | None,
    entity_name: str | None,
    signal_type: str | None,
    item_id: str,
) -> bool:
    """Check if a learning candidate should be created/updated. Returns True if created/updated."""
    threshold = LEARNING_THRESHOLDS.get(rating, 999)
    if threshold == 999:
        return False

    # Count events matching this pattern
    # Try source_path + signal_type first
    pattern_type = None
    count = 0
    example_ids: list[str] = []

    if source_path and signal_type:
        pattern_type = "source_path_signal_type"
        rows = conn.execute(
            """
            SELECT item_id, COUNT(*) as n FROM feedback_events
            WHERE rating = ? AND source_path = ? AND signal_type = ?
            GROUP BY item_id
            """,
            [rating, source_path, signal_type],
        ).fetchall()
        count = sum(r[1] for r in rows)
        example_ids = [r[0] for r in rows[:10]]

    if count < threshold and entity_name and signal_type:
        pattern_type = "entity_signal_type"
        rows = conn.execute(
            """
            SELECT item_id, COUNT(*) as n FROM feedback_events
            WHERE rating = ? AND entity_name = ? AND signal_type = ?
            GROUP BY item_id
            """,
            [rating, entity_name, signal_type],
        ).fetchall()
        count = sum(r[1] for r in rows)
        example_ids = [r[0] for r in rows[:10]]

    if count < threshold:
        return False

    # Create or update candidate
    candidate_id = content_hash(
        f"learning::{pattern_type}::{rating}::{source_path or ''}::{entity_name or ''}::{signal_type or ''}"
    )

    suggested = LEARNING_SUGGESTED_ACTIONS.get(rating, "review")

    existing = conn.execute(
        "SELECT id, event_count FROM feedback_learning_candidates WHERE id = ?",
        [candidate_id],
    ).fetchone()

    now = datetime.utcnow()
    if existing:
        conn.execute(
            """
            UPDATE feedback_learning_candidates
            SET event_count = ?, example_item_ids = ?, updated_at = ?
            WHERE id = ?
            """,
            [count, json.dumps(example_ids), now, candidate_id],
        )
        return True
    else:
        conn.execute(
            """
            INSERT INTO feedback_learning_candidates
                (id, pattern_type, source_path, entity_name, signal_type,
                 rating, event_count, example_item_ids, suggested_action,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            [candidate_id, pattern_type, source_path or "", entity_name or "",
             signal_type or "", rating, count, json.dumps(example_ids),
             suggested, now, now],
        )
        logger.info("Learning candidate created: %s (%s, %s)", candidate_id, pattern_type, rating)
        return True


def list_feedback_patterns(conn, min_count: int = 2) -> list[dict]:
    """Return repeated feedback patterns grouped by source_path + signal_type."""
    try:
        rows = conn.execute(
            """
            SELECT source_path, signal_type, rating, COUNT(*) as n
            FROM feedback_events
            WHERE source_path IS NOT NULL AND source_path != ''
            GROUP BY source_path, signal_type, rating
            HAVING COUNT(*) >= ?
            ORDER BY n DESC
            """,
            [min_count],
        ).fetchall()
    except Exception:
        return []
    return [
        {"source_path": r[0], "signal_type": r[1], "rating": r[2], "count": r[3]}
        for r in rows
    ]


def list_learning_candidates(conn, status: str | None = None) -> list[dict]:
    """Return learning candidates, optionally filtered by status."""
    if status:
        rows = conn.execute(
            """
            SELECT id, pattern_type, source_path, entity_name, signal_type,
                   rating, event_count, example_item_ids, suggested_action,
                   status, created_at, updated_at
            FROM feedback_learning_candidates
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            [status],
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, pattern_type, source_path, entity_name, signal_type,
                   rating, event_count, example_item_ids, suggested_action,
                   status, created_at, updated_at
            FROM feedback_learning_candidates
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        {
            "id": r[0], "pattern_type": r[1], "source_path": r[2],
            "entity_name": r[3], "signal_type": r[4], "rating": r[5],
            "event_count": r[6], "example_item_ids": r[7],
            "suggested_action": r[8], "status": r[9],
            "created_at": r[10], "updated_at": r[11],
        }
        for r in rows
    ]


def update_learning_candidate_status(conn, candidate_id: str, new_status: str) -> None:
    """Update a learning candidate's status."""
    conn.execute(
        "UPDATE feedback_learning_candidates SET status = ?, updated_at = ? WHERE id = ?",
        [new_status, datetime.utcnow(), candidate_id],
    )
