"""Relationship detection — resolves person-to-manager relationships from sources.

Primary source: Obsidian frontmatter metadata already stored in
``raw_documents.metadata`` by the Obsidian ingestor.

Supported frontmatter fields (in precedence order):
1. ``relationship: direct_report | manager | peer | client | external``
2. ``reports_to: <manager name>``
3. ``manager: <manager name>``
4. ``direct_report: true``

Relationship precedence within a single note:
1. Explicit ``relationship`` field (value wins)
2. ``is not`` inference from any other field

Never infers a reporting relationship from job title, seniority, level,
``track: true``, attendance frequency, or meeting title alone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from manager_os.extract.entities import EntityResolver

logger = logging.getLogger(__name__)

# Relationship canonical values
REL_DIRECT_REPORT = "direct_report"
REL_MANAGER = "manager"
REL_PEER = "peer"
REL_CLIENT = "client"
REL_EXTERNAL = "external"
REL_UNKNOWN = "unknown"

# Frontmatter keys that are excluded from relationship inference
_NON_RELATIONSHIP_KEYS = {
    "role", "level", "track", "tags", "type", "title", "aliases",
    "manager_os", "status", "active", "note_type",
}


@dataclass
class ResolvedRelationship:
    """A single resolved relationship for a person.

    ``relationship`` is a canonical value: direct_report | manager | peer |
    client | external | unknown.

    When two Obsidian notes conflict (e.g. one says ``relationship:
    direct_report`` and another says ``relationship: manager``), the first
    one encountered wins and a warning is appended.
    """
    person_name: str
    relationship: str = REL_UNKNOWN
    evidence_source: str = "unknown"
    evidence_path: str | None = None
    warnings: list[str] = field(default_factory=list)


def resolve_person_relationships(
    conn,
    resolver: EntityResolver,
) -> list[ResolvedRelationship]:
    """Scan all Obsidian raw_documents for relationship signals.

    Only considers documents with ``source_type = 'obsidian'``.
    Returns one ``ResolvedRelationship`` per person/client found to have a
    relationship signal, either from explicit frontmatter or from a
    deterministic vault folder-path convention (see ``_detect_relationship``).
    """
    rows = conn.execute(
        """SELECT id, source_path, metadata, content
           FROM raw_documents
           WHERE source_type = 'obsidian'
           ORDER BY ingested_at DESC""",
    ).fetchall()

    person_rels: dict[str, ResolvedRelationship] = {}

    for row in rows:
        doc_id, source_path, metadata_raw, content = row
        fm = _parse_metadata(metadata_raw)

        # Determine which person/client this note is about. Frontmatter
        # name/person/author/entity fields are tried first, then the notes
        # table's entity_name, then the filename — so folder-path-only
        # notes with no frontmatter (the common case in this vault) still
        # resolve via entity_name.
        entity_name = _extract_entity_name(fm, source_path, resolver, conn)
        if not entity_name:
            continue

        rel = _detect_relationship(fm, source_path)
        if rel is None:
            # No relationship signal (frontmatter or folder path) — skip
            continue

        if entity_name in person_rels:
            existing = person_rels[entity_name]
            if existing.relationship != rel.relationship:
                existing.warnings.append(
                    f"Conflicting relationship for {entity_name}: "
                    f"'{existing.relationship}' from {existing.evidence_path} "
                    f"vs '{rel.relationship}' from {rel.evidence_path}. "
                    f"Keeping '{existing.relationship}' (first wins)."
                )
                logger.warning("%s", existing.warnings[-1])
        else:
            rel.person_name = entity_name
            person_rels[entity_name] = rel

    return list(person_rels.values())


def _parse_metadata(raw: Any) -> dict:
    """Parse metadata JSON from raw_documents row."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _extract_entity_name(
    fm: dict,
    source_path: str | None,
    resolver: EntityResolver,
    conn=None,
) -> str | None:
    """Extract a person or client canonical name from frontmatter or source path.

    Tries frontmatter ``name``, ``person``, ``author``, ``entity``, ``client``
    fields, then the notes table's ``entity_name`` for this raw_document
    (works even with zero frontmatter — the common case for folder-path-only
    notes), then finally falls back to resolving the filename stem.
    """
    # Direct name fields — try person resolution first, then client
    for key in ("name", "person", "author"):
        val = fm.get(key)
        if isinstance(val, str) and val.strip():
            resolved = resolver.resolve_person(val.strip())
            if resolved:
                return resolved

    for key in ("entity", "client"):
        val = fm.get(key)
        if isinstance(val, str) and val.strip():
            resolved = resolver.resolve_person(val.strip()) or resolver.resolve_client(val.strip())
            if resolved:
                return resolved

    # Fallback: query notes table for entity_name matching this raw_document.
    # This is the primary path for notes with no frontmatter at all (the
    # common case for team/**/*.md and clients/**/*.md folder-convention
    # notes) — the Obsidian ingestor still populates entity_name from
    # directory-based note_type inference even without frontmatter.
    if conn and source_path:
        row = conn.execute(
            "SELECT entity_name FROM notes WHERE raw_document_id IN "
            "(SELECT id FROM raw_documents WHERE source_path = ?) LIMIT 1",
            [source_path],
        ).fetchone()
        if row and row[0]:
            resolved = resolver.resolve_person(row[0]) or resolver.resolve_client(row[0])
            if resolved:
                return resolved

    # Last resort: try resolving from source path filename
    if source_path:
        import re
        from pathlib import Path
        stem = Path(source_path).stem
        # Remove date prefixes like "2026-06-13-"
        stem = re.sub(r"^\d{4}-\d{2}-\d{2}-?", "", stem)
        # Convert hyphens/underscores to spaces
        stem = stem.replace("-", " ").replace("_", " ")
        stem_title = stem.title()
        resolved = resolver.resolve_person(stem_title) or resolver.resolve_client(stem_title)
        if resolved:
            return resolved

    return None


