"""People name normalization and alias resolution.

Provides a single source of truth for canonical people names across:
- Dashboard display
- Forecast matching
- Action item assignment
- People-health signals
- Deal staffing matching

Config-driven: reads config/people.yaml.

Usage:
    resolver = PeopleNormalizer.from_config(settings)
    canonical = resolver.canonicalize("Taylor")  # → "Taylor Stacey"
    is_tracked = resolver.is_tracked("Taylor Stacey")  # → True
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NormalizedPerson:
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    role: str = ""
    level: str = ""
    track: bool = True

    @property
    def is_tracked(self) -> bool:
        return self.track

    @property
    def all_names(self) -> list[str]:
        """All known names (canonical + aliases), lowercased for matching."""
        names = [self.canonical_name] + self.aliases
        return list(dict.fromkeys(names))  # preserve order, deduplicate


class PeopleNormalizer:
    """Maps raw person names to canonical names using people.yaml config.

    Thread-safe read-only after construction.
    """

    def __init__(self, people: list[Any]) -> None:
        """Build lookup maps from a list of PersonConfig (or dict-like) objects."""
        self._canonical: dict[str, NormalizedPerson] = {}  # canonical_lower → NormalizedPerson
        self._alias_map: dict[str, str] = {}  # any_name_lower → canonical_name

        for p in people:
            name = getattr(p, "name", None) or p.get("name", "") if isinstance(p, dict) else getattr(p, "name", "")
            if not name:
                continue
            aliases = getattr(p, "aliases", []) if not isinstance(p, dict) else p.get("aliases", [])
            role = getattr(p, "role", "") if not isinstance(p, dict) else p.get("role", "")
            level = getattr(p, "level", "") if not isinstance(p, dict) else p.get("level", "")
            track = getattr(p, "track", True) if not isinstance(p, dict) else p.get("track", True)

            person = NormalizedPerson(
                canonical_name=name,
                aliases=list(aliases),
                role=role or "",
                level=level or "",
                track=bool(track),
            )
            self._canonical[name.lower()] = person

            # Register canonical name + all aliases
            for alias in person.all_names:
                self._alias_map[alias.lower()] = name

    @classmethod
    def from_config(cls, settings=None) -> "PeopleNormalizer":
        """Build from people.yaml via the config loader."""
        from manager_os.config import load_people
        people = load_people(settings)
        return cls(people)

    def canonicalize(self, name: str) -> str:
        """Return the canonical name for *name*, or *name* unchanged if unknown."""
        if not name:
            return name
        return self._alias_map.get(name.strip().lower(), name.strip())

    def is_tracked(self, name: str) -> bool:
        """Return True if the (possibly alias) name maps to a tracked person."""
        canonical = self.canonicalize(name)
        person = self._canonical.get(canonical.lower())
        return person.track if person else True  # unknown people default to visible

    def is_known(self, name: str) -> bool:
        """Return True if name resolves to any configured person."""
        return name.strip().lower() in self._alias_map

    def get_person(self, name: str) -> NormalizedPerson | None:
        """Return the NormalizedPerson for *name* (canonical or alias), or None."""
        canonical = self.canonicalize(name)
        return self._canonical.get(canonical.lower())

    def tracked_names(self) -> list[str]:
        """Return sorted canonical names of all tracked people."""
        return sorted(p.canonical_name for p in self._canonical.values() if p.track)

    def all_canonical_names(self) -> list[str]:
        """Return sorted list of all canonical names (tracked or not)."""
        return sorted(p.canonical_name for p in self._canonical.values())

    def canonicalize_list(self, names: list[str]) -> list[str]:
        """Canonicalize a list of names, removing duplicates."""
        seen: set[str] = set()
        result = []
        for n in names:
            canon = self.canonicalize(n)
            if canon not in seen:
                seen.add(canon)
                result.append(canon)
        return result

    def find_unconfigured(self, names: list[str]) -> list[str]:
        """Return names that do not appear in the people config at all."""
        return [n for n in names if not self.is_known(n)]


# ---------------------------------------------------------------------------
# People audit helpers
# ---------------------------------------------------------------------------


@dataclass
class PeopleAuditResult:
    tracked: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    alias_map: dict[str, str] = field(default_factory=dict)       # alias → canonical
    duplicate_candidates: list[tuple[str, str]] = field(default_factory=list)
    unconfigured_in_db: list[str] = field(default_factory=list)   # names seen in DB but not in config


def run_people_audit(conn, settings=None) -> PeopleAuditResult:
    """Run a full people audit against the database.

    Finds:
    - All configured tracked/untracked people
    - All aliases
    - Names seen in notes/signals/forecast not in config
    - Duplicate candidates (names that are close but differ)
    """
    normalizer = PeopleNormalizer.from_config(settings)

    # Collect all person names from DB
    db_names: set[str] = set()

    for (name,) in (conn.execute(
        "SELECT DISTINCT entity_name FROM signals WHERE entity_type = 'person' AND entity_name != ''"
    ).fetchall() or []):
        if name:
            db_names.add(name)

    for (name,) in (conn.execute(
        "SELECT DISTINCT entity_name FROM notes WHERE note_type = '1on1' AND entity_name != ''"
    ).fetchall() or []):
        if name:
            db_names.add(name)

    for (name,) in (conn.execute(
        "SELECT DISTINCT person_name FROM staffing_forecast WHERE person_name != ''"
    ).fetchall() or []):
        if name:
            db_names.add(name)

    for (name,) in (conn.execute(
        "SELECT DISTINCT name FROM people WHERE name != ''"
    ).fetchall() or []):
        if name:
            db_names.add(name)

    # Find unconfigured names
    unconfigured = normalizer.find_unconfigured(list(db_names))

    # Build alias map (non-canonical aliases only)
    alias_map: dict[str, str] = {}
    for p in normalizer._canonical.values():
        for alias in p.aliases:
            if alias.lower() != p.canonical_name.lower():
                alias_map[alias] = p.canonical_name

    # Duplicate candidates: DB names that canonicalize differently from themselves
    dup_candidates: list[tuple[str, str]] = []
    for name in db_names:
        canonical = normalizer.canonicalize(name)
        if canonical != name and name not in [p.canonical_name for p in normalizer._canonical.values()]:
            dup_candidates.append((name, canonical))

    return PeopleAuditResult(
        tracked=normalizer.tracked_names(),
        untracked=sorted(
            p.canonical_name for p in normalizer._canonical.values() if not p.track
        ),
        alias_map=alias_map,
        duplicate_candidates=dup_candidates,
        unconfigured_in_db=sorted(unconfigured),
    )
