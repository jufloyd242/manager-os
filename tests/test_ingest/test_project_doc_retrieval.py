"""Tests for the deterministic project-document retrieval planner.

All tests mock `_run_gemini_retrieval` — no live Gemini/Drive calls.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

import pytest

from manager_os.ingest.project_doc_retrieval import (
    SearchQuery,
    SearchQueryType,
    Candidate,
    RetrievalDiagnostics,
    RetrievalResult,
    RetrievalStatus,
    normalize_opp_id_variants,
    build_query_plan,
    rank_candidates,
    deduplicate,
    execute_retrieval,
    DOC_TYPE_KEYWORDS,
    DOC_TYPE_PRIORITY,
)
from manager_os.utils import normalize_opp_id


# ---------------------------------------------------------------------------
# OPP normalization variants
# ---------------------------------------------------------------------------


class TestNormalizeOppIdVariants:
    def test_preserves_canonical_form(self):
        variants = normalize_opp_id_variants("OPP-12345")
        assert "OPP-12345" in variants

    def test_strips_whitespace(self):
        variants = normalize_opp_id_variants("  OPP-12345  ")
        assert "OPP-12345" in variants

    def test_normalizes_case(self):
        variants = normalize_opp_id_variants("opp-12345")
        assert "OPP-12345" in variants

    def test_generates_space_variant(self):
        variants = normalize_opp_id_variants("OPP-12345")
        assert "OPP 12345" in variants

    def test_generates_no_separator_variant(self):
        variants = normalize_opp_id_variants("OPP-12345")
        assert "OPP12345" in variants

    def test_generates_numeric_only_variant(self):
        variants = normalize_opp_id_variants("OPP-12345")
        assert "12345" in variants

    def test_handles_already_no_prefix(self):
        variants = normalize_opp_id_variants("12345")
        assert "12345" in variants
        assert "OPP-12345" not in variants  # no prefix to add

    def test_handles_space_separator(self):
        variants = normalize_opp_id_variants("OPP 12345")
        assert "OPP-12345" in variants
        assert "OPP12345" in variants
        assert "12345" in variants

    def test_handles_no_separator(self):
        variants = normalize_opp_id_variants("OPP12345")
        assert "OPP-12345" in variants
        assert "OPP 12345" in variants
        assert "12345" in variants

    def test_short_numeric_no_broad_search(self):
        """Numeric-only variant should not be generated for very short numbers
        to avoid broad false-positive sets."""
        variants = normalize_opp_id_variants("OPP-12")
        assert "12" not in variants  # too short, would be too broad

    def test_empty_returns_empty(self):
        assert normalize_opp_id_variants("") == []

    def test_none_returns_empty(self):
        assert normalize_opp_id_variants(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Boundary-safe matching
# ---------------------------------------------------------------------------


class TestBoundarySafeMatching:
    def test_exact_opp_does_not_match_longer_opp(self):
        """OPP-12345 must not match OPP-123456."""
        variants = normalize_opp_id_variants("OPP-12345")
        longer = "OPP-123456"
        for v in variants:
            # A boundary-aware match should not match the longer OPP
            # unless the variant is a substring that happens to appear
            # (which is expected for "12345" in "OPP-123456" — but the
            # ranking function should penalize this).
            pass
        # The real test is in ranking: a candidate with OPP-123456 in
        # its title should NOT rank as an exact match for OPP-12345.
        candidates = [
            Candidate(
                title="Project OPP-123456 SOW",
                url="https://drive.google.com/123456",
                document_type="sow",
                why_matched="OPP-123456 in title",
                source="google_drive",
                file_id="file_123456",
            )
        ]
        ranked = rank_candidates(candidates, "OPP-12345", "Client", "Project")
        # Should not be ranked as exact match — score should be low
        assert ranked[0].score < 50  # not a high-confidence match

    def test_exact_opp_matches_correctly(self):
        candidates = [
            Candidate(
                title="OPP-12345 SOW",
                url="https://drive.google.com/12345",
                document_type="sow",
                why_matched="OPP-12345 in title",
                source="google_drive",
                file_id="file_12345",
            )
        ]
        ranked = rank_candidates(candidates, "OPP-12345", "Client", "Project")
        assert ranked[0].score >= 80  # high-confidence exact match


# ---------------------------------------------------------------------------
# Query plan
# ---------------------------------------------------------------------------


class TestBuildQueryPlan:
    def test_plan_starts_with_opp_only_discovery(self):
        plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        assert len(plan) > 0
        first = plan[0]
        assert first.stage == 1
        assert first.query_type == SearchQueryType.OPP_ONLY

    def test_plan_includes_keyword_expansions(self):
        plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        keyword_queries = [q for q in plan if q.query_type == SearchQueryType.OPP_PLUS_KEYWORD]
        assert len(keyword_queries) > 0

    def test_plan_does_not_require_all_keywords_simultaneously(self):
        """No single query should contain all keywords as AND requirements."""
        plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        for q in plan:
            if q.query_type == SearchQueryType.OPP_PLUS_KEYWORD:
                # Each keyword query should mention only ONE keyword group
                # not all of them combined.
                keyword_count = sum(
                    1 for kw in DOC_TYPE_KEYWORDS.values() if any(k.lower() in q.term.lower() for k in kw)
                )
                assert keyword_count <= 2, f"Query {q.term!r} combines too many keyword groups"

    def test_plan_opp_in_every_narrowed_query(self):
        """Every Stage 2 query must include the OPP number."""
        plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        for q in plan:
            assert "OPP-12345" in q.prompt_text or "OPP12345" in q.prompt_text or "12345" in q.prompt_text

    def test_plan_respects_max_queries(self):
        plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        assert len(plan) <= 10

    def test_plan_includes_sow_keyword(self):
        plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        sow_queries = [q for q in plan if "sow" in q.term.lower() or "statement of work" in q.term.lower()]
        assert len(sow_queries) > 0

    def test_plan_includes_deal_sheet_keyword(self):
        plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        deal_queries = [q for q in plan if "deal sheet" in q.term.lower() or "deal summary" in q.term.lower()]
        assert len(deal_queries) > 0

    def test_plan_includes_loe_keyword(self):
        plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        loe_queries = [q for q in plan if "loe" in q.term.lower() or "level of effort" in q.term.lower() or "estimate" in q.term.lower()]
        assert len(loe_queries) > 0

    def test_dry_run_and_live_use_same_plan(self):
        """Dry-run and live must use the same query plan structure."""
        dry_plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        live_plan = build_query_plan("OPP-12345", "Acme", "AI Platform")
        assert len(dry_plan) == len(live_plan)
        for dq, lq in zip(dry_plan, live_plan):
            assert dq.stage == lq.stage
            assert dq.query_type == lq.query_type
            assert dq.term == lq.term


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


class TestRankCandidates:
    def test_exact_opp_in_filename_ranks_highest(self):
        candidates = [
            Candidate(
                title="Random Doc",
                url="https://drive.google.com/other",
                document_type="other",
                why_matched="",
                source="google_drive",
                file_id="file_other",
            ),
            Candidate(
                title="OPP-12345 SOW",
                url="https://drive.google.com/sow",
                document_type="sow",
                why_matched="OPP in title",
                source="google_drive",
                file_id="file_sow",
            ),
        ]
        ranked = rank_candidates(candidates, "OPP-12345", "Acme", "AI Platform")
        assert ranked[0].title == "OPP-12345 SOW"

    def test_sow_ranks_above_deal_sheet(self):
        candidates = [
            Candidate(
                title="OPP-12345 Deal Sheet",
                url="https://drive.google.com/deal",
                document_type="deal_sheet",
                why_matched="OPP in title",
                source="google_drive",
                file_id="file_deal",
            ),
            Candidate(
                title="OPP-12345 SOW",
                url="https://drive.google.com/sow",
                document_type="sow",
                why_matched="OPP in title",
                source="google_drive",
                file_id="file_sow",
            ),
        ]
        ranked = rank_candidates(candidates, "OPP-12345", "Acme", "AI Platform")
        assert ranked[0].document_type == "sow"
        assert ranked[1].document_type == "deal_sheet"

    def test_exact_opp_without_keyword_remains_valid(self):
        """A document matching exact OPP but no recognized keyword should
        still be included in results, just ranked lower."""
        candidates = [
            Candidate(
                title="OPP-12345 Project Notes",
                url="https://drive.google.com/notes",
                document_type="other",
                why_matched="OPP in title",
                source="google_drive",
                file_id="file_notes",
            ),
        ]
        ranked = rank_candidates(candidates, "OPP-12345", "Acme", "AI Platform")
        assert len(ranked) == 1
        assert ranked[0].score > 0  # still valid

    def test_client_name_match_boosts_score(self):
        candidates = [
            Candidate(
                title="AI Platform Doc",
                url="https://drive.google.com/doc1",
                document_type="other",
                why_matched="",
                source="google_drive",
                file_id="file1",
            ),
            Candidate(
                title="AI Platform Doc with Acme",
                url="https://drive.google.com/doc2",
                document_type="other",
                why_matched="Acme in title",
                source="google_drive",
                file_id="file2",
            ),
        ]
        ranked = rank_candidates(candidates, "OPP-12345", "Acme", "AI Platform")
        # The one with client name match should rank higher
        assert ranked[0].title == "AI Platform Doc with Acme"

    def test_doc_type_priority_order(self):
        """SOW > Deal Sheet > LOE > Proposal > Solution/Architecture > Requirements > Project Plan > Other"""
        assert DOC_TYPE_PRIORITY["sow"] < DOC_TYPE_PRIORITY["deal_sheet"]
        assert DOC_TYPE_PRIORITY["deal_sheet"] < DOC_TYPE_PRIORITY["loe"]
        assert DOC_TYPE_PRIORITY["loe"] < DOC_TYPE_PRIORITY["proposal"]
        assert DOC_TYPE_PRIORITY["proposal"] < DOC_TYPE_PRIORITY["architecture"]
        assert DOC_TYPE_PRIORITY["architecture"] < DOC_TYPE_PRIORITY["requirements"]
        assert DOC_TYPE_PRIORITY["requirements"] < DOC_TYPE_PRIORITY["project_plan"]
        assert DOC_TYPE_PRIORITY["project_plan"] < DOC_TYPE_PRIORITY["other"]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def test_dedup_by_file_id(self):
        candidates = [
            Candidate(title="Doc A", url="url1", document_type="sow", why_matched="", source="google_drive", file_id="file_1"),
            Candidate(title="Doc A (copy)", url="url1", document_type="sow", why_matched="", source="google_drive", file_id="file_1"),
        ]
        deduped = deduplicate(candidates)
        assert len(deduped) == 1

    def test_dedup_by_url_when_no_file_id(self):
        candidates = [
            Candidate(title="Doc A", url="https://drive.google.com/doc1", document_type="sow", why_matched="", source="google_drive", file_id=""),
            Candidate(title="Doc A copy", url="https://drive.google.com/doc1", document_type="sow", why_matched="", source="google_drive", file_id=""),
        ]
        deduped = deduplicate(candidates)
        assert len(deduped) == 1

    def test_dedup_by_normalized_title_fallback(self):
        candidates = [
            Candidate(title="SOW for Project", url="", document_type="sow", why_matched="", source="google_drive", file_id=""),
            Candidate(title="SOW for Project", url="", document_type="sow", why_matched="", source="google_drive", file_id=""),
        ]
        deduped = deduplicate(candidates)
        assert len(deduped) == 1

    def test_different_docs_not_deduped(self):
        candidates = [
            Candidate(title="SOW", url="url1", document_type="sow", why_matched="", source="google_drive", file_id="file_1"),
            Candidate(title="Deal Sheet", url="url2", document_type="deal_sheet", why_matched="", source="google_drive", file_id="file_2"),
        ]
        deduped = deduplicate(candidates)
        assert len(deduped) == 2


# ---------------------------------------------------------------------------
# Execute retrieval (mocked)
# ---------------------------------------------------------------------------


def _mock_gemini_response(documents: list[dict], ok: bool = True, error: str = "") -> str:
    """Build a mock Gemini CLI stdout response."""
    data = {
        "ok": ok,
        "source": "google_drive_project_docs",
        "retrieved_at": datetime.utcnow().isoformat(),
    }
    if error:
        data["error"] = error
    else:
        data["documents"] = documents
    return json.dumps(data)


class TestExecuteRetrieval:
    def test_dry_run_returns_plan_without_calling_gemini(self):
        with patch("manager_os.ingest.project_doc_retrieval._run_gemini_retrieval") as mock:
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=True, timeout=60, limit=5,
            )
            mock.assert_not_called()
            assert result.status == RetrievalStatus.DRY_RUN
            assert len(result.diagnostics.query_attempts) > 0
            assert len(result.documents) == 0

    def test_success_returns_documents(self):
        mock_docs = [
            {"document_type": "sow", "title": "OPP-12345 SOW", "url": "https://drive.google.com/sow", "confidence": 0.9, "why_matched": "OPP in title"},
            {"document_type": "deal_sheet", "title": "OPP-12345 Deal Sheet", "url": "https://drive.google.com/deal", "confidence": 0.85, "why_matched": "OPP in title"},
        ]
        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            return_value=(_mock_gemini_response(mock_docs), ["gemini"]),
        ):
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            assert result.status == RetrievalStatus.SUCCESS
            assert len(result.documents) >= 2
            assert result.diagnostics.accepted_count >= 2

    def test_empty_opp_returns_opp_missing(self):
        result = execute_retrieval("", "Acme", "AI Platform", dry_run=False, timeout=60, limit=5)
        assert result.status == RetrievalStatus.OPP_MISSING

    def test_provider_timeout(self):
        import subprocess
        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            side_effect=subprocess.TimeoutExpired(cmd="gemini", timeout=60),
        ):
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            assert result.status == RetrievalStatus.TIMEOUT

    def test_provider_error(self):
        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            side_effect=RuntimeError("Gemini CLI exited with code 1: connection refused"),
        ):
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            assert result.status == RetrievalStatus.PROVIDER_UNAVAILABLE

    def test_parse_failure(self):
        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            return_value=("not valid json {{{", ["gemini"]),
        ):
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            assert result.status == RetrievalStatus.PARSE_FAILURE

    def test_zero_candidates(self):
        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            return_value=(_mock_gemini_response([]), ["gemini"]),
        ):
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            assert result.status == RetrievalStatus.ZERO_CANDIDATES

    def test_partial_query_failure(self):
        """Some queries succeed, some fail — should return partial_failure with docs."""
        call_count = [0]
        mock_docs = [
            {"document_type": "sow", "title": "OPP-12345 SOW", "url": "https://drive.google.com/sow", "confidence": 0.9, "why_matched": "OPP in title"},
        ]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return (_mock_gemini_response(mock_docs), ["gemini"])
            raise RuntimeError("transient error")

        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            side_effect=side_effect,
        ):
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            assert result.status in (RetrievalStatus.SUCCESS, RetrievalStatus.PARTIAL_FAILURE)
            assert len(result.documents) >= 1
            assert len(result.diagnostics.provider_errors) > 0

    def test_returned_document_ceiling(self):
        """Should not return more than the limit."""
        mock_docs = [
            {"document_type": "sow", "title": f"OPP-12345 Doc {i}", "url": f"https://drive.google.com/doc{i}", "confidence": 0.9, "why_matched": "OPP in title"}
            for i in range(50)
        ]
        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            return_value=(_mock_gemini_response(mock_docs), ["gemini"]),
        ):
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            assert len(result.documents) <= 5

    def test_diagnostics_populated(self):
        mock_docs = [
            {"document_type": "sow", "title": "OPP-12345 SOW", "url": "https://drive.google.com/sow", "confidence": 0.9, "why_matched": "OPP in title"},
        ]
        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            return_value=(_mock_gemini_response(mock_docs), ["gemini"]),
        ):
            result = execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            d = result.diagnostics
            assert d.canonical_opp == "OPP-12345"
            assert len(d.search_variants) > 0
            assert len(d.query_attempts) > 0
            assert d.accepted_count >= 1

    def test_no_drive_writes(self):
        """Retrieval must never write to Drive — verify no write-related subprocess."""
        mock_docs = [
            {"document_type": "sow", "title": "OPP-12345 SOW", "url": "https://drive.google.com/sow", "confidence": 0.9, "why_matched": "OPP in title"},
        ]
        with patch(
            "manager_os.ingest.project_doc_retrieval._run_gemini_retrieval",
            return_value=(_mock_gemini_response(mock_docs), ["gemini"]),
        ) as mock:
            execute_retrieval(
                "OPP-12345", "Acme", "AI Platform",
                dry_run=False, timeout=60, limit=5,
            )
            # Verify the prompt sent to Gemini contains read-only instructions
            for call_args in mock.call_args_list:
                prompt = call_args[0][0] if call_args[0] else call_args[1].get("prompt", "")
                assert "read-only" in prompt.lower() or "do not" in prompt.lower()
