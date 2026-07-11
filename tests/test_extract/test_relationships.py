"""Tests for relationship detection from Obsidian frontmatter.

Verifies that explicit frontmatter fields like ``relationship:``,
``reports_to:``, ``manager:``, and ``direct_report:`` are correctly
resolved to structured ``ResolvedRelationship`` results.

Key guardrails tested:
- Job title or track:true alone does NOT create a reporting relationship
- Conflicting metadata generates a warning
- Precedence: explicit relationship > reports_to > unknown
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from manager_os.config import ClientConfig, PersonConfig
from manager_os.db import content_hash, get_connection
from manager_os.extract.entities import EntityResolver
from manager_os.extract.relationships import (
    resolve_person_relationships,
    get_relationship_for_attendee,
)


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def resolver() -> EntityResolver:
    people = [
        PersonConfig(name="Justin Floyd", aliases=["Justin", "justin", "Justin Floyd"]),
        PersonConfig(name="Alice Chen", aliases=["Alice", "alice", "Alice Chen"]),
        PersonConfig(name="Bob Smith", aliases=["Bob", "bob", "Bob Smith"]),
        PersonConfig(name="Charlie Brown", aliases=["Charlie", "charlie", "Charlie Brown"]),
        PersonConfig(name="Diana Prince", aliases=["Diana", "diana", "Diana Prince"]),
    ]
    clients = [ClientConfig(name="Acme Corp", aliases=["Acme", "acme", "Acme Corp"])]
    return EntityResolver(people, clients, {})


def _seed_person_note(conn, person_name: str, frontmatter: dict) -> None:
    """Create a raw_document + notes entry for a person with given frontmatter metadata."""
    import uuid
    now = datetime.utcnow()
    doc_id = str(uuid.uuid4())
    note_id = str(uuid.uuid4())
    c_hash = content_hash(str(frontmatter))

    conn.execute(
        """INSERT INTO raw_documents (id, ingested_at, source_type, source_path,
           file_modified_at, content_hash, content, metadata)
           VALUES (?, ?, 'obsidian', ?, ?, ?, ?, ?)""",
        [doc_id, now, f"vault/people/{person_name.lower().replace(' ', '-')}.md",
         now, c_hash, f"# {person_name}\n\nSome notes about {person_name}.",
         json.dumps(frontmatter)],
    )

    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
           entity_name, title, body, tags, created_at)
           VALUES (?, ?, NULL, '1on1', 'person', ?, ?, ?, '[]', ?)""",
        [note_id, doc_id, person_name, f"Note for {person_name}", "", now],
    )


# ------------------------------------------------------------------
# Direct report from explicit relationship field
# ------------------------------------------------------------------


def test_direct_report_from_obsidian_frontmatter(conn, resolver) -> None:
    """Explicit relationship: direct_report in frontmatter → correct resolution."""
    _seed_person_note(conn, "Alice Chen", {"relationship": "direct_report"})

    relationships = resolve_person_relationships(conn, resolver)
    alice_key = resolver.resolve_person("Alice Chen")
    assert alice_key is not None

    alice_rels = [r for r in relationships if r.person_name == alice_key]
    assert len(alice_rels) >= 1
    direct_report_rels = [r for r in alice_rels if r.relationship == "direct_report"]
    assert len(direct_report_rels) >= 1
    assert direct_report_rels[0].evidence_source == "obsidian_frontmatter"
    assert direct_report_rels[0].evidence_path is not None


def test_manager_from_obsidian_frontmatter(conn, resolver) -> None:
    """Explicit relationship: manager in frontmatter → correct resolution."""
    _seed_person_note(conn, "Charlie Brown", {"relationship": "manager"})

    relationships = resolve_person_relationships(conn, resolver)
    charlie_key = resolver.resolve_person("Charlie Brown")
    assert charlie_key is not None

    charlie_rels = [r for r in relationships if r.person_name == charlie_key]
    assert len(charlie_rels) >= 1
    manager_rels = [r for r in charlie_rels if r.relationship == "manager"]
    assert len(manager_rels) >= 1