def _detect_relationship(fm: dict, source_path: str | None) -> ResolvedRelationship | None:
    """Detect relationship from frontmatter metadata, falling back to a
    deterministic vault folder-path convention when no frontmatter signal
    exists.

    Precedence (frontmatter always wins over folder path):
    1. ``relationship: direct_report | manager | peer | client | external``
    2. ``reports_to: <manager name>``  (sets person as direct_report)
    3. ``manager: <manager name>``      (sets person as direct_report)
    4. ``direct_report: true``
    5. Folder path convention (only when 1-4 found nothing):
       - ``team/directs/**``      -> direct_report
       - ``team/my manager/**``   -> manager
       - ``team/other/**``        -> peer
       - ``team/me/**``           -> not a relationship signal (returns None)
       - ``clients/**``           -> client
    """
    # 1. Explicit relationship field
    rel_value = fm.get("relationship")
    if isinstance(rel_value, str) and rel_value.strip():
        rel_lower = rel_value.strip().lower()
        if rel_lower in (REL_DIRECT_REPORT, REL_MANAGER, REL_PEER, REL_CLIENT, REL_EXTERNAL):
            return ResolvedRelationship(
                person_name="",  # filled by caller
                relationship=rel_lower,
                evidence_source="obsidian_frontmatter",
                evidence_path=source_path,
            )

    # 2. reports_to: <manager name>
    reports_to = fm.get("reports_to")
    if isinstance(reports_to, str) and reports_to.strip():
        return ResolvedRelationship(
            person_name="",
            relationship=REL_DIRECT_REPORT,
            evidence_source="obsidian_frontmatter",
            evidence_path=source_path,
            warnings=[],
        )

    # 3. manager: <manager name>
    manager = fm.get("manager")
    if isinstance(manager, str) and manager.strip():
        return ResolvedRelationship(
            person_name="",
            relationship=REL_DIRECT_REPORT,
            evidence_source="obsidian_frontmatter",
            evidence_path=source_path,
        )

    # 4. direct_report: true
    dr = fm.get("direct_report")
    if dr is True or (isinstance(dr, str) and dr.lower() == "true"):
        return ResolvedRelationship(
            person_name="",
            relationship=REL_DIRECT_REPORT,
            evidence_source="obsidian_frontmatter",
            evidence_path=source_path,
        )

    # 5. Folder-path convention fallback — deterministic, path-based only
    # (never inferred from title/content/seniority).
    if source_path:
        folder_rel = _relationship_from_folder_path(source_path)
        if folder_rel is not None:
            return ResolvedRelationship(
                person_name="",
                relationship=folder_rel,
                evidence_source="obsidian_folder_path",
                evidence_path=source_path,
            )

    return None


def _relationship_from_folder_path(source_path: str) -> str | None:
    """Map a vault folder-path convention to a canonical relationship.

    Matches (case-insensitive) path segments:
        team/directs/**      -> direct_report
        team/my manager/**   -> manager
        team/other/**        -> peer
        team/me/**           -> None (self, not a relationship)
        clients/**           -> client

    Returns None if no convention path segment is found.
    """
    normalized = source_path.replace("\\", "/").lower()

    if "/team/directs/" in normalized or normalized.startswith("team/directs/"):
        return REL_DIRECT_REPORT
    if "/team/my manager/" in normalized or normalized.startswith("team/my manager/"):
        return REL_MANAGER
    if "/team/other/" in normalized or normalized.startswith("team/other/"):
        return REL_PEER
    if "/team/me/" in normalized or normalized.startswith("team/me/"):
        return None
    if "/clients/" in normalized or normalized.startswith("clients/"):
        return REL_CLIENT

    return None


def get_relationship_for_attendee(
    attendee_name: str,
    relationships: list[ResolvedRelationship],
    resolver: EntityResolver,
) -> ResolvedRelationship:
    """Look up the relationship for a specific attendee name.

    Returns a ResolvedRelationship with relationship='unknown' if not found.
    """
    canonical = resolver.resolve_person(attendee_name) or attendee_name

    for rel in relationships:
        if rel.person_name == canonical:
            return rel

    return ResolvedRelationship(
        person_name=canonical,
        relationship=REL_UNKNOWN,
        evidence_source="not_found",
    )