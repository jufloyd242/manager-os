# Security & Data Hygiene

This document explains the security posture of Manager OS and how to work
safely with both sample and real data before and after connecting sensitive
manager information.

---

## 1. Local-first / read-only posture

Manager OS is designed to run entirely on your local machine:

- **No cloud services are written to.** All commands read from configured
  sources (Obsidian vault, CSV files, GWS JSON snapshots) and write only to a
  local DuckDB file and local output directories.
- **No data leaves the machine** during normal use — there is no telemetry in
  the application code, no API calls except the optional LLM extraction step
  (see below), and no sync to any remote store.
- **The optional LLM step** (`manager-os extract --mode llm` or
  `manager-os meeting-prep --llm`) sends note text to the configured OpenAI-
  compatible endpoint. Do not enable this step until you have reviewed your
  privacy obligations with respect to your employer and any client data.
- **Streamlit** (the dashboard server) runs locally on `http://localhost:8501`
  and is not exposed to the network by default. See §4 for disabling its
  optional usage-statistics reporting.

---

## 2. Sample data vs real data

The repository ships with **fake fixture data** under `tests/fixtures/` so
you can run the full pipeline without any real information:

| Fixture path | Content |
|---|---|
| `tests/fixtures/vault/` | Three synthetic Obsidian notes (1:1, client status, deal) |
| `tests/fixtures/forecast.csv` | Nine fake staffing rows |
| `tests/fixtures/deals.csv` | Five fake deals |
| `tests/fixtures/summaries/` | One synthetic daily summary |
| `tests/fixtures/gws_snapshots/` | Synthetic calendar, Gmail, and Chat JSON |

**All names, clients, and deal amounts in fixtures are invented.** They do not
correspond to any real person, organisation, or commercial arrangement.

The `manager-os demo-reset` command rebuilds the demo database from these
fixtures at any time:

```bash
manager-os demo-reset --date 2026-06-13
```

The demo database is written to `data/demo/manager_os_demo.duckdb`, which is
excluded from version control (see §3).

---

## 3. What not to commit

The `.gitignore` at the repository root already excludes everything listed
below. This section documents **why** each entry matters.

### Environment file

```
.env
```

Your `.env` file contains file-system paths and optionally an `OPENAI_API_KEY`.
Never commit it. Use `.env.example` as a template — it contains no secrets.

### Real Obsidian vault notes

Your vault path is set via `MANAGER_OS_VAULT_PATH`. The vault itself lives
outside this repository and is never copied into it. If you create any
vault-related helper scripts, ensure they write outside the repo root.

### Raw input data (`data/raw/`)

```
data/raw/*
!data/raw/.gitkeep
```

This directory holds the CSV files and GWS snapshot JSON files that the
ingest commands read from. These may contain:

- Staffing allocations and salaries
- Client names and engagement details
- Internal deal pipeline information
- Raw Gmail and Calendar content

**None of these should ever be committed.** The `.gitkeep` placeholder is the
only tracked file; all actual data files are excluded by the wildcard rule.

### Processed database (`data/processed/`)

```
data/processed/
```

The production DuckDB file at `data/processed/manager_os.duckdb` contains the
entire extracted knowledge graph — signals, action items, people data, client
status, and decision history. Committing it would expose all of that in
version control history.

### Demo database (`data/demo/`)

```
data/demo/
```

The demo DuckDB file is also excluded. Although it contains only fixture data
in normal use, the exclusion prevents a `--yes-demo` run against real paths
from accidentally committing the result.

### Generated output (`output/`)

```
output/
```

The `output/` directory contains generated markdown briefs, EOD closeouts,
weekly exec updates, and meeting prep documents. These derive from real data
and may contain sensitive management commentary.

### DuckDB files (all locations)

```
*.duckdb
*.duckdb.wal
```

Belt-and-suspenders catch for any DuckDB file that ends up in an unexpected
location.

### GWS snapshots

GWS snapshot JSON files belong in `data/raw/gws_snapshots/` which is already
covered by the `data/raw/*` rule. Never place GWS snapshots inside `tests/`
or anywhere else in the repository.

---

## 4. Disabling Streamlit telemetry

Streamlit collects anonymous usage statistics by default. To opt out, create
(or edit) `~/.streamlit/config.toml`:

```toml
[browser]
gatherUsageStats = false
```

This setting applies globally to all Streamlit apps on your machine. Restart
the dashboard after changing it.

---

## 5. Safe testing with fake fixtures

All automated tests use in-memory DuckDB databases (`:memory:`) or temporary
files created by pytest's `tmp_path` fixture. They never read from or write to
your production paths or `.env` settings.

The golden E2E test (`tests/test_golden_e2e.py`) and demo-reset tests
(`tests/test_cli_demo_reset.py`) inject fixture paths via environment variables
and are fully self-contained.

To run the test suite safely at any time:

```bash
source .venv/bin/activate
pytest tests/ -q
```

No `.env` is required for tests. If a `.env` is present, the
`MANAGER_OS_*` environment variable overrides passed in by the test harness
take precedence within each test invocation.

To confirm the sample workflow end-to-end without touching your production
database:

```bash
manager-os demo-reset --date 2026-06-13
manager-os status
```

---

## 6. Transitioning from sample data to real data

Follow these steps in order:

### Step 1 — Prepare your environment file

```bash
cp .env.example .env
```

Edit `.env` and set:

```dotenv
MANAGER_OS_VAULT_PATH=/absolute/path/to/your/obsidian/vault
MANAGER_OS_DB_PATH=./data/processed/manager_os.duckdb
MANAGER_OS_FORECAST_CSV=./data/raw/forecast.csv
MANAGER_OS_DEALS_CSV=./data/raw/deals.csv
MANAGER_OS_WORKSPACE_SUMMARY_DIR=./data/raw/summaries
MANAGER_OS_GWS_SNAPSHOT_DIR=./data/raw/gws_snapshots
```

### Step 2 — Place real CSV files

Copy your staffing forecast and deal pipeline exports into `data/raw/`:

```
data/raw/forecast.csv
data/raw/deals.csv
```

These files are excluded from version control by `.gitignore`.

### Step 3 — Verify with `manager-os status`

```bash
manager-os status
```

Confirm that:
- Mode shows **production** (not "sample data")
- Database path points to `data/processed/`
- The sample-data warning is absent

### Step 4 — Run the first ingest

```bash
manager-os ingest --date $(date +%F)
```

The first ingest against a real vault will take longer than the fixture run.
Check the Skipped/Failed counts. Use `--verbose` if anything looks unexpected.

### Step 5 — Extract and generate

```bash
manager-os extract --date $(date +%F)
manager-os brief   --date $(date +%F)
manager-os status
```

Review the brief. Confirm signal counts and entity names look correct.

### Step 6 — Optional LLM extraction

Only after you have confirmed the rule-based signals are working correctly,
and only if you are comfortable sending note text to an external API:

```bash
# Set your key in .env (never commit it)
# OPENAI_API_KEY=sk-...

manager-os extract --date $(date +%F) --mode llm
```

### Step 7 — Launch the dashboard

```bash
manager-os dashboard
# opens http://localhost:8501
```

---

## Quick checklist before using real data

- [ ] `.env` exists and is listed in `.gitignore`
- [ ] `~/.streamlit/config.toml` has `gatherUsageStats = false`
- [ ] `manager-os status` shows mode **production**
- [ ] No real notes, CSVs, or snapshots are in `tests/fixtures/`
- [ ] `git status` shows no untracked files under `data/` or `output/`
- [ ] LLM extraction is disabled (`--mode rules`) until explicitly reviewed
