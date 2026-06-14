"""Config audit — scan Obsidian vault metadata to suggest people/client/deal config.

Reads note frontmatter, titles, and directory structure (not full body text
by default) to surface candidate entries for config/people.yaml,
config/clients.yaml, and config/deal_aliases.yaml.

Safety guarantees
-----------------
- Never modifies config files.
- Never writes to DuckDB.
- Never calls external APIs.
- Only outputs to output/config_audit/ (which must be gitignored).
- Does not print or expose full note body text.

Confidence levels
-----------------
high   — explicit frontmatter field (entity, person, client, deal, name)
medium — filename / title / first-level heading match
low    — directory convention or weak heuristic (body scan if enabled)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frontmatter keys treated as authoritative entity indicators
# ---------------------------------------------------------------------------
_FM_PERSON_KEYS = ("entity", "person", "name")
_FM_CLIENT_KEYS = ("entity", "client", "account")
_FM_DEAL_KEYS = ("entity", "deal", "deal_name", "opportunity")

# Note-type → category mapping (mirrors obsidian.py logic)
_TYPE_MAP = {
    "1on1": "person",
    "one-on-one": "person",
    "client": "client",
    "client-status": "client",
    "deal": "deal",
    "meeting": None,
    "team": None,
    "practice": None,
}
_DIR_TYPE_MAP = {
    "1on1": "person",
    "1-on-1": "person",
    "one-on-ones": "person",
    "clients": "client",
    "client-notes": "client",
    "deals": "deal",
    "deal-notes": "deal",
}

_SKIP_DIRS = {".obsidian", ".git", ".trash"}

# Body-text keywords (only used when include_body_signals=True)
_BODY_PERSON_PATTERNS = [re.compile(r"\b1[:\-–—]1\b", re.IGNORECASE)]
_BODY_CLIENT_PATTERNS = [
    re.compile(r"\b(client|customer|account)\s*[:–—]\s*(.+)", re.IGNORECASE)
]
_BODY_DEAL_PATTERNS = [
    re.compile(r"\b(sow|loe|deal|opportunity|close date)\b", re.IGNORECASE)
]

# First-line heading pattern
_HEADING_RE = re.compile(r"^#+\s+(.+)", re.MULTILINE)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CandidateEntry:
    name: str
    confidence: str  # high | medium | low
    source: str      # e.g. "frontmatter:entity" or "filename" or "heading"
    note_path: str   # relative path to the note
    category: str    # person | client | deal
    note_type: str = ""
    extra: str = ""  # extra context (e.g., role, tags) — no body excerpts


@dataclass
class AuditResult:
    vault_path: str
    notes_scanned: int = 0
    notes_skipped: int = 0
    candidate_people: list[CandidateEntry] = field(default_factory=list)
    candidate_clients: list[CandidateEntry] = field(default_factory=list)
    candidate_deals: list[CandidateEntry] = field(default_factory=list)
    possible_aliases: list[dict] = field(default_factory=list)
    unresolved_entities: list[dict] = field(default_factory=list)
    config_gaps: list[str] = field(default_factory=list)

    def all_candidates(self) -> list[CandidateEntry]:
        return self.candidate_people + self.candidate_clients + self.candidate_deals


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def scan_vault(
    vault_path: str,
    existing_people: list[str] | None = None,
    existing_clients: list[str] | None = None,
    existing_deals: list[str] | None = None,
    limit: Optional[int] = None,
    include_body_signals: bool = False,
) -> AuditResult:
    """Scan an Obsidian vault and return candidate config entries.

    Args:
        vault_path: Path to the Obsidian vault root.
        existing_people: Known canonical person names (to detect gaps).
        existing_clients: Known canonical client names.
        existing_deals: Known canonical deal names.
        limit: Optional cap on number of notes to scan.
        include_body_signals: If True, do a light body scan for additional
            candidates. Body text is never included in the output.

    Returns:
        AuditResult with all candidates and metadata.
    """
    vault = Path(vault_path)
    if not vault.exists():
        raise FileNotFoundError(f"Vault path not found: {vault_path}")

    existing_people_set = {n.lower() for n in (existing_people or [])}
    existing_clients_set = {n.lower() for n in (existing_clients or [])}
    existing_deals_set = {n.lower() for n in (existing_deals or [])}

    result = AuditResult(vault_path=str(vault))
    seen_names: set[str] = set()  # deduplicate candidates

    md_files = sorted(vault.rglob("*.md"))
    if limit is not None:
        md_files = md_files[:limit]

    for md_file in md_files:
        # Skip hidden dirs / .obsidian etc.
        if any(part.startswith(".") for part in md_file.parts):
            result.notes_skipped += 1
            continue
        if any(skip in md_file.parts for skip in _SKIP_DIRS):
            result.notes_skipped += 1
            continue

        result.notes_scanned += 1
        _scan_file(
            md_file,
            vault,
            result,
            seen_names,
            existing_people_set,
            existing_clients_set,
            existing_deals_set,
            include_body_signals=include_body_signals,
        )

    # Identify config gaps: candidates not already in config
    _compute_gaps(result, existing_people_set, existing_clients_set, existing_deals_set)

    return result


def _scan_file(
    md_file: Path,
    vault: Path,
    result: AuditResult,
    seen_names: set[str],
    existing_people: set[str],
    existing_clients: set[str],
    existing_deals: set[str],
    include_body_signals: bool,
) -> None:
    try:
        raw = md_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        result.notes_skipped += 1
        return

    # Parse frontmatter without importing the full frontmatter library
    fm, body = _parse_frontmatter(raw)
    rel_path = str(md_file.relative_to(vault))

    note_type = _infer_note_type(fm, md_file)
    category = _TYPE_MAP.get(note_type)

    # Infer category from directory if not set by note_type
    if category is None:
        for part in reversed(md_file.parts):
            cat = _DIR_TYPE_MAP.get(part.lower())
            if cat:
                category = cat
                break

    # Title: from frontmatter > first heading > filename
    title = (
        str(fm.get("title", "")).strip()
        or _first_heading(body)
        or md_file.stem.replace("_", " ").replace("-", " ")
    )

    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    # --- High confidence: explicit frontmatter entity field ---
    if category == "person":
        name = _first_value(fm, _FM_PERSON_KEYS)
        if name:
            _add_candidate(
                result, seen_names,
                CandidateEntry(
                    name=name, confidence="high",
                    source="frontmatter:entity", note_path=rel_path,
                    category="person", note_type=note_type,
                    extra=f"tags={tags}" if tags else "",
                ),
            )
        else:
            # Medium: infer from filename / title
            stem_name = _clean_stem(md_file.stem)
            if stem_name:
                _add_candidate(
                    result, seen_names,
                    CandidateEntry(
                        name=stem_name, confidence="medium",
                        source="filename", note_path=rel_path,
                        category="person", note_type=note_type,
                    ),
                )

    elif category == "client":
        name = _first_value(fm, _FM_CLIENT_KEYS)
        if name:
            _add_candidate(
                result, seen_names,
                CandidateEntry(
                    name=name, confidence="high",
                    source="frontmatter:entity", note_path=rel_path,
                    category="client", note_type=note_type,
                ),
            )
        else:
            stem_name = _clean_stem(md_file.stem)
            if stem_name:
                _add_candidate(
                    result, seen_names,
                    CandidateEntry(
                        name=stem_name, confidence="medium",
                        source="filename", note_path=rel_path,
                        category="client", note_type=note_type,
                    ),
                )

    elif category == "deal":
        name = _first_value(fm, _FM_DEAL_KEYS)
        if name:
            _add_candidate(
                result, seen_names,
                CandidateEntry(
                    name=name, confidence="high",
                    source="frontmatter:entity", note_path=rel_path,
                    category="deal", note_type=note_type,
                ),
            )
        else:
            stem_name = _clean_stem(md_file.stem)
            if stem_name:
                _add_candidate(
                    result, seen_names,
                    CandidateEntry(
                        name=stem_name, confidence="medium",
                        source="filename", note_path=rel_path,
                        category="deal", note_type=note_type,
                    ),
                )
    else:
        # Unknown category: still check frontmatter for any entity
        name = _first_value(fm, ("entity", "person", "client", "deal", "name"))
        if name:
            # Assign category by note type hint or tag
            inferred_cat = _guess_category_from_context(note_type, tags)
            _add_candidate(
                result, seen_names,
                CandidateEntry(
                    name=name, confidence="medium",
                    source="frontmatter:entity", note_path=rel_path,
                    category=inferred_cat or "unresolved", note_type=note_type,
                ),
            )

    # Light body scan (never exposes body text in output)
    if include_body_signals and body:
        _scan_body_light(body, rel_path, note_type, result, seen_names)


def _scan_body_light(
    body: str,
    rel_path: str,
    note_type: str,
    result: AuditResult,
    seen_names: set[str],
) -> None:
    """Lightly scan body for additional signals without exposing content."""
    for pattern in _BODY_CLIENT_PATTERNS:
        for m in pattern.finditer(body):
            # Only capture short names (not multi-sentence excerpts)
            raw_name = m.group(2).strip()
            if raw_name and len(raw_name) < 60 and "\n" not in raw_name:
                _add_candidate(
                    result, seen_names,
                    CandidateEntry(
                        name=raw_name, confidence="low",
                        source="body:client_pattern", note_path=rel_path,
                        category="client", note_type=note_type,
                    ),
                )


def _add_candidate(
    result: AuditResult,
    seen_names: set[str],
    entry: CandidateEntry,
) -> None:
    dedup_key = f"{entry.category}::{entry.name.lower()}"
    if dedup_key in seen_names:
        return
    seen_names.add(dedup_key)

    if entry.category == "person":
        result.candidate_people.append(entry)
    elif entry.category == "client":
        result.candidate_clients.append(entry)
    elif entry.category == "deal":
        result.candidate_deals.append(entry)
    else:
        result.unresolved_entities.append(
            {"name": entry.name, "note_path": entry.note_path, "confidence": entry.confidence}
        )


def _compute_gaps(
    result: AuditResult,
    existing_people: set[str],
    existing_clients: set[str],
    existing_deals: set[str],
) -> None:
    """Populate config_gaps and possible_aliases."""
    for entry in result.candidate_people:
        nl = entry.name.lower()
        if nl not in existing_people:
            result.config_gaps.append(f"person: {entry.name!r} not in config/people.yaml")
            # Suggest aliases: lowercase, no-space, and original
            aliases = list({entry.name, entry.name.lower(), entry.name.replace(" ", "").lower()})
            result.possible_aliases.append({
                "category": "person",
                "name": entry.name,
                "suggested_aliases": aliases,
            })

    for entry in result.candidate_clients:
        nl = entry.name.lower()
        if nl not in existing_clients:
            result.config_gaps.append(f"client: {entry.name!r} not in config/clients.yaml")
            aliases = list({entry.name, entry.name.lower(), entry.name.replace(" ", "").lower()})
            result.possible_aliases.append({
                "category": "client",
                "name": entry.name,
                "suggested_aliases": aliases,
            })

    for entry in result.candidate_deals:
        nl = entry.name.lower()
        if nl not in existing_deals:
            result.config_gaps.append(f"deal: {entry.name!r} not in config/deal_aliases.yaml")


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_report(result: AuditResult, report_date: date | None = None) -> str:
    """Render a markdown audit report (no body text included)."""
    if report_date is None:
        report_date = date.today()

    lines: list[str] = []

    lines.append(f"# Config Audit Preview — {report_date}")
    lines.append("")
    lines.append("> **Safety notice:** This file may contain names from your local Obsidian vault.")
    lines.append("> Do not commit this file. It is listed under `output/` which is gitignored.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"**Vault scanned:** `{result.vault_path}`")
    lines.append(f"**Notes scanned:** {result.notes_scanned}")
    lines.append(f"**Notes skipped:** {result.notes_skipped}")
    lines.append(f"**Candidate people:** {len(result.candidate_people)}")
    lines.append(f"**Candidate clients:** {len(result.candidate_clients)}")
    lines.append(f"**Candidate deals:** {len(result.candidate_deals)}")
    lines.append(f"**Config gaps:** {len(result.config_gaps)}")
    lines.append("")

    # People
    lines.append("---")
    lines.append("")
    lines.append("## Candidate People")
    lines.append("")
    if result.candidate_people:
        lines.append("| Name | Confidence | Source | Note |")
        lines.append("|------|-----------|--------|------|")
        for e in result.candidate_people:
            lines.append(f"| {e.name} | {e.confidence} | {e.source} | {e.note_path} |")
    else:
        lines.append("*No candidate people found.*")
    lines.append("")

    # Clients
    lines.append("---")
    lines.append("")
    lines.append("## Candidate Clients")
    lines.append("")
    if result.candidate_clients:
        lines.append("| Name | Confidence | Source | Note |")
        lines.append("|------|-----------|--------|------|")
        for e in result.candidate_clients:
            lines.append(f"| {e.name} | {e.confidence} | {e.source} | {e.note_path} |")
    else:
        lines.append("*No candidate clients found.*")
    lines.append("")

    # Deals
    lines.append("---")
    lines.append("")
    lines.append("## Candidate Deals")
    lines.append("")
    if result.candidate_deals:
        lines.append("| Name | Confidence | Source | Note |")
        lines.append("|------|-----------|--------|------|")
        for e in result.candidate_deals:
            lines.append(f"| {e.name} | {e.confidence} | {e.source} | {e.note_path} |")
    else:
        lines.append("*No candidate deals found.*")
    lines.append("")

    # Possible aliases
    if result.possible_aliases:
        lines.append("---")
        lines.append("")
        lines.append("## Possible Aliases")
        lines.append("")
        lines.append("Suggested starter aliases for new config entries:")
        lines.append("")
        lines.append("| Category | Name | Suggested Aliases |")
        lines.append("|----------|------|-------------------|")
        for a in result.possible_aliases:
            aliases_str = ", ".join(f"`{s}`" for s in a["suggested_aliases"])
            lines.append(f"| {a['category']} | {a['name']} | {aliases_str} |")
        lines.append("")

    # Unresolved
    if result.unresolved_entities:
        lines.append("---")
        lines.append("")
        lines.append("## Unresolved Entities")
        lines.append("")
        lines.append("Entities found in notes but category could not be inferred:")
        lines.append("")
        lines.append("| Name | Confidence | Note |")
        lines.append("|------|-----------|------|")
        for u in result.unresolved_entities:
            lines.append(f"| {u['name']} | {u['confidence']} | {u['note_path']} |")
        lines.append("")

    # Config gaps
    if result.config_gaps:
        lines.append("---")
        lines.append("")
        lines.append("## Config Gaps")
        lines.append("")
        lines.append("These candidates have no matching entry in the current config:")
        lines.append("")
        for gap in result.config_gaps:
            lines.append(f"- {gap}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> **Reminder:** Review these suggestions manually.")
    lines.append("> Copy safe entries into `config/people.yaml`, `config/clients.yaml`,")
    lines.append("> or `config/deal_aliases.yaml`. Do not commit this report.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file. Returns (fm_dict, body)."""
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, raw
    fm_text = raw[3:end].strip()
    body = raw[end + 4:].strip()
    try:
        import yaml
        fm = yaml.safe_load(fm_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, body


def _infer_note_type(fm: dict, file_path: Path) -> str:
    raw_type = str(fm.get("type", "")).lower().strip()
    if raw_type in _TYPE_MAP:
        return raw_type
    # Alias normalisation
    if raw_type == "one-on-one":
        return "1on1"
    for part in reversed(file_path.parts):
        part_lower = part.lower()
        if part_lower in _DIR_TYPE_MAP:
            return part_lower
    return ""


def _first_value(fm: dict, keys: tuple) -> str:
    for k in keys:
        v = fm.get(k)
        if v:
            return str(v).strip()
    return ""


def _first_heading(body: str) -> str:
    m = _HEADING_RE.search(body)
    return m.group(1).strip() if m else ""


def _clean_stem(stem: str) -> str:
    """Convert a filename stem to a plausible display name."""
    name = stem.replace("_", " ").replace("-", " ").strip()
    # Skip if it looks like a date or hash
    if re.match(r"^[\d\-_]+$", name):
        return ""
    # Title-case single-word names; leave multi-word as-is
    words = name.split()
    if len(words) <= 4:
        return " ".join(w.capitalize() for w in words)
    return name


def _guess_category_from_context(note_type: str, tags: list) -> str | None:
    tags_lower = {str(t).lower() for t in tags}
    if note_type in ("1on1",) or "1on1" in tags_lower or "people" in tags_lower:
        return "person"
    if "client" in tags_lower or "account" in tags_lower:
        return "client"
    if "deal" in tags_lower or "sow" in tags_lower:
        return "deal"
    return None
