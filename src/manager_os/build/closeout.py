"""End-of-day closeout and weekly exec update generator.

Produces a markdown closeout summary for a given date:
  - Stats: signals new/acknowledged/resolved, action items closed, decisions made
  - Unresolved signals that rolled over
  - Decisions made today
  - Action items closed today vs still open
  - Optional weekly exec update draft (generated on Fridays or when forced)

Writes to output/closeout/YYYY-MM-DD.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment

from manager_os.db import content_hash

logger = logging.getLogger(__name__)

_OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "output" / "closeout"

# ------------------------------------------------------------------
# Templates
# ------------------------------------------------------------------

_CLOSEOUT_TEMPLATE = """\
# EOD Closeout — {{ closeout_date }}

## Signal Summary
| Status | Count |
|--------|-------|
| 🆕 New today | {{ stats.new_today }} |
| ✅ Resolved | {{ stats.resolved_today }} |
| 👍 Acknowledged | {{ stats.acknowledged_today }} |
| 🔄 Still open | {{ stats.still_open }} |

## Action Items
| Status | Count |
|--------|-------|
| ✅ Closed today | {{ stats.action_items_closed }} |
| 🔄 Still open | {{ stats.action_items_open }} |

## Decisions Made Today
{% if decisions %}
{% for d in decisions %}
- **{{ d.entity_name }}**: {{ d.description }}
{% endfor %}
{% else %}
*No decisions recorded.*
{% endif %}

## Unresolved High/Critical Signals Carrying Over
{% if unresolved_signals %}
{% for s in unresolved_signals %}
- 🔴 **{{ s.entity_name }}** (`{{ s.signal_type }}`): {{ s.summary }}
{% endfor %}
{% else %}
*None — clean slate tomorrow! 🎉*
{% endif %}

---
*Generated {{ generated_at }}*
"""

_WEEKLY_EXEC_TEMPLATE = """\
# Weekly Exec Update — Week of {{ week_start }}

## Highlights
{% for h in highlights %}
- {{ h }}
{% endfor %}

## Risks & Escalations
{% if critical_risks %}
{% for r in critical_risks %}
- 🔴 **{{ r.entity_name }}**: {{ r.summary }}
{% endfor %}
{% else %}
*None this week.*
{% endif %}

## Staffing
- **Overallocated**: {{ staffing.overallocated | join(', ') or 'None' }}
- **Underallocated**: {{ staffing.underallocated | join(', ') or 'None' }}
- **Available capacity**: {{ staffing.available | join(', ') or 'None' }}

## Deal Pipeline Actions
{% if deal_signals %}
{% for d in deal_signals %}
- **{{ d.entity_name }}**: {{ d.summary }}
{% endfor %}
{% else %}
*No deal actions this week.*
{% endif %}

## Decisions Made This Week
{% for d in decisions %}
- {{ d.description }}
{% endfor %}

