# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (src layout, editable with dev deps)
pip install -e ".[dev]"
# or
uv sync --dev

# Run all tests (coverage auto-enabled via pyproject.toml addopts)
pytest tests/

# Run a single test file or test
pytest tests/test_db.py
pytest tests/test_db.py::test_content_hash

# Coverage HTML report
pytest tests/ --cov --cov-report=html

# Lint
ruff check src/

# Type check
pyright src/

# CLI (after install)
manager-os --help
manager-os ingest [--source all|obsidian|forecast|deals|summary|gws|workspace] [--date YYYY-MM-DD] [--force] [--dry-run]
manager-os extract [--mode rules|llm|both] [--date YYYY-MM-DD] [--dry-run]
manager-os brief [--date YYYY-MM-DD]
manager-os dashboard
manager-os daily [--no-workspace] [--rules-only] [--skip-brief]
manager-os meeting-prep [--date YYYY-MM-DD] [--meeting SLUG] [--llm]
manager-os closeout [--date YYYY-MM-DD]
manager-os status
manager-os demo-reset [--dry-run] [--yes-demo]
```

## Architecture

### Data flow

```
ingest → extract → build → dashboard
```

Each stage writes to a local DuckDB file (`data/processed/manager_os.duckdb`). Nothing writes to external systems.

### Database layer (`db.py`)

`get_connection(db_path)` opens DuckDB and calls `init_schema()`, which runs `_SCHEMA_DDL` (all `CREATE TABLE IF NOT EXISTS`) followed by `_MIGRATIONS_DDL` (idempotent `ALTER TABLE ADD COLUMN IF NOT EXISTS` statements). All tests use `":memory:"` — never the real DB file.

Content-hash dedup: `content_hash(text)` returns SHA-256 hex. The `raw_documents.id` is the hash of the source path + content, making re-runs no-ops by default. Pass `--force` to bypass.

`seed_from_config(conn, settings)` populates `people` and `clients` from YAML on every ingest via `INSERT OR IGNORE`, so it never overwrites enriched data.

### Ingest (`ingest/`)

One module per source. Each exposes a function (e.g. `ingest_vault`, `ingest_forecast`) returning an `IngestResult` with `.ingested`, `.skipped`, `.failed`, `.skip_reasons`. Skip reasons in `_SAFE_SKIP_REASONS` (cli.py) are safe to suppress; others surface as warnings.

### Extract (`extract/`)

- `signals.py` — rule engine, no LLM. Four rules: risk keywords → `risk`; stale 1:1 (>14 days) → `people_health`; SOW deadline <7 days → `sow_loe_review`; overallocation >100% in 14 days → `utilization_risk`.
- `llm_signals.py` — optional LLM pass (`--mode llm|both`). Raises `LLMExtractionUnavailable` when API not configured; `extract` silently skips, `daily --llm-only` fails loudly.
- `action_items.py`, `decisions.py` — regex-based extraction, always run after signal extraction.
- `entities.py` — `EntityResolver` maps raw names to canonical people/clients/deals using aliases from `config/people.yaml`, `config/clients.yaml`, `config/deal_aliases.yaml`.
- `meeting_prep.py` — context scoring; gathers signals + notes for a meeting's attendees/entities.

### Build (`build/`)

- `daily_brief.py` — assembles markdown brief from open signals.
- `dashboard_data.py` — all DuckDB query functions consumed by Streamlit. When adding a new dashboard tab, query functions go here.
- `closeout.py` — EOD summary + optional weekly exec update (auto on Fridays).

### Dashboard (`dashboard/app.py`)

Streamlit 6-tab app: Today, People, Clients, Deals, Forecast, Meeting Prep. Excluded from coverage (`omit = ["*/dashboard/app.py"]`). Data comes exclusively from `build/dashboard_data.py`.

### Config (`config.py`)

Settings loaded via `pydantic-settings` from `.env` (all `MANAGER_OS_*` env vars). YAML loaders: `load_people()`, `load_clients()`, `load_deal_aliases()`, `load_source_priority()`. Config dir defaults to `./config/`.

### Schemas (`schemas.py`)

All Pydantic v2 models. Every DB entity has a corresponding model here. LLM output is also parsed through Pydantic for validation.

### LLM integration

Two modes: OpenAI-compatible API (via `OPENAI_API_KEY` + `MANAGER_OS_LLM_MODEL`) for `extract --mode llm`; Gemini CLI subprocess for `meeting-prep --llm` and workspace retrieval. LLM is never required — rule extraction always works without it.
