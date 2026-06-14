"""Tests for the signal usefulness feedback loop."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.build.signal_feedback import (
    VALID_RATINGS,
    SUPPRESSED_RATINGS,
    rate_signal,
    get_feedback_report,
)
from manager_os.build.daily_brief import generate_daily_brief


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def _seed_signal(
    conn,
    entity_name: str = "Acme Corp",
    signal_type: str = "risk",
    severity: str = "high",
    summary: str = "Test signal",
    status: str = "open",
) -> str:
    sig_id = content_hash(f"sig::{entity_name}::{signal_type}::{severity}::{summary}")
    conn.execute(
        """
        INSERT INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, confidence, status, created_at, updated_at)
        VALUES (?, ?, 'rule', '', 'client', ?, ?, ?, ?, 'Matters.', TRUE, 1.0, ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [sig_id, date.today().isoformat(), entity_name, signal_type, severity, summary, status],
    )
    return sig_id


# ---------------------------------------------------------------------------
# rate_signal — valid cases
# ---------------------------------------------------------------------------


class TestRateSignalValid:
    def test_valid_rating_persisted(self, conn) -> None:
        sig_id = _seed_signal(conn)
        rate_signal(conn, signal_id=sig_id, rating="useful")
        row = conn.execute("SELECT rating FROM signals WHERE id = ?", [sig_id]).fetchone()
        assert row is not None
        assert row[0] == "useful"

    def test_all_valid_ratings_accepted(self, conn) -> None:
        for rating in VALID_RATINGS:
            sig_id = _seed_signal(conn, entity_name=f"Corp_{rating}", summary=f"sig_{rating}")
            rate_signal(conn, signal_id=sig_id, rating=rating)
            row = conn.execute("SELECT rating FROM signals WHERE id = ?", [sig_id]).fetchone()
            assert row[0] == rating, f"Expected {rating}, got {row[0]}"

    def test_note_stored_in_status_log(self, conn) -> None:
        sig_id = _seed_signal(conn)
        rate_signal(conn, signal_id=sig_id, rating="not_useful", note="Too generic")
        row = conn.execute(
            "SELECT note FROM signal_status_log WHERE signal_id = ?", [sig_id]
        ).fetchone()
        assert row is not None
        assert row[0] == "Too generic"

    def test_rating_written_to_status_log(self, conn) -> None:
        sig_id = _seed_signal(conn)
        rate_signal(conn, signal_id=sig_id, rating="duplicate")
        row = conn.execute(
            "SELECT old_status, new_status, changed_by FROM signal_status_log WHERE signal_id = ?",
            [sig_id],
        ).fetchone()
        assert row is not None
        assert row[1] == "duplicate"
        assert row[2] == "cli"

    def test_snooze_until_stored(self, conn) -> None:
        sig_id = _seed_signal(conn)
        snooze = date.today() + timedelta(days=7)
        rate_signal(conn, signal_id=sig_id, rating="snoozed", snooze_until=snooze)
        row = conn.execute(
            "SELECT rating, snooze_until FROM signals WHERE id = ?", [sig_id]
        ).fetchone()
        assert row[0] == "snoozed"
        assert str(row[1]) == str(snooze)

    def test_rating_history_preserved_across_re_ratings(self, conn) -> None:
        sig_id = _seed_signal(conn)
        rate_signal(conn, signal_id=sig_id, rating="not_useful")
        rate_signal(conn, signal_id=sig_id, rating="useful")
        rows = conn.execute(
            "SELECT new_status FROM signal_status_log WHERE signal_id = ? ORDER BY changed_at",
            [sig_id],
        ).fetchall()
        statuses = [r[0] for r in rows]
        assert "not_useful" in statuses
        assert "useful" in statuses
        assert len(statuses) == 2


# ---------------------------------------------------------------------------
# rate_signal — invalid cases
# ---------------------------------------------------------------------------


class TestRateSignalInvalid:
    def test_unknown_signal_id_raises(self, conn) -> None:
        with pytest.raises(ValueError, match="Signal not found"):
            rate_signal(conn, signal_id="nonexistent_id", rating="useful")

    def test_invalid_rating_raises(self, conn) -> None:
        sig_id = _seed_signal(conn)
        with pytest.raises(ValueError, match="Invalid rating"):
            rate_signal(conn, signal_id=sig_id, rating="totally_wrong")

    def test_snooze_until_without_snoozed_rating_raises(self, conn) -> None:
        sig_id = _seed_signal(conn)
        with pytest.raises(ValueError, match="only valid.*snoozed"):
            rate_signal(
                conn,
                signal_id=sig_id,
                rating="useful",
                snooze_until=date.today() + timedelta(days=5),
            )


# ---------------------------------------------------------------------------
# get_feedback_report
# ---------------------------------------------------------------------------


