"""Daily brief generator.

Queries signals, action items, and decisions from DuckDB, ranks them by
relevance, applies per-section limits, and renders a concise markdown brief
that Justin can review in under 5 minutes.

Ranking factors (higher = more important):
- Severity: critical=100, high=50, medium=20, low=5
- requires_manager_attention: +30
- Due date proximity: ≤3 days +40, ≤7 days +20, ≤14 days +10
- Confidence: score × confidence (0–1)

Per-section defaults (overridable via max_items):
- risks: 3  people: 3  deals: 3  follow-ups: 3  utilization: 3  meetings: 5

Low-priority (severity='low') signals are hidden by default;
pass include_low_priority=True to show them.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from manager_os.db import content_hash
from manager_os.schemas import ActionItem, DailyBrief, Signal

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"
_OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "output" / "daily_briefs"

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_SEVERITY_SCORE = {"critical": 100, "high": 50, "medium": 20, "low": 5}

# Default per-section item limits
_DEFAULT_LIMITS = {
    "risks": 3,
    "people": 3,
    "deals": 3,
    "follow_ups": 3,
    "utilization": 3,
    "decisions": 3,
    "meetings": 5,
    "other": 2,
}


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _deduplicate_signals(signals: list[Signal]) -> tuple[list[Signal], int]:
    """Suppress lower-scored duplicate signals from the same source note + type.

    Signals that share the same non-empty source_path and signal_type are
    considered duplicates; only the highest-scored one (first in the already-
    ranked list) is kept. Signals with an empty source_path are never
    deduplicated — they are computed from DB fields, not from a single note.

    Returns (unique_signals, suppressed_count).
    """
    seen: set[tuple[str, str]] = set()
    unique: list[Signal] = []
    suppressed = 0
    for s in signals:
        if not s.source_path:
            unique.append(s)
            continue
        key = (s.source_path, s.signal_type)
        if key in seen:
            suppressed += 1
        else:
            seen.add(key)
            unique.append(s)
    return unique, suppressed


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _brief_item_id(prefix: str, db_id: str) -> str:
    """Return the stable brief item ID used in the rendered markdown.

    Format: ``<prefix>:<first-16-chars-of-db-id>``
    The short prefix makes IDs readable in the CLI / markdown.
    """
    return f"{prefix}:{db_id[:16]}"


def _score_signal(
    signal: Signal,
    today: date,
    direct_index: dict[str, str] | None = None,
    source_index: dict[str, str] | None = None,
) -> float:
    """Compute a relevance score for a signal. Higher = more urgent."""
    score = float(_SEVERITY_SCORE.get(signal.severity, 5))
    if signal.requires_manager_attention:
        score += 30
    if signal.due_date:
        days = (signal.due_date - today).days
        if days <= 3:
            score += 40
        elif days <= 7:
            score += 20
        elif days <= 14:
            score += 10
    # Structured signals (from DB fields, not keyword matching) rank higher
    if signal.signal_type in ("sow_loe_review", "utilization_risk", "deal_change"):
        score += 25
    # Weak keyword-only signals rank lower
    if signal.signal_type == "risk" and signal.confidence < 0.75:
        score -= 10
    # Low-confidence signals rank lower
    score *= max(signal.confidence, 0.1)
    # Apply feedback adjustments when indexes are available
    if direct_index is not None:
        from manager_os.build.feedback import apply_feedback_score
        item_id = _brief_item_id("signal", signal.id)
        score = apply_feedback_score(
            score, item_id, signal.source_path,
            direct_index, source_index or {},
        )
    return score


def _rank_signals(
    signals: list[Signal],
    today: date,
    direct_index: dict[str, str] | None = None,
    source_index: dict[str, str] | None = None,
) -> list[Signal]:
    """Return signals sorted by relevance score descending."""
    return sorted(
        signals,
        key=lambda s: _score_signal(s, today, direct_index, source_index),
        reverse=True,
    )


def _apply_limit(
    items: list,
    limit: int,
    include_low: bool,
    severity_attr: str = "severity",
) -> tuple[list, int]:
    """Filter out low-priority items (unless include_low) and apply the cap.

    Returns (shown, hidden_count).
    """
    if not include_low:
        items = [i for i in items if getattr(i, severity_attr, "high") != "low"]
    shown = items[:limit]
    hidden = len(items) - len(shown)
    return shown, hidden


def _score_action_item(ai: ActionItem, today: date) -> float:
    """Score an action item for ranking within the follow-ups section."""
    score = 50.0  # base
    if ai.due_date:
        days = (ai.due_date - today).days
        if days <= 3:
            score += 40
        elif days <= 7:
            score += 20
        elif days <= 14:
            score += 10
    return score


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_signals(conn, target_date: date) -> list[Signal]:
    rows = conn.execute(
        """
        SELECT id, signal_date, source, source_path, entity_type, entity_name,
               signal_type, severity, summary, why_it_matters,
               requires_manager_attention, owner, due_date, confidence, status,
               created_at, updated_at
        FROM signals
        WHERE status = 'open'
          AND (rating IS NULL OR rating NOT IN ('not_useful', 'duplicate', 'wrong_entity', 'resolved'))
        ORDER BY
            CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                          WHEN 'medium' THEN 2 ELSE 3 END,
            signal_date DESC
        """
    ).fetchall()
    signals = []
    for row in rows:
        try:
            s = Signal(
                id=row[0], signal_date=row[1], source=row[2], source_path=row[3] or "",
                entity_type=row[4], entity_name=row[5], signal_type=row[6],
                severity=row[7], summary=row[8], why_it_matters=row[9] or "",
                requires_manager_attention=bool(row[10]), owner=row[11] or "",
                due_date=row[12], confidence=float(row[13]), status=row[14],
                created_at=row[15], updated_at=row[16],
            )
            # Safety net: skip signals whose source_path belongs to excluded/context
            # notes (tier filtering should handle this at extraction time, but
            # tier guard here protects against stale/legacy signals).
            if _is_noisy_source_path(s.source_path):
                continue
            signals.append(s)
        except Exception as exc:
            logger.warning("Skipping malformed signal row: %s", exc)
    return signals


def _is_noisy_source_path(source_path: str) -> bool:
    """Return True if the source_path is from known excluded/context categories."""
    if not source_path:
        return False
    sp = source_path.lower()
    noisy_substrings = [
        "/training/",
        "/hiring/",
        "/quotes/",
        "/docs/",
        "/scripts/",
        "/drafts/",
        "/archive/",
        "_manager-os/",
        ".obsidian/",
        "gemini.md",
        "claude.md",
        "agents.md",
        "readme.md",
        "_template.md",
        "general.md",
        "mentorships/",
        "onboarding/",
        "client meeting flow/",
        "/sada/",
        "/templates/",
        "/imports/",
        "/day-to-day/general.md",
        "/manager/general.md",
        "/deals/deal_scraper.md",
    ]
    return any(sub in sp for sub in noisy_substrings)


def _load_action_items(conn) -> list[ActionItem]:
    today = date.today()
    rows = conn.execute(
        """
        SELECT id, signal_id, source_note_id, assigned_to, description,
               due_date, status, created_at,
               feedback_rating, feedback_reason, snooze_until
        FROM action_items
        WHERE status = 'open'
          AND (snooze_until IS NULL OR snooze_until <= ?)
          AND (feedback_rating IS NULL
               OR feedback_rating NOT IN ('noisy', 'stale', 'wrong', 'dismissed'))
        ORDER BY due_date NULLS LAST
        """,
        [today],
    ).fetchall()
    items = []
    for row in rows:
        try:
            ai = ActionItem(
                id=row[0], signal_id=row[1], source_note_id=row[2],
                assigned_to=row[3], description=row[4],
                due_date=row[5], status=row[6], created_at=row[7],
                feedback_rating=row[8], feedback_reason=row[9],
                snooze_until=row[10],
            )
            if not _is_junk_action_item(ai.description):
                items.append(ai)
        except Exception as exc:
            logger.warning("Skipping malformed action item: %s", exc)
    return items


# Minimum token count and min length for a useful action item
_AI_MIN_WORDS = 3
_AI_MIN_CHARS = 10
_AI_MAX_CHARS = 250  # Very long descriptions are usually boilerplate paragraphs

# Phrases that indicate template/meta/boilerplate junk
_JUNK_PATTERNS = [
    "analyze the document",
    "update all relevant",
    "routinely be running",
    "multiple agents",
    "be around",
    "use this template",
    "fill in the",
    "add your",
    "insert here",
    "todo:",
    "action item:",
    "your name",
    "your action",
    "see dashboard",
    # Additional vague fragments
    "implement isolated",
    "increase delivery velocity",
    "use expel",
    "feedback from customer",
    "increase delivery",
    # Job description / boilerplate sentences
    "achieve these goals",
    "operate like developer",
    "not settle for finding",
    "hands-on project",
    "we will unlock",
    "we will learn the loopholes",
    "we will strive to be",
    "we will recommend a process",
    "be moving along",
    "get back to you",
    "internalize, but",
    "always seek advise",
    "of the session",
    "this one",
    "be approving",
    "longer sales cycles",
    # Hobby / personal items that leak from personal notes
    "pickleball",
    "golf",
    # Vague waiting-on fragments with no real entity or action
    "waiting on next steps from him",
    "waiting on this one",
    "waiting on signature",
    "waiting on sr ",
    "waiting on sr to",
]

# Regex for action item descriptions that are clearly fragments / not real items.
# Catches:
#   - Truncated bold markdown: s**, **A, *A
#   - Bare bullet lines: - or *
#   - Truncated list continuations: "s of ...", "s from ...", "s for ...",
#     "s (Review", "s (For", "s (1:", i.e. any line that starts with a lone 's '
#     (these are split-off tails of "**Actions for ...**" bullets)
_JUNK_RE = re.compile(
    r'^(?:'
    r's\*\*'                              # s**
    r'|\*{1,2}\s*[a-zA-Z]'               # *A or **A (truncated bold)
    r'|[-*]\s*$'                          # bare bullet
    r'|s\s+(?:of|from|for|\(|/)\s*'      # s of / s from / s for / s ( / s /
    r')',
    re.IGNORECASE,
)


def _is_junk_action_item(description: str) -> bool:
    """Return True when the action item description looks like junk/boilerplate."""
    if not description:
        return True
    desc = description.strip()
    # Too short
    if len(desc) < _AI_MIN_CHARS:
        return True
    # Too few words (excludes single-word fragments)
    if len(desc.split()) < _AI_MIN_WORDS:
        return True
    # Very long descriptions are usually boilerplate paragraphs, not action items
    if len(desc) > _AI_MAX_CHARS:
        return True
    # Ends with == (malformed markdown from copy-paste)
    if desc.endswith("=="):
        return True
    # Matches a known junk pattern
    desc_lower = desc.lower()
    if any(pat in desc_lower for pat in _JUNK_PATTERNS):
        return True
    # Looks like a code/markdown fragment
    if _JUNK_RE.match(desc):
        return True
    return False


def _load_decisions(conn) -> list[dict]:
    """Load open decisions (not signal-level decisions — the decisions table)."""
    try:
        rows = conn.execute(
            """
            SELECT id, entity_name, description, decision_date, owner, source_note_id
            FROM decisions
            WHERE status = 'open'
            ORDER BY decision_date NULLS LAST
            LIMIT 20
            """
        ).fetchall()
    except Exception:
        return []
    decisions = []
    for row in rows:
        decisions.append({
            "id": row[0],
            "entity_name": row[1] or "",
            "description": row[2] or "",
            "decision_date": row[3],
            "owner": row[4] or "",
            "source_note_id": row[5] or "",
        })
    return decisions


def _load_meetings(conn, target_date: date) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, start_time, title, attendees
        FROM meetings
        WHERE meeting_date = ?
        ORDER BY start_time NULLS LAST
        """,
        [target_date],
    ).fetchall()
    meetings = []
    for row in rows:
        attendees = json.loads(row[3]) if row[3] else []
        meetings.append({"id": row[0], "start_time": row[1], "title": row[2], "attendees": attendees})
    return meetings


