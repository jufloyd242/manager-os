"""DuckDB connection, schema initialization, and helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb


# ---------------------------------------------------------------------------
# DDL — all tables defined here
# ---------------------------------------------------------------------------

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS raw_documents (
    id                VARCHAR PRIMARY KEY,
    ingested_at       TIMESTAMP NOT NULL,
    source_type       VARCHAR NOT NULL,
    source_path       VARCHAR NOT NULL,
    file_modified_at  TIMESTAMP,
    content_hash      VARCHAR NOT NULL,
    content           VARCHAR NOT NULL,
    metadata          JSON
);

CREATE TABLE IF NOT EXISTS people (
    id                    VARCHAR PRIMARY KEY,
    name                  VARCHAR NOT NULL,
    aliases               JSON,
    role                  VARCHAR,
    level                 VARCHAR,
    current_client        VARCHAR,
    allocation_pct        FLOAT,
    next_availability_date DATE,
    last_1on1_date        DATE,
    morale_signal         VARCHAR,
    growth_topic          VARCHAR,
    blockers              VARCHAR,
    updated_at            TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id                      VARCHAR PRIMARY KEY,
    name                    VARCHAR NOT NULL,
    aliases                 JSON,
    health                  VARCHAR,
    current_team            JSON,
    last_update_date        DATE,
    open_risks              JSON,
    client_sentiment        VARCHAR,
    next_milestone          VARCHAR,
    unresolved_decisions    JSON,
    updated_at              TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id                   VARCHAR PRIMARY KEY,
    account              VARCHAR NOT NULL,
    deal_name            VARCHAR NOT NULL,
    stage                VARCHAR,
    close_date           DATE,
    technical_owner      VARCHAR,
    ae_name              VARCHAR,
    requested_roles      JSON,
    loe_status           VARCHAR,
    sow_status           VARCHAR,
    staffing_feasibility VARCHAR,
    blockers             VARCHAR,
    next_action          VARCHAR,
    updated_at           TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS engagements (
    id          VARCHAR PRIMARY KEY,
    client_id   VARCHAR NOT NULL,
    name        VARCHAR NOT NULL,
    start_date  DATE,
    end_date    DATE,
    status      VARCHAR,
    team        JSON,
    updated_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS staffing_forecast (
    id            VARCHAR PRIMARY KEY,
    person_id     VARCHAR,
    person_name   VARCHAR NOT NULL,
    week_start    DATE NOT NULL,
    client        VARCHAR,
    project       VARCHAR,
    allocation_pct FLOAT,
    forecast_type VARCHAR,
    notes         VARCHAR,
    ingested_at   TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    id              VARCHAR PRIMARY KEY,
    meeting_date    DATE NOT NULL,
    start_time      VARCHAR,
    title           VARCHAR NOT NULL,
    attendees       JSON,
    linked_entities JSON,
    source          VARCHAR,
    external_id     VARCHAR,
    updated_at      TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id              VARCHAR PRIMARY KEY,
    raw_document_id VARCHAR NOT NULL,
    note_date       DATE,
    note_type       VARCHAR,
    entity_type     VARCHAR,
    entity_name     VARCHAR,
    title           VARCHAR,
    body            VARCHAR,
    tags            JSON,
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id                         VARCHAR PRIMARY KEY,
    signal_date                DATE NOT NULL,
    source                     VARCHAR NOT NULL,
    source_path                VARCHAR,
    entity_type                VARCHAR NOT NULL,
    entity_name                VARCHAR NOT NULL,
    signal_type                VARCHAR NOT NULL,
    severity                   VARCHAR NOT NULL,
    summary                    VARCHAR NOT NULL,
    why_it_matters             VARCHAR,
    requires_manager_attention BOOLEAN NOT NULL DEFAULT FALSE,
    owner                      VARCHAR,
    due_date                   DATE,
    confidence                 FLOAT NOT NULL DEFAULT 1.0,
    status                     VARCHAR NOT NULL DEFAULT 'open',
    created_at                 TIMESTAMP NOT NULL,
    updated_at                 TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS action_items (
    id              VARCHAR PRIMARY KEY,
    signal_id       VARCHAR,
    source_note_id  VARCHAR,
    assigned_to     VARCHAR NOT NULL,
    description     VARCHAR NOT NULL,
    due_date        DATE,
    status          VARCHAR NOT NULL DEFAULT 'open',
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id             VARCHAR PRIMARY KEY,
    entity_type    VARCHAR,
    entity_name    VARCHAR,
    description    VARCHAR NOT NULL,
    decision_date  DATE,
    status         VARCHAR NOT NULL DEFAULT 'open',
    owner          VARCHAR,
    source_note_id VARCHAR,
    created_at     TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_briefs (
    id          VARCHAR PRIMARY KEY,
    brief_date  DATE NOT NULL,
    content     VARCHAR NOT NULL,
    signal_ids  JSON,
    created_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_prep (
    id           VARCHAR PRIMARY KEY,
    meeting_id   VARCHAR NOT NULL,
    content      VARCHAR NOT NULL,
    generated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_failures (
    id              VARCHAR PRIMARY KEY,
    failed_at       TIMESTAMP NOT NULL,
    source_path     VARCHAR,
    prompt_used     VARCHAR,
    raw_llm_output  VARCHAR,
    error_type      VARCHAR NOT NULL,
    error_detail    VARCHAR,
    status          VARCHAR NOT NULL DEFAULT 'pending_review'
);

CREATE TABLE IF NOT EXISTS signal_status_log (
    id          VARCHAR PRIMARY KEY,
    signal_id   VARCHAR NOT NULL,
    old_status  VARCHAR NOT NULL,
    new_status  VARCHAR NOT NULL,
    changed_at  TIMESTAMP NOT NULL,
    changed_by  VARCHAR NOT NULL DEFAULT 'dashboard',
    note        VARCHAR
);
"""

