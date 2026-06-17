# Project Index Documentation

## Overview

The Manager OS project index provides a searchable knowledge base of historical closed-won opportunities, enabling delivery intelligence and similar project matching for prospective deals.

## Source of Truth

**Primary Source**: NetSuite AI/ML Closed-Won Opportunities Google Sheet
- Sheet ID: `1qoxa7kh5UPs8yCs6UIAbR9_n_odnYl1lByJi4F85P5A`
- GID: `326551622`
- Export URL: `https://docs.google.com/spreadsheets/d/1qoxa7kh5UPs8yCs6UIAbR9_n_odnYl1lByJi4F85P5A/export?format=csv&gid=326551622`

**Important**: The local CSV is a cache/output only. The Google Sheet is the authoritative source.

## Architecture

### Data Flow

1. **Fetch**: `manager-os project-index-fetch` retrieves the sheet via Gemini CLI
2. **Parse**: Deterministic CSV parser extracts project records
3. **Store**: Projects upserted into `projects` table with OppID as primary key
4. **Enrich**: Optional Google Drive document search by OppID
5. **Search**: Full-text and faceted search across project data

### Key Design Decisions

- **OppID is the primary join key**: All project records use `project::{OppID}` as the stable ID
- **No fallback to local CSV**: If sheet fetch fails, the command fails (unless `--skip-fetch` is used)
- **Metadata validation**: Provenance metadata (`.meta.json`) tracks freshness and integrity
- **Technology extraction**: Automatic extraction from project type, descriptions, and keywords
- **Document enrichment**: Google Drive search finds SOWs, deal sheets, and other artifacts

## Configuration

### Environment Variables

```bash
# Project index source configuration
MANAGER_OS_PROJECT_INDEX_SOURCE=google_sheet_gemini
MANAGER_OS_PROJECT_INDEX_SHEET_URL=https://docs.google.com/spreadsheets/d/1qoxa7kh5UPs8yCs6UIAbR9_n_odnYl1lByJi4F85P5A/edit?gid=326551622#gid=326551622
MANAGER_OS_PROJECT_INDEX_SHEET_ID=1qoxa7kh5UPs8yCs6UIAbR9_n_odnYl1lByJi4F85P5A
MANAGER_OS_PROJECT_INDEX_SHEET_GID=326551622
MANAGER_OS_PROJECT_INDEX_EXPORT_URL=https://docs.google.com/spreadsheets/d/1qoxa7kh5UPs8yCs6UIAbR9_n_odnYl1lByJi4F85P5A/export?format=csv&gid=326551622
MANAGER_OS_PROJECT_INDEX_LOCAL_CSV=./data/raw/project_index.csv
MANAGER_OS_PROJECT_INDEX_DOWNLOAD_TIMEOUT_SECONDS=180
MANAGER_OS_PROJECT_INDEX_STALE_AFTER_HOURS=24
MANAGER_OS_PROJECT_INDEX_REQUIRE_EXACT_SOURCE=true
MANAGER_OS_PROJECT_DOC_SEARCH_ENABLED=true
MANAGER_OS_PROJECT_DOC_SEARCH_LIMIT_PER_PROJECT=10
```

## CSV Schema

The NetSuite sheet contains the following columns:

| Column | Description | Example |
|--------|-------------|---------|
| Year | Fiscal year | 2024 |
| Month | Fiscal month | 3 |
| Services ($) | Services amount/bookings | $267,000 |
| OppID | Opportunity number (primary key) | OPP030034 |
| Close Date | Historical close date | 3/15/2024 |
| Sales Rep | Sales owner | Charlie Lisk |
| Customer | Client/account name | Acme Corp |
| Opp Name | Project/deal name | GenAI Chatbot Implementation |
| Services Delivery Team | Delivery team category | AI/ML |
| Solution Pillar | High-level solution category | GenAI |
| Type | Project type classification | GenAI, ADK, CES, ML, etc. |
| Industry | Industry vertical | Retail, Finance, Healthcare |
| 3-5 words | Short description | AI-powered customer support |
| 1-2 sentences | Project summary | Implemented a GenAI-powered chatbot... |

