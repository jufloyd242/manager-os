"""Signal usefulness feedback loop.

Provides:
- rate_signal()     — record a usefulness rating on a signal
- get_feedback_report() — aggregate rating stats

Supported ratings
-----------------
useful            Signal was actionable and relevant.
not_useful        Signal was noise; not helpful.
duplicate         Signal was already captured elsewhere.
wrong_entity      Signal was attributed to the wrong person/client/deal.
too_low_priority  Signal is real but not worth surfacing at current time.
snoozed           Temporarily silenced until snooze_until date.
resolved          Underlying issue is already handled.

Ratings stored in signals.rating (VARCHAR).
Suppressed ratings (excluded from default daily brief):
    not_useful, duplicate, wrong_entity, resolved
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from manager_os.db import content_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_RATINGS: frozenset[str] = frozenset(
    {
        "useful",
        "not_useful",
        "duplicate",
        "wrong_entity",
        "too_low_priority",
        "snoozed",
        "resolved",
    }
)

# Ratings that cause a signal to be suppressed in the daily brief by default.
SUPPRESSED_RATINGS: frozenset[str] = frozenset(
    {"not_useful", "duplicate", "wrong_entity", "resolved"}
)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def rate_signal(
    conn,
    signal_id: str,
    rating: str,
    note: Optional[str] = None,
    snooze_until: Optional[date] = None,
) -> None:
    """Record a usefulness rating on a signal.

    Args:
        conn: Open DuckDB connection.
        signal_id: Primary key of the signal to rate.
        rating: One of VALID_RATINGS.
        note: Optional free-text note.
        snooze_until: Date until which the signal should be snoozed.
            Only valid when rating == 'snoozed'.

    Raises:
        ValueError: If signal_id not found or rating is invalid.
    """
    if rating not in VALID_RATINGS:
        raise ValueError(
            f"Invalid rating {rating!r}. Valid values: {sorted(VALID_RATINGS)}"
        )
    if snooze_until is not None and rating != "snoozed":
        raise ValueError("--snooze-until is only valid when rating is 'snoozed'")

    row = conn.execute(
        "SELECT id, rating FROM signals WHERE id = ?", [signal_id]
    ).fetchone()
    if row is None:
        raise ValueError(f"Signal not found: {signal_id!r}")

    old_rating = row[1] or "unrated"
    now = datetime.utcnow()

    # Update the signals row
    conn.execute(
        "UPDATE signals SET rating = ?, snooze_until = ?, updated_at = ? WHERE id = ?",
        [rating, snooze_until, now, signal_id],
    )

    # Write audit entry to signal_status_log
    log_id = content_hash(
        f"rating::{signal_id}::{rating}::{now.isoformat()}"
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO signal_status_log
            (id, signal_id, old_status, new_status, changed_at, changed_by, note)
        VALUES (?, ?, ?, ?, ?, 'cli', ?)
        """,
        [log_id, signal_id, old_rating, rating, now, note or ""],
    )

    logger.info("Rated signal %s as %r (was %r)", signal_id, rating, old_rating)


# ---------------------------------------------------------------------------
# Feedback report
# ---------------------------------------------------------------------------


def get_feedback_report(conn) -> dict:
    """Return aggregate rating statistics across all signals.

    Returns a dict with keys:
        total_rated        int
        useful             int
        not_useful         int
        duplicate          int
        wrong_entity       int
        too_low_priority   int
        snoozed            int
        resolved           int
        usefulness_pct     float  (useful / total rated, excl. snoozed+resolved)
        unrated_open       int    (open signals with no rating yet)
        top_rejection_reasons  list[tuple[str, int]]
    """
    # Count by rating for rated signals
    rows = conn.execute(
        "SELECT rating, COUNT(*) FROM signals WHERE rating IS NOT NULL GROUP BY rating"
    ).fetchall()
    counts: dict[str, int] = {r: 0 for r in VALID_RATINGS}
    for rating, n in rows:
        if rating in counts:
            counts[rating] = n

    total_rated = sum(counts.values())

    # Usefulness % = useful / (total - snoozed - resolved)
    denominator = total_rated - counts["snoozed"] - counts["resolved"]
    usefulness_pct = (counts["useful"] / denominator * 100.0) if denominator > 0 else 0.0

    # Unrated open signals
    unrated_open = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE status = 'open' AND rating IS NULL"
    ).fetchone()[0]

    # Top rejection reasons (ordered by count desc)
    rejection_ratings = ["not_useful", "duplicate", "wrong_entity", "too_low_priority"]
    top_rejection = sorted(
        [(r, counts[r]) for r in rejection_ratings if counts[r] > 0],
        key=lambda x: x[1],
        reverse=True,
    )

    return {
        "total_rated": total_rated,
        "useful": counts["useful"],
        "not_useful": counts["not_useful"],
        "duplicate": counts["duplicate"],
        "wrong_entity": counts["wrong_entity"],
        "too_low_priority": counts["too_low_priority"],
        "snoozed": counts["snoozed"],
        "resolved": counts["resolved"],
        "usefulness_pct": usefulness_pct,
        "unrated_open": unrated_open,
        "top_rejection_reasons": top_rejection,
    }
