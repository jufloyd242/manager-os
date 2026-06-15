"""Rule-based signal extraction.

Applies deterministic rules to ingested notes and CSV data to produce
Signal records. No LLM required. LLM extraction is added in Issue #21.
"""

from __future__ import annotations

import json
import logging
import re
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

# ------------------------------------------------------------------
# Noise filters for rule 1
# ------------------------------------------------------------------

# Source path substrings that indicate non-actionable / system / template docs.
# Notes from these paths are excluded from risk signals or downgraded to low.
_NOISY_SOURCE_SUBSTRINGS = [
    "gemini.md",
    "/.gemini/",
    "GEMINI.md",
    "templates/",
    "instructions/",
    "job description",
    "job-description",
    "system/",
    "/prompt",
    "AGENTS.md",
    "README.md",
    # Sales/marketing collateral — not live operational risk
    "pitch",
    "proposal",
    "one-pager",
    "one pager",
]

# Note titles / filenames that are almost always noisy.
_NOISY_TITLE_LOWER = [
    "gemini",
    "agent instruction",
    "system prompt",
    "template",
    "job description",
    "readme",
    "agents.md",
    # Generic operational/process docs that flood the risk list
    "pto handoff",
    "pto plan",
    "okrs",
    "discovery okr",
    "standup",
    "leadership standup",
    "flex pitch",
    # Support/process notes that are process documentation, not live risks
    "google support",
    "support process",
    "escalation process",
    "transition",
]

# A snippet is only actionable if it contains one of these phrases,
# indicating a real consequence rather than a structural keyword.
_ACTIONABLE_RISK_TERMS = [
    "blocked",
    "blocking",
    "at risk",
    "overdue",
    "escalat",
    "delay",
    "miss the",
    "missing deadline",
    "not signed",
    "unsigned",
    "production",
    "outage",
    "incident",
    "resign",
    "leaving",
    "sow",
    "contract",
    "staffing gap",
    "no resource",
    "delivery risk",
]


def _is_noisy_source(source_path: str, title: str) -> bool:
    """Return True if the note looks like a system/template/instruction document."""
    sp_lower = (source_path or "").lower()
    if any(sub in sp_lower for sub in _NOISY_SOURCE_SUBSTRINGS):
        return True
    title_lower = (title or "").lower()
    if any(t in title_lower for t in _NOISY_TITLE_LOWER):
        return True
    return False


def _snippet_is_heading_or_label(s: str) -> bool:
    """Return True if the text is a markdown heading, bold label, or image artifact."""
    s = s.strip()
    if not s:
        return True
    # Markdown heading
    if re.match(r'^#{1,6}\s+', s):
        return True
    # Bold-only label ending with colon: "**Critical Skill:**" or "**Key Concerns:**"
    if re.match(r'^\*\*[A-Za-z][^*]{0,60}:\*\*\s*$', s):
        return True
    # Bullet whose text is only a bold label: "- **Critical Skill:**"
    if re.match(r'^[-*•]\s+\*\*[^*]{0,60}:\*\*\s*$', s):
        return True
    # Markdown image or export artifact containing ]( or ![
    if '](' in s or '![' in s:
        return True
    # HTML comment or metadata line
    if s.startswith('<!--') or s.startswith('<'):
        return True
    return False


