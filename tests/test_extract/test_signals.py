"""Tests for rule-based signal extraction (extract/signals.py)."""

from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.extract.signals import run_rule_extraction
from manager_os.ingest.deals import ingest_deals
from manager_os.ingest.forecast import ingest_forecast
from manager_os.ingest.obsidian import ingest_vault

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    dest = tmp_path / "vault"
    shutil.copytree(FIXTURES / "vault", dest)
    return dest


def _seed_note(conn, body: str, note_type: str = "client", entity_type: str = "client",
               entity_name: str = "Acme Corp", note_date: date | None = None) -> str:
    """Insert a minimal note row directly for testing."""
    import uuid
    note_id = str(uuid.uuid4())
    nd = (note_date or date.today()).isoformat()
    conn.execute(
        """
        INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                           entity_name, title, body, tags, created_at)
        VALUES (?, 'raw-test', ?, ?, ?, ?, 'Test Note', ?, '[]', CURRENT_TIMESTAMP)
        """,
        [note_id, nd, note_type, entity_type, entity_name, body],
    )
    return note_id


def _seed_deal(conn, deal_name: str, account: str, close_date: date, sow_status: str) -> None:
    from manager_os.db import content_hash
    row_id = content_hash(f"{account}::{deal_name}")
    conn.execute(
        """
        INSERT INTO deals (id, account, deal_name, stage, close_date, sow_status, loe_status, updated_at)
        VALUES (?, ?, ?, 'SOW Review', ?, ?, 'signed', CURRENT_TIMESTAMP)
        """,
        [row_id, account, deal_name, close_date.isoformat(), sow_status],
    )


def _seed_forecast_row(conn, person_name: str, week_start: date, allocation_pct: float) -> None:
    from manager_os.db import content_hash
    row_id = content_hash(f"{person_name}::{week_start}::client::project")
    conn.execute(
        """
        INSERT INTO staffing_forecast (id, person_name, week_start, allocation_pct, forecast_type, ingested_at)
        VALUES (?, ?, ?, ?, 'confirmed', CURRENT_TIMESTAMP)
        """,
        [row_id, person_name, week_start.isoformat(), allocation_pct],
    )


# ------------------------------------------------------------------
# Rule 1 — risk keyword
# ------------------------------------------------------------------


def test_rule_risk_keyword_creates_signal(conn) -> None:
    _seed_note(conn, body="The project is at risk of missing the milestone.")
    result = run_rule_extraction(conn, run_date=date.today())
    assert result.written >= 1
    sigs = conn.execute("SELECT signal_type, severity FROM signals").fetchall()
    assert any(s[0] == "risk" and s[1] == "high" for s in sigs)


def test_rule_risk_keyword_no_false_positive(conn) -> None:
    _seed_note(conn, body="Everything is going well. No issues to report.")
    result = run_rule_extraction(conn, run_date=date.today())
    risk_sigs = conn.execute(
        "SELECT id FROM signals WHERE signal_type = 'risk'"
    ).fetchall()
    assert len(risk_sigs) == 0


def test_rule_risk_keyword_various_words(conn) -> None:
    for kw in ["escalated", "blocked", "concern"]:
        _seed_note(conn, body=f"The situation is {kw}.", entity_name=f"Client-{kw}")
    result = run_rule_extraction(conn, run_date=date.today())
    assert result.written >= 3


# ------------------------------------------------------------------
# Rule 2 — stale 1:1
# ------------------------------------------------------------------


def test_rule_stale_1on1_triggers_for_old_note(conn) -> None:
    old_date = date.today() - timedelta(days=20)
    _seed_note(conn, body="Good 1:1.", note_type="1on1", entity_type="person",
               entity_name="Alice Chen", note_date=old_date)
    result = run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT signal_type, entity_name FROM signals WHERE signal_type = 'people_health'"
    ).fetchall()
    assert any(s[1] == "Alice Chen" for s in sigs)


def test_rule_stale_1on1_no_signal_for_recent(conn) -> None:
    recent_date = date.today() - timedelta(days=5)
    _seed_note(conn, body="Good 1:1.", note_type="1on1", entity_type="person",
               entity_name="Alice Chen", note_date=recent_date)
    run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT id FROM signals WHERE signal_type = 'people_health'"
    ).fetchall()
    assert len(sigs) == 0


