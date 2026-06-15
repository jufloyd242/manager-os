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
    """Ingest a wide-format planning spreadsheet CSV."""
    from manager_os.ingest.forecast_wide import parse_wide_forecast

    result = IngestResult()
    parse_result = parse_wide_forecast(csv_path)

    now = datetime.utcnow()

    for record in parse_result.capacity_records:
        try:
            notes = f"section={record.section}"
            if record.target_hours is not None:
                notes += f" target_hours={record.target_hours}"

            row = StaffingForecastRow(
                person_name=record.person_name,
                week_start=record.week_start,
                client="",
                project=record.section,
                allocation_pct=record.allocation,
                forecast_type="capacity",  # type: ignore[arg-type]
                notes=notes,
                ingested_at=now,
            )
            row_id = content_hash(
                f"{row.person_name}::{row.week_start}::{row.project}::capacity"
            )
            row = row.model_copy(update={"id": row_id})

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
                    row.id, row.person_id, row.person_name, row.week_start,
                    row.client, row.project, row.allocation_pct,
                    row.forecast_type, row.notes, row.ingested_at,
                ],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Wide capacity row failed: %s", exc)
            result.failed += 1
            result.errors.append(str(exc))

    for record in parse_result.pipeline_records:
        # Skip records with no assignee (ambiguous)
        if not record.person_name.strip():
            result.skipped += 1
            result.skip_reasons["ambiguous_assignee"] = (
                result.skip_reasons.get("ambiguous_assignee", 0) + 1
            )
            continue

        try:
            notes_parts = [f"section={record.section}"]
            if record.probability is not None:
                notes_parts.append(f"probability={record.probability}")
            if record.requested_alloc is not None:
                notes_parts.append(f"requested_alloc={record.requested_alloc}")
            if record.skillset:
                notes_parts.append(f"skillset={record.skillset}")
            notes_parts.append(f"prospect={record.prospect_label!r}")

            row = StaffingForecastRow(
                person_name=record.person_name,
                week_start=record.week_start,
                client=record.prospect_label,  # stored but NOT validated vs clients.yaml
                project=record.skillset or record.section,
                allocation_pct=record.allocation,
                forecast_type="pipeline",
                notes=" ".join(notes_parts),
                ingested_at=now,
            )
            row_id = content_hash(
                f"{row.person_name}::{row.week_start}::{record.prospect_label}::pipeline"
            )
            row = row.model_copy(update={"id": row_id})

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
                    row.id, row.person_id, row.person_name, row.week_start,
                    row.client, row.project, row.allocation_pct,
                    row.forecast_type, row.notes, row.ingested_at,
                ],
            )
            result.ingested += 1
        except Exception as exc:
            logger.warning("Wide pipeline row failed: %s", exc)
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
