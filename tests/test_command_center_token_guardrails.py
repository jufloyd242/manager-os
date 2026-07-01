"""Tests for token estimation guardrails.

Invariant 6 (registry test file) covers "token estimate must exist for any
non-none-risk command"; this file covers the estimator's own math and its
reuse of the REAL prompt builders (no duplicated prompt template).
"""

from __future__ import annotations

import math

from manager_os.command_center import token_estimator
from manager_os.ingest.project_drive_docs import (
    _build_batch_drive_search_prompt,
    _build_drive_search_prompt,
)


def test_estimate_tokens_none_for_missing_text():
    assert token_estimator.estimate_tokens(None) is None
    assert token_estimator.estimate_tokens("") is None


def test_estimate_tokens_ceil_boundary():
    assert token_estimator.estimate_tokens("abcd") == 1
    assert token_estimator.estimate_tokens("a" * 400) == 100
    assert token_estimator.estimate_tokens("a" * 401) == 101


def test_estimate_for_command_single_project_docs_uses_real_prompt_builder():
    args = {"opportunity_number": "OPP1", "client": "Acme", "project_name": "Widget"}
    chars, tokens = token_estimator.estimate_for_command(
        "project_docs_fetch_print_prompt", args
    )
    expected_prompt = _build_drive_search_prompt("OPP1", "Acme", "Widget")
    assert chars == len(expected_prompt)
    assert tokens == math.ceil(len(expected_prompt) / 4)


def test_estimate_for_command_batch_project_docs_uses_real_prompt_builder():
    projects = [
        {"opportunity_number": "OPP1", "client": "Acme", "project_name": "Widget"},
        {"opportunity_number": "OPP2", "client": "Globex", "project_name": "Gadget"},
    ]
    chars, tokens = token_estimator.estimate_for_command(
        "project_docs_fetch_batch_print_prompt", {"projects": projects}
    )
    expected_prompt = _build_batch_drive_search_prompt(projects)
    assert chars == len(expected_prompt)
    assert tokens == math.ceil(len(expected_prompt) / 4)


def test_estimate_for_command_falls_back_to_registry_default_without_args():
    chars, tokens = token_estimator.estimate_for_command("project_docs_fetch_print_prompt")
    assert chars is not None
    assert tokens is not None

    chars2, tokens2 = token_estimator.estimate_for_command("project_docs_fetch_batch_print_prompt")
    assert chars2 is not None
    assert tokens2 is not None


def test_estimate_for_command_no_prompt_command_returns_none():
    chars, tokens = token_estimator.estimate_for_command("project_memory_report")
    assert chars is None
    assert tokens is None
