# Manager OS

Local-first management dashboard for AI/ML consulting managers. Ingests Obsidian notes, staffing CSVs, deal data, and workspace summaries into DuckDB — extracts management signals, decisions, and action items using deterministic rules — then renders a React dashboard with Today, Deals, Forecast, and Meetings views.

**Read-only with respect to external systems.** No writes to Gmail, Chat, Drive, Calendar, or Sheets.

---

## Quickstart

### 1. Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — set MANAGER_OS_VAULT_PATH and other paths
```

### 2. Frontend Dependencies

```bash
cd frontend
npm install
```

### 3. Start the Backend API

```bash
python -m uvicorn manager_os.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### 4. Start the React Frontend

In another terminal:

```bash
cd frontend
npm run dev
```

Open **http://localhost:5173**.

### 5. Ingest and Refresh

- Click **Refresh from file** on any view to trigger local-only ingestion and extraction (no external or LLM calls).
- Calendar sync is explicit — click **Sync Calendar** for a specific date.

---

## VS Code Tasks

Use **Terminal → Run Task…** for ordered validation:

| # | Task | Purpose |
|---|------|---------|
| 01 | Backend Compile | `python -m compileall src/manager_os` |
| 02 | Backend Targeted Tests | Deals, Forecast, Workspace, Meetings |
| 03 | Backend Full Tests | `pytest tests/ -q` |
| 04 | Backend Ruff | Lint check |
| 05 | Backend Pyright | Type checking |
| 06 | Frontend Lint | `npm run lint` |
| 07 | Frontend Tests | `npm run test` |
| 08 | Frontend Build | `npm run build` |
| 09 | Full Validation | Runs all checks sequentially |
| 10 | Start API | `python -m uvicorn ...` |
| 11 | Start React | `npm run dev` |

See `docs/DASHBOARD_DEVELOPMENT_CHECKLIST.md` for a detailed walkthrough.

---

## Dashboard Views

| View | What it shows |
|------|---------------|
| **Today** | Summary cards (meetings needing prep, deals needing attention, staffing exceptions), top actions (≤5), command center |
| **Deals** | Full deal pipeline with search, filters, attention classification (critical/high/medium/low), progressive detail disclosure, refresh from file |
| **Forecast** | Weekly allocation with week navigation, overallocation/underutilization/available/unknown classification, roll-off detection, exception filtering |
| **Meetings** | Date picker, explicit per-date calendar sync, rule-driven preparation, resolved attendee relationships, workspace context integration |

**Legacy views** (People/Staffing, Projects, Archive) remain under Advanced sidebar.

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Service health |
| GET | `/api/status` | Data source freshness |
| GET | `/api/daily` | Daily operating loop |
| GET | `/api/people` | People dashboard rows |
| GET | `/api/meetings?date=YYYY-MM-DD` | Meetings for date |
| GET | `/api/meetings/{id}/prep` | Deterministic meeting prep |
| POST | `/api/meetings/{id}/prep` | Regenerate meeting prep |
| GET | `/api/deals` | Deals with attention classification |
| GET | `/api/forecast` | Forecast with allocation classification |
| GET | `/api/workspace-context` | Context items with entity linking |
| POST | `/api/refresh` | Local-only data refresh (no external calls) |
| GET | `/api/projects` | Project index |
| GET | `/api/feedback` | Feedback learning candidates |
| GET | `/api/commands` | Command registry |
| GET | `/api/commands/{id}` | Command spec |
| POST | `/api/commands/{id}/validate` | Validate + preview |
| POST | `/api/commands/{id}/run` | Execute command |
| GET | `/api/runs` | Run history |

---

## Workflows

### Deals
1. Open **Deals** view — see all deals with attention levels.
2. Use search/filters to narrow down.
3. Click **More details** to see SOW, LOE, staffing feasibility, blocker.
4. Click **Refresh from file** to reload from configured CSV.

### Forecast
1. Open **Forecast** view — see current week allocation.
2. Use **← Previous / Next →** to navigate weeks.
3. Toggle **Exceptions only** to see only overallocation/underutilization.
4. Roll-off dates appear when future weeks show a meaningful decrease.

### Workspace Context
1. Used automatically inside **Meeting Prep** and available via `GET /api/workspace-context`.
2. Entity-linked with provenance (source type, date, link method).
3. Attention items highlighted with amber background.
4. Click **Why this context?** for detailed provenance.

### Meeting Prep
1. Select a date in **Meetings** view — all local events appear.
2. Click **Sync Calendar** to fetch exactly that date (zero lookback/lookahead).
3. Click a meeting to open preparation.
4. Rule matching is automatic — see which rule matched and why.
5. Prep includes 1:1 context, open risks, action items, deals, and workspace context.
6. Deterministic GET/POST do not call external systems.