# ---------------------------------------------------------------------------
# Signal bucketing
# ---------------------------------------------------------------------------

def _bucket_signals(signals: list[Signal]) -> dict:
    """Split signals into sections. Preserves order (caller already ranked them)."""
    critical, risks, people, deals, utilization, other = [], [], [], [], [], []
    for s in signals:
        if s.severity == "critical":
            critical.append(s)
        elif s.signal_type in ("risk", "blocker", "client_update"):
            risks.append(s)
        elif s.signal_type in ("people_health", "stale_item") and s.entity_type == "person":
            people.append(s)
        elif s.signal_type in ("sow_loe_review", "deal_change", "ask"):
            deals.append(s)
        elif s.signal_type == "utilization_risk":
            utilization.append(s)
        else:
            other.append(s)
    return {
        "critical_signals": critical,
        "risk_signals": risks,
        "people_signals": people,
        "deal_signals": deals,
        "utilization_signals": utilization,
        "other_signals": other,
    }


# ---------------------------------------------------------------------------
# Section allocation (balanced budget helper)
# ---------------------------------------------------------------------------

def _section_alloc(bucketed: dict, total: int) -> dict[str, int]:
    """Return per-section signal counts that sum to at most *total*.

    Structured sections (deals, utilization, people) get guaranteed minimum
    slots when they have data.  Risks fill whatever budget remains.  When
    there are no structured signals, risks consume the full budget.
    Follow-ups are tracked separately and do NOT consume signal budget.
    """
    has = {k: len(v) for k, v in bucketed.items()}

    # Guaranteed minimums for structured signals when they have data
    structured: dict[str, int] = {
        "deals":       min(has["deal_signals"],       4),
        "utilization": min(has["utilization_signals"], 3),
        "people":      min(has["people_signals"],      2),
        "other":       min(has["other_signals"],       1),
    }
    structured_used = sum(structured.values())

    # Risks fill whatever the structured sections haven't claimed, capped at 5
    risk_budget = max(0, total - structured_used)
    risk_alloc = min(risk_budget, 5, has["risk_signals"])

    # Redistribute any leftover (when fewer risks than budget) to structured sections
    leftover = risk_budget - risk_alloc
    if leftover > 0:
        for key in ("deals", "people", "utilization", "other"):
            available_extra = has.get(key + "_signals", 0) - structured[key]
            extra = min(available_extra, leftover)
            if extra > 0:
                structured[key] += extra
                leftover -= extra

    return {
        "risks":       risk_alloc,
        "deals":       structured["deals"],
        "people":      structured["people"],
        "utilization": structured["utilization"],
        "other":       structured["other"],
        "follow_ups":  4,  # action item cap — independent of signal budget
    }