### Project Types

- **GenAI**: Generative AI projects (Gemini, LLM, RAG, chatbot, agent)
- **ADK**: Agent Development Kit projects
- **CES**: Contact Center AI (CCAI, Dialogflow CX, support automation)
- **ML**: Machine Learning (Vertex AI, embeddings, XGBoost, recommendation)
- **Search**: Vertex AI Search, enterprise search, semantic search
- **Media Rec**: Recommendations AI for media
- **Retail Rec**: Recommendations AI for retail
- **DocAI**: Document AI, Doc AI

## Commands

### Fetch Project Index

```bash
# Fetch the project sheet (deterministic, no fallback)
manager-os project-index-fetch

# Dry run to see what would be fetched
manager-os project-index-fetch --dry-run

# Print the Gemini CLI prompt
manager-os project-index-fetch --print-prompt

# Print the export URL
manager-os project-index-fetch --print-url

# Force refresh even if fresh
manager-os project-index-fetch --force
```

### Index Projects

```bash
# Index projects from the sheet (default behavior)
manager-os index-projects

# Skip the fetch step (use existing CSV)
manager-os index-projects --skip-fetch

# Skip Google Drive document enrichment
manager-os index-projects --skip-drive-enrichment

# Enable notes enrichment (not primary source)
manager-os index-projects --notes-enrichment

# Force re-index all projects
manager-os index-projects --force

# Verbose output
manager-os index-projects --verbose

# Dry run
manager-os index-projects --dry-run
```

### Search Projects

```bash
# Free text search
manager-os search-projects "ADK"

# Filter by project type
manager-os search-projects "" --type GenAI

# Filter by industry
manager-os search-projects "" --industry Retail

# Filter by sales rep
manager-os search-projects "" --sales-rep "Charlie Lisk"

# Filter by opportunity number
manager-os search-projects "" --opportunity-number OPP032106

# Filter by technology
manager-os search-projects "Vertex AI" --technology BigQuery

# Filter by year
manager-os search-projects "" --year 2024

# JSON output
manager-os search-projects "ADK" --json

# Combined filters
manager-os search-projects "chatbot" --type GenAI --industry Retail --year 2024
```

### Fetch Project Documents

```bash
# Fetch documents for a specific project
manager-os project-docs-fetch --opportunity-number OPP032106

# Fetch documents for all projects
manager-os project-docs-fetch

# Dry run
manager-os project-docs-fetch --dry-run

# Print the Gemini CLI prompt
manager-os project-docs-fetch --print-prompt
```

### Match Similar Projects

```bash
# Find similar projects for a deal
manager-os match-projects --deal-id <deal_id>

# Find similar projects by opportunity number
manager-os match-projects --opportunity-number OPP032106

# Limit results
manager-os match-projects --deal-id <deal_id> --limit 10

# JSON output
manager-os match-projects --deal-id <deal_id> --json

# Verbose output
manager-os match-projects --deal-id <deal_id> --verbose
```

## Database Schema

### projects table

```sql
CREATE TABLE projects (
    id VARCHAR PRIMARY KEY,  -- project::{OppID}
    project_name VARCHAR,
    client VARCHAR,
    opportunity_number VARCHAR,
    deal_id VARCHAR,
    status VARCHAR,
    start_date DATE,
    end_date DATE,
    technologies_json JSON,
    skills_json JSON,
    team_members_json JSON,
    summary VARCHAR,
    outcome VARCHAR,
    lessons_learned VARCHAR,
    risks_json JSON,
    reusable_artifacts_json JSON,
    source_urls_json JSON,
    source_note_ids_json JSON,
    source_doc_ids_json JSON,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    -- NetSuite sheet specific fields
    source_system VARCHAR,
    source_sheet_url VARCHAR,
    source_sheet_gid VARCHAR,
    year INTEGER,
    month INTEGER,
    services_amount FLOAT,
    close_date DATE,
    sales_rep VARCHAR,
    services_delivery_team VARCHAR,
    solution_pillar VARCHAR,
    project_type VARCHAR,
    industry VARCHAR,
    short_description VARCHAR,
    source_row INTEGER,
    summary_is_generated BOOLEAN DEFAULT FALSE
);
```

