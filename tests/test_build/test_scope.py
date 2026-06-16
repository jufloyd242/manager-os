"""Tests for source scope / source tier classification (scope.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from manager_os.scope import (
    ScopeResult,
    classify_source,
    load_source_scope,
    is_stale,
    walk_vault,
)


# ─────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────


def _mk(path: str) -> str:
    """Convert a posix-style relative path to the proper platform separator."""
    return str(Path(path))


# ─────────────────────────────────────────────────────
# classify_source tests
# ─────────────────────────────────────────────────────


class TestClassifySource:
    """classify_source returns correct tiers for known patterns."""

    def test_training_is_excluded(self) -> None:
        r = classify_source("training/some_note.md")
        assert r.source_tier == "excluded"

    def test_hiring_is_excluded(self) -> None:
        r = classify_source("hiring/job_post.md")
        assert r.source_tier == "excluded"

    def test_quotes_is_excluded(self) -> None:
        r = classify_source("quotes/inspiration.md")
        assert r.source_tier == "excluded"

    def test_docs_is_excluded(self) -> None:
        r = classify_source("docs/setup.md")
        assert r.source_tier == "excluded"

    def test_scripts_is_excluded(self) -> None:
        r = classify_source("scripts/deploy.sh.md")
        assert r.source_tier == "excluded"

    def test_drafts_is_excluded(self) -> None:
        r = classify_source("drafts/rough_idea.md")
        assert r.source_tier == "excluded"

    def test_gemini_md_excluded(self) -> None:
        r = classify_source("GEMINI.md")
        assert r.source_tier == "excluded"

    def test_claude_md_excluded(self) -> None:
        r = classify_source("CLAUDE.md")
        assert r.source_tier == "excluded"

    def test_manager_os_excluded(self) -> None:
        r = classify_source("_manager-os/internal.md")
        assert r.source_tier == "excluded"

    def test_archive_excluded(self) -> None:
        r = classify_source("archive/old_notes.md")
        assert r.source_tier == "excluded"

    def test_dot_obsidian_excluded(self) -> None:
        r = classify_source(".obsidian/workspace.json")
        assert r.source_tier == "excluded"

    def test_team_directs_is_signal(self) -> None:
        r = classify_source(f"team{_mk('/')}directs{_mk('/')}alice.md")
        assert r.source_tier == "signal"

    def test_team_other_is_context(self) -> None:
        r = classify_source(f"team{_mk('/')}other{_mk('/')}someone.md")
        assert r.source_tier == "context"

    def test_engagement_status_is_signal(self) -> None:
        r = classify_source(f"clients{_mk('/')}foo{_mk('/')}engagement-status.md")
        assert r.source_tier == "signal"

    def test_manager_os_false_excludes(self) -> None:
        r = classify_source("team/me/my_note.md", frontmatter={"manager_os": "false"})
        assert r.source_tier == "excluded"

    def test_status_reference_excludes(self) -> None:
        r = classify_source("team/me/my_note.md", frontmatter={"status": "reference"})
        assert r.source_tier == "excluded"

    def test_manager_os_active_includes_as_signal(self) -> None:
        r = classify_source("team/me/my_note.md", frontmatter={"manager_os": "active"})
        assert r.source_tier == "signal"

    def test_default_unknown_path_is_context(self) -> None:
        r = classify_source("some/random/path.md")
        assert r.source_tier == "context", f"Expected context, got {r.source_tier}"


# ─────────────────────────────────────────────────────
# Frontmatter override tests
# ─────────────────────────────────────────────────────


class TestFrontmatterOverrides:
    """Frontmatter fields correctly override path-based classification."""

    def test_fm_active_true_forces_signal_on_context_path(self) -> None:
        r = classify_source(
            f"team{_mk('/')}other{_mk('/')}release_notes.md",
            frontmatter={"active": "true"},
        )
        assert r.source_tier == "signal"

    def test_fm_manager_os_ignore_excludes_signal_path(self) -> None:
        r = classify_source(
            f"team{_mk('/')}directs{_mk('/')}alice.md",
            frontmatter={"manager_os": "ignore"},
        )
        assert r.source_tier == "excluded"

    def test_tag_manager_os_active_includes(self) -> None:
        r = classify_source(
            "some/random/path.md",
            tags=["manager-os-active"],
        )
        # It's default signal anyway, but tag override should preserve that
        assert r.source_tier == "signal"

    def test_tag_archive_excludes(self) -> None:
        r = classify_source(
            f"team{_mk('/')}directs{_mk('/')}alice.md",
            tags=["archive"],
        )
        assert r.source_tier == "excluded"


# ─────────────────────────────────────────────────────
# Staleness tests
# ─────────────────────────────────────────────────────


class TestStaleness:
    """is_stale correctly identifies old documents."""

    def test_recent_document_not_stale(self) -> None:
        from datetime import date, datetime
        today = date(2026, 6, 15)
        recent = datetime(2026, 6, 1)
        assert not is_stale(recent, today=today, max_age_days=120)

    def test_old_document_is_stale(self) -> None:
        from datetime import date, datetime
        today = date(2026, 6, 15)
        old = datetime(2025, 1, 1)
        assert is_stale(old, today=today, max_age_days=120)

    def test_none_not_stale(self) -> None:
        assert not is_stale(None)


# ─────────────────────────────────────────────────────
# Walk vault tests
# ─────────────────────────────────────────────────────


class TestWalkVault:
    """walk_vault correctly classifies files in a real directory."""

    def test_walk_finds_notes_in_fixture(self) -> None:
        import os as _os
        repo_root = Path(__file__).parent.parent.parent
        fixture = repo_root / "tests" / "fixtures" / "vault"
        report = walk_vault(str(fixture))
        assert report.total_notes >= 1
        assert report.signal_count + report.context_count + report.excluded_count == report.total_notes