def test_rule_stale_1on1_exactly_14_days_triggers(conn) -> None:
    # Exactly 14 days = still stale (cutoff is > 14 days)
    boundary = date.today() - timedelta(days=14)
    _seed_note(conn, body="Good 1:1.", note_type="1on1", entity_type="person",
               entity_name="Bob Martinez", note_date=boundary)
    run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT id FROM signals WHERE signal_type = 'people_health' AND entity_name = 'Bob Martinez'"
    ).fetchall()
    assert len(sigs) == 1


# ------------------------------------------------------------------
# Rule 3 — SOW near deadline
# ------------------------------------------------------------------


def test_rule_sow_near_deadline_triggers(conn) -> None:
    close = date.today() + timedelta(days=3)
    _seed_deal(conn, "Big Deal", "Acme Corp", close, sow_status="pending")
    run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT signal_type, severity FROM signals WHERE signal_type = 'sow_loe_review'"
    ).fetchall()
    assert len(sigs) == 1
    assert sigs[0][1] == "high"


def test_rule_sow_no_signal_for_signed(conn) -> None:
    close = date.today() + timedelta(days=3)
    _seed_deal(conn, "Signed Deal", "Acme Corp", close, sow_status="signed")
    run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT id FROM signals WHERE signal_type = 'sow_loe_review'"
    ).fetchall()
    assert len(sigs) == 0


def test_rule_sow_no_signal_for_far_future(conn) -> None:
    far = date.today() + timedelta(days=30)
    _seed_deal(conn, "Future Deal", "Acme Corp", far, sow_status="pending")
    run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT id FROM signals WHERE signal_type = 'sow_loe_review'"
    ).fetchall()
    assert len(sigs) == 0


# ------------------------------------------------------------------
# Rule 4 — overallocation
# ------------------------------------------------------------------


def test_rule_overallocation_triggers(conn) -> None:
    _seed_forecast_row(conn, "David Park", date.today(), 120.0)
    run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT signal_type, entity_name FROM signals WHERE signal_type = 'utilization_risk'"
    ).fetchall()
    assert any(s[1] == "David Park" for s in sigs)


def test_rule_overallocation_no_signal_for_normal(conn) -> None:
    _seed_forecast_row(conn, "Alice Chen", date.today(), 80.0)
    run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT id FROM signals WHERE signal_type = 'utilization_risk'"
    ).fetchall()
    assert len(sigs) == 0


def test_rule_overallocation_no_signal_for_future_week(conn) -> None:
    far_week = date.today() + timedelta(days=30)
    _seed_forecast_row(conn, "Carmen Liu", far_week, 120.0)
    run_rule_extraction(conn, run_date=date.today())
    sigs = conn.execute(
        "SELECT id FROM signals WHERE signal_type = 'utilization_risk'"
    ).fetchall()
    assert len(sigs) == 0


# ------------------------------------------------------------------
# Deduplication
# ------------------------------------------------------------------


def test_signals_not_duplicated_on_second_run(conn) -> None:
    _seed_note(conn, body="This is at risk.")
    run_rule_extraction(conn, run_date=date.today())
    run_rule_extraction(conn, run_date=date.today())
    count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    # Should have exactly 1 risk signal (deduped)
    risk_count = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE signal_type = 'risk'"
    ).fetchone()[0]
    assert risk_count == 1


# ------------------------------------------------------------------
# Integration: fixture data
# ------------------------------------------------------------------


def test_rule_extraction_on_fixture_data(conn, vault_dir: Path) -> None:
    ingest_vault(str(vault_dir), conn)
    ingest_deals(str(FIXTURES / "deals.csv"), conn)
    ingest_forecast(str(FIXTURES / "forecast.csv"), conn)

    # Use a specific run_date so SOW rule fires for Big Retail (close 2026-06-17)
    run_date = date(2026, 6, 13)
    result = run_rule_extraction(conn, run_date=run_date)
    assert result.written >= 1, f"Expected at least 1 signal, got {result.written}"

    signal_types = conn.execute("SELECT DISTINCT signal_type FROM signals").fetchall()
    types = {r[0] for r in signal_types}
    # At least one of the rules should fire on fixture data
    assert len(types) >= 1