### project_documents table

```sql
CREATE TABLE project_documents (
    id VARCHAR PRIMARY KEY,  -- project_document::{project_id}::{document_type}::{url}
    project_id VARCHAR,
    opportunity_number VARCHAR,
    client VARCHAR,
    project_name VARCHAR,
    document_type VARCHAR,  -- sow, deal_sheet, closure_presentation, etc.
    title VARCHAR,
    url VARCHAR,
    source VARCHAR,
    retrieved_at TIMESTAMP,
    search_status VARCHAR,
    confidence FLOAT,
    why_matched VARCHAR,
    error VARCHAR
);
```

## Similar Project Matching

The similar project matcher uses a weighted scoring system:

| Factor | Weight | Description |
|--------|--------|-------------|
| Client/customer match | 20 | Exact client name match |
| Technology/type overlap | 15 per match | Matching technologies or project types |
| Industry match | 10 | Same industry vertical |
| Deal name keywords | 5 per match | Keyword overlap in deal/project names |
| Summary keywords | 3 per match | Keyword overlap in descriptions |
| Delivery team match | 8 | Same services delivery team |
| Solution pillar match | 8 | Same solution pillar |
| Lessons/risks overlap | 2 per match | Overlapping lessons or risks |

## Dashboard Integration

The Projects tab in the Streamlit dashboard provides:

- **Search interface**: Free text search with faceted filters
- **Project cards**: Expandable cards showing project details
- **Related documents**: Links to SOWs, deal sheets, and other artifacts
- **Technology badges**: Visual indicators of technologies used
- **Freshness indicator**: Warning if project index is stale

## Provenance and Freshness

### Metadata File

The `.meta.json` file tracks:

```json
{
  "source": "google_sheet_project_index",
  "sheet_url": "https://docs.google.com/spreadsheets/d/...",
  "sheet_id": "1qoxa7kh5UPs8yCs6UIAbR9_n_odnYl1lByJi4F85P5A",
  "gid": "326551622",
  "retrieved_at": "2026-06-17T10:30:00",
  "local_csv_path": "./data/raw/project_index.csv",
  "row_count": 119,
  "content_hash": "abc123..."
}
```

### Freshness Check

- Default staleness threshold: 24 hours
- Configurable via `MANAGER_OS_PROJECT_INDEX_STALE_AFTER_HOURS`
- Dashboard shows warning if index is stale
- Commands fail if metadata is missing or stale (unless `--skip-fetch` is used)

## Error Handling

### Common Errors

1. **Sheet access denied**: Verify Google account has access to the sheet
2. **Metadata missing**: Run `manager-os project-index-fetch` to initialize
3. **Stale index**: Run `manager-os project-index-fetch --force` to refresh
4. **Invalid CSV**: Check CSV format matches expected schema
5. **Duplicate OppIDs**: Parser handles duplicates by updating existing records

### Recovery

```bash
# Re-fetch the sheet
manager-os project-index-fetch --force

# Re-index all projects
manager-os index-projects --force

# Verify metadata
cat ./data/raw/project_index.csv.meta.json
```

## Best Practices

1. **Run daily**: Include `manager-os project-index-fetch` in your daily workflow
2. **Monitor freshness**: Check dashboard for staleness warnings
3. **Enrich with documents**: Run `manager-os project-docs-fetch` periodically
4. **Use in deal prep**: Run `manager-os match-projects` before deal meetings
5. **Keep sheet updated**: Ensure NetSuite sheet is current before fetching

## Limitations

- **No vector search**: Current implementation uses keyword matching only
- **No automatic document download**: Only metadata is stored, not full documents
- **No notes as primary source**: Notes can enrich but not create projects
- **No real-time sync**: Manual fetch required to update index
- **Gemini CLI dependency**: Requires Gemini CLI to be configured and authenticated

## Future Enhancements

- Vector search for semantic similarity
- Automatic document content extraction
- Bidirectional sync with NetSuite
- Project timeline visualization
- Team member expertise mapping
- Risk pattern detection across projects
