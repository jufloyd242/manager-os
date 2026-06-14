"""Deal status CSV ingestor."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from manager_os.config import SourcePriorityConfig
from manager_os.db import content_hash
from manager_os.schemas import DealRow

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    skip_reasons: dict[str, int] = field(default_factory=dict)


_DEFAULT_COL_MAP = {
    "account": "account",
    "deal": "deal_name",
    "deal name": "deal_name",
    "deal_name": "deal_name",
    "opportunity": "deal_name",
    "stage": "stage",
    "close date": "close_date",
    "close_date": "close_date",
    "expected close": "close_date",
    "tech owner": "technical_owner",
    "technical_owner": "technical_owner",
    "technical owner": "technical_owner",
    "engineer": "technical_owner",
    "ae": "ae_name",
    "ae_name": "ae_name",
    "account executive": "ae_name",
    "loe status": "loe_status",
    "loe_status": "loe_status",
    "sow status": "sow_status",
    "sow_status": "sow_status",
    "feasibility": "staffing_feasibility",
    "staffing_feasibility": "staffing_feasibility",
    "blockers": "blockers",
    "next action": "next_action",
    "next_action": "next_action",
}


def _normalize_columns(df: pd.DataFrame, extra_aliases: dict[str, str]) -> pd.DataFrame:
    """Normalize DataFrame column names to canonical internal names.

    Two-stage: extra_aliases first, then _DEFAULT_COL_MAP.
    """
    stage1 = {k.lower(): v.lower() for k, v in extra_aliases.items()}

    df.columns = [str(c).strip() for c in df.columns]
    rename1: dict[str, str] = {}
    for col in df.columns:
        target = stage1.get(col.lower())
        if target and target != col.lower():
            rename1[col] = target
    if rename1:
        df = df.rename(columns=rename1)

    rename2: dict[str, str] = {}
    for col in df.columns:
        canonical = _DEFAULT_COL_MAP.get(col.lower())
        if canonical and canonical != col:
            rename2[col] = canonical
    return df.rename(columns=rename2)


def _row_stable_id(account: str, deal_name: str) -> str:
    return content_hash(f"{account}::{deal_name}")


def _row_exists(conn, row_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM deals WHERE id = ?", [row_id]
    ).fetchone()
    return row is not None


def ingest_deals(
    csv_path: str,
    conn,
    source_priority: SourcePriorityConfig | None = None,
    force: bool = False,
) -> IngestResult:
    """Ingest a deal status CSV into the deals table.

    Args:
        csv_path: Path to the CSV file.
        conn: Open DuckDB connection.
        source_priority: Optional SourcePriorityConfig with column alias overrides.
        force: If True, re-ingest all rows even if they already exist.
    """
    result = IngestResult()
    extra_aliases = {}
    if source_priority:
        extra_aliases = source_priority.deal_column_aliases

    try:
        df = pd.read_csv(csv_path, dtype=str)
    except Exception as exc:
        raise RuntimeError(f"Could not read deals CSV at {csv_path}: {exc}") from exc

    df = _normalize_columns(df, extra_aliases)

    if "account" not in df.columns:
        raise ValueError(
            f"Deals CSV is missing an 'account' column. "
            f"Available columns after normalization: {list(df.columns)}"
        )
    if "deal_name" not in df.columns:
        raise ValueError(
            f"Deals CSV is missing a 'deal_name' (or 'deal') column. "
            f"Available columns after normalization: {list(df.columns)}"
        )

    now = datetime.utcnow()

    for idx, raw_row in df.iterrows():
        try:
            row_dict: dict = {k: v for k, v in raw_row.dropna().items()}

            # Parse close_date
            close_raw = row_dict.get("close_date", "")
            if close_raw:
                try:
                    row_dict["close_date"] = pd.to_datetime(str(close_raw)).date()
                except Exception:
                    raise ValueError(f"Cannot parse close_date: '{close_raw}'")

            row = DealRow(updated_at=now, **row_dict)
            row_id = _row_stable_id(row.account, row.deal_name)
            row = row.model_copy(update={"id": row_id})

            if not force and _row_exists(conn, row.id):
                result.skipped += 1
                result.skip_reasons["already_exists"] = (
                    result.skip_reasons.get("already_exists", 0) + 1
                )
                continue

            conn.execute(
                """
                INSERT OR REPLACE INTO deals
                    (id, account, deal_name, stage, close_date, technical_owner,
                     ae_name, requested_roles, loe_status, sow_status,
                     staffing_feasibility, blockers, next_action, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.id,
                    row.account,
                    row.deal_name,
                    row.stage,
                    row.close_date,
                    row.technical_owner,
                    row.ae_name,
                    json.dumps(row.requested_roles),
                    row.loe_status,
                    row.sow_status,
                    row.staffing_feasibility,
                    row.blockers,
                    row.next_action,
                    row.updated_at,
                ],
            )
            result.ingested += 1

        except Exception as exc:
            logger.warning("Deal row %s failed validation: %s", idx, exc)
            result.failed += 1
            result.errors.append(f"Row {idx}: {exc}")

    return result