# ===========================================================================
# Severity assignment
# ===========================================================================


class TestRiskKeywordSeverity:
    def test_blocked_keyword_is_high(self, conn) -> None:
        _seed_note(conn, body="Task is blocked by the client approval.")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert all(s[0] == "high" for s in sigs)

    def test_overdue_keyword_is_high(self, conn) -> None:
        _seed_note(conn, body="The deliverable is overdue.", entity_name="Overdue Client")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(s[0] == "high" for s in sigs)

    def test_escalate_keyword_is_high(self, conn) -> None:
        _seed_note(conn, body="We may need to escalate this issue.", entity_name="Escalate Co")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(s[0] == "high" for s in sigs)

    def test_delay_keyword_is_medium(self, conn) -> None:
        _seed_note(conn, body="The project is experiencing a delay.", entity_name="Delay Corp")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(s[0] == "medium" for s in sigs)
        assert not any(s[0] == "high" for s in sigs)

    def test_concern_keyword_is_medium(self, conn) -> None:
        _seed_note(conn, body="There is some concern about the timeline.", entity_name="Concern Inc")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(s[0] == "medium" for s in sigs)

    def test_mixed_notes_not_all_high(self, conn) -> None:
        """Vault with both high and medium keyword notes must not produce all-high signals."""
        _seed_note(conn, body="The project is blocked.", entity_name="Client Alpha")
        _seed_note(conn, body="Some delay expected next week.", entity_name="Client Beta")
        _seed_note(conn, body="Concern about resourcing.", entity_name="Client Gamma")
        run_rule_extraction(conn, run_date=date.today())
        rows = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        severities = {r[0] for r in rows}
        assert "high" in severities
        assert "medium" in severities
        # Not every signal should be high
        assert severities != {"high"}

    def test_bloated_keyword_is_low(self, conn) -> None:
        _seed_note(conn, body="The backlog is getting bloated.", entity_name="Backlog Co")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(s[0] == "low" for s in sigs)
        assert not any(s[0] in ("high", "medium") for s in sigs)

    def test_at_risk_is_high(self, conn) -> None:
        _seed_note(conn, body="The deal is at risk of falling through.", entity_name="Risk Deal")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(s[0] == "high" for s in sigs)

    def test_requires_manager_attention_only_for_high(self, conn) -> None:
        """requires_manager_attention should be True only for high-severity signals."""
        _seed_note(conn, body="We have a concern about scope.", entity_name="Scope Co")
        run_rule_extraction(conn, run_date=date.today())
        rows = conn.execute(
            "SELECT severity, requires_manager_attention FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        for sev, rma in rows:
            if sev == "medium":
                assert not rma, "Medium signals should not require manager attention"

    def test_status_distribution_not_all_high_for_mixed_data(self, conn) -> None:
        """With both high and medium keyword notes, not every risk signal should be high."""
        _seed_note(conn, body="The project is blocked.", entity_name="Client Alpha")
        _seed_note(conn, body="There is some concern about scope.", entity_name="Client Beta")
        _seed_note(conn, body="Some delay expected next sprint.", entity_name="Client Gamma")
        run_rule_extraction(conn, run_date=date.today())
        rows = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        severities = {r[0] for r in rows}
        assert "high" in severities
        assert "medium" in severities
        assert severities != {"high"}


# ===========================================================================
# Phase 4 — source path quality + severity downgrade
# ===========================================================================


class TestSourcePathQuality:
    """Risk signals must store a readable vault path, not a raw_document_id hash."""

    def _seed_note_with_raw_doc(self, conn, source_path: str, body: str,
                                entity_name: str = "Acme Corp") -> None:
        """Seed a raw_document + note pair to simulate full ingest."""
        from manager_os.db import content_hash
        import uuid
        raw_id = content_hash(source_path)
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_documents
                (id, source_type, source_path, content_hash, content, ingested_at)
            VALUES (?, 'obsidian', ?, ?, 'body text', CURRENT_TIMESTAMP)
            """,
            [raw_id, source_path, raw_id],
        )
        note_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                               entity_name, title, body, tags, created_at)
            VALUES (?, ?, ?, 'client', 'client', ?, 'Test Note', ?, '[]', CURRENT_TIMESTAMP)
            """,
            [note_id, raw_id, date.today().isoformat(), entity_name, body],
        )

    def test_source_path_is_vault_path_not_hash(self, conn) -> None:
        vault_path = "/vault/work/clients/Acme/status-2026-06.md"
        self._seed_note_with_raw_doc(conn, vault_path, "The project is blocked.")
        run_rule_extraction(conn, run_date=date.today())
        row = conn.execute(
            "SELECT source_path FROM signals WHERE signal_type = 'risk'"
        ).fetchone()
        assert row is not None
        sp = row[0]
        # Must be the vault path, not a hex hash
        assert sp == vault_path, f"Expected vault path, got: {sp!r}"
        # Must NOT look like a hex hash
        assert not (len(sp) >= 32 and all(c in "0123456789abcdefABCDEF" for c in sp)), (
            f"source_path looks like a hash: {sp!r}"
        )

    def test_summary_contains_risk_keyword_context(self, conn) -> None:
        vault_path = "/vault/work/clients/Acme/status.md"
        body = "Project is on track.\nThe integration work is blocked by client approval.\nOtherwise fine."
        self._seed_note_with_raw_doc(conn, vault_path, body, entity_name="Acme Corp")
        run_rule_extraction(conn, run_date=date.today())
        row = conn.execute(
            "SELECT summary FROM signals WHERE signal_type = 'risk'"
        ).fetchone()
        assert row is not None
        summary = row[0]
        # Summary must not be the generic placeholder
        assert "Risk language detected in note" not in summary or "blocked" in summary.lower()
        # Summary should contain some content from the note
        assert len(summary) > 20


