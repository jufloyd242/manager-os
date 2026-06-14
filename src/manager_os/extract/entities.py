"""Entity resolution — maps raw strings to canonical person/client/deal names.

Resolves against config-loaded people.yaml, clients.yaml, and deal_aliases.yaml.
Exact alias match only (case-insensitive). No fuzzy matching in this version.
"""

from __future__ import annotations

from dataclasses import dataclass

from manager_os.config import ClientConfig, PersonConfig


@dataclass
class EntityMatch:
    entity_type: str  # person | client | deal
    canonical_name: str


class EntityResolver:
    """Resolves raw text strings to canonical entity names from config."""

    def __init__(
        self,
        people: list[PersonConfig],
        clients: list[ClientConfig],
        deal_aliases: dict[str, str],
    ) -> None:
        # Build lowercased lookup tables for O(1) resolution
        self._person_map: dict[str, str] = {}
        for person in people:
            for alias in person.aliases:
                self._person_map[alias.lower().strip()] = person.name

        self._client_map: dict[str, str] = {}
        for client in clients:
            for alias in client.aliases:
                self._client_map[alias.lower().strip()] = client.name

        self._deal_map: dict[str, str] = {}
        for raw, canonical in deal_aliases.items():
            self._deal_map[raw.lower().strip()] = canonical

    # ------------------------------------------------------------------
    # Single-type resolution
    # ------------------------------------------------------------------

    def resolve_person(self, text: str) -> str | None:
        """Return canonical person name or None."""
        return self._person_map.get(text.lower().strip())

    def resolve_client(self, text: str) -> str | None:
        """Return canonical client name or None."""
        return self._client_map.get(text.lower().strip())

    def resolve_deal(self, text: str) -> str | None:
        """Return canonical deal name or None."""
        return self._deal_map.get(text.lower().strip())

    # ------------------------------------------------------------------
    # Multi-type resolution — person → client → deal priority order
    # ------------------------------------------------------------------

    def resolve_any(self, text: str) -> EntityMatch | None:
        """Try person → client → deal. Return the first match or None."""
        name = self.resolve_person(text)
        if name:
            return EntityMatch(entity_type="person", canonical_name=name)
        name = self.resolve_client(text)
        if name:
            return EntityMatch(entity_type="client", canonical_name=name)
        name = self.resolve_deal(text)
        if name:
            return EntityMatch(entity_type="deal", canonical_name=name)
        return None

    # ------------------------------------------------------------------
    # Text scanning — extract all entity mentions from a block of text
    # ------------------------------------------------------------------

    def extract_entities_from_text(self, text: str) -> list[EntityMatch]:
        """Scan text for all entity mentions using n-gram windows (1–4 words).

        Returns a deduplicated list of EntityMatch objects in mention order.
        """
        words = text.split()
        seen: set[tuple[str, str]] = set()
        results: list[EntityMatch] = []

        # Try n-grams from longest to shortest so "Alice Chen" beats "Alice"
        for n in (4, 3, 2, 1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i : i + n])
                match = self.resolve_any(phrase)
                if match:
                    key = (match.entity_type, match.canonical_name)
                    if key not in seen:
                        seen.add(key)
                        results.append(match)

        return results