---
*Generated {{ generated_at }}*
"""


# ------------------------------------------------------------------
# Data gathering
# ------------------------------------------------------------------


@dataclass
class CloseoutStats:
    new_today: int = 0
    resolved_today: int = 0
    acknowledged_today: int = 0
    still_open: int = 0
    action_items_closed: int = 0
    action_items_open: int = 0


@dataclass
class CloseoutResult:
    content: str
    stats: CloseoutStats
    weekly_exec_content: str | None = None
    output_path: Path | None = None


def _get_signal_stats(conn, target_date: date) -> CloseoutStats:
    stats = CloseoutStats()

    # New today: created_at date matches
    stats.new_today = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE CAST(created_at AS DATE) = ?",
        [target_date],
    ).fetchone()[0]

    # Resolved today
    stats.resolved_today = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE status = 'resolved' AND CAST(updated_at AS DATE) = ?",
        [target_date],
    ).fetchone()[0]

    # Acknowledged today
    stats.acknowledged_today = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE status = 'acknowledged' AND CAST(updated_at AS DATE) = ?",
        [target_date],
    ).fetchone()[0]

    # Still open
    stats.still_open = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE status = 'open'"
    ).fetchone()[0]

    # Action items closed today
    stats.action_items_closed = conn.execute(
        "SELECT COUNT(*) FROM action_items WHERE status = 'done' AND CAST(created_at AS DATE) <= ?",
        [target_date],
    ).fetchone()[0]

    stats.action_items_open = conn.execute(
        "SELECT COUNT(*) FROM action_items WHERE status = 'open'"
    ).fetchone()[0]

    return stats


def _get_unresolved_high_signals(conn) -> list:
    rows = conn.execute(
        "SELECT entity_name, signal_type, summary FROM signals "
        "WHERE status = 'open' AND severity IN ('critical', 'high') "
        "ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, entity_name"
    ).fetchall()
    return [{"entity_name": r[0], "signal_type": r[1], "summary": r[2]} for r in rows]


def _get_decisions_for_date(conn, target_date: date) -> list:
    rows = conn.execute(
        "SELECT entity_name, description FROM decisions WHERE decision_date = ? AND status = 'made'",
        [target_date],
    ).fetchall()
    return [{"entity_name": r[0] or "", "description": r[1]} for r in rows]


def _get_week_highlights(conn, week_start: date, week_end: date) -> list[str]:
    """Generate bullet-point highlights from signals and decisions this week."""
    highlights = []

    # Count signals this week by type
    type_counts = conn.execute(
        "SELECT signal_type, COUNT(*) FROM signals "
        "WHERE signal_date BETWEEN ? AND ? GROUP BY signal_type ORDER BY COUNT(*) DESC LIMIT 5",
        [week_start, week_end],
    ).fetchall()
    for st, cnt in type_counts:
        highlights.append(f"{cnt} `{st}` signal(s) detected")

    # Count decisions
    dec_count = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE decision_date BETWEEN ? AND ?",
        [week_start, week_end],
    ).fetchone()[0]
    if dec_count:
        highlights.append(f"{dec_count} decision(s) recorded this week")

    # Count closed action items
    closed = conn.execute(
        "SELECT COUNT(*) FROM action_items WHERE status = 'done'"
    ).fetchone()[0]
    if closed:
        highlights.append(f"{closed} action item(s) resolved")

    if not highlights:
        highlights.append("No significant signals this week.")
    return highlights[:6]


def _get_week_critical_risks(conn, week_start: date, week_end: date) -> list:
    rows = conn.execute(
        "SELECT entity_name, summary FROM signals "
        "WHERE severity IN ('critical', 'high') AND signal_date BETWEEN ? AND ? AND status = 'open'",
        [week_start, week_end],
    ).fetchall()
    return [{"entity_name": r[0], "summary": r[1]} for r in rows]


def _get_week_deal_signals(conn, week_start: date, week_end: date) -> list:
    rows = conn.execute(
        "SELECT entity_name, summary FROM signals "
        "WHERE entity_type = 'deal' AND signal_date BETWEEN ? AND ? AND status = 'open'",
        [week_start, week_end],
    ).fetchall()
    return [{"entity_name": r[0], "summary": r[1]} for r in rows]


def _get_forecast_summary_simple(conn, as_of: date) -> dict:
    from datetime import timedelta
    horizon = as_of + timedelta(days=14)
    rows = conn.execute(
        "SELECT person_name, SUM(allocation_pct) FROM staffing_forecast "
        "WHERE week_start BETWEEN ? AND ? GROUP BY person_name",
        [as_of, horizon],
    ).fetchall()
    overallocated = [r[0] for r in rows if r[1] and r[1] > 100]
    underallocated = [r[0] for r in rows if r[1] and r[1] < 50]
    available = [r[0] for r in rows if r[1] and 50 <= r[1] <= 100]
    return {"overallocated": overallocated, "underallocated": underallocated, "available": available}


# ------------------------------------------------------------------
# Main functions
# ------------------------------------------------------------------


def generate_closeout(
    conn,
    target_date: date | None = None,
    include_weekly: bool | None = None,
) -> CloseoutResult:
    """Generate the EOD closeout document.

    Args:
        conn: Open DuckDB connection.
        target_date: Date to generate closeout for. Defaults to today.
        include_weekly: If True, always generate weekly exec update.
                        If None (default), generate on Fridays only.
                        If False, never generate.

    Returns:
        CloseoutResult with rendered content and stats.
    """
    if target_date is None:
        target_date = date.today()

    stats = _get_signal_stats(conn, target_date)
    unresolved = _get_unresolved_high_signals(conn)
    decisions = _get_decisions_for_date(conn, target_date)

    env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)

    closeout_content = env.from_string(_CLOSEOUT_TEMPLATE).render(
        closeout_date=target_date.isoformat(),
        stats=stats,
        decisions=decisions,
        unresolved_signals=unresolved,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )

    # Weekly exec update
    is_friday = target_date.weekday() == 4
    should_weekly = include_weekly if include_weekly is not None else is_friday
    weekly_content: str | None = None

    if should_weekly:
        week_start = target_date - timedelta(days=target_date.weekday())
        week_end = target_date

        highlights = _get_week_highlights(conn, week_start, week_end)
        critical_risks = _get_week_critical_risks(conn, week_start, week_end)
        deal_signals = _get_week_deal_signals(conn, week_start, week_end)
        staffing = _get_forecast_summary_simple(conn, as_of=target_date)
        week_decisions = conn.execute(
            "SELECT description FROM decisions WHERE decision_date BETWEEN ? AND ?",
            [week_start, week_end],
        ).fetchall()

        weekly_content = env.from_string(_WEEKLY_EXEC_TEMPLATE).render(
            week_start=week_start.isoformat(),
            highlights=highlights,
            critical_risks=critical_risks,
            deal_signals=deal_signals,
            staffing=staffing,
            decisions=[{"description": r[0]} for r in week_decisions],
            generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        )

    return CloseoutResult(
        content=closeout_content,
        stats=stats,
        weekly_exec_content=weekly_content,
    )


def write_closeout_to_file(
    result: CloseoutResult,
    target_date: date,
    output_dir: str | None = None,
) -> Path:
    """Write closeout (and optional weekly exec update) to files.

    Returns the path to the main closeout file.
    """
    out_dir = Path(output_dir) if output_dir else _OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    main_path = out_dir / f"{target_date.isoformat()}.md"
    main_path.write_text(result.content, encoding="utf-8")
    result.output_path = main_path

    if result.weekly_exec_content:
        weekly_path = out_dir / f"{target_date.isoformat()}-weekly-exec.md"
        weekly_path.write_text(result.weekly_exec_content, encoding="utf-8")

    return main_path