# Migrations applied after the main DDL.  Each statement must be idempotent.
_MIGRATIONS_DDL = """
ALTER TABLE signals ADD COLUMN IF NOT EXISTS rating VARCHAR;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS snooze_until DATE;
CREATE TABLE IF NOT EXISTS forecast_pipeline_demand (
    id VARCHAR PRIMARY KEY,
    source_section VARCHAR,
    week_start DATE,
    prospect_or_deal VARCHAR,
    probability FLOAT,
    requested_allocation FLOAT,
    skillset VARCHAR,
    demand_hours FLOAT,
    candidate_people JSON,
    staffing_status VARCHAR,
    record_type VARCHAR,
    forecast_type VARCHAR,
    source_row INTEGER,
    notes VARCHAR,
    ingested_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS forecast_summary_metric (
    id VARCHAR PRIMARY KEY,
    source_section VARCHAR,
    week_start DATE,
    metric_name VARCHAR,
    metric_value FLOAT,
    raw_value VARCHAR,
    record_type VARCHAR,
    source_row INTEGER,
    ingested_at TIMESTAMP NOT NULL
);
ALTER TABLE deals ADD COLUMN IF NOT EXISTS deal_id VARCHAR;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS next_steps VARCHAR;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS delivery_comment VARCHAR;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS forecast_category VARCHAR;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS probability FLOAT;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS services_amount FLOAT;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS last_status_changed_date DATE;
ALTER TABLE deals ADD COLUMN IF NOT EXISTS source_format VARCHAR
"""

_ALL_TABLES = [
    "raw_documents",
    "people",
    "clients",
    "deals",
    "engagements",
    "staffing_forecast",
    "forecast_pipeline_demand",
    "forecast_summary_metric",
    "meetings",
    "notes",
    "signals",
    "action_items",
    "decisions",
    "daily_briefs",
    "meeting_prep",
    "extraction_failures",
    "signal_status_log",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_connection(db_path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Return an open DuckDB connection, initializing the schema on first use."""
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(db_path)
    init_schema(conn)
    return conn


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create all tables if they don't already exist, then apply migrations."""
    conn.executemany("", [])  # no-op to ensure connection is alive
    for statement in _SCHEMA_DDL.strip().split(";\n\n"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)
    # Idempotent column additions / schema migrations
    for statement in _MIGRATIONS_DDL.strip().split(";"):
        stmt = statement.strip()
        if stmt:
            conn.execute(stmt)


def content_hash(text: str) -> str:
    """Return a SHA-256 hex digest of the given text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def list_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Return a sorted list of all table names in the database."""
    rows = conn.execute("SHOW TABLES").fetchall()
    return sorted(row[0] for row in rows)


def seed_from_config(conn: duckdb.DuckDBPyConnection, settings) -> dict[str, int]:
    """Populate the people and clients tables from YAML config.

    This is idempotent — rows are INSERT OR IGNORE so existing data
    (enriched by ingest/extract) is never overwritten.

    Args:
        conn: Open DuckDB connection.
        settings: Settings object with config_dir.

    Returns:
        Dict with counts {"people": N, "clients": N}.
    """
    import json
    from datetime import datetime

    try:
        from manager_os.config import load_people, load_clients
    except ImportError:
        return {"people": 0, "clients": 0}

    try:
        people = load_people(settings)
    except Exception:
        people = []

    try:
        clients = load_clients(settings)
    except Exception:
        clients = []

    now = datetime.utcnow()
    seeded = {"people": 0, "clients": 0}

    for p in people:
        person_id = content_hash(f"config::person::{p.name}")
        existing = conn.execute("SELECT id FROM people WHERE id = ?", [person_id]).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO people
                (id, name, aliases, role, level, current_client, allocation_pct,
                 next_availability_date, last_1on1_date, morale_signal,
                 growth_topic, blockers, updated_at)
            VALUES (?, ?, ?, ?, ?, '', NULL, NULL, NULL, 'green', '', '', ?)
            """,
            [person_id, p.name, json.dumps(p.aliases), p.role, p.level, now],
        )
        seeded["people"] += 1

    for c in clients:
        client_id = content_hash(f"config::client::{c.name}")
        existing = conn.execute("SELECT id FROM clients WHERE id = ?", [client_id]).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO clients
                (id, name, aliases, health, current_team, last_update_date,
                 open_risks, client_sentiment, next_milestone, unresolved_decisions, updated_at)
            VALUES (?, ?, ?, 'green', '[]', NULL, '[]', 'neutral', '', '[]', ?)
            """,
            [client_id, c.name, json.dumps(c.aliases), now],
        )
        seeded["clients"] += 1

    return seeded
