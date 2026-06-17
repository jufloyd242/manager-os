"""Tests for manager-os daily --rules-only / --llm-only extraction mode.

Covers:
- _resolve_daily_extract_mode helper
- dry-run mode display
- extraction branch routing (rules/llm/both)
- llm-only fail-fast on conflict and LLM unavailable
- llm-limit / llm-timeout-seconds passthrough
- existing 'manager-os extract --mode llm' not broken
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from manager_os.cli import app as cli_app, _resolve_daily_extract_mode

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Unit: _resolve_daily_extract_mode
# ---------------------------------------------------------------------------


class TestResolveDailyExtractMode:
    def test_default_is_both(self):
        assert _resolve_daily_extract_mode(False, False) == "both"

    def test_rules_only(self):
        assert _resolve_daily_extract_mode(True, False) == "rules"

    def test_llm_only(self):
        assert _resolve_daily_extract_mode(False, True) == "llm"

    def test_conflict_raises(self):
        with pytest.raises(typer.BadParameter):
            _resolve_daily_extract_mode(True, True)

    def test_conflict_message(self):
        with pytest.raises(typer.BadParameter) as exc_info:
            _resolve_daily_extract_mode(True, True)
        assert "only one" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# CLI: dry-run shows correct extract mode
# ---------------------------------------------------------------------------


def _env(tmp_path) -> dict:
    """Minimal env vars for CLI invocation."""
    return {
        "MANAGER_OS_DB_PATH": str(tmp_path / "test.duckdb"),
        "MANAGER_OS_VAULT_PATH": str(FIXTURES / "vault"),
        "MANAGER_OS_FORECAST_CSV": str(FIXTURES / "forecast.csv"),
        "MANAGER_OS_DEALS_CSV": str(FIXTURES / "deals.csv"),
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        "MANAGER_OS_CONFIG_DIR": str(REPO_ROOT / "config"),
        "MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED": "false",
    }


def _run(*args: str, tmp_path) -> object:
    return CliRunner().invoke(cli_app, list(args), env=_env(tmp_path))


class TestDailyDryRunExtractMode:
    def test_default_dry_run_shows_both(self, tmp_path):
        result = _run("daily", "--dry-run", "--skip-brief", tmp_path=tmp_path)
        assert result.exit_code == 0, result.output
        assert "both" in result.output.lower()

    def test_rules_only_dry_run_shows_rules(self, tmp_path):
        result = _run("daily", "--dry-run", "--rules-only", "--skip-brief", tmp_path=tmp_path)
        assert result.exit_code == 0, result.output
        assert "rules" in result.output.lower()

    def test_llm_only_dry_run_shows_llm(self, tmp_path):
        result = _run("daily", "--dry-run", "--llm-only", "--skip-brief", tmp_path=tmp_path)
        assert result.exit_code == 0, result.output
        assert "llm" in result.output.lower()

    def test_conflict_exits_nonzero(self, tmp_path):
        result = _run("daily", "--dry-run", "--rules-only", "--llm-only", "--skip-brief",
                      tmp_path=tmp_path)
        assert result.exit_code != 0

    def test_conflict_shows_error_message(self, tmp_path):
        result = _run("daily", "--dry-run", "--rules-only", "--llm-only", "--skip-brief",
                      tmp_path=tmp_path)
        combined = (result.output or "") + str(result.exception or "")
        assert "only one" in combined.lower() or "rules-only" in combined.lower()


# ---------------------------------------------------------------------------
# CLI: extraction branch routing (real extract phase, mocked internals)
# ---------------------------------------------------------------------------


def _seed_note(conn) -> None:
    """Insert a minimal note so extraction doesn't abort early."""
    from manager_os.db import content_hash
    import json
    from datetime import date

    doc_id = content_hash("raw::test")
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_documents
            (id, ingested_at, source_type, source_path, content_hash, content, metadata)
        VALUES (?, CURRENT_TIMESTAMP, 'obsidian', '/test.md', ?, 'test note', ?)
        """,
        [doc_id, doc_id, json.dumps({"source_tier": "signal"})],
    )
    note_id = content_hash("note::test")
    conn.execute(
        """
        INSERT OR IGNORE INTO notes
            (id, raw_document_id, note_date, note_type, entity_type, entity_name,
             title, body, tags, created_at)
        VALUES (?, ?, ?, '1on1', 'person', 'Alice', 'Test', 'Test body', '[]', CURRENT_TIMESTAMP)
        """,
        [note_id, doc_id, date.today().isoformat()],
    )


_FAKE_RULE_RESULT = MagicMock(written=0, skipped=0, failed=0, skip_reasons={})
_FAKE_LLM_RESULT = MagicMock(written=0, skipped=0, failed=0, skip_reasons={})
_FAKE_AI_RESULT = MagicMock(written=0, skipped=0, failed=0, skip_reasons={})
_FAKE_DEC_RESULT = MagicMock(written=0, skipped=0, failed=0, skip_reasons={})


# Patch targets: imports happen inside the daily() function body,
# so we patch at the source module level.
_PATCH_RULES = "manager_os.extract.signals.run_rule_extraction"
_PATCH_LLM = "manager_os.extract.llm_signals.run_llm_extraction"
_PATCH_AI = "manager_os.extract.action_items.extract_action_items_from_all_notes"
_PATCH_DEC = "manager_os.extract.decisions.extract_decisions_from_all_notes"


def _invoke_daily_extract_only(tmp_path, extra_args: list[str]):
    """Run daily with --skip-ingest --skip-brief and the given extra args."""
    from manager_os.db import get_connection

    db_path = str(tmp_path / "test.duckdb")
    conn = get_connection(db_path)
    _seed_note(conn)
    conn.close()

    env = {**_env(tmp_path), "MANAGER_OS_DB_PATH": db_path}
    return CliRunner().invoke(
        cli_app,
        ["daily", "--skip-ingest", "--skip-brief"] + extra_args,
        env=env,
    )


class TestDailyExtractionBranchRouting:
    def test_default_runs_both_branches(self, tmp_path):
        with (
            patch(_PATCH_RULES, return_value=_FAKE_RULE_RESULT) as mock_rules,
            patch(_PATCH_LLM, return_value=_FAKE_LLM_RESULT) as mock_llm,
            patch(_PATCH_AI, return_value=_FAKE_AI_RESULT),
            patch(_PATCH_DEC, return_value=_FAKE_DEC_RESULT),
        ):
            result = _invoke_daily_extract_only(tmp_path, [])
            assert result.exit_code == 0, result.output
            mock_rules.assert_called_once()
            mock_llm.assert_called_once()

    def test_rules_only_skips_llm(self, tmp_path):
        with (
            patch(_PATCH_RULES, return_value=_FAKE_RULE_RESULT) as mock_rules,
            patch(_PATCH_LLM, return_value=_FAKE_LLM_RESULT) as mock_llm,
            patch(_PATCH_AI, return_value=_FAKE_AI_RESULT),
            patch(_PATCH_DEC, return_value=_FAKE_DEC_RESULT),
        ):
            result = _invoke_daily_extract_only(tmp_path, ["--rules-only"])
            assert result.exit_code == 0, result.output
            mock_rules.assert_called_once()
            mock_llm.assert_not_called()

    def test_llm_only_skips_rules(self, tmp_path):
        with (
            patch(_PATCH_RULES, return_value=_FAKE_RULE_RESULT) as mock_rules,
            patch(_PATCH_LLM, return_value=_FAKE_LLM_RESULT) as mock_llm,
            patch(_PATCH_AI, return_value=_FAKE_AI_RESULT),
            patch(_PATCH_DEC, return_value=_FAKE_DEC_RESULT),
        ):
            result = _invoke_daily_extract_only(tmp_path, ["--llm-only"])
            assert result.exit_code == 0, result.output
            mock_rules.assert_not_called()
            mock_llm.assert_called_once()

    def test_llm_only_passes_llm_limit(self, tmp_path):
        with (
            patch(_PATCH_RULES, return_value=_FAKE_RULE_RESULT),
            patch(_PATCH_LLM, return_value=_FAKE_LLM_RESULT) as mock_llm,
            patch(_PATCH_AI, return_value=_FAKE_AI_RESULT),
            patch(_PATCH_DEC, return_value=_FAKE_DEC_RESULT),
        ):
            _invoke_daily_extract_only(tmp_path, ["--llm-only", "--llm-limit", "7"])
            call_kwargs = mock_llm.call_args
            assert call_kwargs is not None
            args, kwargs = call_kwargs
            max_cand = kwargs.get("max_candidates", args[2] if len(args) > 2 else None)
            assert max_cand == 7

    def test_llm_only_passes_timeout(self, tmp_path):
        with (
            patch(_PATCH_RULES, return_value=_FAKE_RULE_RESULT),
            patch(_PATCH_LLM, return_value=_FAKE_LLM_RESULT) as mock_llm,
            patch(_PATCH_AI, return_value=_FAKE_AI_RESULT),
            patch(_PATCH_DEC, return_value=_FAKE_DEC_RESULT),
        ):
            _invoke_daily_extract_only(tmp_path, ["--llm-only", "--llm-timeout-seconds", "30"])
            call_kwargs = mock_llm.call_args
            args, kwargs = call_kwargs
            timeout = kwargs.get("timeout_seconds", None)
            assert timeout == 30


# ---------------------------------------------------------------------------
# LLM-only fail-fast when LLM unavailable
# ---------------------------------------------------------------------------


class TestLLMOnlyFailFast:
    def test_llm_only_fails_when_unavailable(self, tmp_path):
        from manager_os.extract.llm_signals import LLMExtractionUnavailable

        with (
            patch(_PATCH_RULES, return_value=_FAKE_RULE_RESULT),
            patch(_PATCH_LLM, side_effect=LLMExtractionUnavailable("Gemini CLI not found")),
            patch(_PATCH_AI, return_value=_FAKE_AI_RESULT),
            patch(_PATCH_DEC, return_value=_FAKE_DEC_RESULT),
        ):
            result = _invoke_daily_extract_only(tmp_path, ["--llm-only"])
            assert result.exit_code != 0

    def test_llm_only_error_message_is_clear(self, tmp_path):
        from manager_os.extract.llm_signals import LLMExtractionUnavailable

        with (
            patch(_PATCH_RULES, return_value=_FAKE_RULE_RESULT),
            patch(_PATCH_LLM, side_effect=LLMExtractionUnavailable("Gemini CLI not found")),
            patch(_PATCH_AI, return_value=_FAKE_AI_RESULT),
            patch(_PATCH_DEC, return_value=_FAKE_DEC_RESULT),
        ):
            result = _invoke_daily_extract_only(tmp_path, ["--llm-only"])
            assert "llm" in result.output.lower() or "gemini" in result.output.lower()

    def test_default_both_warns_and_continues_when_llm_unavailable(self, tmp_path):
        from manager_os.extract.llm_signals import LLMExtractionUnavailable

        with (
            patch(_PATCH_RULES, return_value=_FAKE_RULE_RESULT),
            patch(_PATCH_LLM, side_effect=LLMExtractionUnavailable("Gemini CLI not found")),
            patch(_PATCH_AI, return_value=_FAKE_AI_RESULT),
            patch(_PATCH_DEC, return_value=_FAKE_DEC_RESULT),
        ):
            result = _invoke_daily_extract_only(tmp_path, [])
            # default both: should NOT exit 1 (non-fatal)
            assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# manager-os extract --mode llm still works (not broken)
# ---------------------------------------------------------------------------


class TestExtractCommandUnaffected:
    def test_extract_mode_llm_dry_run(self, tmp_path):
        """'manager-os extract --mode llm --dry-run' must still work."""
        env = _env(tmp_path)
        # Need a db with at least one note for dry-run to not bail
        from manager_os.db import get_connection
        conn = get_connection(env["MANAGER_OS_DB_PATH"])
        _seed_note(conn)
        conn.close()

        result = CliRunner().invoke(
            cli_app,
            ["extract", "--mode", "llm", "--dry-run"],
            env=env,
        )
        assert result.exit_code == 0, result.output
