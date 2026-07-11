# AGENTS.md — Manager OS

## Purpose
Manager OS is a local-first Python API and React dashboard for a senior AI/ML consulting manager. It ingests Obsidian vault notes, staffing/forecast CSVs, deal CSVs, and daily workspace summaries into a local DuckDB database. It extracts management signals, decisions, and action items using deterministic rules, then serves a React dashboard with Today, Deals, Forecast, and Meetings views through a FastAPI backend.

**Read-only with respect to external systems.** No writes to Gmail, Chat, Drive, Calendar, or Sheets.

---

## Module Map

| Module | Purpose |
|--------|---------|
| `src/manager_os/cli.py` | Typer CLI app — all commands registered here |
| `src/manager_os/startup.py` | Startup, preflight, doctor, build, and process lifecycle |
| `src/manager_os/config.py` | Loads and validates YAML configs + .env settings |
| `src/manager_os/db.py` | DuckDB connection, schema init, helpers |
| `src/manager_os/schemas.py` | All Pydantic v2 models |
| `src/manager_os/ingest/obsidian.py` | Recursively ingests Obsidian vault .md files |
| `src/manager_os/ingest/forecast.py` | Ingests staffing forecast CSV |
| `src/manager_os/ingest/deals.py` | Ingests deal status CSV |
| `src/manager_os/ingest/workspace_summary.py` | Ingests daily workspace summary markdown |
| `src/manager_os/ingest/gws_client.py` | Reads pre-saved GWS JSON snapshots |
| `src/manager_os/ingest/workspace_snapshot.py` | Reads workspace activity snapshots |
| `src/manager_os/extract/entities.py` | Resolves raw text → canonical person/client/deal |
| `src/manager_os/extract/signals.py` | Rule-based signal extraction → signals table |
| `src/manager_os/extract/action_items.py` | Extracts commitments and follow-ups from notes |
| `src/manager_os/extract/decisions.py` | Extracts decisions from notes |
| `src/manager_os/extract/meeting_prep.py` | Builds per-meeting context records |
| `src/manager_os/extract/relationships.py` | Relationship resolution from Obsidian metadata |
| `src/manager_os/build/daily_brief.py` | Assembles markdown daily brief from signals |
| `src/manager_os/build/daily_operating_loop.py` | Builds the daily operating loop payload |
| `src/manager_os/build/dashboard_data.py` | All DuckDB query functions for dashboard |
| `src/manager_os/build/deals_dashboard.py` | Enriched deal queries with attention classification |
| `src/manager_os/build/forecast_dashboard.py` | Enriched forecast queries with allocation classification |
| `src/manager_os/build/workspace_context.py` | Normalized workspace context with entity linking |
| `src/manager_os/build/closeout.py` | EOD closeout + weekly exec update |
| `src/manager_os/api/app.py` | FastAPI app factory — all routes registered here |
| `src/manager_os/api/deals_routes.py` | `GET /api/deals` |
| `src/manager_os/api/forecast_routes.py` | `GET /api/forecast` |
| `src/manager_os/api/workspace_context_routes.py` | `GET /api/workspace-context` |
| `src/manager_os/api/meeting_prep_routes.py` | `GET|POST /api/meetings/{id}/prep` |
| `src/manager_os/api/refresh_routes.py` | `POST /api/refresh` |
| `src/manager_os/api/services.py` | Data-shaping helpers for API routes |
| `src/manager_os/api/models.py` | Pydantic response models |
| `src/manager_os/api/deps.py` | FastAPI dependency injection |
| `src/manager_os/dashboard/app.py` | Streamlit multi-tab dashboard (legacy) |

---

## CLI Commands

```
manager-os start       Start production dashboard (API + built React)
manager-os dev         Start development mode (API + Vite)
manager-os doctor      Diagnose setup and configuration
manager-os build       Build React frontend for production
manager-os ingest [--source all|obsidian|forecast|deals|summary] [--date YYYY-MM-DD] [--force]
manager-os extract [--date YYYY-MM-DD] [--mode rules|llm|both] [--entity person|client|deal|all]
manager-os brief [--date YYYY-MM-DD] [--output PATH]
manager-os daily [--no-workspace] [--rules-only] [--skip-brief]
manager-os dashboard
manager-os meeting-prep [--date YYYY-MM-DD] [--meeting SLUG] [--llm]
manager-os closeout [--date YYYY-MM-DD]
manager-os status
manager-os demo-reset [--dry-run] [--yes-demo]
```

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
| POST | `/api/refresh` | Local-only data refresh |
| GET | `/api/projects` | Project index |
| GET | `/api/feedback` | Feedback learning candidates |
| GET | `/api/commands` | Command registry |
| POST | `/api/commands/{id}/validate` | Validate + preview |
| POST | `/api/commands/{id}/run` | Execute command |

---

## Frontend

| Module | Purpose |
|--------|---------|
| `frontend/src/App.tsx` | Main app with view routing |
| `frontend/src/api/client.ts` | API client with all endpoint types |
| `frontend/src/components/Sidebar.tsx` | Navigation (Today, Deals, Forecast, Meetings) |
| `frontend/src/features/deals/DealsView.tsx` | Full deal pipeline view |
| `frontend/src/features/forecast/ForecastView.tsx` | Weekly allocation view |
| `frontend/src/features/workspaceContext/WorkspaceContextPanel.tsx` | Reusable context panel |
| `frontend/src/components/MeetingsView.tsx` | Date picker, meeting list, prep |

---

## Key Design Decisions

1. **DuckDB only** — no external DB server; the `.duckdb` file lives in `data/processed/`
2. **Content-hash dedup** — re-running `ingest` on unchanged files is a no-op
3. **Signals are the primary artifact** — everything flows into the `signals` table
4. **LLM is optional** — the rule engine produces useful signals without any model
5. **No writes to external systems** — read-only architecture
6. **Config drives entity resolution** — canonical names come from `config/people.yaml` and `config/clients.yaml`
7. **Deterministic endpoints never call external systems** — calendar sync is explicit and date-specific
8. **React-first** — Streamlit is legacy, no new features added there

---

## Running Locally

```bash
./manager-os start
```

Or for development:

```bash
./manager-os dev
```

Or manually:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# edit .env with your actual paths
./manager-os build
python -m uvicorn manager_os.api.app:app --host 127.0.0.1 --port 8000 --reload
# In another terminal:
cd frontend && npm install && npm run dev
```
cd frontend && npm install && npm run dev
```

---

## Testing

```bash
pytest tests/ -q
pytest tests/ --cov --cov-report=html
cd frontend && npm run test && npm run build
```

Fixtures are in `tests/fixtures/`. All tests use in-memory DuckDB (`:memory:`).
