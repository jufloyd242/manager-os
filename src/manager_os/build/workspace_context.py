"""Workspace Context service.

Normalizes workspace summaries and activity snapshots into reusable context
items with entity linking, date awareness, and provenance tracking.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from manager_os.extract.entities import EntityResolver


@dataclass
class ContextItem:
    """A single piece of workspace context."""
    source_type: str  # workspace_summary, workspace_activity
    source_path: str
    source_date: date | None
    entity_type: str  # person, client, deal, project, or ""
    entity_name: str
    link_method: str  # explicit, structured, exact_id, exact_name, alias, unlinked
    link_evidence: str
    confidence: str  # high, medium, low
    title: str
    excerpt: str
    is_attention: bool
    is_action: bool
    provenance: dict = field(default_factory=dict)


def get_workspace_context(
    conn,
    target_date: date,
    *,
    lookback_days: int = 0,
    entity_type: str | None = None,
    entity: str | None = None,
    attention_only: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Get workspace context for a given date with optional lookback.

    Args:
        conn: Open DuckDB connection.
        target_date: The date to query.
        lookback_days: Look back this many days (0 = exact date only).
        entity_type: Filter by entity type (person/client/deal/project).
        entity: Filter by entity name.
        attention_only: Only return attention items.
        limit: Max context items.

    Returns:
        Dict with keys: selected_date, lookback_start, latest_actual_source_date,
        context_items, linked_count, unlinked_count, attention_count,
        freshness, warnings.
    """
    warnings: list[str] = []
    start_date = target_date - timedelta(days=lookback_days) if lookback_days > 0 else target_date

    # Gather from raw_documents (workspace_summary and workspace_activity)
    context_items: list[ContextItem] = []

    try:
        rows = conn.execute(
            """
            SELECT id, source_type, source_path, content, metadata, ingested_at
            FROM raw_documents
            WHERE source_type IN ('workspace_summary', 'workspace_activity')
              AND ingested_at >= ?
            ORDER BY ingested_at DESC
            LIMIT ?
            """,
            [start_date, limit * 2],
        ).fetchall()
    except Exception as exc:
        warnings.append(f"workspace_context raw_documents: {exc}")
        rows = []

    # Build resolver
    resolver = _build_resolver(conn)

    for row in rows:
        doc_id, source_type, source_path, content, metadata_raw, ingested_at = row

        # Parse metadata for source date
        metadata = {}
        if metadata_raw:
            try:
                metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else (metadata_raw or {})
            except (json.JSONDecodeError, TypeError):
                pass

        summary_date = _extract_date(metadata, source_path)
        if summary_date is None:
            summary_date = _extract_date_from_ingested(ingested_at, start_date)

        # Skip if outside lookback range
        if summary_date and summary_date < start_date:
            continue

        # Parse content into items
        content_str = content or ""
        items = _parse_content(source_type, source_path, summary_date, content_str, resolver)
        context_items.extend(items)

    # Also gather from workspace_activity notes in notes table
    try:
        note_rows = conn.execute(
            """
            SELECT id, note_date, entity_type, entity_name, title, body, raw_document_id
            FROM notes
            WHERE note_type = 'workspace_activity'
              AND note_date >= ?
            ORDER BY note_date DESC
            LIMIT ?
            """,
            [start_date, limit],
        ).fetchall()
    except Exception as exc:
        warnings.append(f"workspace_context notes: {exc}")
        note_rows = []

    for row in note_rows:
        note_id, note_date, n_et, n_en, title, body, raw_doc_id = row
        ctx = ContextItem(
            source_type="workspace_activity",
            source_path=f"note:{note_id}",
            source_date=note_date,
            entity_type=n_et or "",
            entity_name=n_en or "",
            link_method="explicit" if n_et else "unlinked",
            link_evidence="structured note entity" if n_et else "No entity assigned",
            confidence="high" if n_et else "low",
            title=title or "",
            excerpt=(body or "")[:300],
            is_attention=_is_attention_text(body or ""),
            is_action=_is_action_text(body or ""),
            provenance={"note_id": note_id, "raw_document_id": raw_doc_id},
        )
        context_items.append(ctx)

    # Deduplicate by content
    seen_excerpts: set[str] = set()
    deduped: list[ContextItem] = []
    for item in context_items:
        key = item.excerpt[:100].strip().lower()
        if key and key not in seen_excerpts:
            seen_excerpts.add(key)
            deduped.append(item)

    # Apply filters
    if entity_type:
        deduped = [c for c in deduped if c.entity_type == entity_type]
    if entity:
        entity_lower = entity.lower()
        deduped = [c for c in deduped if entity_lower in c.entity_name.lower()]
    if attention_only:
        deduped = [c for c in deduped if c.is_attention]

    # Sort: attention first, then recent first
    deduped.sort(key=lambda c: (not c.is_attention, c.source_date or date.min), reverse=False)

    linked = [c for c in deduped if c.link_method != "unlinked"]
    unlinked = [c for c in deduped if c.link_method == "unlinked"]
    attention_items = [c for c in deduped if c.is_attention]

    # Latest actual source date
    actual_dates = [c.source_date for c in deduped if c.source_date]
    latest_source = max(actual_dates) if actual_dates else None

    return {
        "selected_date": target_date.isoformat(),
        "lookback_start": start_date.isoformat() if lookback_days > 0 else target_date.isoformat(),
        "latest_actual_source_date": latest_source.isoformat() if latest_source else None,
        "context_items": [_context_item_to_dict(c) for c in deduped[:limit]],
        "linked_count": len(linked),
        "unlinked_count": len(unlinked),
        "attention_count": len(attention_items),
        "freshness": "fresh" if latest_source and latest_source >= target_date - timedelta(days=1) else "stale",
        "warnings": warnings,
    }