def _snippet_is_actionable(snippet: str) -> bool:
    """Return True if the snippet contains at least one actionable risk term."""
    lower = snippet.lower()
    return any(term in lower for term in _ACTIONABLE_RISK_TERMS)


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
        SELECT n.id, n.raw_document_id, n.note_date, n.note_type,
               n.entity_type, n.entity_name, n.title, n.body,
               rd.source_path as file_path
        FROM notes n
        LEFT JOIN raw_documents rd ON n.raw_document_id = rd.id
        WHERE n.body IS NOT NULL
        """
    ).fetchall()

    signals = []
    for row in rows:
        (note_id, raw_document_id, note_date, note_type,
         entity_type, entity_name, title, body, file_path) = row
        if not _contains_risk_keyword(body or ""):
            continue

        # Use the actual vault path when available; fall back to doc id
        source_path = file_path or raw_document_id or note_id

        # Skip system/template/instruction documents entirely
        if _is_noisy_source(source_path, title):
            continue

        # Determine entity for the signal
        etype = entity_type or "team"
        ename = entity_name or title or "Unknown"

        # Map note entity_type to valid Signal entity_type
        if etype not in ("person", "client", "deal", "team", "practice"):
            etype = "team"

        severity, confidence = _risk_keyword_severity(body or "")

        has_named_entity = bool(entity_name and entity_name.strip())

        # Extract best actionable snippet
        trigger_snippet = _extract_risk_snippet(body or "", max_chars=120)

        # If we could not find a non-heading, actionable snippet, downgrade sharply.
        # This prevents section headings ("## Critical Risks") from creating high signals.
        if not trigger_snippet:
            severity = "low"
            confidence = min(confidence, 0.40)
        elif severity == "high" and not _snippet_is_actionable(trigger_snippet):
            # High keyword found but snippet isn't truly actionable — downgrade to medium
            severity = "medium"
            confidence = min(confidence, 0.60)

        # Downgrade to low when there is no named entity (generic notes)
        if not has_named_entity and severity == "high":
            severity = "medium"
            confidence = min(confidence, 0.65)
        elif not has_named_entity and severity == "medium":
            severity = "low"
            confidence = min(confidence, 0.50)

        display_title = title or "untitled"
        if trigger_snippet:
            summary = f"{display_title}: {trigger_snippet}"
        else:
            summary = f"Risk language in note: {display_title}"

        why = (
            f"Note '{display_title}' contains risk-related language"
            + (f" about {ename}" if has_named_entity and ename != display_title else "")
            + "."
        )

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
            summary=summary,
            why_it_matters=why,
            requires_manager_attention=(severity == "high"),
            confidence=confidence,
        )
        signals.append(sig)
    return signals


def _extract_risk_snippet(body: str, max_chars: int = 120) -> str:
    """Return the best actionable sentence containing a risk keyword.

    Prefers sentences that are not headings/labels and that contain an
    actionable term.  Falls back to the first keyword sentence if needed.
    """
    all_keywords = _HIGH_RISK_KEYWORDS + _MEDIUM_RISK_KEYWORDS + _LOW_RISK_KEYWORDS
    sentences = re.split(r"(?<=[.!?])\s+|\n+", body)

    first_match: str = ""
    for sent in sentences:
        sent_lower = sent.lower()
        if not any(kw in sent_lower for kw in all_keywords):
            continue
        stripped = sent.strip()
        if _snippet_is_heading_or_label(stripped):
            continue
        if not first_match:
            first_match = stripped
        if _snippet_is_actionable(stripped):
            snippet = stripped
            if len(snippet) > max_chars:
                snippet = snippet[:max_chars].rsplit(" ", 1)[0] + "…"
            return snippet

    # Fall back to first non-heading match (may not be fully actionable)
    if first_match:
        if len(first_match) > max_chars:
            first_match = first_match[:max_chars].rsplit(" ", 1)[0] + "…"
        return first_match
    return ""


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
    """Emit sow_loe_review signal for deals closing within 14 days without a signed SOW."""
    deadline = run_date + timedelta(days=14)

    # deal_id is an optional column added for NetSuite; fall back gracefully
    try:
        rows = conn.execute(
            """
            SELECT id, account, deal_name, close_date, sow_status, loe_status,
                   probability, services_amount, deal_id
            FROM deals
            WHERE close_date IS NOT NULL
              AND close_date <= ?
              AND close_date >= ?
              AND (sow_status IS NULL OR sow_status != 'signed')
            """,
            [deadline, run_date],
        ).fetchall()
        has_deal_id = True
    except Exception:
        rows = conn.execute(
            """
            SELECT id, account, deal_name, close_date, sow_status, loe_status,
                   probability, services_amount
            FROM deals
            WHERE close_date IS NOT NULL
              AND close_date <= ?
              AND close_date >= ?
              AND (sow_status IS NULL OR sow_status != 'signed')
            """,
            [deadline, run_date],
        ).fetchall()
        has_deal_id = False

    signals = []
    for row in rows:
        if has_deal_id:
            _, account, deal_name, close_date, sow_status, loe_status, probability, services_amount, deal_id = row
        else:
            _, account, deal_name, close_date, sow_status, loe_status, probability, services_amount = row
            deal_id = None

        if isinstance(close_date, str):
            close_date = date.fromisoformat(close_date)
        days_left = (close_date - run_date).days
        severity = "high" if days_left <= 7 else "medium"

        value_str = ""
        if services_amount:
            value_str = f" (${services_amount:,.0f})"

        # Build a readable source reference: "deals::OPP025010" or "deals::{deal_name}"
        source_ref = f"deals::{deal_id}" if deal_id else f"deals::{deal_name}"

        sig_id = _signal_dedup_id(run_date, f"deal::{deal_name}", "sow_loe_review", deal_name)
        sig = Signal(
            id=sig_id,
            signal_date=run_date,
            source="rule",
            source_path=source_ref,
            entity_type="deal",
            entity_name=deal_name,
            signal_type="sow_loe_review",
            severity=severity,
            summary=f"SOW unsigned — {deal_name}{value_str} closes in {days_left} day(s) ({close_date})",
            why_it_matters=(
                f"Deal '{deal_name}' for {account} closes in {days_left} days "
                f"but SOW is '{sow_status or 'not-started'}'.{value_str}"
            ),
            requires_manager_attention=(severity == "high"),
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
