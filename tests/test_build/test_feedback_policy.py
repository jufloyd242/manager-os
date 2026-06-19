"""Tests for feedback policy layer — signal status changes, learning candidates,
dashboard filtering, and daily brief scoring integration.

No LLM/Gemini calls. All deterministic.
"""

from __future__ import annotations

from datetime import date

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.build.feedback import mark
from manager_os.build.feedback_policy import (
    HIDDEN_STATUSES,
    RATING_TO_SIGNAL_STATUS,
    apply_signal_feedback_effect,
    get_feedback_effects_for_signal,
    should_hide_signal_by_default,
    score_delta_for_signal,
    list_feedback_patterns,
    list_learning_candidates,
    update_learning_candidate_status,
)
from manager_os.build.dashboard_data import get_today_signals


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    c = get_connection(":memory:")
    yield c
    c.close()


def _seed_signal(conn, entity_name="Acme", signal_type="risk", severity="high",
                 source_path="/notes/test.md", status="open") -> str:
    sig_id = content_hash(f"sig::{entity_name}::{signal_type}::{severity}::{date.today()}")
    conn.execute(
        """
        INSERT INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, confidence, status, created_at, updated_at)
        VALUES (?, ?, 'rule', ?, 'client', ?, ?, ?, 'Test signal', '',
                TRUE, 1.0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [sig_id, date.today().isoformat(), source_path,
         entity_name, signal_type, severity, status],
    )
    return sig_id


class _FakeSignal:
    """Lightweight signal object for policy tests."""
    def __init__(self, sig_id, status="open", source_path="/notes/test.md",
                 entity_name="Acme", signal_type="risk", severity="high"):
        self.id = sig_id
        self.status = status
        self.source_path = source_path
        self.entity_name = entity_name
        self.signal_type = signal_type
        self.severity = severity


# ===========================================================================
# Phase 2 — Rating semantics / immediate status changes
# ===========================================================================


class TestSignalStatusChanges:
    def test_wrong_sets_status_to_wrong(self, conn):
        sig_id = _seed_signal(conn)
        mark(conn, item_id=f"signal:{sig_id[:16]}", rating="wrong",
             source_path="/notes/test.md", entity_name="Acme", signal_type="risk")
        result = apply_signal_feedback_effect(
            conn, signal_id=sig_id, item_id=f"signal:{sig_id[:16]}",
            rating="wrong", source_path="/notes/test.md",
            entity_name="Acme", signal_type="risk",
        )
        assert result["status_changed"] is True
        assert result["new_status"] == "wrong"
        status = conn.execute("SELECT status FROM signals WHERE id = ?", [sig_id]).fetchone()[0]
        assert status == "wrong"

    def test_noisy_sets_status_to_noisy(self, conn):
        sig_id = _seed_signal(conn)
        apply_signal_feedback_effect(
            conn, signal_id=sig_id, item_id=f"signal:{sig_id[:16]}",
            rating="noisy", source_path="/notes/test.md",
            entity_name="Acme", signal_type="risk",
        )
        status = conn.execute("SELECT status FROM signals WHERE id = ?", [sig_id]).fetchone()[0]
        assert status == "noisy"

    def test_stale_sets_status_to_stale(self, conn):
        sig_id = _seed_signal(conn)
        apply_signal_feedback_effect(
            conn, signal_id=sig_id, item_id=f"signal:{sig_id[:16]}",
            rating="stale", source_path="/notes/test.md",
            entity_name="Acme", signal_type="risk",
        )
        status = conn.execute("SELECT status FROM signals WHERE id = ?", [sig_id]).fetchone()[0]
        assert status == "stale"

    def test_missing_context_sets_needs_context(self, conn):
        sig_id = _seed_signal(conn)
        apply_signal_feedback_effect(
            conn, signal_id=sig_id, item_id=f"signal:{sig_id[:16]}",
            rating="missing-context", source_path="/notes/test.md",
            entity_name="Acme", signal_type="risk",
        )
        status = conn.execute("SELECT status FROM signals WHERE id = ?", [sig_id]).fetchone()[0]
        assert status == "needs_context"

    def test_useful_does_not_change_status(self, conn):
        sig_id = _seed_signal(conn)
        apply_signal_feedback_effect(
            conn, signal_id=sig_id, item_id=f"signal:{sig_id[:16]}",
            rating="useful", source_path="/notes/test.md",
            entity_name="Acme", signal_type="risk",
        )
        status = conn.execute("SELECT status FROM signals WHERE id = ?", [sig_id]).fetchone()[0]
        assert status == "open"  # unchanged


# ===========================================================================
# Phase 5 — Dashboard filtering
# ===========================================================================


class TestDashboardFiltering:
    def test_default_excludes_wrong_noisy_stale(self, conn):
        _seed_signal(conn, entity_name="A", status="open")
        _seed_signal(conn, entity_name="B", status="wrong")
        _seed_signal(conn, entity_name="C", status="noisy")
        _seed_signal(conn, entity_name="D", status="stale")
        signals = get_today_signals(conn, min_severity="low")
        names = {s.entity_name for s in signals}
        assert "A" in names
        assert "B" not in names
        assert "C" not in names
        assert "D" not in names

    def test_default_includes_needs_context(self, conn):
        _seed_signal(conn, entity_name="Ctx", status="needs_context")
        signals = get_today_signals(conn, min_severity="low")
        names = {s.entity_name for s in signals}
        assert "Ctx" in names

    def test_include_hidden_shows_all(self, conn):
        _seed_signal(conn, entity_name="H", status="wrong")
        signals = get_today_signals(conn, min_severity="low", include_feedback_hidden=True)
        names = {s.entity_name for s in signals}
        assert "H" in names

    def test_should_hide_signal_by_default(self, conn):
        sig = _FakeSignal("abc", status="wrong")
        hidden, reason = should_hide_signal_by_default(conn, sig)
        assert hidden is True
        assert "wrong" in reason

    def test_should_not_hide_open_signal(self, conn):
        sig = _FakeSignal("abc", status="open")
        hidden, _ = should_hide_signal_by_default(conn, sig)
        assert hidden is False


# ===========================================================================
# Phase 6 — Learning candidates
# ===========================================================================


class TestLearningCandidates:
    def test_wrong_creates_candidate_after_1_event(self, conn):
        sig_id = _seed_signal(conn)
        mark(conn, item_id=f"signal:{sig_id[:16]}", rating="wrong",
             source_path="/notes/wrong.md", entity_name="Acme", signal_type="risk")
        apply_signal_feedback_effect(
            conn, signal_id=sig_id, item_id=f"signal:{sig_id[:16]}",
            rating="wrong", source_path="/notes/wrong.md",
            entity_name="Acme", signal_type="risk",
        )
        candidates = list_learning_candidates(conn, status="open")
        assert len(candidates) >= 1
        assert any(c["rating"] == "wrong" for c in candidates)

    def test_noisy_creates_candidate_after_2_events(self, conn):
        # First event — below threshold
        sig1 = _seed_signal(conn, entity_name="E1")
        mark(conn, item_id=f"signal:{sig1[:16]}", rating="noisy",
             source_path="/notes/noisy.md", entity_name="NoisyEntity", signal_type="risk")
        apply_signal_feedback_effect(
            conn, signal_id=sig1, item_id=f"signal:{sig1[:16]}",
            rating="noisy", source_path="/notes/noisy.md",
            entity_name="NoisyEntity", signal_type="risk",
        )
        candidates = list_learning_candidates(conn, status="open")
        assert len(candidates) == 0  # threshold=2, not met yet

        # Second event — meets threshold
        sig2 = _seed_signal(conn, entity_name="E2")
        mark(conn, item_id=f"signal:{sig2[:16]}", rating="noisy",
             source_path="/notes/noisy.md", entity_name="NoisyEntity", signal_type="risk")
        apply_signal_feedback_effect(
            conn, signal_id=sig2, item_id=f"signal:{sig2[:16]}",
            rating="noisy", source_path="/notes/noisy.md",
            entity_name="NoisyEntity", signal_type="risk",
        )
        candidates = list_learning_candidates(conn, status="open")
        assert len(candidates) >= 1

    def test_useful_does_not_create_candidate(self, conn):
        sig_id = _seed_signal(conn)
        mark(conn, item_id=f"signal:{sig_id[:16]}", rating="useful",
             source_path="/notes/good.md", entity_name="Good", signal_type="risk")
        apply_signal_feedback_effect(
            conn, signal_id=sig_id, item_id=f"signal:{sig_id[:16]}",
            rating="useful", source_path="/notes/good.md",
            entity_name="Good", signal_type="risk",
        )
        candidates = list_learning_candidates(conn, status="open")
        assert len(candidates) == 0

    def test_update_candidate_status(self, conn):
        sig_id = _seed_signal(conn)
        mark(conn, item_id=f"signal:{sig_id[:16]}", rating="wrong",
             source_path="/notes/w.md", entity_name="W", signal_type="risk")
        apply_signal_feedback_effect(
            conn, signal_id=sig_id, item_id=f"signal:{sig_id[:16]}",
            rating="wrong", source_path="/notes/w.md",
            entity_name="W", signal_type="risk",
        )
        candidates = list_learning_candidates(conn, status="open")
        assert len(candidates) >= 1
        cid = candidates[0]["id"]
        update_learning_candidate_status(conn, cid, "accepted")
        accepted = list_learning_candidates(conn, status="accepted")
        assert any(c["id"] == cid for c in accepted)
        open_cands = list_learning_candidates(conn, status="open")
        assert not any(c["id"] == cid for c in open_cands)


# ===========================================================================
# Phase 8 — Score deltas
# ===========================================================================


class TestScoreDeltas:
    def test_wrong_exact_item_large_penalty(self, conn):
        sig_id = _seed_signal(conn)
        mark(conn, item_id=f"signal:{sig_id[:16]}", rating="wrong",
             source_path="/notes/x.md", entity_name="X", signal_type="risk")
        sig = _FakeSignal(sig_id, source_path="/notes/x.md", entity_name="X", signal_type="risk")
        delta, explanations = score_delta_for_signal(conn, sig)
        assert delta <= -100  # exact wrong = -100
        assert any("wrong" in e for e in explanations)

    def test_useful_exact_item_positive(self, conn):
        sig_id = _seed_signal(conn)
        mark(conn, item_id=f"signal:{sig_id[:16]}", rating="useful",
             source_path="/notes/g.md", entity_name="G", signal_type="risk")
        sig = _FakeSignal(sig_id, source_path="/notes/g.md", entity_name="G", signal_type="risk")
        delta, _ = score_delta_for_signal(conn, sig)
        assert delta > 0

    def test_no_feedback_zero_delta(self, conn):
        sig = _FakeSignal("nonexistent", source_path="/none.md", entity_name="None", signal_type="risk")
        delta, explanations = score_delta_for_signal(conn, sig)
        assert delta == 0.0
        assert explanations == []


# ===========================================================================
# Phase 5 — get_feedback_effects_for_signal
# ===========================================================================


class TestFeedbackEffectsForSignal:
    def test_returns_latest_rating(self, conn):
        sig_id = _seed_signal(conn)
        mark(conn, item_id=f"signal:{sig_id[:16]}", rating="wrong",
             source_path="/notes/x.md", entity_name="X", signal_type="risk")
        sig = _FakeSignal(sig_id, status="wrong", source_path="/notes/x.md",
                          entity_name="X", signal_type="risk")
        effects = get_feedback_effects_for_signal(conn, sig)
        assert effects["latest_rating"] == "wrong"
        assert effects["hidden_by_default"] is True

    def test_open_signal_not_hidden(self, conn):
        sig = _FakeSignal("abc123", status="open")
        effects = get_feedback_effects_for_signal(conn, sig)
        assert effects["hidden_by_default"] is False


# ===========================================================================
# Phase 10 — list_feedback_patterns
# ===========================================================================


class TestFeedbackPatterns:
    def test_patterns_require_min_count(self, conn):
        sig1 = _seed_signal(conn, entity_name="P1")
        sig2 = _seed_signal(conn, entity_name="P2")
        mark(conn, item_id=f"signal:{sig1[:16]}", rating="noisy",
             source_path="/notes/pat.md", entity_name="Pat", signal_type="risk")
        mark(conn, item_id=f"signal:{sig2[:16]}", rating="noisy",
             source_path="/notes/pat.md", entity_name="Pat", signal_type="risk")
        patterns = list_feedback_patterns(conn, min_count=2)
        assert len(patterns) >= 1
        assert any(p["source_path"] == "/notes/pat.md" for p in patterns)

    def test_no_patterns_below_threshold(self, conn):
        sig_id = _seed_signal(conn)
        mark(conn, item_id=f"signal:{sig_id[:16]}", rating="noisy",
             source_path="/notes/single.md", entity_name="S", signal_type="risk")
        patterns = list_feedback_patterns(conn, min_count=2)
        assert not any(p["source_path"] == "/notes/single.md" for p in patterns)


# ===========================================================================
# Phase 4 — Dashboard import smoke test
# ===========================================================================


class TestDashboardImport:
    def test_dashboard_compiles(self):
        import py_compile
        from pathlib import Path
        app_path = Path(__file__).parent.parent.parent / "src" / "manager_os" / "dashboard" / "app.py"
        py_compile.compile(str(app_path), doraise=True)

    def test_feedback_policy_compiles(self):
        import py_compile
        from pathlib import Path
        p = Path(__file__).parent.parent.parent / "src" / "manager_os" / "build" / "feedback_policy.py"
        py_compile.compile(str(p), doraise=True)