def _global_alloc(
    bucketed: dict,
    n_decisions: int,
    n_follow_ups: int,
    n_waiting_on: int,
    total: int,
) -> dict[str, int]:
    """Allocate *total* primary items across ALL sections.

    Budget: deals + utilization + people + risks + other
            + decisions + follow_ups + waiting_on = total
    Priority: structured signals > decisions > follow_ups > waiting_on > risks.
    """
    has = {k: len(v) for k, v in bucketed.items()}

    # Fixed floors for high-priority sections
    alloc: dict[str, int] = {
        "deals":       min(has["deal_signals"],        4),
        "utilization": min(has["utilization_signals"],  3),
        "people":      min(has["people_signals"],       2),
        "other":       min(has["other_signals"],        1),
        "decisions":   min(n_decisions,                 3),
        "follow_ups":  min(n_follow_ups,                4),
        "waiting_on":  min(n_waiting_on,                3),
    }
    used = sum(alloc.values())
    remaining = max(0, total - used)

    # Risks get at most 5, capped by available and remaining budget
    risk_alloc = min(remaining, 5, has["risk_signals"])
    alloc["risks"] = risk_alloc
    remaining -= risk_alloc

    # Leftover goes to deals → people → utilization → follow_ups → waiting_on
    for key, pool in (
        ("deals",       has.get("deal_signals", 0)),
        ("people",      has.get("people_signals", 0)),
        ("utilization", has.get("utilization_signals", 0)),
        ("follow_ups",  n_follow_ups),
        ("waiting_on",  n_waiting_on),
    ):
        if remaining <= 0:
            break
        extra = min(pool - alloc[key], remaining)
        if extra > 0:
            alloc[key] += extra
            remaining -= extra

    return alloc


