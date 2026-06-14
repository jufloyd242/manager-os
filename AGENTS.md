# AGENTS.md — Manager OS

## Purpose
Manager OS is a local-first Python CLI and Streamlit dashboard for a senior AI/ML consulting manager. It ingests Obsidian vault notes, staffing/forecast CSVs, deal CSVs, and daily workspace summaries into a local DuckDB database. It extracts management signals using deterministic rules and (optionally) LLM prompts, then generates a daily brief and renders a 6-tab dashboard.

**Everything is read-only with respect to external systems in the MVP.** No writes to Gmail, Chat, Drive, Calendar, or Sheets.

---

## Module Map

| Module | Purpose |
|--------|---------|
| `src/manager_os/cli.py` | Typer CLI app — all commands registered here |
| `src/manager_os/config.py` | Loads and validates YAML configs + .env settings |
| `src/manager_os/db.py` | DuckDB connection, schema init, helpers |
| `src/manager_os/schemas.py` | All Pydantic v2 models |
| `src/manager_os/ingest/obsidian.py` | Recursively ingests Obsidian vault .md files |
| `src/manager_os/ingest/forecast.py` | Ingests staffing forecast CSV |
| `src/manager_os/ingest/deals.py` | Ingests deal status CSV |
| `src/manager_os/ingest/workspace_summary.py` | Ingests daily workspace summary markdown |
| `src/manager_os/ingest/gws_client.py` | (Phase 5) Reads pre-saved GWS JSON snapshots |
| `src/manager_os/extract/entities.py` | Resolves raw text → canonical person/client/deal |
| `src/manager_os/extract/signals.py` | Rule-based + LLM signal extraction → signals table |
| `src/manager_os/extract/action_items.py` | Extracts commitments and follow-ups from notes |
| `src/manager_os/extract/meeting_prep.py` | Builds per-meeting context records |
| `src/manager_os/build/daily_brief.py` | Assembles markdown daily brief from signals |
| `src/manager_os/build/dashboard_data.py` | All DuckDB query functions for Streamlit |
| `src/manager_os/build/closeout.py` | EOD closeout + weekly exec update |
| `src/manager_os/dashboard/app.py` | Streamlit multi-tab dashboard |

---

## CLI Commands

```
manager-os ingest [--source all|obsidian|forecast|deals|summary] [--date YYYY-MM-DD] [--force]
manager-os extract [--date YYYY-MM-DD] [--mode rules|llm|both] [--entity person|client|deal|all]
manager-os brief [--date YYYY-MM-DD] [--output PATH]
manager-os dashboard
manager-os meeting-prep [--date YYYY-MM-DD] [--meeting SLUG]
manager-os closeout [--date YYYY-MM-DD]
```

---

## Key Design Decisions

1. **DuckDB only** — no external DB server; the `.duckdb` file lives in `data/processed/`
2. **Content-hash dedup** — re-running `ingest` on unchanged files is a no-op
3. **Signals are the primary artifact** — everything flows into the `signals` table
4. **LLM is optional** — the rule engine produces useful signals without any model
5. **No writes to external systems** — read-only until Phase 7, and even then only to local Obsidian vault with explicit user confirmation
6. **Config drives entity resolution** — canonical names come from `config/people.yaml` and `config/clients.yaml`

---

## Running Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# edit .env with your actual paths
manager-os --help
```

---

## Testing

```bash
pytest tests/
pytest tests/ --cov --cov-report=html
```

Fixtures are in `tests/fixtures/`. All tests use in-memory DuckDB (`:memory:`).
