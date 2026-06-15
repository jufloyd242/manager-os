"""Feedback loop for Manager OS daily brief items.

Supports marking any primary brief item (signal, action item, waiting-on,
deal, decision) as useful / noisy / stale / wrong / missing-context.

Feedback is stored in the local ``feedback`` DuckDB table so it survives
re-runs.  The table is gitignored (it lives inside data/processed/).

Item ID format (stable across re-runs when the underlying source is the same):
    signal:<signal_db_id[:16]>     — a ranked signal
    action:<action_item_id[:16]>   — a manager follow-up
    waiting:<action_item_id[:16]>  — a waiting-on (non-manager) action item
    deal:<deal_id_or_name>         — a SOW-deadline deal signal
    decision:<decision_id[:16]>    — a decision item

Ratings
-------
useful            Item was actionable and relevant — boost similar items.
noisy             Item was low-signal noise — demote similar items.
stale             Old/historical item, no longer relevant — suppress.
wrong             Extractor error (wrong entity/type) — suppress that pattern.
missing-context   Keep item but require better evidence before surfacing again.
"""

from __future__ import annotations

import logging
from datetime import datetime

from manager_os.db import content_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_RATINGS: frozenset[str] = frozenset(
    {"useful", "noisy", "stale", "wrong", "missing-context"}
)

# Ratings that suppress items from future briefs
SUPPRESSED_RATINGS: frozenset[str] = frozenset({"noisy", "stale", "wrong"})