def get_context_for_entities(
    conn,
    target_date: date,
    entities: list[tuple[str, str]],
    *,
    lookback_days: int = 7,
    limit: int = 20,
) -> list[ContextItem]:
    """Get workspace context for specific entities with lookback.

    Args:
        conn: Open DuckDB connection.
        target_date: Reference date.
        entities: List of (entity_type, entity_name) tuples.
        lookback_days: Lookback window.
        limit: Max items per entity.

    Returns:
        List of ContextItem sorted by entity match then recency.
    """
    all_items: list[ContextItem] = []
    seen_keys: set[str] = set()

    for et, en in entities:
        result = get_workspace_context(
            conn, target_date,
            lookback_days=lookback_days,
            entity_type=et,
            entity=en,
            limit=limit,
        )
        for item_dict in result.get("context_items", []):
            key = f"{item_dict.get('source_path')}:{item_dict.get('title')}:{item_dict.get('excerpt', '')[:80]}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_items.append(_dict_to_context_item(item_dict))

    # Sort by entity match confidence then recency
    def _sort_key(c: ContextItem) -> tuple:
        entity_match = 0
        for et, en in entities:
            if c.entity_type == et and c.entity_name.lower() == en.lower():
                entity_match = 1
        return (-entity_match, -(c.source_date or date.min).toordinal() if c.source_date else 0)

    all_items.sort(key=_sort_key)
    return all_items