class TestEntityBasedSeverityDowngrade:
    """Notes without a named entity should not produce high-severity risk signals."""

    def test_note_without_entity_high_keyword_downgraded_to_medium(self, conn) -> None:
        # Note with entity_name='' (generic/untitled)
        _seed_note(conn, body="This is blocked and urgent.", entity_name="")
        run_rule_extraction(conn, run_date=date.today())
        rows = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        # Should be medium (downgraded from high due to missing entity)
        assert any(r[0] == "medium" for r in rows)
        assert not any(r[0] == "high" for r in rows)

    def test_note_without_entity_medium_keyword_downgraded_to_low(self, conn) -> None:
        _seed_note(conn, body="There is a concern about something.", entity_name="")
        run_rule_extraction(conn, run_date=date.today())
        rows = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(r[0] == "low" for r in rows)

    def test_note_with_entity_high_keyword_stays_high(self, conn) -> None:
        _seed_note(conn, body="Acme project is blocked.", entity_name="Acme Corp")
        run_rule_extraction(conn, run_date=date.today())
        rows = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(r[0] == "high" for r in rows)


class TestSOWDeadlineWindow:
    """SOW near-deadline rule should now fire within 14 days, not just 7."""

    def test_sow_fires_within_14_days(self, conn) -> None:
        close = date.today() + timedelta(days=10)
        _seed_deal(conn, "Medium Deal", "Acme", close, sow_status="pending")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'sow_loe_review'"
        ).fetchall()
        assert len(sigs) == 1
        # 10 days → medium (not urgent yet)
        assert sigs[0][0] == "medium"

    def test_sow_fires_within_7_days_as_high(self, conn) -> None:
        close = date.today() + timedelta(days=5)
        _seed_deal(conn, "Urgent Deal", "Acme", close, sow_status="pending")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'sow_loe_review'"
        ).fetchall()
        assert len(sigs) == 1
        assert sigs[0][0] == "high"

    def test_sow_no_signal_beyond_14_days(self, conn) -> None:
        far = date.today() + timedelta(days=20)
        _seed_deal(conn, "Far Deal", "Acme", far, sow_status="pending")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT id FROM signals WHERE signal_type = 'sow_loe_review'"
        ).fetchall()
        assert len(sigs) == 0