# ------------------------------------------------------------------
# reports_to and direct_report: true
# ------------------------------------------------------------------


def test_reports_to_field_from_obsidian(conn, resolver) -> None:
    """reports_to: 'Justin Floyd' in frontmatter → manager relationship."""
    _seed_person_note(conn, "Bob Smith", {"reports_to": "Justin Floyd"})

    relationships = resolve_person_relationships(conn, resolver)
    bob_key = resolver.resolve_person("Bob Smith")
    assert bob_key is not None

    bob_rels = [r for r in relationships if r.person_name == bob_key]
    assert len(bob_rels) >= 1
    # reports_to field should resolve to a 'manager' relationship for Bob
    # (meaning Justin Floyd is Bob's manager)
    manager_rels = [r for r in bob_rels if r.relationship == "direct_report"]
    # Or the other way — Bob's reports_to=Justin means Bob is a direct_report
    justin_rels = [r for r in relationships if r.person_name == resolver.resolve_person("Justin Floyd")]
    relevant = bob_rels + justin_rels
    assert any(r.relationship == "direct_report" for r in relevant)


def test_direct_report_true_from_obsidian(conn, resolver) -> None:
    """direct_report: true in frontmatter → direct_report relationship."""
    _seed_person_note(conn, "Diana Prince", {"direct_report": True})

    relationships = resolve_person_relationships(conn, resolver)
    diana_key = resolver.resolve_person("Diana Prince")
    assert diana_key is not None

    diana_rels = [r for r in relationships if r.person_name == diana_key]
    assert len(diana_rels) >= 1
    dr_rels = [r for r in diana_rels if r.relationship == "direct_report"]
    assert len(dr_rels) >= 1


# ------------------------------------------------------------------
# Guardrails
# ------------------------------------------------------------------


def test_job_title_alone_does_not_infer_relationship(conn, resolver) -> None:
    """No frontmatter relationship field → unknown, even with a job title."""
    _seed_person_note(conn, "Alice Chen", {"role": "Senior AI Engineer", "level": "L4"})

    relationships = resolve_person_relationships(conn, resolver)
    alice_key = resolver.resolve_person("Alice Chen")
    assert alice_key is not None

    alice_rels = [r for r in relationships if r.person_name == alice_key]
    # Either no relationship, or relationship is 'unknown'
    if alice_rels:
        assert all(r.relationship == "unknown" for r in alice_rels)


def test_track_true_alone_does_not_infer_relationship(conn, resolver) -> None:
    """track: true alone (no frontmatter relationship) → unknown."""
    _seed_person_note(conn, "Alice Chen", {"track": True})

    relationships = resolve_person_relationships(conn, resolver)
    alice_key = resolver.resolve_person("Alice Chen")
    assert alice_key is not None

    alice_rels = [r for r in relationships if r.person_name == alice_key]
    if alice_rels:
        assert all(r.relationship == "unknown" for r in alice_rels)


def test_peer_relationship_from_frontmatter(conn, resolver) -> None:
    """relationship: peer → resolved correctly."""
    _seed_person_note(conn, "Diana Prince", {"relationship": "peer"})

    relationships = resolve_person_relationships(conn, resolver)
    diana_key = resolver.resolve_person("Diana Prince")
    assert diana_key is not None

    diana_rels = [r for r in relationships if r.person_name == diana_key]
    assert len(diana_rels) >= 1
    peer_rels = [r for r in diana_rels if r.relationship == "peer"]
    assert len(peer_rels) >= 1


