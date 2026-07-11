"""Entity resolution — maps raw strings to canonical person/client/deal names.

Resolves against config-loaded people.yaml, clients.yaml, and deal_aliases.yaml.
Exact alias match, case-insensitive. Also supports a lightweight email-address
fallback for resolve_person(): calendar attendees frequently arrive as raw
email addresses (e.g. "alice.chen@company.com") rather than display names, and
those addresses are rarely listed verbatim as aliases in people.yaml. When an
exact alias match fails and the input looks like an email address, the local
part (before "@") is matched against aliases — first as a dotted
"firstname.lastname" split, then as the whole local part — so
"alice.chen@sada.com" or "alice@sada.com" both resolve via existing "alice"/
"alice chen" aliases without requiring every email to be hand-listed.
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
        """Return canonical person name or None.

        Tries an exact alias match first. If that fails and `text` looks
        like an email address, falls back to matching the local part
        (before "@") against configured aliases — both as-is and with dots
        replaced by spaces (so "alice.chen@x.com" can match an "alice chen"
        alias, not just "alice.chen").
        """
        cleaned = text.lower().strip()
        exact = self._person_map.get(cleaned)
        if exact:
            return exact

        if "@" in cleaned:
            local_part = cleaned.split("@", 1)[0]
            # Try the local part as-is (e.g. "alice" from "alice@x.com")
            match = self._person_map.get(local_part)
            if match:
                return match
            # Try dotted local part with dots→spaces (e.g. "alice.chen" -> "alice chen")
            match = self._person_map.get(local_part.replace(".", " "))
            if match:
                return match
            # Try just the first dot-segment (e.g. "alice.chen" -> "alice")
            first_segment = local_part.split(".", 1)[0]
            match = self._person_map.get(first_segment)
            if match:
                return match

        return None

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
