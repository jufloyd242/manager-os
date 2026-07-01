"""Token estimation for command center prompts.

Uses a simple `ceil(chars / 4)` heuristic. For project-docs-fetch commands,
reuses the REAL existing prompt builders
(`_build_drive_search_prompt` / `_build_batch_drive_search_prompt` from
`manager_os.ingest.project_drive_docs`) instead of duplicating prompt text,
so estimates never drift from what's actually sent to Gemini.
"""

from __future__ import annotations

import math
from typing import Optional

from manager_os.command_center import registry
from manager_os.ingest.project_drive_docs import (
    _build_batch_drive_search_prompt,
    _build_drive_search_prompt,
)

_PROJECT_DOCS_SINGLE_COMMANDS = {
    "project_docs_fetch_dry_run",
    "project_docs_fetch_print_prompt",
    "project_docs_fetch_live_single",
}
_PROJECT_DOCS_BATCH_COMMANDS = {
    "project_docs_fetch_batch_dry_run",
    "project_docs_fetch_batch_print_prompt",
    "project_docs_fetch_batch_live_bounded",
}


def estimate_tokens(text: Optional[str]) -> Optional[int]:
    """Return ceil(len(text)/4), or None if text is falsy (no prompt applicable).

    None (not 0) is used deliberately to distinguish "no prompt applies to
    this command" from "an empty prompt was measured".
    """
    if not text:
        return None
    return math.ceil(len(text) / 4)


def estimate_for_command(
    command_id: str, args: Optional[dict] = None
) -> tuple[Optional[int], Optional[int]]:
    """Return (prompt_chars, estimated_input_tokens) for a command.

    For project_docs_fetch_* commands, builds the REAL prompt from supplied
    args (opportunity_number/client/project_name for single;
    `projects` list of dicts for batch) when enough info is present;
    otherwise falls back to the registry's placeholder default estimate.
    All other commands return (None, None) — no LLM prompt applies.
    """
    args = args or {}
    spec = registry.get(command_id)

    if command_id in _PROJECT_DOCS_SINGLE_COMMANDS:
        opportunity_number = args.get("opportunity_number")
        if opportunity_number:
            prompt = _build_drive_search_prompt(
                opportunity_number, args.get("client", ""), args.get("project_name", "")
            )
            return len(prompt), estimate_tokens(prompt)
        return spec.estimated_prompt_chars, spec.estimated_input_tokens

    if command_id in _PROJECT_DOCS_BATCH_COMMANDS:
        projects = args.get("projects")
        if projects:
            prompt = _build_batch_drive_search_prompt(projects)
            return len(prompt), estimate_tokens(prompt)
        return spec.estimated_prompt_chars, spec.estimated_input_tokens

    return None, None