class TestFeedbackReport:
    def test_empty_db_returns_zeros(self, conn) -> None:
        report = get_feedback_report(conn)
        assert report["total_rated"] == 0
        assert report["useful"] == 0
        assert report["usefulness_pct"] == 0.0
        assert report["unrated_open"] == 0

    def test_counts_ratings_correctly(self, conn) -> None:
        for i in range(3):
            s = _seed_signal(conn, entity_name=f"Useful{i}", summary=f"sig_u{i}")
            rate_signal(conn, signal_id=s, rating="useful")
        for i in range(2):
            s = _seed_signal(conn, entity_name=f"Noise{i}", summary=f"sig_n{i}")
            rate_signal(conn, signal_id=s, rating="not_useful")
        s = _seed_signal(conn, entity_name="Dup", summary="sig_dup")
        rate_signal(conn, signal_id=s, rating="duplicate")

        report = get_feedback_report(conn)
        assert report["total_rated"] == 6
        assert report["useful"] == 3
        assert report["not_useful"] == 2
        assert report["duplicate"] == 1

    def test_usefulness_pct_excludes_snoozed_and_resolved(self, conn) -> None:
        # 2 useful, 1 not_useful, 1 snoozed, 1 resolved
        for i in range(2):
            s = _seed_signal(conn, entity_name=f"U{i}", summary=f"u{i}")
            rate_signal(conn, signal_id=s, rating="useful")
        s = _seed_signal(conn, entity_name="Noise", summary="noise")
        rate_signal(conn, signal_id=s, rating="not_useful")
        s = _seed_signal(conn, entity_name="Snooze", summary="snooze")
        rate_signal(conn, signal_id=s, rating="snoozed")
        s = _seed_signal(conn, entity_name="Resolved", summary="resolved")
        rate_signal(conn, signal_id=s, rating="resolved")

        report = get_feedback_report(conn)
        # denominator = 5 total - 1 snoozed - 1 resolved = 3
        # useful / denom = 2/3 = 66.7%
        assert abs(report["usefulness_pct"] - (2 / 3 * 100)) < 0.1

    def test_unrated_open_count(self, conn) -> None:
        _seed_signal(conn, entity_name="Unrated", summary="unrated sig")
        s = _seed_signal(conn, entity_name="Rated", summary="rated sig")
        rate_signal(conn, signal_id=s, rating="useful")
        report = get_feedback_report(conn)
        assert report["unrated_open"] == 1

    def test_top_rejection_reasons_sorted_by_count(self, conn) -> None:
        for i in range(3):
            s = _seed_signal(conn, entity_name=f"Nu{i}", summary=f"nu{i}")
            rate_signal(conn, signal_id=s, rating="not_useful")
        for i in range(2):
            s = _seed_signal(conn, entity_name=f"Dup{i}", summary=f"dup{i}")
            rate_signal(conn, signal_id=s, rating="duplicate")

        report = get_feedback_report(conn)
        reasons = report["top_rejection_reasons"]
        assert reasons[0] == ("not_useful", 3)
        assert reasons[1] == ("duplicate", 2)


# ---------------------------------------------------------------------------
# Daily brief suppression
# ---------------------------------------------------------------------------


class TestDailyBriefSuppression:
    def test_suppressed_ratings_excluded_from_brief(self, conn) -> None:
        for rating in SUPPRESSED_RATINGS:
            s = _seed_signal(
                conn,
                entity_name=f"Corp_{rating}",
                summary=f"Suppressed signal {rating}",
            )
            rate_signal(conn, signal_id=s, rating=rating)
        brief = generate_daily_brief(conn, target_date=date.today())
        for rating in SUPPRESSED_RATINGS:
            assert f"Suppressed signal {rating}" not in brief.content, (
                f"Signal rated {rating!r} should be suppressed in brief"
            )

    def test_useful_signal_still_shown_in_brief(self, conn) -> None:
        s = _seed_signal(conn, entity_name="Good Corp", summary="Actionable signal")
        rate_signal(conn, signal_id=s, rating="useful")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Actionable signal" in brief.content

    def test_snoozed_signal_still_shown_in_brief(self, conn) -> None:
        """Snoozed is not in SUPPRESSED_RATINGS — shown but the user chose to snooze."""
        s = _seed_signal(conn, entity_name="Snooze Corp", summary="Snoozed signal")
        rate_signal(
            conn,
            signal_id=s,
            rating="snoozed",
            snooze_until=date.today() + timedelta(days=3),
        )
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Snoozed signal" in brief.content

    def test_too_low_priority_not_suppressed_by_default(self, conn) -> None:
        """too_low_priority is not in SUPPRESSED_RATINGS — still shown."""
        s = _seed_signal(conn, entity_name="LowPri Corp", summary="Low priority signal")
        rate_signal(conn, signal_id=s, rating="too_low_priority")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Low priority signal" in brief.content

    def test_unrated_signals_shown_normally(self, conn) -> None:
        _seed_signal(conn, entity_name="Normal Corp", summary="Normal open signal")
        brief = generate_daily_brief(conn, target_date=date.today())
        assert "Normal open signal" in brief.content
