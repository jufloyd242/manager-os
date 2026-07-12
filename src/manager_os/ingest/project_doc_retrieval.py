"""Deterministic project-document retrieval planner.

Replaces the single-prompt LLM approach with a two-stage query plan:
  Stage 1: OPP-only discovery (broadest — just the OPP number)
  Stage 2: OPP + keyword expansions (separate queries per keyword group)

The retrieval mechanism is still Gemini CLI (no new external integrations),
but the query planning, candidate ranking, deduplication, and diagnostics are
now deterministic and testable.

Read-only: no writes to Google Drive. Every prompt includes read-only
instructions.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from manager_os.ingest.workspace_gemini import _run_gemini_retrieval
from manager_os.utils import normalize_opp_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_QUERY_ATTEMPTS = 10
MAX_RAW_CANDIDATES = 100
MAX_RETURNED_DOCUMENTS = 20

# Minimum numeric length to generate a numeric-only variant (avoid broad
# false-positive sets for very short numbers like "12").
_MIN_NUMERIC_VARIANT_LENGTH = 4

# Document-type keywords for Stage 2 expansion.
# Each key maps to a list of keyword variants for that document type.
DOC_TYPE_KEYWORDS: dict[str, list[str]] = {
    "sow": ["SOW", "Statement of Work", "Statement-of-Work"],
    "deal_sheet": ["Deal Sheet", "DealSheet", "Deal Summary", "Opportunity Summary"],
    "loe": ["LOE", "Level of Effort", "Estimate", "Scoping"],
    "proposal": ["Proposal", "Order Form", "RFP Response"],
    "architecture": ["Solution Design", "Architecture", "Technical Design"],
    "requirements": ["Requirements", "Discovery"],
    "project_plan": ["Project Plan", "Kickoff"],
}

# Document-type priority for ranking (lower = higher priority).
DOC_TYPE_PRIORITY: dict[str, int] = {
    "sow": 1,
    "deal_sheet": 2,
    "loe": 3,
    "proposal": 4,
    "architecture": 5,
    "requirements": 6,
    "project_plan": 7,
    "other": 8,
}

# Keyword groups for Stage 2 — each group is a separate query.
# We use the first keyword from each DOC_TYPE_KEYWORDS entry as the
# representative term for that group.
_STAGE2_KEYWORD_GROUPS: list[tuple[str, list[str]]] = [
    ("sow", DOC_TYPE_KEYWORDS["sow"]),
    ("deal_sheet", DOC_TYPE_KEYWORDS["deal_sheet"]),
    ("loe", DOC_TYPE_KEYWORDS["loe"]),
    ("proposal", DOC_TYPE_KEYWORDS["proposal"]),
    ("architecture", DOC_TYPE_KEYWORDS["architecture"]),
    ("requirements", DOC_TYPE_KEYWORDS["requirements"]),
    ("project_plan", DOC_TYPE_KEYWORDS["project_plan"]),
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SearchQueryType(str, Enum):
    OPP_ONLY = "opp_only"
    OPP_PLUS_KEYWORD = "opp_plus_keyword"


class RetrievalStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_FAILURE = "partial_failure"
    DRY_RUN = "dry_run"
    OPP_MISSING = "opp_missing"
    INVALID_OPP = "invalid_opp"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUTH_FAILURE = "auth_failure"
    QUERY_REJECTED = "query_rejected"
    TIMEOUT = "timeout"
    PARSE_FAILURE = "parse_failure"
    ZERO_CANDIDATES = "zero_candidates"
    ALL_REJECTED = "all_rejected"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SearchQuery:
    """A single query in the retrieval plan."""
    query_id: str
    stage: int  # 1 = OPP discovery, 2 = keyword expansion
    term: str  # the keyword or "OPP_ONLY"
    query_type: SearchQueryType
    prompt_text: str


@dataclass
class Candidate:
    """A raw candidate document from a retrieval query."""
    title: str
    url: str
    document_type: str = "other"
    why_matched: str = ""
    source: str = "google_drive"
    file_id: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0  # set by rank_candidates


@dataclass
class RankedCandidate(Candidate):
    """A candidate with a deterministic ranking score."""
    score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)


@dataclass
class RetrievalDiagnostics:
    """Structured diagnostics for a retrieval run."""
    canonical_opp: str = ""
    search_variants: list[str] = field(default_factory=list)
    query_attempts: list[dict[str, Any]] = field(default_factory=list)
    deduplicated_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    rejection_reasons: list[str] = field(default_factory=list)
    ranked_selected: list[dict[str, Any]] = field(default_factory=list)
    provider_warnings: list[str] = field(default_factory=list)
    provider_errors: list[str] = field(default_factory=list)
    pagination_info: str = ""
    execution_duration_ms: float = 0.0


@dataclass
class RetrievalResult:
    """Result of a retrieval execution."""
    status: RetrievalStatus
    documents: list[RankedCandidate] = field(default_factory=list)
    diagnostics: RetrievalDiagnostics = field(default_factory=RetrievalDiagnostics)
    error: str = ""


# ---------------------------------------------------------------------------
# OPP normalization variants
# ---------------------------------------------------------------------------


def normalize_opp_id_variants(value: str | None) -> list[str]:
    """Generate bounded search variants for an OPP identifier.

    Preserves the canonical form and generates safe variants:
      OPP-12345 → [OPP-12345, OPP 12345, OPP12345, 12345]

    Only generates numeric-only variant when the numeric portion is >= 4
    digits to avoid broad false-positive sets.

    Args:
        value: Raw OPP identifier (may have whitespace, mixed case).

    Returns:
        List of variant strings, canonical form first. Empty if input
        is empty/None.
    """
    if not value:
        return []

    canonical = normalize_opp_id(value)
    if not canonical:
        return []

    variants: list[str] = [canonical]

    # Extract the numeric portion
    numeric_match = re.search(r"(\d+)", canonical)
    if not numeric_match:
        return variants

    numeric = numeric_match.group(1)
    prefix = re.sub(r"[^A-Za-z]", "", canonical[: numeric_match.start()])

    # If there's a prefix (e.g. "OPP"), generate separator variants
    if prefix:
        # Hyphen-separated variant: "OPP-12345"
        hyphen_variant = f"{prefix}-{numeric}"
        if hyphen_variant not in variants:
            variants.append(hyphen_variant)

        # Space-separated variant: "OPP 12345"
        space_variant = f"{prefix} {numeric}"
        if space_variant not in variants:
            variants.append(space_variant)

        # No-separator variant: "OPP12345"
        no_sep_variant = f"{prefix}{numeric}"
        if no_sep_variant not in variants:
            variants.append(no_sep_variant)

    # Numeric-only variant (only if long enough to avoid broad matches)
    if len(numeric) >= _MIN_NUMERIC_VARIANT_LENGTH:
        if numeric not in variants:
            variants.append(numeric)

    return variants


# ---------------------------------------------------------------------------
# Query plan
# ---------------------------------------------------------------------------


_READ_ONLY_INSTRUCTION = (
    "[Read-only. Metadata only. Do NOT create, edit, delete, send, move, or "
    "modify anything in Google Drive. Search My Drive, Shared Drives, and "
    "files shared with the user. Follow shortcut targets. Return ONLY JSON.]"
)


def _build_opp_only_prompt(opp: str, client: str, project_name: str) -> str:
    """Build a Stage 1 OPP-only discovery prompt."""
    retrieved_at = datetime.utcnow().isoformat()
    return (
        f"{_READ_ONLY_INSTRUCTION}\n"
        f"Find Drive docs containing the identifier '{opp}' "
        f"(client={client}; project={project_name}). "
        f"Search filenames, document titles, document body/content, and folder names. "
        f"Do NOT require any specific document-type keyword — any file mentioning '{opp}' is a candidate.\n\n"
        f"Return ONLY JSON:\n"
        f'{{"ok":true,"source":"google_drive_project_docs",'
        f'"retrieved_at":"{retrieved_at}",'
        f'"documents":[{{"document_type":"sow|deal_sheet|loe|proposal|architecture|requirements|project_plan|other",'
        f'"title":"str","url":"str","file_id":"str","confidence":0.9,"why_matched":"str"}}]}}\n'
        f'Fail: {{"ok":false,"source":"google_drive_project_docs","error":"str"}}'
    )


def _build_opp_plus_keyword_prompt(
    opp: str, keywords: list[str], client: str, project_name: str
) -> str:
    """Build a Stage 2 OPP+keyword expansion prompt."""
    retrieved_at = datetime.utcnow().isoformat()
    keyword_list = " OR ".join(keywords)
    return (
        f"{_READ_ONLY_INSTRUCTION}\n"
        f"Find Drive docs containing the identifier '{opp}' "
        f"AND any of these document-type terms: {keyword_list} "
        f"(client={client}; project={project_name}). "
        f"Search filenames, document titles, document body/content, and folder names. "
        f"Include Shared Drives and shortcut targets.\n\n"
        f"Return ONLY JSON:\n"
        f'{{"ok":true,"source":"google_drive_project_docs",'
        f'"retrieved_at":"{retrieved_at}",'
        f'"documents":[{{"document_type":"sow|deal_sheet|loe|proposal|architecture|requirements|project_plan|other",'
        f'"title":"str","url":"str","file_id":"str","confidence":0.9,"why_matched":"str"}}]}}\n'
        f'Fail: {{"ok":false,"source":"google_drive_project_docs","error":"str"}}'
    )


def build_query_plan(
    opp: str,
    client: str,
    project_name: str,
) -> list[SearchQuery]:
    """Build a two-stage retrieval query plan.

    Stage 1: OPP-only discovery (broadest — no keyword requirement).
    Stage 2: OPP + keyword expansions (one query per keyword group).

    The plan is deterministic and identical for dry-run and live execution.

    Args:
        opp: Canonical OPP identifier.
        client: Client name.
        project_name: Project name.

    Returns:
        List of SearchQuery objects, Stage 1 first.
    """
    plan: list[SearchQuery] = []

    # Stage 1: OPP-only discovery
    plan.append(SearchQuery(
        query_id="stage1_opp_only",
        stage=1,
        term="OPP_ONLY",
        query_type=SearchQueryType.OPP_ONLY,
        prompt_text=_build_opp_only_prompt(opp, client, project_name),
    ))

    # Stage 2: OPP + keyword expansions (one per keyword group)
    for doc_type, keywords in _STAGE2_KEYWORD_GROUPS:
        if len(plan) >= MAX_QUERY_ATTEMPTS:
            break
        plan.append(SearchQuery(
            query_id=f"stage2_{doc_type}",
            stage=2,
            term=keywords[0],  # representative term
            query_type=SearchQueryType.OPP_PLUS_KEYWORD,
            prompt_text=_build_opp_plus_keyword_prompt(opp, keywords, client, project_name),
        ))

    return plan[:MAX_QUERY_ATTEMPTS]


# ---------------------------------------------------------------------------
# Candidate ranking
# ---------------------------------------------------------------------------


def _is_boundary_safe_match(text: str, variant: str) -> bool:
    """Check if a variant appears in text with word boundaries.

    Prevents OPP-12345 from matching OPP-123456.
    """
    if not variant:
        return False
    # Escape regex special chars in the variant
    escaped = re.escape(variant)
    # Use word boundaries — but also check for non-alphanumeric boundaries
    # since OPP-12345 has a hyphen which is a boundary char.
    pattern = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    return bool(re.search(pattern, text, re.IGNORECASE))


def rank_candidates(
    candidates: list[Candidate],
    opp: str,
    client: str,
    project_name: str,
) -> list[RankedCandidate]:
    """Rank candidates using deterministic scoring signals.

    Score components (higher = better):
      1. Exact canonical OPP in filename/title (40 pts)
      2. Exact canonical OPP in body/content (30 pts — approximated by why_matched)
      3. Normalized OPP variant in filename/title (25 pts)
      4. Normalized OPP variant in content (15 pts)
      5. Recognized doc-type keyword in title (10 pts)
      6. Recognized doc-type keyword in content (5 pts)
      7. Project/client/account name match (10 pts)
      8. Preferred doc-type priority (0-8 pts, lower priority = more pts)
      9. Confidence from provider (0-10 pts)
      10. Recency (never above identity)

    A document matching exact OPP but no keyword still gets a positive score.

    Args:
        candidates: Raw candidates from retrieval.
        opp: Canonical OPP identifier.
        client: Client name.
        project_name: Project name.

    Returns:
        Candidates sorted by score descending, each as RankedCandidate.
    """
    canonical = normalize_opp_id(opp)
    variants = normalize_opp_id_variants(opp)
    # Variants excluding the canonical form (for lower-tier matching)
    non_canonical_variants = [v for v in variants if v != canonical]

    ranked: list[RankedCandidate] = []

    for cand in candidates:
        score = 0.0
        reasons: list[str] = []

        title_lower = (cand.title or "").lower()
        why_lower = (cand.why_matched or "").lower()

        # 1. Exact canonical OPP in filename/title
        if _is_boundary_safe_match(cand.title or "", canonical):
            score += 40
            reasons.append("exact OPP in title")
        # 2. Exact canonical OPP in content (approximated by why_matched)
        elif _is_boundary_safe_match(cand.why_matched or "", canonical):
            score += 30
            reasons.append("exact OPP in content")

        # 3. Normalized OPP variant in title
        for v in non_canonical_variants:
            if _is_boundary_safe_match(cand.title or "", v):
                score += 25
                reasons.append(f"OPP variant '{v}' in title")
                break
        # 4. Normalized OPP variant in content
        else:
            for v in non_canonical_variants:
                if _is_boundary_safe_match(cand.why_matched or "", v):
                    score += 15
                    reasons.append(f"OPP variant '{v}' in content")
                    break

        # 5. Recognized doc-type keyword in title
        for keywords in DOC_TYPE_KEYWORDS.values():
            for kw in keywords:
                if kw.lower() in title_lower:
                    score += 10
                    reasons.append(f"keyword '{kw}' in title")
                    break
            else:
                continue
            break

        # 6. Recognized doc-type keyword in content
        for keywords in DOC_TYPE_KEYWORDS.values():
            for kw in keywords:
                if kw.lower() in why_lower:
                    score += 5
                    reasons.append(f"keyword '{kw}' in content")
                    break
            else:
                continue
            break

        # 7. Project/client/account name match
        if client and client.lower() in title_lower:
            score += 10
            reasons.append("client name in title")
        if project_name and project_name.lower() in title_lower:
            score += 5
            reasons.append("project name in title")

        # 8. Preferred doc-type priority
        priority = DOC_TYPE_PRIORITY.get(cand.document_type, DOC_TYPE_PRIORITY["other"])
        # Lower priority number = higher score contribution (8 - priority + 1)
        score += max(0, 9 - priority)
        reasons.append(f"doc_type={cand.document_type} (priority={priority})")

        # 9. Confidence from provider
        score += min(10, cand.confidence * 10)

        ranked.append(RankedCandidate(
            title=cand.title,
            url=cand.url,
            document_type=cand.document_type,
            why_matched=cand.why_matched,
            source=cand.source,
            file_id=cand.file_id,
            confidence=cand.confidence,
            metadata=cand.metadata,
            score=score,
            match_reasons=reasons,
        ))

    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _dedup_key(cand: Candidate) -> str:
    """Generate a dedup key in priority order."""
    if cand.file_id:
        return f"fid:{cand.file_id}"
    if cand.url:
        return f"url:{cand.url}"
    # Normalized title fallback
    return f"title:{(cand.title or '').strip().lower()}"


def deduplicate(candidates: list[Candidate]) -> list[Candidate]:
    """Deduplicate candidates by stable identity.

    Priority: file_id → URL → normalized title.

    Args:
        candidates: Raw candidates (may contain duplicates across queries).

    Returns:
        Deduplicated list, preserving first-seen order.
    """
    seen: set[str] = set()
    result: list[Candidate] = []
    for cand in candidates:
        key = _dedup_key(cand)
        if key not in seen:
            seen.add(key)
            result.append(cand)
    return result


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_gemini_response(raw: str) -> dict[str, Any]:
    """Parse JSON from a Gemini CLI response, stripping markdown fences."""
    from manager_os.utils import clean_and_parse_json
    return clean_and_parse_json(raw)


def _candidates_from_response(data: dict[str, Any]) -> list[Candidate]:
    """Extract candidates from a parsed Gemini response."""
    if not data.get("ok"):
        return []

    docs = data.get("documents", [])
    candidates: list[Candidate] = []
    for doc_data in docs:
        title = doc_data.get("title", "")
        url = doc_data.get("url", "")
        doc_type = doc_data.get("document_type", "other")
        if not doc_type or doc_type == "other":
            # Try to detect from title
            from manager_os.ingest.project_drive_docs import detect_document_type
            doc_type = detect_document_type(title, url)
        candidates.append(Candidate(
            title=title,
            url=url,
            document_type=doc_type or "other",
            why_matched=doc_data.get("why_matched", ""),
            source="google_drive",
            file_id=doc_data.get("file_id", ""),
            confidence=float(doc_data.get("confidence", 0.0)),
            metadata=doc_data.get("metadata", {}),
        ))
    return candidates


# ---------------------------------------------------------------------------
# Execute retrieval
# ---------------------------------------------------------------------------


def execute_retrieval(
    opportunity_number: str,
    client: str,
    project_name: str,
    *,
    dry_run: bool = False,
    timeout: int = 60,
    limit: int = 5,
) -> RetrievalResult:
    """Execute the deterministic retrieval plan.

    Orchestrates the two-stage query plan, calls Gemini CLI per query,
    collects/parses/ranks/dedups candidates, and returns structured
    diagnostics with error classification.

    Args:
        opportunity_number: Raw OPP identifier.
        client: Client name.
        project_name: Project name.
        dry_run: If True, return the query plan without calling Gemini.
        timeout: Timeout in seconds per Gemini CLI call.
        limit: Maximum documents to return.

    Returns:
        RetrievalResult with status, documents, and diagnostics.
    """
    start_time = datetime.utcnow()

    # Validate OPP
    if not opportunity_number or not opportunity_number.strip():
        return RetrievalResult(
            status=RetrievalStatus.OPP_MISSING,
            error="No opportunity number provided.",
        )

    canonical_opp = normalize_opp_id(opportunity_number)
    variants = normalize_opp_id_variants(opportunity_number)
    plan = build_query_plan(canonical_opp, client, project_name)

    diagnostics = RetrievalDiagnostics(
        canonical_opp=canonical_opp,
        search_variants=variants,
        query_attempts=[],
    )

    # Dry run: return the plan without calling Gemini
    if dry_run:
        for q in plan:
            diagnostics.query_attempts.append({
                "query_id": q.query_id,
                "stage": q.stage,
                "term": q.term,
                "query_type": q.query_type.value,
            })
        elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
        diagnostics.execution_duration_ms = elapsed
        return RetrievalResult(
            status=RetrievalStatus.DRY_RUN,
            diagnostics=diagnostics,
        )

    # Live retrieval: execute each query
    all_candidates: list[Candidate] = []
    query_errors: list[str] = []
    query_successes = 0
    query_failures = 0

    for q in plan:
        if len(all_candidates) >= MAX_RAW_CANDIDATES:
            diagnostics.pagination_info = f"Stopped after {len(all_candidates)} raw candidates (ceiling)"
            break

        attempt_info: dict[str, Any] = {
            "query_id": q.query_id,
            "stage": q.stage,
            "term": q.term,
            "query_type": q.query_type.value,
            "candidate_count": 0,
            "error": None,
        }

        try:
            raw, _cmd = _run_gemini_retrieval(q.prompt_text, use_yolo=True, timeout=timeout)
            data = _parse_gemini_response(raw)
            candidates = _candidates_from_response(data)
            attempt_info["candidate_count"] = len(candidates)
            all_candidates.extend(candidates)
            query_successes += 1
        except TimeoutExpired:
            attempt_info["error"] = "timeout"
            query_errors.append(f"Query {q.query_id}: timeout after {timeout}s")
            query_failures += 1
        except json.JSONDecodeError as e:
            attempt_info["error"] = f"parse error: {e}"
            query_errors.append(f"Query {q.query_id}: parse error: {e}")
            query_failures += 1
        except RuntimeError as e:
            attempt_info["error"] = str(e)
            query_errors.append(f"Query {q.query_id}: {e}")
            query_failures += 1
        except Exception as e:
            attempt_info["error"] = str(e)
            query_errors.append(f"Query {q.query_id}: {e}")
            query_failures += 1

        diagnostics.query_attempts.append(attempt_info)

    diagnostics.provider_errors = query_errors

    # Classify overall status
    if query_successes == 0 and query_failures > 0:
        # All queries failed — classify the error
        first_error = query_errors[0] if query_errors else ""
        if "timeout" in first_error.lower():
            status = RetrievalStatus.TIMEOUT
        elif "parse error" in first_error.lower():
            status = RetrievalStatus.PARSE_FAILURE
        elif "auth" in first_error.lower() or "credential" in first_error.lower():
            status = RetrievalStatus.AUTH_FAILURE
        else:
            status = RetrievalStatus.PROVIDER_UNAVAILABLE
        elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
        diagnostics.execution_duration_ms = elapsed
        return RetrievalResult(
            status=status,
            diagnostics=diagnostics,
            error=first_error,
        )

    # Deduplicate
    deduped = deduplicate(all_candidates)
    diagnostics.deduplicated_count = len(deduped)

    if len(deduped) == 0:
        elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
        diagnostics.execution_duration_ms = elapsed
        return RetrievalResult(
            status=RetrievalStatus.ZERO_CANDIDATES,
            diagnostics=diagnostics,
        )

    # Rank
    ranked = rank_candidates(deduped, canonical_opp, client, project_name)

    # Apply limit
    selected = ranked[:limit]
    diagnostics.accepted_count = len(selected)
    diagnostics.rejected_count = len(ranked) - len(selected)
    if diagnostics.rejected_count > 0:
        diagnostics.rejection_reasons.append(
            f"Exceeded limit of {limit} documents; {diagnostics.rejected_count} lower-ranked docs not returned."
        )

    # Populate ranked_selected diagnostics (safe metadata only)
    for c in selected:
        diagnostics.ranked_selected.append({
            "title": c.title,
            "document_type": c.document_type,
            "score": c.score,
            "match_reasons": c.match_reasons,
        })

    elapsed = (datetime.utcnow() - start_time).total_seconds() * 1000
    diagnostics.execution_duration_ms = elapsed

    # Determine final status
    if query_failures > 0 and query_successes > 0:
        status = RetrievalStatus.PARTIAL_FAILURE
    else:
        status = RetrievalStatus.SUCCESS

    return RetrievalResult(
        status=status,
        documents=selected,
        diagnostics=diagnostics,
    )


# Re-export TimeoutExpired for test patching
from subprocess import TimeoutExpired  # noqa: E402