# Score deltas applied during ranking when matching feedback is found
_RATING_DELTA: dict[str, float] = {
    "useful":          +25.0,
    "noisy":           -30.0,
    "stale":           -40.0,
    "wrong":           -50.0,
    "missing-context": -10.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _feedback_id(item_id: str, rating: str) -> str:
    """Stable dedup key — one feedback record per (item_id, rating)."""
    return content_hash(f"feedback::{item_id}::{rating}")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def mark(
    conn,
    item_id: str,
    rating: str,
    *,
    reason: str | None = None,
    source_path: str | None = None,
    entity_name: str | None = None,
    signal_type: str | None = None,
) -> None:
    """Record a feedback rating for a brief item.

    Args:
        conn: Open DuckDB connection.
        item_id: Stable brief item ID (e.g. ``signal:abc123``, ``deal:OPP025010``).
        rating: One of ``useful``, ``noisy``, ``stale``, ``wrong``,
            ``missing-context``.
        reason: Optional free-text explanation.
        source_path: The file / source path of the underlying item.
        entity_name: The entity name for context matching.
        signal_type: The signal_type for context matching.

    Raises:
        ValueError: If rating is not valid.
    """
    if rating not in VALID_RATINGS:
        raise ValueError(
            f"Invalid rating {rating!r}. Valid values: {sorted(VALID_RATINGS)}"
        )

    # Resolve metadata from the DB when the item is a known signal or action item
    if source_path is None and item_id.startswith("signal:"):
        sig_id = item_id[len("signal:"):]
        row = conn.execute(
            "SELECT source_path, entity_name, signal_type FROM signals WHERE id LIKE ?",
            [sig_id + "%"],
        ).fetchone()
        if row:
            source_path, entity_name, signal_type = row[0], row[1], row[2]
    elif item_id.startswith("action:"):
        ai_id = item_id[len("action:"):]
        row = conn.execute(
            "SELECT source_note_id, assigned_to FROM action_items WHERE id LIKE ?",
            [ai_id + "%"],
        ).fetchone()
        if row:
            source_path = source_path or row[0] or ""
            entity_name = entity_name or row[1] or ""
            signal_type = signal_type or "action_item"
            # Also persist feedback_rating on the action_item row itself
            from manager_os.build.dashboard_data import update_action_item
            try:
                full_id_row = conn.execute(
                    "SELECT id FROM action_items WHERE id LIKE ?", [ai_id + "%"]
                ).fetchone()
                if full_id_row:
                    update_action_item(
                        conn, full_id_row[0],
                        feedback_rating=rating,
                        feedback_reason=reason,
                    )
            except Exception:
                pass

    fid = _feedback_id(item_id, rating)
    now = datetime.utcnow()

    # Determine item_type from the prefix
    item_type = item_id.split(":")[0] if ":" in item_id else "unknown"

    conn.execute(
        """
        INSERT OR REPLACE INTO feedback
            (id, item_id, item_type, rating, reason, source_path, entity_name,
             signal_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [fid, item_id, item_type, rating, reason, source_path, entity_name,
         signal_type, now],
    )
    logger.info("Feedback: %s → %s (reason=%r)", item_id, rating, reason)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_feedback(conn, limit: int = 50) -> list[dict]:
    """Return the most recent feedback entries."""
    rows = conn.execute(
        """
        SELECT id, item_id, item_type, rating, reason, source_path,
               entity_name, signal_type, created_at
        FROM feedback
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [
        {
            "id": r[0],
            "item_id": r[1],
            "item_type": r[2],
            "rating": r[3],
            "reason": r[4],
            "source_path": r[5],
            "entity_name": r[6],
            "signal_type": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]


def get_feedback_summary(conn) -> dict:
    """Return aggregate feedback statistics.

    Returns a dict with:
        counts_by_rating   dict[str, int]
        top_noisy_sources  list[tuple[source_path, count]]
        top_stale_sources  list[tuple[source_path, count]]
        top_wrong_types    list[tuple[signal_type, count]]
        useful_types       list[tuple[signal_type, count]]
        total              int
    """
    # Counts by rating
    rating_rows = conn.execute(
        "SELECT rating, COUNT(*) FROM feedback GROUP BY rating ORDER BY COUNT(*) DESC"
    ).fetchall()
    counts_by_rating: dict[str, int] = {r: 0 for r in VALID_RATINGS}
    total = 0
    for rating, n in rating_rows:
        if rating in counts_by_rating:
            counts_by_rating[rating] = n
        total += n

    def _top(rating_val: str, col: str, limit: int = 5) -> list[tuple]:
        return conn.execute(
            f"""
            SELECT {col}, COUNT(*) as n
            FROM feedback
            WHERE rating = ? AND {col} IS NOT NULL AND {col} != ''
            GROUP BY {col}
            ORDER BY n DESC
            LIMIT ?
            """,
            [rating_val, limit],
        ).fetchall()

    return {
        "total": total,
        "counts_by_rating": counts_by_rating,
        "top_noisy_sources": _top("noisy", "source_path"),
        "top_stale_sources": _top("stale", "source_path"),
        "top_wrong_types":   _top("wrong", "signal_type"),
        "useful_types":      _top("useful", "item_type"),
    }


# ---------------------------------------------------------------------------
# Ranking adjustments
# ---------------------------------------------------------------------------

def load_feedback_index(conn) -> dict[str, str]:
    """Return a mapping of item_id → most-recent rating for use during ranking.

    Also builds secondary indexes keyed by source_path and entity_name so
    ranking can boost/demote similar items that haven't been directly rated.
    """
    rows = conn.execute(
        """
        SELECT item_id, rating
        FROM (
            SELECT item_id, rating,
                   ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY created_at DESC) AS rn
            FROM feedback
        ) ranked
        WHERE rn = 1
        """
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def load_source_feedback_index(conn) -> dict[str, str]:
    """Return source_path → most-common-rating for indirect matching."""
    try:
        rows = conn.execute(
            """
            SELECT source_path, rating, COUNT(*) as n
            FROM feedback
            WHERE source_path IS NOT NULL AND source_path != ''
            GROUP BY source_path, rating
            ORDER BY source_path, n DESC
            """
        ).fetchall()
    except Exception:
        return {}
    # Keep only the dominant rating per source_path
    seen: set[str] = set()
    result: dict[str, str] = {}
    for source_path, rating, _ in rows:
        if source_path not in seen:
            result[source_path] = rating
            seen.add(source_path)
    return result


def apply_feedback_score(
    score: float,
    item_id: str,
    source_path: str,
    direct_index: dict[str, str],
    source_index: dict[str, str],
) -> float:
    """Adjust *score* based on any matching feedback.

    Direct item match takes precedence; source-path match is a weaker signal.
    """
    rating = direct_index.get(item_id)
    if rating is None and source_path:
        rating = source_index.get(source_path)
    if rating is None:
        return score
    delta = _RATING_DELTA.get(rating, 0.0)
    return score + delta
