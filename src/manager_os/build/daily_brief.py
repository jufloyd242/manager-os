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
# Ranking
# ---------------------------------------------------------------------------

def _score_signal(signal: Signal, today: date) -> float:
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
    # Low-confidence signals rank lower
    score *= max(signal.confidence, 0.1)
    return score


def _rank_signals(signals: list[Signal], today: date) -> list[Signal]:
    """Return signals sorted by relevance score descending."""
    return sorted(signals, key=lambda s: _score_signal(s, today), reverse=True)


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
            signals.append(Signal(
                id=row[0], signal_date=row[1], source=row[2], source_path=row[3] or "",
                entity_type=row[4], entity_name=row[5], signal_type=row[6],
                severity=row[7], summary=row[8], why_it_matters=row[9] or "",
                requires_manager_attention=bool(row[10]), owner=row[11] or "",
                due_date=row[12], confidence=float(row[13]), status=row[14],
                created_at=row[15], updated_at=row[16],
            ))
        except Exception as exc:
            logger.warning("Skipping malformed signal row: %s", exc)
    return signals


def _load_action_items(conn) -> list[ActionItem]:
    rows = conn.execute(
        """
        SELECT id, signal_id, source_note_id, assigned_to, description, due_date, status, created_at
        FROM action_items
        WHERE status = 'open'
        ORDER BY due_date NULLS LAST
        """
    ).fetchall()
    items = []
    for row in rows:
        try:
            items.append(ActionItem(
                id=row[0], signal_id=row[1], source_note_id=row[2],
                assigned_to=row[3], description=row[4],
                due_date=row[5], status=row[6], created_at=row[7],
            ))
        except Exception as exc:
            logger.warning("Skipping malformed action item: %s", exc)
    return items


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

    # Effective per-section limits
    limits = {k: (max_items if max_items is not None else v) for k, v in _DEFAULT_LIMITS.items()}

    # Load raw data
    all_signals = _load_signals(conn, target_date)
    all_action_items = _load_action_items(conn)
    decisions = _load_decisions(conn)
    meetings = _load_meetings(conn, target_date)

    # Rank signals globally then bucket
    ranked_signals = _rank_signals(all_signals, target_date)
    bucketed = _bucket_signals(ranked_signals)

    signal_ids = [s.id for s in all_signals]

    # Apply per-section limits and compute overflow
    overflow: dict[str, int] = {}

    critical_shown = bucketed["critical_signals"]  # never truncate critical
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

    # Decisions: cap at limit
    decisions_shown = decisions[: limits["decisions"]]
    overflow["decisions"] = max(0, len(decisions) - len(decisions_shown))

    # Follow-ups Justin owes: manager action items, ranked by due date
    manager_ais = [ai for ai in all_action_items if ai.assigned_to in ("manager", "Manager")]
    manager_ais_ranked = sorted(manager_ais, key=lambda ai: _score_action_item(ai, target_date), reverse=True)
    follow_ups_shown = manager_ais_ranked[: limits["follow_ups"]]
    overflow["follow_ups"] = max(0, len(manager_ais_ranked) - len(follow_ups_shown))

    # All other open action items (not manager)
    other_ais = [ai for ai in all_action_items if ai.assigned_to not in ("manager", "Manager")]

    # Meetings
    meetings_shown = meetings[: limits["meetings"]]
    overflow["meetings"] = max(0, len(meetings) - len(meetings_shown))

    # Totals for header
    total_hidden = sum(overflow.values())
    total_open_action_items = len(all_action_items)

    # Render template
    env = Environment(
        loader=FileSystemLoader(str(_PROMPTS_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    # Custom filter for source path display
    env.filters["basename"] = lambda p: Path(p).name if p else ""

    template = env.get_template("daily_brief.md")
    content = template.render(
        brief_date=target_date.isoformat(),
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        total_signals=len(all_signals),
        open_action_items=total_open_action_items,
        meeting_count=len(meetings),
        total_hidden=total_hidden,
        # Sections
        critical_signals=critical_shown,
        risk_signals=risks_shown,
        people_signals=people_shown,
        deal_signals=deals_shown,
        utilization_signals=utilization_shown,
        other_signals=other_shown,
        decisions=decisions_shown,
        follow_ups=follow_ups_shown,
        other_action_items=other_ais,
        meetings=meetings_shown,
        # Overflow counts per section
        overflow=overflow,
        # Legacy compat (action_items was used by old template / tests)
        action_items=all_action_items,
    )

    brief = DailyBrief(
        id=content_hash(f"daily_brief::{target_date.isoformat()}"),
        brief_date=target_date,
        content=content,
        signal_ids=signal_ids,
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
