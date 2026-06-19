"""Shared test fixtures and guardrails for the Manager OS test suite.

Key guardrails:
- All tests use in-memory DuckDB (:memory:) — no real DB files.
- Gemini/LLM subprocess calls are blocked by default via auto-use fixture.
- No test reads real Obsidian vault or real CSV data.
- Date-sensitive tests use controlled dates via fixtures.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from manager_os.db import get_connection

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem_conn():
    """In-memory DuckDB connection with full schema. Auto-closed."""
    conn = get_connection(":memory:")
    yield conn
    conn.close()


@pytest.fixture()
def controlled_date():
    """Fixed date for deterministic tests — avoids time-dependent failures."""
    return date(2026, 6, 13)


# ---------------------------------------------------------------------------
# Auto-use guard: block Gemini/LLM subprocess calls in ALL tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _block_gemini_subprocess():
    """Prevent any test from accidentally invoking Gemini CLI subprocess.

    Patches subprocess.run/Popen to raise if the command contains 'gemini'.
    Also patches the LLM_ENABLED flag to False.
    """
    import subprocess

    _orig_run = subprocess.run
    _orig_popen = subprocess.Popen

    def _guarded_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        if "gemini" in cmd_str.lower():
            raise RuntimeError(
                f"TEST GUARDRAIL: Blocked Gemini subprocess call: {cmd_str}"
            )
        return _orig_run(*args, **kwargs)

    def _guarded_popen(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        if "gemini" in cmd_str.lower():
            raise RuntimeError(
                f"TEST GUARDRAIL: Blocked Gemini subprocess.Popen: {cmd_str}"
            )
        return _orig_popen(*args, **kwargs)

    with patch("subprocess.run", side_effect=_guarded_run), \
         patch("subprocess.Popen", side_effect=_guarded_popen), \
         patch("manager_os.llm.gemini_cli.LLM_ENABLED", False):
        yield