# ===========================================================================
# Phase 5 — noise filters, deal source, global budget
# ===========================================================================


class TestNoiseFilters:
    """Risk signals from headings, system docs, and non-actionable snippets are suppressed."""

    def test_critical_skill_heading_is_not_high_risk(self, conn) -> None:
        """A note whose only 'critical' usage is in a heading like '**Critical Skill:**'
        should not produce a high-severity signal."""
        body = (
            "## Engagement Status\n\n"
            "- **Critical Skill:** Gemini Enterprise CX (GECX) / CCAI.\n"
            "- Team is fully staffed and on track.\n"
        )
        _seed_note(conn, body=body, entity_name="Acme Corp")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        # Should be downgraded — not high severity
        assert not any(s[0] == "high" for s in sigs), (
            "Heading-only 'Critical Skill' should not create a high-severity signal"
        )

    def test_critical_risks_section_heading_is_not_high(self, conn) -> None:
        body = "## Critical Risks\n\nNo specific blockers at this time.\n"
        _seed_note(conn, body=body, entity_name="Client Beta")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert not any(s[0] == "high" for s in sigs)

    def test_gemini_md_is_skipped(self, conn) -> None:
        """Notes from GEMINI.md / instruction docs must not produce risk signals."""
        import uuid
        raw_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_documents
                (id, source_type, source_path, content_hash, content, ingested_at)
            VALUES (?, 'obsidian', '/vault/.gemini/GEMINI.md', ?, 'blocked content', CURRENT_TIMESTAMP)
            """,
            [raw_id, raw_id],
        )
        note_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
                               entity_name, title, body, tags, created_at)
            VALUES (?, ?, ?, 'client', 'client', 'Acme', 'GEMINI', 'This is blocked.', '[]', CURRENT_TIMESTAMP)
            """,
            [note_id, raw_id, date.today().isoformat()],
        )
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT id FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert len(sigs) == 0, "GEMINI.md notes must not produce risk signals"

    def test_actionable_blocked_note_remains_high(self, conn) -> None:
        """A note that says 'the integration is blocked by client approval' should stay high."""
        body = "The integration work is blocked by client approval. Risk of escalation."
        _seed_note(conn, body=body, entity_name="Acme Corp")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        assert any(s[0] == "high" for s in sigs)

    def test_image_artifact_snippet_is_suppressed_or_downgraded(self, conn) -> None:
        """Notes whose only keyword match is inside an image export artifact are downgraded."""
        body = (
            "Meeting notes.\n\n"
            "![Exported image](Exported%20image%20critical%20dates.png)\n\n"
            "Action items TBD.\n"
        )
        _seed_note(conn, body=body, entity_name="Corp A")
        run_rule_extraction(conn, run_date=date.today())
        sigs = conn.execute(
            "SELECT severity FROM signals WHERE signal_type = 'risk'"
        ).fetchall()
        # Should not be high — image artifact is not actionable
        assert not any(s[0] == "high" for s in sigs)


class TestDealSourcePath:
    """SOW near-deadline signals should store a readable deals:: source path."""

    def test_deal_signal_source_path_contains_deals_prefix(self, conn) -> None:
        close = date.today() + timedelta(days=3)
        _seed_deal(conn, "Big Deal", "Acme Corp", close, sow_status="pending")
        run_rule_extraction(conn, run_date=date.today())
        row = conn.execute(
            "SELECT source_path FROM signals WHERE signal_type = 'sow_loe_review'"
        ).fetchone()
        assert row is not None
        sp = row[0]
        assert sp.startswith("deals::"), f"Expected deals:: prefix, got: {sp!r}"

    def test_deal_signal_source_path_not_empty(self, conn) -> None:
        close = date.today() + timedelta(days=5)
        _seed_deal(conn, "Another Deal", "Client Co", close, sow_status="pending")
        run_rule_extraction(conn, run_date=date.today())
        row = conn.execute(
            "SELECT source_path FROM signals WHERE signal_type = 'sow_loe_review'"
        ).fetchone()
        assert row is not None
        assert row[0], "Deal signal source_path must not be empty"


