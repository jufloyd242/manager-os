"""Source scope / source tier classification.

Classifies Obsidian vault notes into three tiers:

    signal   — may create operational items (signals, actions, decisions,
               waiting-on, brief/dashboard items)
    context  — enrichment/background only; no standalone operational items
    excluded — never creates any operational items

Precedence (highest to lowest):
    1. Frontmatter ``manager_os:`` / ``status:`` / ``active:`` overrides
    2. Tag-based overrides
    3. exclude_paths (wins over everything except frontmatter inclusion)
    4. signal_paths
    5. context_paths
    6. Default: context
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/source_scope.yaml")

# ─────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────


@dataclass
class ScopeResult:
    source_tier: str  # "signal" | "context" | "excluded"
    scope_reason: str
    is_stale: bool = False
    is_active: bool = False
    matched_rule: str = ""

    def as_metadata(self) -> dict[str, Any]:
        return {
            "source_tier": self.source_tier,
            "scope_reason": self.scope_reason,
            "is_stale": self.is_stale,
            "is_active": self.is_active,
            "matched_rule": self.matched_rule,
        }


# ─────────────────────────────────────────────────────────────
# Configuration loader
# ─────────────────────────────────────────────────────────────


@dataclass
class SourceScopeConfig:
    signal_paths: list[str] = field(default_factory=list)
    context_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    max_note_age_days: int = 120

    always_include_frontmatter: dict[str, list[str]] = field(default_factory=dict)
    always_exclude_frontmatter: dict[str, list[str]] = field(default_factory=dict)
    always_include_tags: list[str] = field(default_factory=list)
    always_exclude_tags: list[str] = field(default_factory=list)


def load_source_scope(config_path: str | Path | None = None) -> SourceScopeConfig:
    """Load source scope configuration from YAML.

    Lookup order:
        1. Explicit *config_path* argument
        2. ``MANAGER_OS_CONFIG_DIR/source_scope.yaml``
        3. ``config/source_scope.yaml`` (repo root)
        4. Built-in defaults (empty lists — all notes default to signal)
    """
    import os as _os

    paths_to_try: list[Path] = []
    if config_path:
        paths_to_try.append(Path(config_path))

    config_dir = _os.environ.get("MANAGER_OS_CONFIG_DIR", "")
    if config_dir:
        paths_to_try.append(Path(config_dir) / "source_scope.yaml")

    paths_to_try.append(DEFAULT_CONFIG_PATH)

    for p in paths_to_try:
        if p.exists():
            with open(p, "r") as fh:
                raw = yaml.safe_load(fh) or {}
            return _build_config(raw, p)

    logger.info("No source_scope.yaml found; using default (all signal)")
    return SourceScopeConfig()


def _build_config(raw: dict, source: Path) -> SourceScopeConfig:
    """Build SourceScopeConfig from raw YAML dict."""
    logger.debug("Loaded source scope config from %s", source)
    return SourceScopeConfig(
        signal_paths=_strings(raw, "signal_paths"),
        context_paths=_strings(raw, "context_paths"),
        exclude_paths=_strings(raw, "exclude_paths"),
        max_note_age_days=int(raw.get("max_note_age_days", 120)),
        always_include_frontmatter=_dict_of_strings(raw, "always_include_frontmatter"),
        always_exclude_frontmatter=_dict_of_strings(raw, "always_exclude_frontmatter"),
        always_include_tags=_strings(raw, "always_include_tags"),
        always_exclude_tags=_strings(raw, "always_exclude_tags"),
    )


def _strings(raw: dict, key: str) -> list[str]:
    val = raw.get(key, [])
    return [str(v) for v in val] if val else []


def _dict_of_strings(raw: dict, key: str) -> dict[str, list[str]]:
    val = raw.get(key, {})
    if not val:
        return {}
    return {str(k): [str(v) for v in vs] for k, vs in val.items()}


# ─────────────────────────────────────────────────────────────
# Path matching
# ─────────────────────────────────────────────────────────────


def _matches_any(rel_path: str, patterns: list[str]) -> bool:
    """Return True if *rel_path* matches any glob pattern in *patterns*.

    Patterns use Unix-style forward-slash globs.
    ``**`` matches across directory boundaries.
    """
    normalized = rel_path.replace("\\", "/")
    for pattern in patterns:
        p = pattern.replace("\\", "/")
        if fnmatch.fnmatch(normalized, p):
            return True
    return False


# ─────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────


def classify_source(
    source_path: str,
    vault_root: str | None = None,
    frontmatter: dict | None = None,
    tags: list[str] | None = None,
    modified_time: datetime | str | None = None,
    today: date | None = None,
    config: SourceScopeConfig | None = None,
) -> ScopeResult:
    """Classify a single vault note into a source tier.

    Args:
        source_path: Absolute or relative file path.
        vault_root: Root of the vault (for computing relative paths).
        frontmatter: Parsed YAML frontmatter dict.
        tags: List of tag strings from frontmatter / note metadata.
        modified_time: File modification time (datetime or ISO string).
        today: Current date for staleness check (default: date.today()).
        config: Pre-loaded config (loads from default location if None).

    Returns:
        ScopeResult with tier, reason, staleness, and activity flags.
    """
    if config is None:
        config = load_source_scope()
    if today is None:
        today = date.today()
    if frontmatter is None:
        frontmatter = {}
    if tags is None:
        tags = []

    # Normalize path
    sp = Path(source_path)
    raw_path = str(sp)
    name_lower = sp.name.lower()

    # Compute relative path for pattern matching
    if vault_root:
        try:
            rel = str(sp.relative_to(vault_root))
        except ValueError:
            rel = sp.name
    else:
        rel = raw_path

    # ── 1. Frontmatter overrides ──────────────────────────
    # manager_os: active / status: active / active: true → signal
    fm_lower: dict[str, str] = {k: str(v).lower().strip() for k, v in frontmatter.items()}

    for fm_key in ("manager_os", "status", "active"):
        val = fm_lower.get(fm_key, "")
        if val in _values_flat(config.always_include_frontmatter.get(fm_key, [])):
            return ScopeResult(
                source_tier="signal",
                scope_reason=f"frontmatter {fm_key}={val} forces signal",
                is_active=True,
                matched_rule=f"always_include_frontmatter.{fm_key}",
            )

    for fm_key in ("manager_os", "status", "active"):
        val = fm_lower.get(fm_key, "")
        if val in _values_flat(config.always_exclude_frontmatter.get(fm_key, [])):
            return ScopeResult(
                source_tier="excluded",
                scope_reason=f"frontmatter {fm_key}={val} forces exclusion",
                matched_rule=f"always_exclude_frontmatter.{fm_key}",
            )

    # ── 2. Tag overrides ────────────────────────────────
    tags_lower = [t.lower().strip() for t in tags]
    for tag in tags_lower:
        if tag in [t.lower() for t in config.always_include_tags]:
            return ScopeResult(
                source_tier="signal",
                scope_reason=f"tag '{tag}' forces signal",
                is_active=True,
                matched_rule="always_include_tags",
            )
        if tag in [t.lower() for t in config.always_exclude_tags]:
            return ScopeResult(
                source_tier="excluded",
                scope_reason=f"tag '{tag}' forces exclusion",
                matched_rule="always_exclude_tags",
            )

    # ── 3. Exclude paths ───────────────────────────────
    if _matches_any(rel, config.exclude_paths):
        return ScopeResult(
            source_tier="excluded",
            scope_reason=f"matches exclude pattern",
            matched_rule=f"exclude_paths: {_first_match(rel, config.exclude_paths)}",
        )

    # ── 4. Signal paths ───────────────────────────────
    if _matches_any(rel, config.signal_paths):
        return ScopeResult(
            source_tier="signal",
            scope_reason="matches signal path pattern",
            is_active=True,
            matched_rule=f"signal_paths: {_first_match(rel, config.signal_paths)}",
        )

    # ── 5. Context paths ───────────────────────────────
    if _matches_any(rel, config.context_paths):
        return ScopeResult(
            source_tier="context",
            scope_reason="matches context path pattern",
            matched_rule=f"context_paths: {_first_match(rel, config.context_paths)}",
        )

    # ── 6. Default: context ─────────────────────────────
    return ScopeResult(
        source_tier="context",
        scope_reason="default — no matching rule, treated as context",
        matched_rule="default",
    )


def _values_flat(values: list[str]) -> set[str]:
    return {v.lower().strip() for v in values}


def _first_match(rel_path: str, patterns: list[str]) -> str:
    normalized = rel_path.replace("\\", "/")
    for pattern in patterns:
        p = pattern.replace("\\", "/")
        if fnmatch.fnmatch(normalized, p):
            return pattern
    return "(none)"


# ─────────────────────────────────────────────────────────────
# Staleness helper
# ─────────────────────────────────────────────────────────────


def is_stale(
    modified_time: datetime | str | None,
    today: date | None = None,
    max_age_days: int = 120,
) -> bool:
    """Return True if the document is older than *max_age_days*."""
    if today is None:
        today = date.today()
    if modified_time is None:
        return False
    if isinstance(modified_time, str):
        try:
            modified_time = datetime.fromisoformat(modified_time)
        except (ValueError, TypeError):
            return False
    dt = modified_time if isinstance(modified_time, datetime) else None
    if dt is None:
        return False
    mtime_date = dt.date() if hasattr(dt, "date") else date.fromtimestamp(0)
    age = (today - mtime_date).days
    return age > max_age_days


# ─────────────────────────────────────────────────────────────
# Vault-scope walker (for scope-preview CLI)
# ─────────────────────────────────────────────────────────────

@dataclass
class VaultScopeReport:
    vault_path: str
    total_notes: int = 0
    signal_count: int = 0
    context_count: int = 0
    excluded_count: int = 0
    stale_count: int = 0
    active_override_count: int = 0
    fm_excluded_count: int = 0

    top_reasons: list[tuple[str, int]] = field(default_factory=list)
    folders_by_tier: dict[str, dict[str, int]] = field(default_factory=dict)

    signal_paths: list[str] = field(default_factory=list)
    context_paths: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)
    stale_paths: list[str] = field(default_factory=list)


def walk_vault(
    vault_path: str,
    config: SourceScopeConfig | None = None,
    today: date | None = None,
) -> VaultScopeReport:
    """Walk a vault directory and classify every `.md` file.

    Does NOT require a database connection — reads filesystem directly.
    """
    if config is None:
        config = load_source_scope()
    if today is None:
        today = date.today()

    vault = Path(vault_path)
    if not vault.exists():
        raise FileNotFoundError(f"Vault path does not exist: {vault_path}")

    report = VaultScopeReport(vault_path=str(vault.resolve()))
    reason_counts: dict[str, int] = {}
    folder_tiers: dict[str, dict[str, int]] = {"signal": {}, "context": {}, "excluded": {}}

    # Walk .md files (skip .obsidian, .git, .trash)
    skip_parts = {".obsidian", ".git", ".trash", ".DS_Store"}

    for md_file in sorted(vault.rglob("*.md")):
        # Skip hidden directories
        parts = set(md_file.parts)
        if parts & skip_parts:
            continue

        report.total_notes += 1

        # Try to read frontmatter
        fm: dict = {}
        tags: list[str] = []
        mtime: datetime | None = None
        try:
            import frontmatter as _fm
            post = _fm.load(str(md_file))
            fm = dict(post.metadata)
            raw_tags = fm.get("tags", [])
            tags = raw_tags if isinstance(raw_tags, list) else [str(raw_tags)]
            mtime = datetime.fromtimestamp(md_file.stat().st_mtime)
        except Exception:
            pass

        result = classify_source(
            source_path=str(md_file),
            vault_root=vault_path,
            frontmatter=fm,
            tags=tags,
            modified_time=mtime,
            today=today,
            config=config,
        )
        stale = is_stale(mtime, today, config.max_note_age_days)
        result.is_stale = stale
        result.is_active = result.is_active and not stale

        # Count
        if result.source_tier == "signal":
            report.signal_count += 1
            report.signal_paths.append(str(md_file))
        elif result.source_tier == "context":
            report.context_count += 1
            report.context_paths.append(str(md_file))
        else:
            report.excluded_count += 1
            report.excluded_paths.append(str(md_file))

        if stale:
            report.stale_count += 1
            report.stale_paths.append(str(md_file))
        if "forces signal" in result.scope_reason:
            report.active_override_count += 1
        if "forces exclusion" in result.scope_reason and "frontmatter" in result.scope_reason:
            report.fm_excluded_count += 1

        reason_counts[result.scope_reason] = reason_counts.get(result.scope_reason, 0) + 1

        # Folder-level tracking
        try:
            rel = str(md_file.relative_to(vault))
            folder = str(Path(rel).parent) if Path(rel).parent != Path(".") else "(root)"
        except ValueError:
            folder = "(external)"
        tier_map = folder_tiers[result.source_tier]
        tier_map[folder] = tier_map.get(folder, 0) + 1

    report.top_reasons = sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    report.folders_by_tier = {
        tier: dict(sorted(m.items(), key=lambda x: x[1], reverse=True)[:10])
        for tier, m in folder_tiers.items()
    }
    return report
