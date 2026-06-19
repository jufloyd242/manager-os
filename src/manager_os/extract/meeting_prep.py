"""Meeting prep generator.

Builds per-meeting context records from signals, notes, and action items
linked to the meeting's attendees and related entities.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from manager_os.db import content_hash
from manager_os.extract.entities import EntityResolver
from manager_os.schemas import MeetingPrepRecord, MeetingRecord, Signal, ActionItem

logger = logging.getLogger(__name__)


@dataclass
class MeetingContextCandidate:
    """A scored context item for meeting prep."""
    source_type: str  # note, signal, action_item, deal, project, document, meeting
    source_id: str
    source_path: str
    title: str
    date: date | None
    entity_type: str
    entity_name: str
    excerpt: str
    score: float
    reasons: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to JSON/markdown-friendly dict."""
        d = asdict(self)
        d["date"] = self.date.isoformat() if self.date else None
        return d

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"
_OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "output" / "meeting_prep"

_MEETING_PREP_TEMPLATE = """\
# Meeting Prep — {{ meeting.title }}
**Date**: {{ meeting.meeting_date }} {% if meeting.start_time %}at {{ meeting.start_time }}{% endif %}

**Attendees**: {{ attendees_str or 'None listed' }}

---
{% if scored_context %}
## Relevant Context
{% for ctx in scored_context %}
### {{ ctx.entity_name or ctx.title }}{% if ctx.date %} — {{ ctx.date }}{% endif %}
Source: {{ ctx.source_path or ctx.source_type }}
Why included: {{ ctx.reasons | join(', ') }}

> {{ ctx.excerpt }}

{% endfor %}
{% endif %}
{% if active_signals %}
## Active Risks & Signals
{% for s in active_signals %}
- **[{{ s.severity | upper }}]** {{ s.entity_name }} — {{ s.summary }}{% if s.due_date %} *(due {{ s.due_date }})* {% endif %}
{% endfor %}
{% endif %}
{% if open_action_items %}
## Open Action Items
{% for ai in open_action_items %}
- [ ] **{{ ai.assigned_to }}**: {{ ai.description }}{% if ai.due_date %} *(by {{ ai.due_date }})* {% endif %}
{% endfor %}
{% endif %}
{% if project_deal_context %}
## Project / Deal Context
{% for item in project_deal_context %}
- **{{ item.title }}**{% if item.metadata.get('client') %} ({{ item.metadata.client }}){% endif %}: {{ item.excerpt[:200] }}
{% endfor %}
{% endif %}
{% if related_documents %}
## Related Documents
{% for doc in related_documents %}
- [{{ doc.title }}]({{ doc.source_path }}) — {{ doc.excerpt[:100] }}
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


def _extract_excerpt(body: str, keywords: list[str], max_chars: int = 500) -> str:
    """Extract excerpt around matched keyword when possible.

    Falls back to first max_chars if no keyword match found.
    """
    if not body:
        return ""
    body = body.strip()
    if len(body) <= max_chars:
        return body

    # Try to find keyword match
    body_lower = body.lower()
    best_pos = -1
    for kw in keywords:
        pos = body_lower.find(kw.lower())
        if pos >= 0:
            best_pos = pos
            break

    if best_pos >= 0:
        # Center excerpt around keyword
        start = max(0, best_pos - max_chars // 3)
        end = min(len(body), start + max_chars)
        excerpt = body[start:end]
        if start > 0:
            excerpt = "..." + excerpt
        if end < len(body):
            excerpt = excerpt + "..."
        return excerpt

    # Fallback: first max_chars
    return body[:max_chars] + ("..." if len(body) > max_chars else "")


def _is_template_note(title: str, body: str) -> bool:
    """Detect template/sample/generated notes."""
    title_lower = title.lower()
    body_lower = body.lower()
    template_markers = ["template", "sample", "example", "placeholder", "generated by"]
    return any(m in title_lower or m in body_lower[:200] for m in template_markers)


def get_relevant_meeting_context(
    meeting: MeetingRecord,
    conn,
    resolver: EntityResolver | None = None,
    *,
    limit: int = 10,
    max_excerpt_chars: int = 500,
) -> list[MeetingContextCandidate]:
    """Gather and rank relevant context for a meeting using deterministic scoring.

    Args:
        meeting: The meeting to gather context for.
        conn: Open DuckDB connection.
        resolver: Optional EntityResolver for attendee name resolution.
        limit: Maximum number of candidates to return.
        max_excerpt_chars: Maximum excerpt length.

    Returns:
        List of MeetingContextCandidate sorted by score descending.
    """
    candidates: list[MeetingContextCandidate] = []
    today = date.today()

    # Build entity sets
    linked_entities: list[tuple[str, str]] = []
    attendee_entities: list[tuple[str, str]] = []
    title_entities: list[tuple[str, str]] = []

    # Explicit linked entities
    for le in meeting.linked_entities:
        et = le.get("entity_type", "")
        en = le.get("entity_name", "")
        if et and en:
            linked_entities.append((et, en))

    # Attendee entities
    if resolver:
        for attendee in meeting.attendees:
            match = resolver.resolve_any(attendee)
            if match:
                attendee_entities.append((match.entity_type, match.canonical_name))

    # Title entities
    if resolver:
        for m in resolver.extract_entities_from_text(meeting.title):
            title_entities.append((m.entity_type, m.canonical_name))

    # Combine all entities with source tracking
    all_entities: set[tuple[str, str]] = set()
    all_entities.update(linked_entities)
    all_entities.update(attendee_entities)
    all_entities.update(title_entities)

    # Extract title keywords for matching
    title_keywords = [w for w in re.findall(r'\b\w+\b', meeting.title.lower()) if len(w) > 3]

    # ------------------------------------------------------------------
    # 1. Notes matching linked entities
    # ------------------------------------------------------------------
    for et, en in linked_entities:
        rows = conn.execute(
            """SELECT id, title, note_date, body, entity_type, entity_name
               FROM notes WHERE entity_type = ? AND entity_name = ?
               ORDER BY note_date DESC NULLS LAST LIMIT 5""",
            [et, en],
        ).fetchall()
        for row in rows:
            note_id, title, note_date, body, n_et, n_en = row
            score = 0.0
            reasons = []

            # Explicit linked entity: +60
            score += 60
            reasons.append("explicit linked entity")

            # Exact entity match: +50
            score += 50
            reasons.append("exact entity match")

            # Recency bonus
            if note_date:
                days_ago = (today - note_date).days
                if days_ago <= 14:
                    score += 10
                    reasons.append("recent (within 14 days)")
                elif days_ago <= 45:
                    score += 5
                    reasons.append("recent (within 45 days)")
                elif days_ago > 180:
                    score -= 20
                    reasons.append("stale (>180 days)")

            # Template penalty
            if _is_template_note(title or "", body or ""):
                score -= 40
                reasons.append("template/sample note")

            excerpt = _extract_excerpt(body or "", [en] + title_keywords, max_excerpt_chars)

            candidates.append(MeetingContextCandidate(
                source_type="note",
                source_id=note_id,
                source_path="",
                title=title or "",
                date=note_date,
                entity_type=n_et,
                entity_name=n_en,
                excerpt=excerpt,
                score=score,
                reasons=reasons,
                metadata={"note_type": "entity_note"},
            ))

    # ------------------------------------------------------------------
    # 2. Notes matching attendees
    # ------------------------------------------------------------------
    for et, en in attendee_entities:
        if (et, en) in linked_entities:
            continue  # Already covered
        rows = conn.execute(
            """SELECT id, title, note_date, body, entity_type, entity_name
               FROM notes WHERE entity_type = ? AND entity_name = ?
               ORDER BY note_date DESC NULLS LAST LIMIT 3""",
            [et, en],
        ).fetchall()
        for row in rows:
            note_id, title, note_date, body, n_et, n_en = row
            score = 0.0
            reasons = []

            # Attendee match: +40
            score += 40
            reasons.append("attendee match")

            # Recency
            if note_date:
                days_ago = (today - note_date).days
                if days_ago <= 14:
                    score += 10
                    reasons.append("recent (within 14 days)")
                elif days_ago <= 45:
                    score += 5
                elif days_ago > 180:
                    score -= 20
                    reasons.append("stale (>180 days)")

            if _is_template_note(title or "", body or ""):
                score -= 40
                reasons.append("template/sample note")

            excerpt = _extract_excerpt(body or "", [en] + title_keywords, max_excerpt_chars)

            candidates.append(MeetingContextCandidate(
                source_type="note",
                source_id=note_id,
                source_path="",
                title=title or "",
                date=note_date,
                entity_type=n_et,
                entity_name=n_en,
                excerpt=excerpt,
                score=score,
                reasons=reasons,
                metadata={"note_type": "attendee_note"},
            ))

    # ------------------------------------------------------------------
    # 3. Notes matching title keywords
    # ------------------------------------------------------------------
    if title_keywords:
        # Build LIKE conditions for each keyword
        conditions = []
        params = []
        for kw in title_keywords[:10]:
            conditions.append("LOWER(title) LIKE ?")
            conditions.append("LOWER(body) LIKE ?")
            params.extend([f"%{kw}%", f"%{kw}%"])
        where_clause = " OR ".join(conditions)
        rows = conn.execute(
            f"""SELECT id, title, note_date, body, entity_type, entity_name
                FROM notes
                WHERE {where_clause}
                ORDER BY note_date DESC NULLS LAST LIMIT 5""",
            params,
        ).fetchall()
        for row in rows:
            note_id, title, note_date, body, n_et, n_en = row
            # Skip if already covered by entity match
            if (n_et, n_en) in all_entities:
                continue

            score = 0.0
            reasons = []

            # Title keyword match: +30
            score += 30
            reasons.append("title keyword match")

            if note_date:
                days_ago = (today - note_date).days
                if days_ago <= 14:
                    score += 10
                    reasons.append("recent (within 14 days)")
                elif days_ago <= 45:
                    score += 5
                elif days_ago > 180:
                    score -= 20
                    reasons.append("stale (>180 days)")

            if _is_template_note(title or "", body or ""):
                score -= 40
                reasons.append("template/sample note")

            excerpt = _extract_excerpt(body or "", title_keywords, max_excerpt_chars)

            candidates.append(MeetingContextCandidate(
                source_type="note",
                source_id=note_id,
                source_path="",
                title=title or "",
                date=note_date,
                entity_type=n_et or "",
                entity_name=n_en or "",
                excerpt=excerpt,
                score=score,
                reasons=reasons,
                metadata={"note_type": "keyword_match"},
            ))

    # ------------------------------------------------------------------
    # 4. Open signals for linked entities
    # ------------------------------------------------------------------
    for et, en in all_entities:
        rows = conn.execute(
            """SELECT id, signal_date, entity_type, entity_name,
                      signal_type, severity, summary, status
               FROM signals
               WHERE entity_type = ? AND entity_name = ? AND status = 'open'
               ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                      WHEN 'medium' THEN 2 ELSE 3 END
               LIMIT 3""",
            [et, en],
        ).fetchall()
        for row in rows:
            sig_id, sig_date, s_et, s_en, sig_type, severity, summary, status = row
            score = 0.0
            reasons = []

            # Open critical/high signal: +35
            if severity in ("critical", "high"):
                score += 35
                reasons.append(f"open {severity} signal")
            else:
                score += 15
                reasons.append("open signal")

            if (s_et, s_en) in linked_entities:
                score += 20
                reasons.append("linked entity signal")

            candidates.append(MeetingContextCandidate(
                source_type="signal",
                source_id=sig_id,
                source_path="",
                title=f"{severity.upper()} signal: {summary[:60]}",
                date=sig_date,
                entity_type=s_et,
                entity_name=s_en,
                excerpt=summary or "",
                score=score,
                reasons=reasons,
                metadata={"signal_type": sig_type, "severity": severity},
            ))

    # ------------------------------------------------------------------
    # 5. Open action items for linked entities
    # ------------------------------------------------------------------
    for et, en in all_entities:
        rows = conn.execute(
            """SELECT id, assigned_to, description, due_date, status
               FROM action_items
               WHERE status = 'open' AND (LOWER(description) LIKE ? OR LOWER(assigned_to) LIKE ?)
               LIMIT 3""",
            [f"%{en.lower()}%", f"%{en.lower()}%"],
        ).fetchall()
        for row in rows:
            ai_id, assigned_to, description, due_date, status = row
            score = 25.0
            reasons = ["open action item"]

            if (et, en) in linked_entities:
                score += 15
                reasons.append("linked entity action item")

            candidates.append(MeetingContextCandidate(
                source_type="action_item",
                source_id=ai_id,
                source_path="",
                title=f"Action: {description[:60]}",
                date=due_date,
                entity_type=et,
                entity_name=en,
                excerpt=description or "",
                score=score,
                reasons=reasons,
                metadata={"assigned_to": assigned_to},
            ))

    # ------------------------------------------------------------------
    # 6. Deals matching meeting title/client/account
    # ------------------------------------------------------------------
    if title_keywords or all_entities:
        deal_keywords = [en for _, en in all_entities if _ == "client"] + title_keywords[:5]
        if deal_keywords:
            # Build LIKE conditions
            conditions = []
            params = []
            for kw in deal_keywords:
                conditions.append("LOWER(account) LIKE ?")
                conditions.append("LOWER(deal_name) LIKE ?")
                params.extend([f"%{kw}%", f"%{kw}%"])
            where_clause = " OR ".join(conditions)
            rows = conn.execute(
                f"""SELECT id, account, deal_name, deal_id, stage, close_date, next_action
                    FROM deals
                    WHERE {where_clause}
                    LIMIT 3""",
                params,
            ).fetchall()
            for row in rows:
                deal_id, account, deal_name, deal_id_str, stage, close_date, next_action = row
                score = 20.0
                reasons = ["deal match"]

                # Check if exact client match
                for _, en in all_entities:
                    if en.lower() in (account or "").lower():
                        score += 30
                        reasons.append("exact client/deal match")
                        break

                excerpt = next_action or f"{stage} - {deal_name}"
                candidates.append(MeetingContextCandidate(
                    source_type="deal",
                    source_id=deal_id,
                    source_path="",
                    title=deal_name or account,
                    date=close_date,
                    entity_type="deal",
                    entity_name=deal_name or account,
                    excerpt=excerpt,
                    score=score,
                    reasons=reasons,
                    metadata={"account": account, "stage": stage},
                ))

    # ------------------------------------------------------------------
    # 7. Projects matching meeting title/client/technology
    # ------------------------------------------------------------------
    if title_keywords or all_entities:
        proj_keywords = [en for _, en in all_entities] + title_keywords[:5]
        if proj_keywords:
            # Build LIKE conditions
            conditions = []
            params = []
            for kw in proj_keywords:
                conditions.append("LOWER(project_name) LIKE ?")
                conditions.append("LOWER(client) LIKE ?")
                conditions.append("LOWER(summary) LIKE ?")
                params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
            where_clause = " OR ".join(conditions)
            rows = conn.execute(
                f"""SELECT id, project_name, client, opportunity_number, summary, technologies_json
                    FROM projects
                    WHERE {where_clause}
                    LIMIT 3""",
                params,
            ).fetchall()
            for row in rows:
                proj_id, proj_name, client, opp_num, summary, tech_json = row
                score = 20.0
                reasons = ["project match"]

                for _, en in all_entities:
                    if en.lower() in (client or "").lower() or en.lower() in (proj_name or "").lower():
                        score += 30
                        reasons.append("exact project/client match")
                        break

                excerpt = summary or proj_name or ""
                candidates.append(MeetingContextCandidate(
                    source_type="project",
                    source_id=proj_id,
                    source_path="",
                    title=proj_name or client,
                    date=None,
                    entity_type="project",
                    entity_name=proj_name or client,
                    excerpt=excerpt[:max_excerpt_chars],
                    score=score,
                    reasons=reasons,
                    metadata={"client": client, "opportunity_number": opp_num},
                ))

    # ------------------------------------------------------------------
    # 8. Project documents metadata
    # ------------------------------------------------------------------
    if all_entities:
        for _, en in all_entities:
            rows = conn.execute(
                """SELECT id, title, document_type, url, why_matched
                   FROM project_documents
                   WHERE LOWER(client) LIKE ? OR LOWER(project_name) LIKE ?
                   LIMIT 2""",
                [f"%{en.lower()}%", f"%{en.lower()}%"],
            ).fetchall()
            for row in rows:
                doc_id, title, doc_type, url, why_matched = row
                score = 15.0
                reasons = ["document metadata match"]

                candidates.append(MeetingContextCandidate(
                    source_type="document",
                    source_id=doc_id,
                    source_path=url or "",
                    title=title or "",
                    date=None,
                    entity_type="document",
                    entity_name=title or "",
                    excerpt=why_matched or f"{doc_type}: {title}",
                    score=score,
                    reasons=reasons,
                    metadata={"document_type": doc_type},
                ))

    # ------------------------------------------------------------------
    # 9. Prior meetings with same attendees/title
    # ------------------------------------------------------------------
    if meeting.attendees:
        # Build LIKE conditions for attendees
        conditions = ["LOWER(title) LIKE ?"]
        params = [f"%{meeting.title[:30].lower()}%"]
        for attendee in meeting.attendees[:5]:
            conditions.append("attendees LIKE ?")
            params.append(f"%{attendee}%")
        where_clause = " OR ".join(conditions)
        rows = conn.execute(
            f"""SELECT id, meeting_date, title, attendees
                FROM meetings
                WHERE id != ? AND ({where_clause})
                ORDER BY meeting_date DESC LIMIT 3""",
            [meeting.id] + params,
        ).fetchall()
        for row in rows:
            mtg_id, mtg_date, mtg_title, mtg_attendees = row
            score = 10.0
            reasons = ["prior meeting with same attendees/title"]

            candidates.append(MeetingContextCandidate(
                source_type="meeting",
                source_id=mtg_id,
                source_path="",
                title=mtg_title or "",
                date=mtg_date,
                entity_type="meeting",
                entity_name=mtg_title or "",
                excerpt=f"Meeting on {mtg_date}",
                score=score,
                reasons=reasons,
                metadata={"attendees": mtg_attendees},
            ))

    # ------------------------------------------------------------------
    # De-duplicate by source_id, sort by score, return top N
    # ------------------------------------------------------------------
    seen: set[str] = set()
    unique_candidates: list[MeetingContextCandidate] = []
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        if c.source_id not in seen:
            seen.add(c.source_id)
            unique_candidates.append(c)

    return unique_candidates[:limit]


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

    Uses scored context retrieval to gather the most relevant notes, signals,
    action items, deals, projects, and documents for the meeting.

    Args:
        meeting: The MeetingRecord to generate prep for.
        conn: Open DuckDB connection.
        resolver: Optional EntityResolver for attendee name resolution.

    Returns:
        MeetingPrepRecord with rendered markdown content.
    """
    # Get scored context candidates
    scored_context = get_relevant_meeting_context(meeting, conn, resolver, limit=10)

    # Also gather signals and action items for dedicated sections
    linked: list[tuple[str, str]] = []
    for le in meeting.linked_entities:
        et = le.get("entity_type", "")
        en = le.get("entity_name", "")
        if et and en:
            linked.append((et, en))
    if resolver:
        for attendee in meeting.attendees:
            match = resolver.resolve_any(attendee)
            if match:
                linked.append((match.entity_type, match.canonical_name))
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

    # Gather signals and action items for dedicated sections
    active_signals = _get_signals_for_entities(conn, unique_linked)
    open_action_items = _get_action_items_for_entities(conn, unique_linked)
    suggested_questions = _build_suggested_questions(active_signals)

    # Separate context by type for template sections
    project_deal_context = [c for c in scored_context if c.source_type in ("project", "deal")]
    related_documents = [c for c in scored_context if c.source_type == "document"]

    attendees_str = ", ".join(meeting.attendees) if meeting.attendees else ""

    # Render template
    env = Environment(autoescape=False, trim_blocks=True, lstrip_blocks=True)
    template = env.from_string(_MEETING_PREP_TEMPLATE)
    content = template.render(
        meeting=meeting,
        attendees_str=attendees_str,
        scored_context=scored_context,
        active_signals=active_signals,
        open_action_items=open_action_items,
        project_deal_context=project_deal_context,
        related_documents=related_documents,
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
    """Append an AI Synthesis section to a MeetingPrepRecord using Gemini CLI.

    Requires MANAGER_OS_GEMINI_CLI_BIN to be set. Silently returns the original prep
    if the CLI binary is not configured or not found.

    Args:
        prep: An existing MeetingPrepRecord to enrich.
        conn: Open DuckDB connection (for writing the updated record back).

    Returns:
        Updated MeetingPrepRecord with AI Synthesis appended to content.
    """
    import os
    import subprocess
    import tempfile
    
    cli_bin = os.environ.get("MANAGER_OS_GEMINI_CLI_BIN", "")
    if not cli_bin:
        logger.debug("MANAGER_OS_GEMINI_CLI_BIN not set — skipping LLM meeting prep enrichment")
        return prep

    # Cap context to avoid token limits
    context = prep.content[:4000]
    full_prompt = _LLM_SYSTEM_PROMPT + "\n\n" + context

    try:
        # Write prompt to temp file to avoid shell escaping issues
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(full_prompt)
            prompt_file = f.name
        
        try:
            cli_args = os.environ.get("MANAGER_OS_GEMINI_CLI_ARGS", "")
            model = os.environ.get("MANAGER_OS_GEMINI_CLI_MODEL", "gemini-2.0-flash")
            
            cmd = [cli_bin]
            if cli_args:
                cmd.extend(cli_args.split())
            cmd.extend(["--model", model, "--prompt-file", prompt_file])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            
            if result.returncode != 0:
                logger.warning("Gemini CLI failed (exit %d): %s", result.returncode, result.stderr)
                return prep
            
            synthesis = result.stdout.strip()
        finally:
            os.unlink(prompt_file)
            
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

    # Check if record exists to avoid INSERT OR REPLACE
    existing = conn.execute(
        "SELECT id FROM meeting_prep WHERE id = ?", [updated_prep.id]
    ).fetchone()
    
    if existing:
        conn.execute(
            "UPDATE meeting_prep SET content = ?, generated_at = ? WHERE id = ?",
            [updated_prep.content, updated_prep.generated_at, updated_prep.id],
        )
    else:
        conn.execute(
            "INSERT INTO meeting_prep (id, meeting_id, content, generated_at) VALUES (?, ?, ?, ?)",
            [updated_prep.id, updated_prep.meeting_id, updated_prep.content, updated_prep.generated_at],
        )

    return updated_prep
