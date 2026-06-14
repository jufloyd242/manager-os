# Manager OS

Local-first management dashboard for AI/ML consulting managers. Ingests Obsidian notes, staffing CSVs, deal data, and Google Workspace snapshots into DuckDB — extracts management signals, decisions, and action items using deterministic rules (and optionally an LLM) — then renders a daily command center with a 6-tab Streamlit dashboard.

**Everything is read-only with respect to external systems.** No writes to Gmail, Chat, Drive, Calendar, or Sheets.

---

## Quickstart

```bash
# 1. Clone and set up environment
git clone <repo-url>
cd manager-os
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env — set MANAGER_OS_VAULT_PATH and other paths

# 3. Ingest your data (seeds people/clients from config automatically)
manager-os ingest

# 4. Extract signals, action items, and decisions
manager-os extract

# 5. Generate today's brief
manager-os brief

# 6. Open the dashboard (http://localhost:8501)
manager-os dashboard
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `manager-os ingest [--source all\|obsidian\|forecast\|deals\|summary\|gws] [--verbose]` | Ingest from configured sources; `-v` shows per-source skip reasons |
| `manager-os extract [--mode rules\|llm\|both] [--verbose]` | Run signal + action item + decision extraction; `-v` shows skip reasons |
| `manager-os brief [--date YYYY-MM-DD]` | Generate today's markdown daily brief |
| `manager-os dashboard` | Launch the Streamlit dashboard |
| `manager-os meeting-prep [--date] [--meeting SLUG] [--llm]` | Generate meeting prep docs |
| `manager-os closeout [--date] [--weekly\|--no-weekly]` | Generate EOD closeout + optional weekly exec update |
| `manager-os status` | Show DB table counts, open signals/actions, and mode (demo/sample/production) |
| `manager-os demo-reset [--date] [--dry-run] [--yes-demo]` | Rebuild demo DB + sample artifacts from fixture data |

---

## Dashboard Tabs

| Tab | What it shows |
|-----|---------------|
| **Today** | Open signals by severity, action items, Ack/Dismiss buttons |
| **People** | Team health, days since 1:1, allocation, morale (auto from signals) |
| **Clients** | Client health (🔴/🟡/🟢), last update, open risks |
| **Deals** | Deal pipeline, days to close, SOW/LOE status, urgency highlighting |
| **Forecast** | 2-week/30-day/60-day staffing summary, overallocation flags |
| **Meeting Prep** | Select a meeting → generate contextual prep + optional 🤖 LLM synthesis |

---

## Data Sources

| Source | What it reads | CLI flag |
|--------|--------------|----------|
| Obsidian vault | `.md` notes (1:1, client, deal, meeting) | `--source obsidian` |
| Forecast CSV | Staffing forecast spreadsheet export | `--source forecast` |
| Deals CSV | Deal pipeline spreadsheet export | `--source deals` |
| Workspace summary | Daily `YYYY-MM-DD.md` summary | `--source summary` |
| GWS snapshots | Pre-saved Calendar / Gmail / Chat JSON | `--source gws` |

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

