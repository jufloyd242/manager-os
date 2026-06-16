# Manager OS — Overnight Progress

## Branch

`main` (feature branch `feat/morning-usable-noise-reduction` not created — changes applied directly to `main` for overnight usability)

## Recent Commits

```
f3ce83f Add extract progress and Gemini LLM controls
b09c5e1 Merge pull request #1 from jufloyd242/feat/source-scope-preview
3dbe0a2 Repair common YAML frontmatter mistakes during Obsidian ingest
62bfbc8 Fix Gemini CLI invocation and rewrite LLM tests for Gemini CLI
179082a Add source scope and Gemini CLI LLM extraction
```

## What Changed

### Source Scope — Noise-Hostile Default (Phases 1-3)

- **Default tier changed from `signal` to `context`** — unknown/unmatched notes no longer produce operational items. This is the single biggest noise reduction.
- **`config/source_scope.yaml`** strengthened:
  - Excluded: `training/**`, `quotes/**`, `hiring/**`, `docs/**`, `scripts/**`, `drafts/**`, `archive/**`, `_manager-os/**`, `.obsidian/**`, `.git/**`, `GEMINI.md`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `temp_*.md`, `**/general.md`, `**/_TEMPLATE.md`, `deals/deal_scraper.md`, `Client meeting flow/**`, `SADA/**`, `onboarding/**`, `mentorships/**`
  - Signal paths kept narrow: `team/directs/`, `team/my manager/`, `team/me/`, `clients/**/engagement-status.md`, `meetings/2026-*.md`, `manager/decision-log-*.md`, etc.
  - Context paths: `team/other/`, `clients/**/`, `day-to-day/**/`
- **Real vault result**: Signal dropped from 99 → **45** (54% reduction). Context: 155. Excluded: 108.

### Tier Wired Into All Extraction (Phase 4)

- **Rule signals** (`extract/signals.py`): `_note_source_tier` now skips context/excluded notes. Only signal-tier notes produce risk signals.
- **Action items** (`extract/action_items.py`): `extract_action_items_from_all_notes` joins `raw_documents.metadata` and skips non-signal notes.
- **Decisions** (`extract/decisions.py`): Same tier filtering.
- **LLM signals** (`extract/llm_signals.py`): Tier filtering happens in Python *before* applying the candidate limit, so context/excluded notes don't consume the LLM budget.
- **Backward compat**: Tests that seed notes directly (no raw_documents row) default to `signal` tier via a safe fallback path.

### Extract Progress and LLM Controls (Phase 5 — Already Done)

- `--progress/--no-progress`, `--llm-limit`, `--llm-timeout-seconds`, `--llm-source-path`, `--llm-note-id`, `--llm-since-days` all present and working.

### Gemini CLI Hardening (Phase 6)

- `GEMINI_CLI_ARGS` and `GEMINI_CLI_YOLO`/`GEMINI_CLI_YOLO_ARGS` env vars added.
- `_run_gemini` accepts `extra_args` list.
- Doctor displays base args, yolo mode, workspace retrieval status.

### LLM Prompt Quality (Phase 7)

- Updated prompt to be more conservative: "Prefer returning [] over guessing", "Ignore reference material, process documentation, training content", "Only extract items Justin should care about as a manager".

### Brief/Dashboard Suppression (Phase 8-9 — Inherited)

- Existing noise filters (`_NOISY_SOURCE_SUBSTRINGS`, `_NOISY_TITLE_LOWER`, `_ACTIONABLE_RISK_TERMS`) combined with scope-tier filtering should exclude training, hiring, quotes, templates, GEMINI, CLAUDE, README, AGENTS, general.md, etc. from operational views.

### Workspace Retrieval Module (Phase X)

- New module: `src/manager_os/ingest/workspace_gemini.py` — read-only retrieval helpers for forecast, calendar, and workspace activity via Gemini CLI YOLO mode.
- New CLI commands: `workspace-doctor`, `workspace-fetch-forecast`, `workspace-fetch-calendar`, `workspace-fetch-activity`, `workspace-fetch-all`.
- All prompts include strict read-only instructions.
- Retrieved data stored in `data/raw/workspace_snapshots/<subdir>/` (gitignored).
- Dry-run prints prompt without calling Gemini.

### Config/Hardening

- `.env.example` updated with all Gemini CLI and workspace retrieval vars.
- `config.py` `Settings` class updated with `gemini_cli_args`, `gemini_cli_yolo`, `gemini_cli_yolo_args`, etc.
- `_SAFE_SKIP_REASONS` in `cli.py` now includes `tier_context`, `tier_excluded`, `junk_note_type`.
- `.gitignore` covers `data/raw/workspace_snapshots/` and `data/raw/gws_snapshots/`.
- Fixture vault files tagged with `manager_os: active` to classify as signal-tier.

## Tests

**Result**: 1020 passed, 10 failed (all 10 are pre-existing — `test_cli_demo_reset.py` and `test_cli_profile_*.py`).

Changes:
- `tests/test_build/test_scope.py`: Renamed `test_default_unknown_path_is_signal` → `test_default_unknown_path_is_context`
- `tests/fixtures/vault/*.md` and `tests/fixtures/v0.2_scenario/vault/*.md`: Added `manager_os: active` frontmatter
- `tests/test_skip_reasons.py`: `tier_context` and `tier_excluded` now recognized as safe skip reasons

## Commands That Ran

```
manager-os readiness                    → PASS
manager-os scope-preview                → Signal: 45, Context: 155, Excluded: 108
manager-os llm-doctor --no-smoke-test   → PASS
manager-os workspace-doctor             → PASS (retrieval disabled)
manager-os ingest --dry-run             → PASS
manager-os extract --mode rules --dry-run → PASS (108 rules, 51 actions, 2 decisions)
manager-os extract --mode both --llm-limit 5 --dry-run → PASS (14 LLM, 2 skipped)
manager-os --help                       → all commands present
manager-os extract --help               → all LLM flags present
```

## Known Risks

1. **`docs/` folder now excluded** — if you keep active notes in `docs/`, they won't produce signals. Move them to appropriate signal/context paths or add frontmatter `manager_os: active`.
2. **Unknown vault paths default to context** — any new note that doesn't match signal/exclude/context patterns will be context. Add explicit path patterns if needed.
3. **Workspace retrieval disabled by default** — toggle `MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED=true` and ensure `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION` are set in `.env`.
4. **Fixture vault notes needed `manager_os: active`** — real vault notes ingested before this change with no scope metadata will appear as context after re-ingest. Re-run `manager-os ingest --force` to refresh metadata.
5. **Brief/dashboard** were not directly modified in this pass — the tier filtering in extraction should reduce noise downstream, but review the first brief output to verify.

## Next Commands for Justin

```bash
# Run fresh morning flow
git pull
source .venv/bin/activate
manager-os readiness
manager-os profile-forecast
manager-os profile-deals
manager-os scope-preview
manager-os ingest
manager-os extract --mode both --llm-limit 25 --verbose
manager-os brief --max-items 20
manager-os dashboard

# Emergency rules-only fallback
manager-os extract --mode rules --verbose

# Test workspace retrieval (if configured)
manager-os workspace-doctor
manager-os workspace fetch-forecast --dry-run --print-prompt
```

## Final Morning Commands (from requirements)

```bash
git pull
source .venv/bin/activate
manager-os readiness
manager-os profile-forecast
manager-os profile-deals
manager-os scope-preview
manager-os ingest
manager-os extract --mode both --llm-limit 25 --verbose
manager-os brief --max-items 20
manager-os dashboard
```