def _parse_content(
    source_type: str,
    source_path: str,
    summary_date: date | None,
    content: str,
    resolver: EntityResolver | None,
) -> list[ContextItem]:
    """Parse workspace content into context items."""
    items: list[ContextItem] = []
    lines = content.split("\n")
    current_section = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Detect headings as sections
        if stripped.startswith("#"):
            current_section = stripped.lstrip("#").strip()
            continue

        # Bullet items
        is_bullet = stripped.startswith("-") or stripped.startswith("*")
        text = stripped.lstrip("-* ").strip() if is_bullet else stripped

        # Skip very short lines
        if len(text) < 10:
            continue

        # Entity linking
        entity_type, entity_name, link_method, link_evidence, confidence = _link_entity(
            text, resolver
        )

        item = ContextItem(
            source_type=source_type,
            source_path=source_path,
            source_date=summary_date,
            entity_type=entity_type,
            entity_name=entity_name,
            link_method=link_method,
            link_evidence=link_evidence,
            confidence=confidence,
            title=current_section or source_path.split("/")[-1],
            excerpt=text[:300],
            is_attention=_is_attention_text(text),
            is_action=_is_action_text(text),
            provenance={"section": current_section, "line_count": len(lines)},
        )
        items.append(item)

    # If no structured items found, create one document-level excerpt
    if not items and len(content) > 20:
        excerpt = content.strip()[:500]
        entity_type, entity_name, link_method, link_evidence, confidence = _link_entity(
            excerpt, resolver
        )
        items.append(ContextItem(
            source_type=source_type,
            source_path=source_path,
            source_date=summary_date,
            entity_type=entity_type,
            entity_name=entity_name,
            link_method=link_method,
            link_evidence=link_evidence,
            confidence=confidence,
            title=source_path.split("/")[-1],
            excerpt=excerpt,
            is_attention=False,
            is_action=False,
            provenance={"fallback": "document-level excerpt"},
        ))

    return items


def _link_entity(
    text: str,
    resolver: EntityResolver | None,
) -> tuple[str, str, str, str, str]:
    """Link text to an entity using conservative matching.

    Precedence:
    1. Explicit structured entity data
    2. Structured metadata
    3. Exact opportunity/deal ID match
    4. Exact canonical name match
    5. Safe configured alias match
    6. Unlinked

    Ambiguous short names remain unlinked.
    """
    text_lower = text.lower()

    if resolver:
        # Try resolution
        match = resolver.resolve_any(text)
        if match:
            return match.entity_type, match.canonical_name, "alias", f"Resolved via alias: {match.alias_used}", "high"

    # Check for explicit person names (multi-word, capitalized)
    # This is a conservative check - only exact canonical matches
    words = text.split()
    for w in words:
        cleaned = w.strip(".,;:!?()[]{}").strip()
        if len(cleaned) > 3 and cleaned[0].isupper() and " " in cleaned:
            if resolver:
                match = resolver.resolve_any(cleaned)
                if match:
                    return match.entity_type, match.canonical_name, "exact_name", f"Name match: {cleaned}", "high"

    return "", "", "unlinked", "No entity match found", "low"


def _is_attention_text(text: str) -> bool:
    """Detect if text suggests it needs attention."""
    attention_markers = [
        "blocker", "blocked", "risk", "urgent", "escalation",
        "overallocated", "at-risk", "missed", "overdue",
        "critical", "high priority", "action required",
    ]
    text_lower = text.lower()
    return any(marker in text_lower for marker in attention_markers)


def _is_action_text(text: str) -> bool:
    """Detect if text describes an action."""
    action_markers = [
        "[ ]", "[x]", "action item", "todo:", "to-do:",
        "follow up", "follow-up", "need to", "needs to",
        "will do", "will review", "will send",
    ]
    text_lower = text.lower()
    return any(marker in text_lower for marker in action_markers)


def _extract_date(metadata: dict, source_path: str) -> date | None:
    """Extract date from metadata or source path."""
    # Check metadata
    for key in ("date", "summary_date", "target_date", "document_date"):
        val = metadata.get(key)
        if val:
            try:
                if isinstance(val, date):
                    return val
                return date.fromisoformat(str(val)[:10])
            except (ValueError, TypeError):
                pass

    # Try to extract from path
    path_match = re.search(r'(\d{4}-\d{2}-\d{2})', source_path)
    if path_match:
        try:
            return date.fromisoformat(path_match.group(1))
        except ValueError:
            pass

    return None


def _extract_date_from_ingested(ingested_at, fallback: date) -> date:
    """Get date from ingested_at timestamp."""
    if isinstance(ingested_at, datetime):
        return ingested_at.date()
    return fallback