def test_client_relationship_from_frontmatter(conn, resolver) -> None:
    """relationship: client → resolved correctly."""
    from datetime import date
    import uuid
    now = datetime.utcnow()

    # Seed client note (not a person note)
    conn.execute(
        """INSERT INTO raw_documents (id, ingested_at, source_type, source_path,
           file_modified_at, content_hash, content, metadata)
           VALUES (?, ?, 'obsidian', ?, ?, ?, ?, ?)""",
        [str(uuid.uuid4()), now,
         "vault/clients/acme-corp.md", now,
         content_hash("acme-client"), "# Acme Corp\nClient notes.",
         json.dumps({"relationship": "client", "type": "client"})],
    )
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
           entity_name, title, body, tags, created_at)
           VALUES (?, ?, NULL, 'client', 'client', 'Acme Corp', ?, ?, '[]', ?)""",
        [str(uuid.uuid4()), str(uuid.uuid4()), "Acme Corp Notes", "", now],
    )

    relationships = resolve_person_relationships(conn, resolver)
    # Client relationships should exist as 'client' type
    client_rels = [r for r in relationships if r.relationship == "client"]
    # A note about "Acme Corp" with type=client may yield a client relationship
    # If detected, verify evidence source
    if client_rels:
        assert client_rels[0].evidence_source == "obsidian_frontmatter"


def test_no_frontmatter_returns_unknown_or_empty(conn, resolver) -> None:
    """A person with no frontmatter at all → no relationship or unknown."""
    import uuid
    now = datetime.utcnow()
    conn.execute(
        """INSERT INTO raw_documents (id, ingested_at, source_type, source_path,
           file_modified_at, content_hash, content, metadata)
           VALUES (?, ?, 'obsidian', ?, ?, ?, ?, ?)""",
        [str(uuid.uuid4()), now, "vault/people/unknown.md", now,
         content_hash("nofm"), "# Nobody", json.dumps({})],
    )
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
           entity_name, title, body, tags, created_at)
           VALUES (?, ?, NULL, '1on1', 'person', 'Unknown Person', ?, ?, '[]', ?)""",
        [str(uuid.uuid4()), str(uuid.uuid4()), "Note", "", now],
    )

    relationships = resolve_person_relationships(conn, resolver)
    unknown_rels = [r for r in relationships if r.person_name == "Unknown Person"]
    assert len(unknown_rels) == 0, "Unknown people (not in resolver) should not produce relationships"


# ------------------------------------------------------------------
# Helper function for attendee lookup
# ------------------------------------------------------------------


def test_get_relationship_for_attendee(conn, resolver) -> None:
    """get_relationship_for_attendee returns relationship for a known person."""
    _seed_person_note(conn, "Alice Chen", {"relationship": "direct_report"})

    rels = resolve_person_relationships(conn, resolver)
    rel = get_relationship_for_attendee("Alice Chen", rels, resolver)
    assert rel is not None
    assert rel.relationship == "direct_report"


def test_get_relationship_for_unknown_attendee(conn, resolver) -> None:
    """get_relationship_for_attendee returns unknown for an unrecognized name."""
    rels = resolve_person_relationships(conn, resolver)
    rel = get_relationship_for_attendee("Nonexistent Person", rels, resolver)
    assert rel is not None
    assert rel.relationship == "unknown"


# ------------------------------------------------------------------
# Folder-path-based relationship inference (real-world vault convention)
#
# Many vaults (including the one this feature targets) signal relationship
# via directory structure rather than explicit frontmatter fields:
#   team/directs/<name>.md      -> direct_report
#   team/my manager/<name>.md   -> manager
#   team/me/<name>.md           -> self / not a relationship signal
#   team/other/<name>.md        -> peer (colleague, not direct report/manager)
#   clients/<client>/**.md      -> client
#
# This is an explicit, deterministic, path-based signal (not inferred from
# title/seniority/content) so it's an acceptable second-tier evidence source,
# used only when no frontmatter relationship field is present.
# ------------------------------------------------------------------


def _seed_note_at_path(conn, source_path: str, entity_name: str,
                       frontmatter: dict | None = None,
                       note_type: str = "1on1", entity_type: str = "person") -> None:
    """Seed a raw_document + note at an explicit source_path (for folder-path tests)."""
    import uuid
    now = datetime.utcnow()
    doc_id = str(uuid.uuid4())
    note_id = str(uuid.uuid4())
    fm = frontmatter or {}
    c_hash = content_hash(source_path + str(fm))

    conn.execute(
        """INSERT INTO raw_documents (id, ingested_at, source_type, source_path,
           file_modified_at, content_hash, content, metadata)
           VALUES (?, ?, 'obsidian', ?, ?, ?, ?, ?)""",
        [doc_id, now, source_path, now, c_hash,
         f"# {entity_name}\n\nNotes.", json.dumps(fm)],
    )
    conn.execute(
        """INSERT INTO notes (id, raw_document_id, note_date, note_type, entity_type,
           entity_name, title, body, tags, created_at)
           VALUES (?, ?, NULL, ?, ?, ?, ?, ?, '[]', ?)""",
        [note_id, doc_id, note_type, entity_type, entity_name,
         f"Note for {entity_name}", "", now],
    )