# ---------------------------------------------------------------------------
# DB write
# ---------------------------------------------------------------------------

def _write_brief_to_db(conn, brief: DailyBrief) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO daily_briefs (id, brief_date, content, signal_ids, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [brief.id, brief.brief_date, brief.content, json.dumps(brief.signal_ids), brief.created_at],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_daily_brief(
    conn,
    target_date: date | None = None,
    max_items: int | None = None,
    include_low_priority: bool = False,
) -> DailyBrief:
    """Generate a ranked, concise daily brief for the given date.

    Args:
        conn: Open DuckDB connection.
        target_date: Date to generate brief for. Defaults to today.
        max_items: Override the per-section item limit. Defaults to section-
            specific values (risks=3, people=3, deals=3, follow_ups=3,
            utilization=3, decisions=3, meetings=5).
        include_low_priority: If True, include severity='low' signals that
            are normally hidden. Defaults to False.

    Returns:
        DailyBrief with ranked, concise markdown content.
    """
    if target_date is None:
        target_date = date.today()

    # Load raw data
    all_signals = _load_signals(conn, target_date)
    all_action_items = _load_action_items(conn)
    decisions = _load_decisions(conn)
    meetings = _load_meetings(conn, target_date)

    # Load feedback indexes for ranking adjustments (graceful if table missing)
    try:
        from manager_os.build.feedback import (
            load_feedback_index,
            load_source_feedback_index,
        )
        direct_fb = load_feedback_index(conn)
        source_fb = load_source_feedback_index(conn)
    except Exception:
        direct_fb = {}
        source_fb = {}

    # Rank globally with feedback, deduplicate, then apply low-priority filter
    ranked_signals = _rank_signals(all_signals, target_date, direct_fb, source_fb)
    deduped_signals, suppressed_count = _deduplicate_signals(ranked_signals)
    visible_signals = (
        [s for s in deduped_signals if s.severity != "low"]
        if not include_low_priority
        else deduped_signals
    )

    signal_ids = [s.id for s in all_signals]

    # Follow-ups Justin owes (manager action items), ranked by due date
    manager_ais = [ai for ai in all_action_items if ai.assigned_to in ("manager", "Manager")]
    manager_ais_ranked = sorted(manager_ais, key=lambda ai: _score_action_item(ai, target_date), reverse=True)

    # Waiting-on items (all AIs not assigned to manager), junk-filtered
    other_ais = [ai for ai in all_action_items if ai.assigned_to not in ("manager", "Manager")]

    overflow: dict[str, int] = {}

    if max_items is not None:
        # ---- Global budget: all primary sections count toward max_items ----
        bucketed_all = _bucket_signals(visible_signals)
        alloc = _global_alloc(
            bucketed_all,
            len(decisions),
            len(manager_ais_ranked),
            len(other_ais),
            max_items,
        )

        critical_shown    = bucketed_all["critical_signals"]  # criticals always included
        risks_shown       = bucketed_all["risk_signals"][:alloc["risks"]]
        people_shown      = bucketed_all["people_signals"][:alloc["people"]]
        deals_shown       = bucketed_all["deal_signals"][:alloc["deals"]]
        utilization_shown = bucketed_all["utilization_signals"][:alloc["utilization"]]
        other_shown       = bucketed_all["other_signals"][:alloc["other"]]
        decisions_shown   = decisions[:alloc["decisions"]]
        follow_ups_shown  = manager_ais_ranked[:alloc["follow_ups"]]
        other_ais_shown   = other_ais[:alloc["waiting_on"]]

        overflow = {
            "critical":    0,
            "risks":       max(0, len(bucketed_all["risk_signals"])        - len(risks_shown)),
            "people":      max(0, len(bucketed_all["people_signals"])      - len(people_shown)),
            "deals":       max(0, len(bucketed_all["deal_signals"])        - len(deals_shown)),
            "utilization": max(0, len(bucketed_all["utilization_signals"]) - len(utilization_shown)),
            "other":       max(0, len(bucketed_all["other_signals"])       - len(other_shown)),
            "decisions":   max(0, len(decisions)           - len(decisions_shown)),
            "follow_ups":  max(0, len(manager_ais_ranked)  - len(follow_ups_shown)),
            "waiting_on":  max(0, len(other_ais)            - len(other_ais_shown)),
        }

    else:
        # ---- Per-section mode (default) ----
        limits = dict(_DEFAULT_LIMITS)
        bucketed = _bucket_signals(visible_signals)

        critical_shown = bucketed["critical_signals"]
        overflow["critical"] = 0

        risks_shown, overflow["risks"] = _apply_limit(
            bucketed["risk_signals"], limits["risks"], include_low_priority
        )
        people_shown, overflow["people"] = _apply_limit(
            bucketed["people_signals"], limits["people"], include_low_priority
        )
        deals_shown, overflow["deals"] = _apply_limit(
            bucketed["deal_signals"], limits["deals"], include_low_priority
        )
        utilization_shown, overflow["utilization"] = _apply_limit(
            bucketed["utilization_signals"], limits["utilization"], include_low_priority
        )
        other_shown, overflow["other"] = _apply_limit(
            bucketed["other_signals"], limits["other"], include_low_priority
        )

        decisions_shown = decisions[: limits["decisions"]]
        overflow["decisions"] = max(0, len(decisions) - len(decisions_shown))
        follow_ups_shown = manager_ais_ranked[: limits["follow_ups"]]
        overflow["follow_ups"] = max(0, len(manager_ais_ranked) - len(follow_ups_shown))
        # In per-section mode show up to 5 waiting-on items (uncapped, not in signal budget)
        other_ais_shown = other_ais[:5]
        overflow["waiting_on"] = max(0, len(other_ais) - len(other_ais_shown))

    # Meetings are always shown up to their default limit, never counted against item budget
    meet_limit = _DEFAULT_LIMITS["meetings"]
    meetings_shown = meetings[:meet_limit]
    overflow["meetings"] = max(0, len(meetings) - len(meetings_shown))

    # --- Totals for header ---
    shown_signal_count = (
        len(critical_shown) + len(risks_shown) + len(people_shown)
        + len(deals_shown) + len(utilization_shown) + len(other_shown)
    )
    # Total primary items shown = signals + decisions + follow-ups + waiting-on
    # (meetings excluded — they're always shown separately, not budgeted)
    shown_total = (
        shown_signal_count
        + len(decisions_shown)
        + len(follow_ups_shown)
        + len(other_ais_shown)
    )
    # Candidate pool = signals + manager AIs + decisions + waiting-on
    # (meetings excluded from candidate pool — always rendered uncapped)
    total_candidates = (
        len(all_signals)
        + len(manager_ais_ranked)
        + len(decisions)
        + len(other_ais)
    )
    total_hidden = total_candidates - shown_total
    total_open_action_items = len(all_action_items)
    # Quality-filter flag: shown < max_items means filters suppressed some items
    quality_filtered = max_items is not None and shown_total < max_items

    # ---- Annotate each shown item with a stable brief_id for feedback CLI ----
    class _Tagged:
        """Thin wrapper that adds a ``brief_id`` attribute to any item."""
        __slots__ = ("_item", "brief_id")
        def __init__(self, item, brief_id: str) -> None:
            self._item = item
            self.brief_id = brief_id
        def __getattr__(self, name: str):
            return getattr(self._item, name)

    def _tag_signals(items: list, prefix: str) -> list:
        return [_Tagged(s, _brief_item_id(prefix, s.id)) for s in items]

    def _tag_ais(items: list, prefix: str) -> list:
        return [_Tagged(ai, _brief_item_id(prefix, ai.id)) for ai in items]

    def _tag_deals(items: list) -> list:
        """Deal signals use the OPP ID from source_path when available."""
        tagged = []
        for s in items:
            sp = s.source_path or ""
            if sp.startswith("deals::"):
                ref = sp[len("deals::"):]
                brid = f"deal:{ref}"
            else:
                brid = _brief_item_id("deal", s.id)
            tagged.append(_Tagged(s, brid))
        return tagged

    # Tag all sections
    critical_shown  = _tag_signals(critical_shown,    "signal")
    risks_shown     = _tag_signals(risks_shown,        "signal")
    people_shown    = _tag_signals(people_shown,       "signal")
    deal_signals_t  = _tag_deals(deals_shown)
    utilization_shown = _tag_signals(utilization_shown, "signal")
    other_shown     = _tag_signals(other_shown,        "signal")
    follow_ups_shown  = _tag_ais(follow_ups_shown,     "action")
    other_ais_shown   = _tag_ais(other_ais_shown,      "waiting")
    decisions_shown_t = [
        _Tagged(d, _brief_item_id("decision", d["id"] if isinstance(d, dict) else d.id))
        for d in decisions_shown
    ]

    # Render template
    env = Environment(
        loader=FileSystemLoader(str(_PROMPTS_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Custom filter for readable source path
    def _readable_path(p: str) -> str:
        if not p:
            return "(no source)"
        stripped = p.strip()
        # Hex hash (>= 32 chars, all hex) — suppress
        if len(stripped) >= 32 and all(c in "0123456789abcdefABCDEF" for c in stripped):
            return "(no source)"
        # deals::OPP025010 → "deals.csv · OPP025010"
        if stripped.startswith("deals::"):
            ref = stripped[len("deals::"):]
            return f"deals.csv · {ref}"
        return Path(p).name if p else "(no source)"

    env.filters["basename"] = lambda p: Path(p).name if p else ""
    env.filters["readable_path"] = _readable_path

    template = env.get_template("daily_brief.md")
    content = template.render(
        brief_date=target_date.isoformat(),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        total_signals=len(all_signals),
        shown_signals=shown_signal_count,
        # Global candidate/shown counts for header
        total_candidates=total_candidates,
        shown_total=shown_total,
        total_follow_ups=len(manager_ais_ranked),
        total_decisions=len(decisions),
        total_waiting_on=len(other_ais),
        suppressed_count=suppressed_count,
        open_action_items=total_open_action_items,
        meeting_count=len(meetings),
        total_hidden=total_hidden,
        quality_filtered=quality_filtered,
        # Sections (tagged with brief_id for feedback)
        critical_signals=critical_shown,
        risk_signals=risks_shown,
        people_signals=people_shown,
        deal_signals=deal_signals_t,
        utilization_signals=utilization_shown,
        other_signals=other_shown,
        decisions=decisions_shown_t,
        follow_ups=follow_ups_shown,
        other_action_items=other_ais_shown,
        meetings=meetings_shown,
        # Overflow counts per section
        overflow=overflow,
        # Legacy compat
        action_items=all_action_items,
    )

    brief = DailyBrief(
        id=content_hash(f"daily_brief::{target_date.isoformat()}"),
        brief_date=target_date,
        content=content,
        signal_ids=signal_ids,
        shown_signals=shown_total,  # total primary bullets rendered
    )

    _write_brief_to_db(conn, brief)
    return brief


def write_brief_to_file(brief: DailyBrief, output_path: str | None = None) -> Path:
    """Write a DailyBrief to a markdown file and return the path."""
    if output_path:
        out_file = Path(output_path)
    else:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_file = _OUTPUT_DIR / f"{brief.brief_date}.md"

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(brief.content, encoding="utf-8")
    return out_file