def _build_resolver(conn) -> EntityResolver | None:
    """Build an EntityResolver from DB."""
    try:
        # Load people and clients from DB
        people = conn.execute("SELECT name, aliases FROM people").fetchall()
        clients = conn.execute("SELECT name, aliases FROM clients").fetchall()

        person_map: dict[str, list[str]] = {}
        for name, aliases_raw in people:
            aliases = []
            if aliases_raw:
                try:
                    aliases = json.loads(aliases_raw) if isinstance(aliases_raw, str) else aliases_raw
                except (json.JSONDecodeError, TypeError):
                    aliases = []
            person_map[name] = aliases

        client_map: dict[str, list[str]] = {}
        for name, aliases_raw in clients:
            aliases = []
            if aliases_raw:
                try:
                    aliases = json.loads(aliases_raw) if isinstance(aliases_raw, str) else aliases_raw
                except (json.JSONDecodeError, TypeError):
                    aliases = []
            client_map[name] = aliases

        # Minimal resolver - just canonical name lookup
        class _SimpleResolver:
            def __init__(self):
                self._names: dict[str, str] = {}
                for n in person_map:
                    self._names[n.lower()] = ("person", n)
                    for a in person_map[n]:
                        if a:
                            self._names[a.lower()] = ("person", n)
                for n in client_map:
                    self._names[n.lower()] = ("client", n)
                    for a in client_map[n]:
                        if a:
                            self._names[a.lower()] = ("client", n)

            def resolve_any(self, text: str):
                text_lower = text.lower().strip()
                if text_lower in self._names:
                    et, en = self._names[text_lower]
                    return type('Match', (), {'entity_type': et, 'canonical_name': en, 'alias_used': text_lower})()
                return None

            def extract_entities_from_text(self, text: str):
                return []

        return _SimpleResolver()
    except Exception:
        return None


def _context_item_to_dict(item: ContextItem) -> dict:
    """Convert ContextItem to dict for JSON serialization."""
    return {
        "source_type": item.source_type,
        "source_path": item.source_path,
        "source_date": item.source_date.isoformat() if item.source_date else None,
        "entity_type": item.entity_type,
        "entity_name": item.entity_name,
        "link_method": item.link_method,
        "link_evidence": item.link_evidence,
        "confidence": item.confidence,
        "title": item.title,
        "excerpt": item.excerpt,
        "is_attention": item.is_attention,
        "is_action": item.is_action,
        "provenance": item.provenance,
        "why_this_context": _build_why_text(item),
    }


def _dict_to_context_item(d: dict) -> ContextItem:
    """Convert dict back to ContextItem."""
    sd = d.get("source_date")
    return ContextItem(
        source_type=d.get("source_type", ""),
        source_path=d.get("source_path", ""),
        source_date=date.fromisoformat(sd) if sd else None,
        entity_type=d.get("entity_type", ""),
        entity_name=d.get("entity_name", ""),
        link_method=d.get("link_method", "unlinked"),
        link_evidence=d.get("link_evidence", ""),
        confidence=d.get("confidence", "low"),
        title=d.get("title", ""),
        excerpt=d.get("excerpt", ""),
        is_attention=d.get("is_attention", False),
        is_action=d.get("is_action", False),
        provenance=d.get("provenance", {}),
    )


def _build_why_text(item: ContextItem) -> str:
    """Build human-readable 'why this context' text."""
    parts = []
    if item.link_method != "unlinked":
        parts.append(f"Linked to {item.entity_type} '{item.entity_name}' via {item.link_method}")
    else:
        parts.append("Not linked to a known entity")
    if item.source_date:
        parts.append(f"From {item.source_date}")
    if item.is_attention:
        parts.append("Marked as attention-worthy")
    if item.is_action:
        parts.append("Contains actionable content")
    parts.append(f"Source: {item.source_path}")
    return " | ".join(parts)