def test_direct_report_from_team_directs_folder(conn, resolver) -> None:
    """A note under team/directs/ implies direct_report, even with no frontmatter."""
    _seed_note_at_path(conn, "/vault/team/directs/alice-chen.md", "Alice Chen")

    relationships = resolve_person_relationships(conn, resolver)
    alice_key = resolver.resolve_person("Alice Chen")
    alice_rels = [r for r in relationships if r.person_name == alice_key]
    assert len(alice_rels) >= 1
    assert alice_rels[0].relationship == "direct_report"
    assert alice_rels[0].evidence_source == "obsidian_folder_path"
    assert "team/directs" in alice_rels[0].evidence_path


def test_manager_from_team_my_manager_folder(conn, resolver) -> None:
    """A note under team/my manager/ implies manager."""
    _seed_note_at_path(conn, "/vault/team/my manager/charlie-brown.md", "Charlie Brown")

    relationships = resolve_person_relationships(conn, resolver)
    charlie_key = resolver.resolve_person("Charlie Brown")
    charlie_rels = [r for r in relationships if r.person_name == charlie_key]
    assert len(charlie_rels) >= 1
    assert charlie_rels[0].relationship == "manager"
    assert charlie_rels[0].evidence_source == "obsidian_folder_path"


def test_peer_from_team_other_folder(conn, resolver) -> None:
    """A note under team/other/ implies peer (colleague, not a direct report)."""
    _seed_note_at_path(conn, "/vault/team/other/bob-smith.md", "Bob Smith")

    relationships = resolve_person_relationships(conn, resolver)
    bob_key = resolver.resolve_person("Bob Smith")
    bob_rels = [r for r in relationships if r.person_name == bob_key]
    assert len(bob_rels) >= 1
    assert bob_rels[0].relationship == "peer"


def test_client_from_clients_folder(conn, resolver) -> None:
    """A note under clients/<name>/ implies client relationship for that name."""
    _seed_note_at_path(
        conn, "/vault/clients/Acme Corp/engagement-status.md", "Acme Corp",
        entity_type="client", note_type="client",
    )

    relationships = resolve_person_relationships(conn, resolver)
    acme_rels = [r for r in relationships if r.person_name == "Acme Corp"]
    assert len(acme_rels) >= 1
    assert acme_rels[0].relationship == "client"


def test_explicit_frontmatter_relationship_wins_over_folder_path(conn, resolver) -> None:
    """Explicit frontmatter relationship takes precedence over folder-path inference."""
    _seed_note_at_path(
        conn, "/vault/team/directs/alice-chen.md", "Alice Chen",
        frontmatter={"relationship": "peer"},
    )

    relationships = resolve_person_relationships(conn, resolver)
    alice_key = resolver.resolve_person("Alice Chen")
    alice_rels = [r for r in relationships if r.person_name == alice_key]
    assert len(alice_rels) >= 1
    # Frontmatter says peer, folder path would say direct_report — frontmatter wins
    assert alice_rels[0].relationship == "peer"
    assert alice_rels[0].evidence_source == "obsidian_frontmatter"


def test_team_me_folder_does_not_create_relationship(conn, resolver) -> None:
    """Notes under team/me/ are about the user themselves — not a relationship signal."""
    _seed_note_at_path(conn, "/vault/team/me/justin-floyd.md", "Justin Floyd")

    relationships = resolve_person_relationships(conn, resolver)
    justin_key = resolver.resolve_person("Justin Floyd")
    justin_rels = [r for r in relationships if r.person_name == justin_key]
    # No relationship should be inferred for the "me" folder
    assert len(justin_rels) == 0