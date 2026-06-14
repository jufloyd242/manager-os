"""Meeting prep generator.

Builds per-meeting context records from signals, notes, and action items
linked to the meeting's attendees and related entities.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from manager_os.db import content_hash
from manager_os.extract.entities import EntityResolver
from manager_os.schemas import MeetingPrepRecord, MeetingRecord, Signal, ActionItem

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"
_OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "output" / "meeting_prep"

_MEETING_PREP_TEMPLATE = """\
# Meeting Prep — {{ meeting.title }}
**Date**: {{ meeting.meeting_date }} {% if meeting.start_time %}at {{ meeting.start_time }}{% endif %}

**Attendees**: {{ attendees_str or 'None listed' }}

---
{% if entity_context %}
## Relevant Context
{% for ctx in entity_context %}
### {{ ctx.entity_type | title }}: {{ ctx.entity_name }}
{% if ctx.last_note_summary %}
**Last note** ({{ ctx.last_note_date }}):
> {{ ctx.last_note_summary }}
{% endif %}
{% endfor %}
{% endif %}
{% if open_signals %}
## Active Risks & Signals
{% for s in open_signals %}
- **[{{ s.severity | upper }}]** {{ s.entity_name }} — {{ s.summary }}{% if s.due_date %} *(due {{ s.due_date }})* {% endif %}
{% endfor %}
{% endif %}
{% if open_action_items %}
## Open Action Items
{% for ai in open_action_items %}
- [ ] **{{ ai.assigned_to }}**: {{ ai.description }}{% if ai.due_date %} *(by {{ ai.due_date }})* {% endif %}
{% endfor %}
{% endif %}
{% if suggested_questions %}
## Suggested Questions
{% for q in suggested_questions %}
- {{ q }}
{% endfor %}
{% endif %}

---
*Generated {{ generated_at }}*
"""


def _get_entity_context(conn, entity_type: str, entity_name: str) -> dict:
    """Pull the most recent note for an entity."""
    row = conn.execute(
        """
        SELECT title, note_date, body FROM notes
        WHERE entity_type = ? AND entity_name = ?
        ORDER BY note_date DESC NULLS LAST
        LIMIT 1
        """,
        [entity_type, entity_name],
    ).fetchone()
    if not row:
        return {"entity_type": entity_type, "entity_name": entity_name,
                "last_note_summary": None, "last_note_date": None}
    # Truncate body for context
    body = (row[2] or "").strip()
    summary = body[:400] + ("..." if len(body) > 400 else "")
    return {
        "entity_type": entity_type,
        "entity_name": entity_name,
        "last_note_summary": summary or None,
        "last_note_date": row[1],
    }


def _get_signals_for_entities(conn, entities: list[tuple[str, str]]) -> list[Signal]:
    """Return open signals for the given (entity_type, entity_name) pairs."""
    signals = []
    for entity_type, entity_name in entities:
        rows = conn.execute(
            """
            SELECT id, signal_date, source, source_path, entity_type, entity_name,
                   signal_type, severity, summary, why_it_matters,
                   requires_manager_attention, owner, due_date, confidence, status,
                   created_at, updated_at
            FROM signals
            WHERE entity_type = ? AND entity_name = ? AND status = 'open'
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                   WHEN 'medium' THEN 2 ELSE 3 END
            """,
            [entity_type, entity_name],
        ).fetchall()
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
            except Exception:
                pass
    return signals


def _get_action_items_for_entities(conn, entities: list[tuple[str, str]]) -> list[ActionItem]:
    """Return open action items whose descriptions mention entity names."""
    all_items = conn.execute(
        "SELECT id, signal_id, source_note_id, assigned_to, description, due_date, status, created_at "
        "FROM action_items WHERE status = 'open'"
    ).fetchall()
    entity_names = {name.lower() for _, name in entities}
    matched = []
    for row in all_items:
        desc = (row[4] or "").lower()
        if any(name in desc for name in entity_names):
            try:
                matched.append(ActionItem(
                    id=row[0], signal_id=row[1], source_note_id=row[2],
                    assigned_to=row[3], description=row[4],
                    due_date=row[5], status=row[6], created_at=row[7],
                ))
            except Exception:
                pass
    return matched


def _build_suggested_questions(signals: list[Signal]) -> list[str]:
    """Generate simple rule-based questions from signal types present."""
    questions = []
    signal_types = {s.signal_type for s in signals}
    severities = {s.severity for s in signals}

    if "risk" in signal_types or "blocker" in signal_types:
        questions.append("What is the current status of the risk items we identified last time?")
    if "sow_loe_review" in signal_types:
        questions.append("Where are we on the SOW / LOE — any blockers to signing?")
    if "people_health" in signal_types:
        questions.append("How are you feeling about your current workload and team support?")
    if "utilization_risk" in signal_types:
        questions.append("Is the current allocation realistic — do we need to adjust the staffing plan?")
    if "decision" in signal_types:
        questions.append("Which open decisions need a resolution from us today?")
    if "ask" in signal_types:
        questions.append("What do you need from me to unblock your work this week?")
    if "critical" in severities:
        questions.append("Given the critical issue we flagged — what's the fastest path to resolution?")

    if not questions:
        questions.append("What's going well and what needs attention?")
        questions.append("Are there any upcoming risks I should be aware of?")
        questions.append("What do you need from me before our next check-in?")

    return questions[:5]  # cap at 5


def generate_meeting_prep(
    meeting: MeetingRecord,
    conn,
    resolver: EntityResolver | None = None,
) -> MeetingPrepRecord:
    """Generate a meeting prep document for the given meeting.

    Args:
        meeting: The MeetingRecord to generate prep for.
        conn: Open DuckDB connection.
        resolver: Optional EntityResolver for attendee name resolution.

    Returns:
        MeetingPrepRecord with rendered markdown content.
    """
    # Resolve attendees and linked entities
    linked: list[tuple[str, str]] = []

    # From meeting.linked_entities (explicit)
    for le in meeting.linked_entities:
        et = le.get("entity_type", "")
        en = le.get("entity_name", "")
        if et and en:
            linked.append((et, en))

    # From attendees — resolve each through the EntityResolver
    if resolver:
        for attendee in meeting.attendees:
            match = resolver.resolve_any(attendee)
            if match:
                linked.append((match.entity_type, match.canonical_name))

    # From meeting title — scan for entity mentions
    if resolver:
        for m in resolver.extract_entities_from_text(meeting.title):
            pair = (m.entity_type, m.canonical_name)
            if pair not in linked:
                linked.append(pair)

    # Deduplicate
    seen_linked: set[tuple[str, str]] = set()
    unique_linked = []
    for pair in linked:
        if pair not in seen_linked:
            seen_linked.add(pair)
            unique_linked.append(pair)

    # Gather data
    entity_context = [_get_entity_context(conn, et, en) for et, en in unique_linked]
    open_signals = _get_signals_for_entities(conn, unique_linked)
    open_action_items = _get_action_items_for_entities(conn, unique_linked)
    suggested_questions = _build_suggested_questions(open_signals)

    attendees_str = ", ".join(meeting.attendees) if meeting.attendees else ""

    # Render template
    env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
    template = env.from_string(_MEETING_PREP_TEMPLATE)
    content = template.render(
        meeting=meeting,
        attendees_str=attendees_str,
        entity_context=entity_context,
        open_signals=open_signals,
        open_action_items=open_action_items,
        suggested_questions=suggested_questions,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    )

    prep = MeetingPrepRecord(
        id=content_hash(f"meeting_prep::{meeting.id}"),
        meeting_id=meeting.id,
        content=content,
    )

    # Write to DB
    conn.execute(
        "INSERT OR REPLACE INTO meeting_prep (id, meeting_id, content, generated_at) VALUES (?, ?, ?, ?)",
        [prep.id, prep.meeting_id, prep.content, prep.generated_at],
    )

    return prep


def write_meeting_prep_to_file(prep: MeetingPrepRecord, meeting_title: str,
                               meeting_date: date, output_path: str | None = None) -> Path:
    """Write meeting prep to a markdown file."""
    if output_path:
        out_file = Path(output_path)
    else:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = meeting_title.lower().replace(" ", "-")[:40]
        out_file = _OUTPUT_DIR / f"{meeting_date.isoformat()}-{slug}.md"

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(prep.content, encoding="utf-8")
    return out_file


# ---------------------------------------------------------------------------
# LLM enhancement pass (Issue #24)
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """\
You are an expert executive assistant preparing a manager for an upcoming meeting.
Given the meeting prep context below, produce a short "AI Synthesis" section with:

1. **Key things to know** (2-4 bullet points — the most important context to walk in with)
2. **Top 3 sharpest questions** (more targeted than the rule-based suggestions)

Reply in markdown only. No preamble, no explanation outside the markdown.
Start your response directly with:

## 🤖 AI Synthesis
"""


def enrich_meeting_prep_with_llm(prep: MeetingPrepRecord, conn) -> MeetingPrepRecord:
    """Append an AI Synthesis section to a MeetingPrepRecord using an LLM.

    Requires OPENAI_API_KEY to be set. Silently returns the original prep
    if the API key is missing or the openai package is not installed.

    Args:
        prep: An existing MeetingPrepRecord to enrich.
        conn: Open DuckDB connection (for writing the updated record back).

    Returns:
        Updated MeetingPrepRecord with AI Synthesis appended to content.
    """
    import os
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        logger.debug("OPENAI_API_KEY not set — skipping LLM meeting prep enrichment")
        return prep

    try:
        import openai  # type: ignore[import]
    except ImportError:
        logger.debug("openai package not installed — skipping LLM enrichment")
        return prep

    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("MANAGER_OS_LLM_MODEL", "gpt-4o-mini")
    client_kwargs: dict = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = openai.OpenAI(**client_kwargs)

    # Cap context to avoid token limits
    context = prep.content[:4000]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            temperature=0.3,
            max_tokens=600,
        )
        synthesis = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("LLM meeting prep enrichment failed: %s", exc)
        return prep

    if not synthesis:
        return prep

    updated_content = prep.content.rstrip() + "\n\n" + synthesis + "\n"
    updated_prep = MeetingPrepRecord(
        id=prep.id,
        meeting_id=prep.meeting_id,
        content=updated_content,
        generated_at=prep.generated_at,
    )

    conn.execute(
        "INSERT OR REPLACE INTO meeting_prep (id, meeting_id, content, generated_at) VALUES (?, ?, ?, ?)",
        [updated_prep.id, updated_prep.meeting_id, updated_prep.content, updated_prep.generated_at],
    )

    return updated_prep
