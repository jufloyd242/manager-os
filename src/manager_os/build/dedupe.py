"""Central deduplication and ranking for brief/dashboard signals.

Problem: The same fact can appear multiple times — a rule signal and a Gemini
signal detecting the same utilization issue, the same SOW deadline warning
repeated across reruns, or the same person/week overallocation from different
source notes.

This module provides domain-aware dedup keys and a ranking function.  The
brief and dashboard callers pass a list of signals and get back a smaller,
deduplicated list with only the best representative per group.

Usage::

    from manager_os.build.dedupe import dedupe_signals

    unique = dedupe_signals(signals, today=date.today())
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from manager_os.schemas import Signal


# ------------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return " ".join((name or "").lower().split())


def _summary_fingerprint(summary: str, max_words: int = 8) -> str:
    """Return a short fingerprint from a summary.

    Strips punctuation, lowercases, takes the first *max_words* words.
    """
    words = re.findall(r"[a-z0-9]+", (summary or "").lower())
    return " ".join(words[:max_words])


def _source_rank(source: str) -> int:
    """Structured data sources outrank LLM, which outranks rule keyword matches."""
    if source in ("deal", "deals", "forecast", "staffing"):
        return 0
    if source == "llm":
        return 1
    return 2  # "rule" or anything else


# ------------------------------------------------------------------
# Dedup keys
# ------------------------------------------------------------------

def build_dedup_key(s: Signal) -> tuple[str, str]:
    """Return a (key_type, key_value) tuple for domain-aware deduplication.

    Different signal types use different key schemas so we don't
    accidentally collapse different kinds of signals together.
    """
    entity = _normalize_name(s.entity_name)

    if s.signal_type == "utilization_risk":
        due = str(s.due_date) if s.due_date else ""
        return ("utilization_risk", f"{entity}::{due}")

    if s.signal_type == "sow_loe_review":
        due = str(s.due_date) if s.due_date else ""
        sp_norm = _normalize_name(s.source_path or "")
        return ("sow_loe_review", f"{entity}::{due}::{sp_norm}")

    if s.signal_type == "people_health":
        return ("people_health", entity)

    if s.signal_type in ("risk", "blocker", "client_update", "staffing_change",
                         "deal_change", "ask"):
        fp = _summary_fingerprint(s.summary)
        return (s.signal_type, f"{s.entity_type}::{entity}::{fp}")

    if s.signal_type == "follow_up":
        fp = _summary_fingerprint(s.summary)
        return ("follow_up", f"{entity}::{fp}")

    # Generic fallback
    fp = _summary_fingerprint(s.summary)
    return (s.signal_type, f"{s.entity_type}::{entity}::{fp}")


# ------------------------------------------------------------------
# Ranking within a duplicate group
# ------------------------------------------------------------------

_SEVERITY_SCORE = {"critical": 5, "high": 4, "medium": 2, "low": 1}


def _pick_best(candidates: list[Signal], today: date) -> Signal:
    """Return the single best signal from a group of duplicates.

    Prefers:
      1. Highest severity
      2. requires_manager_attention=True
      3. Higher confidence
      4. Structured source > LLM > rule
      5. Newer signal_date
      6. Richer why_it_matters
    """
    def sort_key(s: Signal):
        sev = _SEVERITY_SCORE.get(s.severity, 0)
        rma = 1 if s.requires_manager_attention else 0
        conf = s.confidence or 0.0
        src = _source_rank(s.source)
        # newer date = higher score (invert: more recent = larger int)
        sd = s.signal_date.toordinal() if s.signal_date else 0
        evidence_len = len(s.why_it_matters or "")
        return (sev, rma, conf, -src, sd, evidence_len)

    return max(candidates, key=sort_key)


# ------------------------------------------------------------------
# Main dedupe entry point
# ------------------------------------------------------------------

def dedupe_signals(
    signals: list[Signal],
    today: date | None = None,
) -> tuple[list[Signal], int, list[dict[str, Any]]]:
    """Deduplicate signals by domain-aware keys and return best per group.

    Args:
        signals: Raw signal list (should already be filtered to open/active).
        today: Reference date (default: today).

    Returns:
        (unique_signals, suppressed_count, debug_info)
        *debug_info* is a list of dicts with:
          key, kept_id, duplicate_ids, duplicate_count
    """
    if today is None:
        today = date.today()

    groups: dict[tuple[str, str], list[Signal]] = {}
    for s in signals:
        key = build_dedup_key(s)
        groups.setdefault(key, []).append(s)

    unique: list[Signal] = []
    suppressed = 0
    debug: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for s in signals:
        key = build_dedup_key(s)
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(s)
            continue
        # Duplicate — find the group, suppress and keep the better one
        suppressed += 1
        # Already has one entry in unique — compare and replace if needed
        # Find the existing entry with the same key
        for idx, u in enumerate(unique):
            if build_dedup_key(u) == key:
                # Compare and replace with the better one
                if _pick_best([u, s], today).id == s.id:
                    unique[idx] = s
                break

    return unique, suppressed, debug


# ------------------------------------------------------------------
# Dashboard forecast dedup (per-person, per-week)
# ------------------------------------------------------------------

def dedupe_forecast_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate forecast rows: keep one per person+week combination.

    When multiple rows exist for the same person+week (e.g. two clients,
    two projects), keep the one with the highest allocation.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        key = (
            _normalize_name(r.get("person_name", "")),
            str(r.get("week_start", "")),
        )
        groups.setdefault(key, []).append(r)

    result = []
    for key, group in groups.items():
        if len(group) == 1:
            result.append(group[0])
        else:
            best = max(group, key=lambda r: float(r.get("allocation_pct", 0)))
            result.append(best)
    return result