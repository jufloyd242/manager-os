"""Rule-based signal extraction.

Applies deterministic rules to ingested notes and CSV data to produce
Signal records. No LLM required. LLM extraction is added in Issue #21.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from manager_os.db import content_hash
from manager_os.schemas import Signal

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Rule 1 — Risk keyword in note body
# ------------------------------------------------------------------

# Keywords are tiered by urgency. The highest tier that matches determines severity.
_HIGH_RISK_KEYWORDS = [
    "at risk",
    "escalat",
    "blocked",
    "blocking",
    "overdue",
    "urgent",
    "critical",
    "red flag",
]

_MEDIUM_RISK_KEYWORDS = [
    "delay",
    "delayed",
    "miss",
    "missed",
    "concern",
    "concerned",
]

_LOW_RISK_KEYWORDS = [
    "bloated",
]


def _contains_risk_keyword(text: str) -> bool:
    lower = text.lower()
    return any(
        kw in lower
        for kw in _HIGH_RISK_KEYWORDS + _MEDIUM_RISK_KEYWORDS + _LOW_RISK_KEYWORDS
    )


def _risk_keyword_severity(text: str) -> tuple[str, float]:
    """Return (severity, confidence) for the highest-tier keyword found in text."""
    lower = text.lower()
    if any(kw in lower for kw in _HIGH_RISK_KEYWORDS):
        return "high", 0.85
    if any(kw in lower for kw in _MEDIUM_RISK_KEYWORDS):
        return "medium", 0.70
    return "low", 0.50


# ------------------------------------------------------------------
# Dedup helper
# ------------------------------------------------------------------


def _signal_dedup_id(signal_date: date, source_path: str, signal_type: str, entity_name: str) -> str:
    key = f"{signal_date}::{source_path}::{signal_type}::{entity_name}"
    return content_hash(key)


def _signal_exists(conn, signal_id: str) -> bool:
    row = conn.execute("SELECT id FROM signals WHERE id = ?", [signal_id]).fetchone()
    return row is not None


def _write_signal(conn, signal: Signal) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO signals
            (id, signal_date, source, source_path, entity_type, entity_name,
             signal_type, severity, summary, why_it_matters,
             requires_manager_attention, owner, due_date, confidence,
             status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            signal.id,
            signal.signal_date,
            signal.source,
            signal.source_path,
            signal.entity_type,
            signal.entity_name,
            signal.signal_type,
            signal.severity,
            signal.summary,
            signal.why_it_matters,
            signal.requires_manager_attention,
            signal.owner,
            signal.due_date,
            signal.confidence,
            signal.status,
            signal.created_at,
            signal.updated_at,
        ],
    )


@dataclass
class ExtractionResult:
    written: int = 0
    skipped: int = 0
    failed: int = 0
    signals: list[Signal] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------------
# Rule 1: risk keyword in note body → client/deal/person risk signal
# ------------------------------------------------------------------


def _rule_risk_keywords(conn, run_date: date) -> list[Signal]:
    """Emit a risk signal for any note whose body contains risk keywords."""
    rows = conn.execute(
        """
        SELECT id, raw_document_id, note_date, note_type, entity_type, entity_name, title, body
        FROM notes
        WHERE body IS NOT NULL
        """
    ).fetchall()

    signals = []
    for row in rows:
        note_id, raw_document_id, note_date, note_type, entity_type, entity_name, title, body = row
        source_path = raw_document_id or note_id
        if not _contains_risk_keyword(body or ""):
            continue

        # Determine entity for the signal
        etype = entity_type or "team"
        ename = entity_name or title or "Unknown"

        # Map note entity_type to valid Signal entity_type
        if etype not in ("person", "client", "deal", "team", "practice"):
            etype = "team"

        severity, confidence = _risk_keyword_severity(body or "")

        sig_id = _signal_dedup_id(run_date, source_path or note_id, "risk", ename)
        sig = Signal(
            id=sig_id,
            signal_date=run_date,
            source="rule",
            source_path=source_path or "",
            entity_type=etype,  # type: ignore[arg-type]
            entity_name=ename,
            signal_type="risk",
            severity=severity,
            summary=f"Risk language detected in note: {title or 'untitled'}",
            why_it_matters="Note contains risk-indicating language that may require manager attention.",
            requires_manager_attention=(severity == "high"),
            confidence=confidence,
        )
        signals.append(sig)
    return signals


# ------------------------------------------------------------------
# Rule 2: stale 1:1 — no 1:1 note in last 14 days
# ------------------------------------------------------------------


def _rule_stale_1on1(conn, run_date: date) -> list[Signal]:
    """Emit a people_health signal for each person without a recent 1:1."""
    cutoff = run_date - timedelta(days=14)

    rows = conn.execute(
        """
        SELECT entity_name, MAX(note_date) as last_1on1
        FROM notes
        WHERE note_type = '1on1' AND entity_name IS NOT NULL AND entity_name != ''
        GROUP BY entity_name
        """
    ).fetchall()

    signals = []
    for entity_name, last_1on1 in rows:
        if last_1on1 is None:
            last_date = None
        else:
            last_date = last_1on1 if isinstance(last_1on1, date) else date.fromisoformat(str(last_1on1))

        if last_date is None or last_date <= cutoff:
            days_ago = (run_date - last_date).days if last_date else None
            days_str = f"{days_ago} days ago" if days_ago else "never"
            sig_id = _signal_dedup_id(run_date, f"1on1::{entity_name}", "people_health", entity_name)
            sig = Signal(
                id=sig_id,
                signal_date=run_date,
                source="rule",
                source_path="",
                entity_type="person",
                entity_name=entity_name,
                signal_type="people_health",
                severity="medium",
                summary=f"No 1:1 with {entity_name} in the last 14 days (last: {days_str})",
                why_it_matters="Regular 1:1s are important for team health and early risk detection.",
                requires_manager_attention=False,
                confidence=1.0,
            )
            signals.append(sig)
    return signals


# ------------------------------------------------------------------
# Rule 3: near-deadline deal with unsigned SOW
# ------------------------------------------------------------------


def _rule_sow_near_deadline(conn, run_date: date) -> list[Signal]:
    """Emit sow_loe_review signal for deals closing within 7 days without a signed SOW."""
    deadline = run_date + timedelta(days=7)

    rows = conn.execute(
        """
        SELECT id, account, deal_name, close_date, sow_status, loe_status
        FROM deals
        WHERE close_date IS NOT NULL
          AND close_date <= ?
          AND close_date >= ?
          AND (sow_status IS NULL OR sow_status != 'signed')
        """,
        [deadline, run_date],
    ).fetchall()

    signals = []
    for row in rows:
        _, account, deal_name, close_date, sow_status, loe_status = row
        if isinstance(close_date, str):
            close_date = date.fromisoformat(close_date)
        days_left = (close_date - run_date).days

        sig_id = _signal_dedup_id(run_date, f"deal::{deal_name}", "sow_loe_review", deal_name)
        sig = Signal(
            id=sig_id,
            signal_date=run_date,
            source="rule",
            source_path="",
            entity_type="deal",
            entity_name=deal_name,
            signal_type="sow_loe_review",
            severity="high",
            summary=f"SOW unsigned with {days_left} day(s) until close date ({close_date})",
            why_it_matters=f"Deal '{deal_name}' for {account} closes in {days_left} days but SOW is '{sow_status}'.",
            requires_manager_attention=True,
            due_date=close_date,
            confidence=1.0,
        )
        signals.append(sig)
    return signals


# ------------------------------------------------------------------
# Rule 4: overallocation in forecast
# ------------------------------------------------------------------


def _rule_overallocation(conn, run_date: date) -> list[Signal]:
    """Emit utilization_risk signal for engineers over 100% in the next two weeks."""
    horizon = run_date + timedelta(days=14)

    rows = conn.execute(
        """
        SELECT person_name, week_start, SUM(allocation_pct) as total_alloc
        FROM staffing_forecast
        WHERE week_start >= ? AND week_start <= ?
        GROUP BY person_name, week_start
        HAVING SUM(allocation_pct) > 100
        """,
        [run_date, horizon],
    ).fetchall()

    signals = []
    for person_name, week_start, total_alloc in rows:
        if isinstance(week_start, str):
            week_start = date.fromisoformat(week_start)
        sig_id = _signal_dedup_id(run_date, f"forecast::{person_name}::{week_start}", "utilization_risk", person_name)
        sig = Signal(
            id=sig_id,
            signal_date=run_date,
            source="rule",
            source_path="",
            entity_type="person",
            entity_name=person_name,
            signal_type="utilization_risk",
            severity="high",
            summary=f"{person_name} is at {total_alloc:.0f}% allocation week of {week_start}",
            why_it_matters="Overallocation leads to burnout and delivery risk.",
            requires_manager_attention=True,
            confidence=1.0,
        )
        signals.append(sig)
    return signals


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------


def run_rule_extraction(conn, run_date: date | None = None) -> ExtractionResult:
    """Run all rule-based extraction rules and write Signal records to DuckDB.

    Args:
        conn: Open DuckDB connection with schema initialized.
        run_date: The date to use for signal_date. Defaults to today.

    Returns:
        ExtractionResult with written/skipped counts and Signal list.
    """
    if run_date is None:
        run_date = date.today()

    result = ExtractionResult()
    all_signals: list[Signal] = []

    rule_fns = [
        _rule_risk_keywords,
        _rule_stale_1on1,
        _rule_sow_near_deadline,
        _rule_overallocation,
    ]

    for rule_fn in rule_fns:
        try:
            signals = rule_fn(conn, run_date)
            all_signals.extend(signals)
        except Exception as exc:
            logger.error("Rule %s failed: %s", rule_fn.__name__, exc)
            result.failed += 1

    for signal in all_signals:
        try:
            if _signal_exists(conn, signal.id):
                result.skipped += 1
                result.skip_reasons["signal_already_exists"] = (
                    result.skip_reasons.get("signal_already_exists", 0) + 1
                )
            else:
                _write_signal(conn, signal)
                result.written += 1
                result.signals.append(signal)
        except Exception as exc:
            logger.error("Failed to write signal %s: %s", signal.id, exc)
            result.failed += 1

    return result
