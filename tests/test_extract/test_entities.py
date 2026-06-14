"""Tests for extract/entities.py."""

from __future__ import annotations

import pytest

from manager_os.config import ClientConfig, PersonConfig
from manager_os.extract.entities import EntityMatch, EntityResolver


@pytest.fixture()
def resolver() -> EntityResolver:
    people = [
        PersonConfig(name="Alice Chen", aliases=["Alice", "alice", "Alice Chen"]),
        PersonConfig(name="Bob Martinez", aliases=["Bob", "bob", "Bob Martinez"]),
    ]
    clients = [
        ClientConfig(name="Acme Corp", aliases=["Acme", "acme", "Acme Corp", "ACME"]),
        ClientConfig(name="Big Retail Co", aliases=["Big Retail", "big retail", "BRC"]),
    ]
    deal_aliases = {
        "ACME ML Platform Q3": "Acme Corp — ML Platform Build",
        "Big Retail Recs v2": "Big Retail Co — Recommendation Engine Phase 2",
    }
    return EntityResolver(people, clients, deal_aliases)


# ------------------------------------------------------------------
# resolve_person
# ------------------------------------------------------------------


def test_resolve_person_by_lowercase_alias(resolver: EntityResolver) -> None:
    assert resolver.resolve_person("alice") == "Alice Chen"


def test_resolve_person_by_uppercase(resolver: EntityResolver) -> None:
    assert resolver.resolve_person("ALICE") == "Alice Chen"


def test_resolve_person_full_name(resolver: EntityResolver) -> None:
    assert resolver.resolve_person("Alice Chen") == "Alice Chen"


def test_resolve_person_no_match(resolver: EntityResolver) -> None:
    assert resolver.resolve_person("Carmen") is None


def test_resolve_person_empty_string(resolver: EntityResolver) -> None:
    assert resolver.resolve_person("") is None


# ------------------------------------------------------------------
# resolve_client
# ------------------------------------------------------------------


def test_resolve_client_lowercase(resolver: EntityResolver) -> None:
    assert resolver.resolve_client("acme") == "Acme Corp"


def test_resolve_client_all_caps(resolver: EntityResolver) -> None:
    assert resolver.resolve_client("ACME") == "Acme Corp"


def test_resolve_client_full_name(resolver: EntityResolver) -> None:
    assert resolver.resolve_client("Acme Corp") == "Acme Corp"


def test_resolve_client_no_match(resolver: EntityResolver) -> None:
    assert resolver.resolve_client("FinServ") is None


# ------------------------------------------------------------------
# resolve_deal
# ------------------------------------------------------------------


def test_resolve_deal_exact(resolver: EntityResolver) -> None:
    result = resolver.resolve_deal("ACME ML Platform Q3")
    assert result == "Acme Corp — ML Platform Build"


def test_resolve_deal_no_match(resolver: EntityResolver) -> None:
    assert resolver.resolve_deal("Unknown Deal") is None


# ------------------------------------------------------------------
# resolve_any — priority order: person > client > deal
# ------------------------------------------------------------------


def test_resolve_any_person(resolver: EntityResolver) -> None:
    match = resolver.resolve_any("alice")
    assert match is not None
    assert match.entity_type == "person"
    assert match.canonical_name == "Alice Chen"


def test_resolve_any_client(resolver: EntityResolver) -> None:
    match = resolver.resolve_any("acme")
    assert match is not None
    assert match.entity_type == "client"
    assert match.canonical_name == "Acme Corp"


def test_resolve_any_deal(resolver: EntityResolver) -> None:
    match = resolver.resolve_any("ACME ML Platform Q3")
    assert match is not None
    assert match.entity_type == "deal"


def test_resolve_any_no_match(resolver: EntityResolver) -> None:
    assert resolver.resolve_any("completely unknown") is None


# ------------------------------------------------------------------
# extract_entities_from_text
# ------------------------------------------------------------------


def test_extract_entities_finds_person_and_client(resolver: EntityResolver) -> None:
    text = "Had a good sync with Alice and Acme Corp today"
    matches = resolver.extract_entities_from_text(text)
    types_and_names = {(m.entity_type, m.canonical_name) for m in matches}
    assert ("person", "Alice Chen") in types_and_names
    assert ("client", "Acme Corp") in types_and_names


def test_extract_entities_deduplicates(resolver: EntityResolver) -> None:
    # "alice" appears twice — should produce one EntityMatch
    text = "Alice said alice will follow up"
    matches = resolver.extract_entities_from_text(text)
    person_matches = [m for m in matches if m.entity_type == "person" and m.canonical_name == "Alice Chen"]
    assert len(person_matches) == 1


def test_extract_entities_prefers_longer_ngram(resolver: EntityResolver) -> None:
    # "Alice Chen" (2-gram) should match as a person, not two separate 1-gram misses
    text = "Alice Chen joined the call"
    matches = resolver.extract_entities_from_text(text)
    names = [m.canonical_name for m in matches]
    assert "Alice Chen" in names
    # Should not double-count Alice from the 1-gram pass
    alice_matches = [m for m in matches if m.canonical_name == "Alice Chen"]
    assert len(alice_matches) == 1


def test_extract_entities_empty_text(resolver: EntityResolver) -> None:
    assert resolver.extract_entities_from_text("") == []


def test_extract_entities_no_matches(resolver: EntityResolver) -> None:
    assert resolver.extract_entities_from_text("no entities mentioned here at all") == []


def test_extract_entities_multi_client(resolver: EntityResolver) -> None:
    text = "Acme Corp and Big Retail both need attention"
    matches = resolver.extract_entities_from_text(text)
    client_names = {m.canonical_name for m in matches if m.entity_type == "client"}
    assert "Acme Corp" in client_names
    assert "Big Retail Co" in client_names
