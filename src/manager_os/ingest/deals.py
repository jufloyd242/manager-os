"""Deal status CSV ingestor."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

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


# ---------------------------------------------------------------------------
# Column maps
# ---------------------------------------------------------------------------

# NetSuite export column → canonical internal name.
_NETSUITE_COL_MAP: dict[str, str] = {
    "netsuite opportunity id": "deal_id",
    "netsuite customer": "account",
    "netsuite delivery comment": "delivery_comment",
    "netsuite next steps": "next_steps",
    "netsuite opportunity status": "stage",
    "netsuite expected close date": "close_date",
    "netsuite forecast category": "forecast_category",
    "netsuite probability (%)": "probability",
    "netsuite services ($)": "services_amount",
    "netsuite last status changed date": "last_status_changed_date",
}

_DEFAULT_COL_MAP = {
    # account / client
    "account": "account",
    "client": "account",
    "customer": "account",
    # deal name
    "deal": "deal_name",
    "deal name": "deal_name",
    "deal_name": "deal_name",
    "opportunity": "deal_name",
    "opportunity name": "deal_name",
    "deal id": "deal_id",
    "deal_id": "deal_id",
    # stage
    "stage": "stage",
    "status": "stage",
    "opportunity status": "stage",
    # dates
    "close date": "close_date",
    "close_date": "close_date",
    "expected close": "close_date",
    "expected close date": "close_date",
    "last status changed date": "last_status_changed_date",
    "last_status_changed_date": "last_status_changed_date",
    # people
    "tech owner": "technical_owner",
    "technical_owner": "technical_owner",
    "technical owner": "technical_owner",
    "engineer": "technical_owner",
    "ae": "ae_name",
    "ae_name": "ae_name",
    "account executive": "ae_name",
    # status fields
    "loe status": "loe_status",
    "loe_status": "loe_status",
    "sow status": "sow_status",
    "sow_status": "sow_status",
    "feasibility": "staffing_feasibility",
    "staffing_feasibility": "staffing_feasibility",
    # pipeline / opportunity fields
    "blockers": "blockers",
    "next action": "next_action",
    "next_action": "next_action",
    "next steps": "next_steps",
    "next_steps": "next_steps",
    "probability": "probability",
    "probability (%)": "probability",
    "forecast category": "forecast_category",
    "forecast_category": "forecast_category",
    "services": "services_amount",
    "services ($)": "services_amount",
    "services_amount": "services_amount",
    "delivery comment": "delivery_comment",
    "delivery_comment": "delivery_comment",
}


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def is_netsuite_format(csv_path: str) -> bool:
    """Return True if the CSV looks like a NetSuite deal export."""
    try:
        df = pd.read_csv(csv_path, nrows=0, dtype=str)
    except Exception:
        return False
    cols_lower = {str(c).strip().lower() for c in df.columns}
    return (
        "netsuite opportunity id" in cols_lower
        and "netsuite customer" in cols_lower
    )


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_date(val: str) -> Optional[date]:
    """Parse a date string, including natural-language formats like 'Jun 19, 2026'."""
    s = str(val).strip() if val else ""
    if not s or s.lower() in ("nan", "none", ""):
        return None
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return None


def _parse_probability(val: str) -> Optional[float]:
    """Parse probability: '0.75' → 0.75, '75%' → 0.75, '75.00%' → 0.75."""
    s = str(val).strip() if val else ""
    if not s or s.lower() in ("nan", "none", ""):
        return None
    try:
        if s.endswith("%"):
            return float(s.rstrip("%").strip()) / 100.0
        f = float(s)
        # Values > 1 are assumed to be percentages (e.g. 75 → 0.75)
        if f > 1.0:
            return f / 100.0
        return f
    except ValueError:
        return None


def _parse_services_amount(val: str) -> Optional[float]:
    """Parse services amount: '$213,960' → 213960.0, '213960' → 213960.0."""
    s = str(val).strip() if val else ""
    if not s or s.lower() in ("nan", "none", ""):
        return None
    # Remove $ and commas
    s = re.sub(r"[$,]", "", s).strip()
    try:
        return float(s)
    except ValueError:
        return None


def _derive_deal_name(account: str, deal_id: str) -> str:
    """Derive a display deal_name when no explicit name column exists."""
    account = (account or "").strip()
    deal_id = (deal_id or "").strip()
    if account and deal_id:
        return f"{account} - {deal_id}"
    return account or deal_id or ""


# ---------------------------------------------------------------------------
# Column normalization
# ---------------------------------------------------------------------------

def _normalize_columns(df: pd.DataFrame, extra_aliases: dict[str, str]) -> pd.DataFrame:
    """Normalize DataFrame column names to canonical internal names.

    Three-stage: extra_aliases first, then NetSuite map, then _DEFAULT_COL_MAP.
    """
    stage1 = {k.lower(): v.lower() for k, v in extra_aliases.items()}

    df.columns = [str(c).strip() for c in df.columns]

    # Stage 1: user-supplied aliases
    rename1: dict[str, str] = {}
    for col in df.columns:
        target = stage1.get(col.lower())
        if target and target != col.lower():
            rename1[col] = target
    if rename1:
        df = df.rename(columns=rename1)

    # Stage 2: NetSuite-specific names (before generic map to avoid conflicts)
    rename_ns: dict[str, str] = {}
    for col in df.columns:
        canonical = _NETSUITE_COL_MAP.get(col.lower())
        if canonical and canonical != col:
            rename_ns[col] = canonical
    if rename_ns:
        df = df.rename(columns=rename_ns)

    # Stage 3: generic canonical names
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

    netsuite = is_netsuite_format(csv_path)

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

    # For NetSuite format, derive deal_name from account + deal_id if not present.
    if "deal_name" not in df.columns:
        if netsuite and "deal_id" in df.columns:
            df["deal_name"] = df.apply(
                lambda r: _derive_deal_name(
                    str(r.get("account", "")).strip(),
                    str(r.get("deal_id", "")).strip(),
                ),
                axis=1,
            )
        else:
            raise ValueError(
                f"Deals CSV is missing a 'deal_name' (or 'deal') column. "
                f"Available columns after normalization: {list(df.columns)}"
            )

    now = datetime.utcnow()
    source_format = "netsuite" if netsuite else "normalized"

    for idx, raw_row in df.iterrows():
        try:
            row_dict: dict = {k: v for k, v in raw_row.dropna().items()}

            # Parse close_date
            close_raw = str(row_dict.get("close_date", "")).strip()
            if close_raw and close_raw.lower() not in ("nan", "none", ""):
                parsed = _parse_date(close_raw)
                if parsed is None:
                    raise ValueError(f"Cannot parse close_date: '{close_raw}'")
                row_dict["close_date"] = parsed
            elif "close_date" in row_dict:
                del row_dict["close_date"]

            # Parse last_status_changed_date
            lsc_raw = str(row_dict.get("last_status_changed_date", "")).strip()
            if lsc_raw and lsc_raw.lower() not in ("nan", "none", ""):
                parsed_lsc = _parse_date(lsc_raw)
                if parsed_lsc is not None:
                    row_dict["last_status_changed_date"] = parsed_lsc
                else:
                    del row_dict["last_status_changed_date"]
            elif "last_status_changed_date" in row_dict:
                del row_dict["last_status_changed_date"]

            # Parse probability
            prob_raw = str(row_dict.get("probability", "")).strip()
            if prob_raw and prob_raw.lower() not in ("nan", "none", ""):
                parsed_prob = _parse_probability(prob_raw)
                if parsed_prob is not None:
                    row_dict["probability"] = parsed_prob
                else:
                    del row_dict["probability"]
            elif "probability" in row_dict:
                del row_dict["probability"]

            # Parse services_amount
            svc_raw = str(row_dict.get("services_amount", "")).strip()
            if svc_raw and svc_raw.lower() not in ("nan", "none", ""):
                parsed_svc = _parse_services_amount(svc_raw)
                if parsed_svc is not None:
                    row_dict["services_amount"] = parsed_svc
                else:
                    del row_dict["services_amount"]
            elif "services_amount" in row_dict:
                del row_dict["services_amount"]

            # Strip fields not in DealRow to avoid Pydantic errors for NetSuite extras
            known_fields = set(DealRow.model_fields.keys())
            row_dict = {k: v for k, v in row_dict.items() if k in known_fields}

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
                    (id, account, deal_name, deal_id, stage, close_date,
                     technical_owner, ae_name, requested_roles, loe_status,
                     sow_status, staffing_feasibility, blockers, next_action,
                     next_steps, delivery_comment, forecast_category,
                     probability, services_amount, last_status_changed_date,
                     source_format, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.id,
                    row.account,
                    row.deal_name,
                    row.deal_id,
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
                    row.next_steps,
                    row.delivery_comment,
                    row.forecast_category,
                    row.probability,
                    row.services_amount,
                    row.last_status_changed_date,
                    source_format,
                    row.updated_at,
                ],
            )
            result.ingested += 1

        except Exception as exc:
            logger.warning("Deal row %s failed validation: %s", idx, exc)
            result.failed += 1
            result.errors.append(f"Row {idx}: {exc}")

    return result

