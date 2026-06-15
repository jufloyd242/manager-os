"""Staffing forecast CSV ingestor."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from manager_os.config import SourcePriorityConfig
from manager_os.db import content_hash
from manager_os.schemas import StaffingForecastRow

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)


# Default canonical column name mapping
_DEFAULT_COL_MAP = {
    "person": "person_name",
    "name": "person_name",
    "employee": "person_name",
    "week": "week_start",
    "week start": "week_start",
    "week_start": "week_start",
    "client": "client",
    "project": "project",
    "engagement": "project",
    "allocation": "allocation_pct",
    "allocation %": "allocation_pct",
    "pct allocated": "allocation_pct",
    "allocation_pct": "allocation_pct",
    "type": "forecast_type",
    "status": "forecast_type",
    "forecast_type": "forecast_type",
    "notes": "notes",
}


def _normalize_columns(df: pd.DataFrame, extra_aliases: dict[str, str]) -> pd.DataFrame:
    """Normalize DataFrame column names to canonical internal names.

    Two-stage resolution:
    1. Apply extra_aliases (from source_priority YAML) to handle non-standard names
    2. Apply _DEFAULT_COL_MAP to get the final canonical name
    """
    # Stage 1: rename raw columns using extra_aliases (e.g. "Person" → "person")
    stage1 = {k.lower(): v.lower() for k, v in extra_aliases.items()}

    df.columns = [str(c).strip() for c in df.columns]
    rename1: dict[str, str] = {}
    for col in df.columns:
        target = stage1.get(col.lower())
        if target and target != col.lower():
            rename1[col] = target
    if rename1:
        df = df.rename(columns=rename1)

    # Stage 2: apply default canonical map (e.g. "person" → "person_name")
    rename2: dict[str, str] = {}
    for col in df.columns:
        canonical = _DEFAULT_COL_MAP.get(col.lower())
        if canonical and canonical != col:
            rename2[col] = canonical
    return df.rename(columns=rename2)


def _row_stable_id(person_name: str, week_start: str) -> str:
    return content_hash(f"{person_name}::{week_start}")


def _row_exists(conn, row_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM staffing_forecast WHERE id = ?", [row_id]
    ).fetchone()
    return row is not None


def ingest_forecast(
    csv_path: str,
    conn,
    source_priority: SourcePriorityConfig | None = None,
    force: bool = False,
) -> IngestResult:
    """Ingest a staffing forecast CSV into the staffing_forecast table.

    Auto-detects whether the CSV is in normalized long format or wide
    planning-spreadsheet format (AI/ML sections).

    Args:
        csv_path: Path to the CSV file.
        conn: Open DuckDB connection.
        source_priority: Optional SourcePriorityConfig with column alias overrides.
        force: If True, re-ingest all rows even if they already exist.
    """
    from manager_os.ingest.forecast_wide import is_wide_format

    if is_wide_format(csv_path):
        return _ingest_wide_forecast(csv_path, conn, force=force)

    return _ingest_normalized_forecast(csv_path, conn, source_priority=source_priority, force=force)


def _ingest_wide_forecast(
    csv_path: str,
    conn,
    force: bool = False,
) -> IngestResult:
    """Ingest a wide-format planning spreadsheet CSV.

    PersonForecastRecord  → staffing_forecast (forecast_type='capacity')
    PipelineDemandRecord  → forecast_pipeline_demand
    PipelineOpportunityRecord → forecast_pipeline_demand (week_start=NULL)
    SummaryMetricRecord   → forecast_summary_metric
    """
    from manager_os.ingest.forecast_wide import parse_wide_forecast

    result = IngestResult()
    parse_result = parse_wide_forecast(csv_path)
    now = datetime.utcnow()

    # PersonForecastRecord → staffing_forecast
    for record in parse_result.person_forecast:
        try:
            row = StaffingForecastRow(
                person_name=record.person_name,
                week_start=record.week_start,
                client="",
                project=record.source_section,
                allocation_pct=record.planned_hours,
                forecast_type="capacity",  # type: ignore[arg-type]
                notes=(
                    f"section={record.source_section}"
                    + (f" target_hours={record.target_hours}" if record.target_hours is not None else "")
                ),
                ingested_at=now,
            )
            row_id = content_hash(
                f"{row.person_name}::{row.week_start}::{row.project}::capacity"
            )
            row = row.model_copy(update={"id": row_id})
            if not force and _row_exists(conn, row.id):
                result.skipped += 1
                result.skip_reasons["already_exists"] = result.skip_reasons.get("already_exists", 0) + 1
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO staffing_forecast
                    (id, person_id, person_name, week_start, client, project,
                     allocation_pct, forecast_type, notes, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row.id, row.person_id, row.person_name, row.week_start,
                 row.client, row.project, row.allocation_pct,
                 row.forecast_type, row.notes, row.ingested_at],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Wide person_forecast row failed: %s", exc)
            result.failed += 1
            result.errors.append(str(exc))

    # PipelineDemandRecord → forecast_pipeline_demand
    for record in parse_result.pipeline_demand:
        try:
            row_id = content_hash(
                f"pd::{record.source_section}::{record.week_start}::"
                f"{record.prospect_or_deal}::{record.source_row}"
            )
            if not force:
                existing = conn.execute(
                    "SELECT id FROM forecast_pipeline_demand WHERE id = ?", [row_id]
                ).fetchone()
                if existing:
                    result.skipped += 1
                    result.skip_reasons["already_exists"] = result.skip_reasons.get("already_exists", 0) + 1
                    continue
            conn.execute(
                """
                INSERT OR REPLACE INTO forecast_pipeline_demand
                    (id, source_section, week_start, prospect_or_deal, probability,
                     requested_allocation, skillset, demand_hours, candidate_people,
                     staffing_status, record_type, forecast_type, source_row, notes, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row_id, record.source_section, record.week_start, record.prospect_or_deal,
                 record.probability, record.requested_allocation, record.skillset,
                 record.demand_hours, json.dumps(record.candidate_people),
                 record.staffing_status, record.record_type, record.forecast_type,
                 record.source_row, None, now],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Wide pipeline_demand row failed: %s", exc)
            result.failed += 1
            result.errors.append(str(exc))

    # PipelineOpportunityRecord → forecast_pipeline_demand (week_start=NULL)
    for record in parse_result.pipeline_opportunities:
        try:
            row_id = content_hash(
                f"po::{record.source_section}::{record.prospect_or_deal}::{record.source_row}"
            )
            if not force:
                existing = conn.execute(
                    "SELECT id FROM forecast_pipeline_demand WHERE id = ?", [row_id]
                ).fetchone()
                if existing:
                    result.skipped += 1
                    result.skip_reasons["already_exists"] = result.skip_reasons.get("already_exists", 0) + 1
                    continue
            conn.execute(
                """
                INSERT OR REPLACE INTO forecast_pipeline_demand
                    (id, source_section, week_start, prospect_or_deal, probability,
                     requested_allocation, skillset, demand_hours, candidate_people,
                     staffing_status, record_type, forecast_type, source_row, notes, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row_id, record.source_section, None, record.prospect_or_deal,
                 record.probability, record.requested_allocation, record.skillset,
                 0.0, json.dumps(record.candidate_people),
                 record.status, record.record_type, record.forecast_type,
                 record.source_row, None, now],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Wide pipeline_opportunity row failed: %s", exc)
            result.failed += 1
            result.errors.append(str(exc))

    # SummaryMetricRecord → forecast_summary_metric
    for record in parse_result.summary_metrics:
        try:
            row_id = content_hash(
                f"sm::{record.source_section}::{record.week_start}::"
                f"{record.metric_name}::{record.source_row}"
            )
            if not force:
                existing = conn.execute(
                    "SELECT id FROM forecast_summary_metric WHERE id = ?", [row_id]
                ).fetchone()
                if existing:
                    result.skipped += 1
                    result.skip_reasons["already_exists"] = result.skip_reasons.get("already_exists", 0) + 1
                    continue
            conn.execute(
                """
                INSERT OR REPLACE INTO forecast_summary_metric
                    (id, source_section, week_start, metric_name, metric_value,
                     raw_value, record_type, source_row, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row_id, record.source_section, record.week_start, record.metric_name,
                 record.metric_value, record.raw_value, record.record_type,
                 record.source_row, now],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Wide summary_metric row failed: %s", exc)
            result.failed += 1
            result.errors.append(str(exc))

    return result


def _ingest_normalized_forecast(
    csv_path: str,
    conn,
    source_priority: SourcePriorityConfig | None = None,
    force: bool = False,
) -> IngestResult:
    """Ingest a normalized long-format forecast CSV (original behaviour)."""
    result = IngestResult()
    extra_aliases = {}
    if source_priority:
        extra_aliases = source_priority.forecast_column_aliases

    try:
        df = pd.read_csv(csv_path, dtype=str)
    except Exception as exc:
        raise RuntimeError(f"Could not read forecast CSV at {csv_path}: {exc}") from exc

    df = _normalize_columns(df, extra_aliases)

    if "person_name" not in df.columns:
        raise ValueError(
            f"Forecast CSV is missing a 'person' column. "
            f"Available columns after normalization: {list(df.columns)}"
        )
    if "week_start" not in df.columns:
        raise ValueError(
            f"Forecast CSV is missing a 'week_start' (or 'week') column. "
            f"Available columns after normalization: {list(df.columns)}"
        )

    now = datetime.utcnow()

    for idx, raw_row in df.iterrows():
        try:
            row_dict = raw_row.dropna().to_dict()

            # Parse allocation_pct
            alloc_raw = row_dict.get("allocation_pct", "0")
            try:
                row_dict["allocation_pct"] = float(str(alloc_raw).replace("%", "").strip())
            except ValueError:
                row_dict["allocation_pct"] = 0.0

            # Parse week_start
            week_str = str(row_dict.get("week_start", "")).strip()
            try:
                week_date = pd.to_datetime(week_str).date()
                row_dict["week_start"] = week_date
            except Exception:
                raise ValueError(f"Cannot parse week_start: '{week_str}'")

            row = StaffingForecastRow(ingested_at=now, **row_dict)
            row_id = _row_stable_id(row.person_name, str(row.week_start))
            row_id_with_client = content_hash(
                f"{row.person_name}::{row.week_start}::{row.client}::{row.project}"
            )
            row = row.model_copy(update={"id": row_id_with_client})

            if not force and _row_exists(conn, row.id):
                result.skipped += 1
                result.skip_reasons["already_exists"] = (
                    result.skip_reasons.get("already_exists", 0) + 1
                )
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO staffing_forecast
                    (id, person_id, person_name, week_start, client, project,
                     allocation_pct, forecast_type, notes, ingested_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.id,
                    row.person_id,
                    row.person_name,
                    row.week_start,
                    row.client,
                    row.project,
                    row.allocation_pct,
                    row.forecast_type,
                    row.notes,
                    row.ingested_at,
                ],
            )
            result.ingested += 1

        except Exception as exc:
            logger.warning("Forecast row %s failed validation: %s", idx, exc)
            result.failed += 1
            result.errors.append(f"Row {idx}: {exc}")

    return result