### Local Refresh vs Calendar Sync
- **Local Refresh** (`POST /api/refresh`): Ingests Obsidian, deals CSV, forecast CSV, workspace summaries. Never calls external APIs.
- **Calendar Sync** (`POST /api/meetings/sync-calendar`): Explicit, date-specific, bounded. Only called when user clicks the button.

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `manager-os ingest [--source all\|obsidian\|forecast\|deals\|summary]` | Ingest from configured sources |
| `manager-os extract [--mode rules\|llm\|both]` | Run signal + action item + decision extraction |
| `manager-os brief [--date YYYY-MM-DD]` | Generate markdown daily brief |
| `manager-os daily [--no-workspace] [--rules-only]` | Run full daily pipeline |
| `manager-os meeting-prep [--date] [--meeting SLUG] [--llm]` | Generate meeting prep docs |
| `manager-os closeout [--date]` | EOD closeout |
| `manager-os status` | DB table counts, open signals/actions |
| `manager-os demo-reset [--date] [--dry-run] [--yes-demo]` | Rebuild demo DB from fixtures |

---

## Data Sources

| Source | What it reads | Config path |
|--------|--------------|-------------|
| Obsidian vault | `.md` notes (1:1, client, deal, meeting) | `vault_path` |
| Forecast CSV | Staffing forecast | `forecast_csv` |
| Deals CSV | Deal pipeline | `deals_csv` |
| Workspace summary | Daily `YYYY-MM-DD.md` | `workspace_summary_dir` |
| Workspace activity | Structured JSON snapshots | `gws_snapshot_dir` |

---

## External Call Safety

- Deterministic GET endpoints never call external systems.
- Calendar sync requires explicit user action.
- LLM extraction is opt-in (`--mode llm` or `--llm`).
- Tests use `auto-use` fixture to block accidental Gemini subprocess calls.

---

## Testing

```bash
# Full backend suite
pytest tests/ -q

# Targeted tests
pytest tests/test_api_deals.py tests/test_api_forecast.py tests/test_api_workspace_context.py

# Frontend
cd frontend && npm run test && npm run build

# Lint
ruff check src/
cd frontend && npm run lint
```

Fixtures in `tests/fixtures/`. All tests use in-memory DuckDB (`:memory:`).

---

## Architecture

```
ingest → extract → build → API → React
```

Every stage reads from/writes to local DuckDB. The API layer exposes query results via FastAPI. The React frontend consumes the API with honest error states (no fake operational data on failure).

## Known Limitations

- Calendar sync endpoint (`POST /api/meetings/sync-calendar`) is defined in the service contract but requires Calendar API integration.
- Project document gaps are categorized as Medium priority — they don't dominate Today, Deals, or Meeting Prep.
- No predictive deal scoring, staffing optimization, or automated reassignments.
- No writes to external systems.

GWS snapshot files are read from `MANAGER_OS_GWS_SNAPSHOT_DIR` (default: `./data/raw/gws_snapshots/`). Layout: `calendar/YYYY-MM-DD.json`, `gmail/YYYY-MM-DD.json`, `chat/YYYY-MM-DD.json`.

---

## Configuration

### Environment variables (`.env`)

```
MANAGER_OS_VAULT_PATH=./vault
MANAGER_OS_FORECAST_CSV=./data/raw/forecast.csv
MANAGER_OS_DEALS_CSV=./data/raw/deals.csv
MANAGER_OS_WORKSPACE_SUMMARY_DIR=./data/raw/summaries
MANAGER_OS_GWS_SNAPSHOT_DIR=./data/raw/gws_snapshots
MANAGER_OS_DB_PATH=./data/processed/manager_os.duckdb
MANAGER_OS_CONFIG_DIR=./config

# Optional LLM (extract --mode llm, meeting-prep --llm)
OPENAI_API_KEY=sk-...
MANAGER_OS_LLM_MODEL=gpt-4o-mini   # or any OpenAI-compatible model
```

### Config YAMLs (`config/`)

| File | Purpose |
|------|---------|
| `config/people.yaml` | Team members with aliases, role, level |
| `config/clients.yaml` | Clients with aliases and engagement info |
| `config/deal_aliases.yaml` | Raw deal name → canonical name mappings |
| `config/source_priority.yaml` | CSV column aliases, confidence weights |

---

## Signal Rules (rule engine, no LLM required)

1. **Risk keywords** — note body contains escalat/blocker/delay/at risk → `signal_type=risk`
2. **Stale 1:1** — last 1:1 note > 14 days ago → `signal_type=people_health`
3. **SOW deadline** — deal closing within 7 days with unsigned SOW → `signal_type=sow_loe_review`
4. **Overallocation** — forecast >100% within next 14 days → `signal_type=utilization_risk`

---

## Requirements

- Python 3.11+
- See `pyproject.toml` for full dependency list
- Optional: `openai` package for LLM extraction and meeting prep enrichment

## Project Structure

See [AGENTS.md](AGENTS.md) for the full module map and design decisions.

## Testing

```bash
pytest tests/                    # 426 tests, all in-memory DuckDB
pytest tests/ --cov --cov-report=html
```

