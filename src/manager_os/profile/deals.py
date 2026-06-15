"""Deals CSV profiler — read-only validation before ingest.

Reads headers and a sample of rows from a deals/SOW CSV, applies the
same column normalisation used by the ingestor, and returns a
:class:`DealsProfile` describing what was found.

Supports two CSV formats:
- ``normalized``: internal format with ``account``, ``deal_name``, etc.
- ``netsuite``: NetSuite opportunity export with ``NetSuite Opportunity ID``,
  ``NetSuite Customer``, etc.

No data is written to DuckDB and no files are modified.
"""

from __future__ import annotations

import json as _json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd

from manager_os.config import ClientConfig, SourcePriorityConfig
from manager_os.ingest.deals import (
    _normalize_columns,
    _parse_date,
    _parse_probability,
    _parse_services_amount,
    _derive_deal_name,
    is_netsuite_format,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Required canonical columns for the normalized format.
_REQUIRED_NORMALIZED: list[str] = ["account", "deal_name"]

# Required canonical columns for NetSuite format.
# deal_name is derived, so only deal_id + account are required.
_REQUIRED_NETSUITE: list[str] = ["deal_id", "account"]

# Optional canonical columns that are useful for signal extraction.
_OPTIONAL_CANONICAL: list[str] = [
    "deal_id",
    "stage",
    "close_date",
    "technical_owner",
    "ae_name",
    "loe_status",
    "sow_status",
    "staffing_feasibility",
    "blockers",
    "next_action",
    "next_steps",
    "delivery_comment",
    "forecast_category",
    "probability",
    "services_amount",
    "last_status_changed_date",
]

# Display labels for canonical names (shown in the output table).
_FIELD_DISPLAY: dict[str, str] = {
    "account": "account/client",
    "deal_name": "deal name",
    "deal_id": "deal ID",
    "stage": "stage",
    "close_date": "close date",
    "technical_owner": "owner",
    "ae_name": "AE/ECA",
    "loe_status": "LOE status",
    "sow_status": "SOW status",
    "staffing_feasibility": "staffing feasibility",
    "blockers": "blockers",
    "next_action": "next action",
    "next_steps": "next steps",
    "delivery_comment": "delivery comment",
    "forecast_category": "forecast category",
    "probability": "probability",
    "services_amount": "services ($)",
    "last_status_changed_date": "last status changed",
}

# SOW/LOE values that count as "present/resolved" for the profiler.
_SIGNED_VALUES: frozenset[str] = frozenset({"signed"})

_LATE_STAGES: frozenset[str] = frozenset({
    "sow review", "sow_review", "proposal",
    "negotiation", "commit", "closed",
})

# How many days ahead to flag an imminent close date.
_CLOSE_DATE_WARN_DAYS = 14

# Days without status change before flagging as stale
_STALE_STATUS_DAYS = 30

# Maximum characters for cell values before truncating.
_MAX_VAL_LEN = 50

# High-value deal threshold (services_amount)
_HIGH_VALUE_THRESHOLD = 100_000.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DealIssue:
    """A single per-row finding from the deals profiler."""

    row_index: int
    issue_type: str   # close_date_soon | missing_sow | missing_loe |
                      # no_owner | unknown_client | high_value_no_staffing |
                      # malformed_close_date | malformed_probability |
                      # malformed_services_amount | missing_close_date |
                      # missing_deal_name | missing_account |
                      # no_next_steps | stale_status_date
    field: str
    value: str
    detail: str = ""
    severity: str = "warning"   # "warning" | "info" | "error"


@dataclass
class DealsProfile:
    """Full profile result from :func:`profile_deals_csv`."""

    path: str
    total_rows: int
    sample_size: int
    raw_columns: list[str]
    normalized_columns: list[str]
    column_mapping: dict[str, str]
    fields_found: list[str]
    fields_missing: list[str]
    sample_rows: list[dict[str, str]]
    issues: list[DealIssue]
    can_ingest: bool
    detected_format: str = "normalized"
    derived_deal_name_count: int = 0
    netsuite_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truncate(val: Any, max_len: int = _MAX_VAL_LEN) -> str:
    s = str(val) if val is not None else ""
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _client_known(
    name: str,
    names_lower: set[str],
    aliases_lower: set[str],
) -> bool:
    n = name.strip().lower()
    return n in names_lower or n in aliases_lower


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def profile_deals_csv(
    csv_path: str,
    *,
    clients: list[ClientConfig] | None = None,
    source_priority: SourcePriorityConfig | None = None,
    sample_size: int = 10,
    reference_date: date | None = None,
) -> DealsProfile:
    """Profile a deals CSV without writing to the database.

    Args:
        csv_path:        Path to the CSV file.
        clients:         Client config for entity resolution (optional).
                         For NetSuite format, unknown accounts are NOT reported
                         as warning-level by default (they are prospects, not clients).
        source_priority: Column alias overrides from source_priority.yaml.
        sample_size:     Maximum number of rows to include in the sample.
        reference_date:  Date to use for close-date proximity checks.
                         Defaults to today.

    Returns:
        A :class:`DealsProfile` describing what was found.

    Raises:
        RuntimeError: If the CSV file cannot be read.
    """
    extra_aliases: dict[str, str] = {}
    if source_priority:
        extra_aliases = source_priority.deal_column_aliases

    # Detect format before reading the full file
    netsuite = is_netsuite_format(csv_path)
    detected_format = "netsuite" if netsuite else "normalized"

    try:
        df = pd.read_csv(csv_path, dtype=str)
    except Exception as exc:
        raise RuntimeError(
            f"Could not read deals CSV at '{csv_path}': {exc}"
        ) from exc

    raw_columns = list(df.columns)
    total_rows = len(df)

    df_norm = _normalize_columns(df.copy(), extra_aliases)

    # For NetSuite format, derive deal_name from account + deal_id if not present.
    derived_deal_name_count = 0
    if netsuite and "deal_name" not in df_norm.columns and "deal_id" in df_norm.columns:
        df_norm["deal_name"] = df_norm.apply(
            lambda r: _derive_deal_name(
                str(r.get("account", "")).strip() if pd.notna(r.get("account", "")) else "",
                str(r.get("deal_id", "")).strip() if pd.notna(r.get("deal_id", "")) else "",
            ),
            axis=1,
        )
        derived_deal_name_count = int((df_norm["deal_name"] != "").sum())

    normalized_columns = list(df_norm.columns)

    column_mapping: dict[str, str] = {
        raw: norm for raw, norm in zip(raw_columns, normalized_columns[:len(raw_columns)])
    }

    # Required fields depend on format
    required = _REQUIRED_NETSUITE if netsuite else _REQUIRED_NORMALIZED
    all_tracked = list(dict.fromkeys(required + _OPTIONAL_CANONICAL))
    fields_found = [f for f in all_tracked if f in normalized_columns]
    fields_missing = [f for f in required if f not in normalized_columns]
    # For NetSuite, if deal_name was derived, it's effectively present
    if netsuite and "deal_name" in df_norm.columns and "deal_name" in fields_missing:
        fields_missing.remove("deal_name")
    can_ingest = len(fields_missing) == 0

    today = reference_date or date.today()
    soon_threshold = today + timedelta(days=_CLOSE_DATE_WARN_DAYS)
    stale_threshold = today - timedelta(days=_STALE_STATUS_DAYS)

    # Client name sets for entity resolution
    client_names_lower: set[str] = set()
    client_aliases_lower: set[str] = set()
    if clients:
        for c in clients:
            client_names_lower.add(c.name.lower())
            for alias in c.aliases:
                client_aliases_lower.add(alias.lower())

    # Sample rows (truncated values)
    sample_df = df_norm.head(sample_size)
    sample_rows: list[dict[str, str]] = [
        {
            col: _truncate(val)
            for col, val in row.items()
            if pd.notna(val) and str(val).strip() and str(val).strip().lower() not in ("nan", "none")
        }
        for _, row in sample_df.iterrows()
    ]

    # Per-row issue detection (full dataset)
    issues: list[DealIssue] = []

    # NetSuite summary stats
    malformed_close_date_count = 0
    malformed_probability_count = 0
    malformed_services_count = 0
    no_next_steps_count = 0
    close_soon_count = 0
    stale_status_count = 0

    for idx, row in df_norm.iterrows():
        int_idx = int(idx)  # type: ignore[arg-type]

        # Unknown client — only for normalized format, or when clients explicitly
        # provided and this is not NetSuite format.
        # NetSuite Customer values are prospects, not necessarily signed clients.
        if not netsuite and "account" in df_norm.columns and clients is not None:
            account_val = str(row.get("account", "")).strip()
            if (
                account_val
                and account_val.lower() not in ("nan", "none", "")
                and not _client_known(account_val, client_names_lower, client_aliases_lower)
            ):
                issues.append(DealIssue(
                    row_index=int_idx,
                    issue_type="unknown_client",
                    field="account",
                    value=_truncate(account_val),
                    detail="Not found in config/clients.yaml",
                ))

        # Close date checks
        close_raw = str(row.get("close_date", "")).strip() if "close_date" in df_norm.columns else ""
        close_date_parsed: Optional[date] = None

        if "close_date" in df_norm.columns:
            if not close_raw or close_raw.lower() in ("nan", "none", ""):
                issues.append(DealIssue(
                    row_index=int_idx,
                    issue_type="missing_close_date",
                    field="close_date",
                    value="",
                    detail="close_date is empty",
                ))
            else:
                close_date_parsed = _parse_date(close_raw)
                if close_date_parsed is None:
                    malformed_close_date_count += 1
                    issues.append(DealIssue(
                        row_index=int_idx,
                        issue_type="malformed_close_date",
                        field="close_date",
                        value=_truncate(close_raw),
                        detail="Cannot parse as date",
                        severity="warning",
                    ))
                elif close_date_parsed <= soon_threshold:
                    days_left = (close_date_parsed - today).days
                    if days_left >= 0:
                        close_soon_count += 1
                        issues.append(DealIssue(
                            row_index=int_idx,
                            issue_type="close_date_soon",
                            field="close_date",
                            value=_truncate(close_raw),
                            detail=f"{days_left} day(s) until close",
                        ))

        # Stale last_status_changed_date
        if "last_status_changed_date" in df_norm.columns:
            lsc_raw = str(row.get("last_status_changed_date", "")).strip()
            if lsc_raw and lsc_raw.lower() not in ("nan", "none", ""):
                lsc_parsed = _parse_date(lsc_raw)
                if lsc_parsed is not None and lsc_parsed < stale_threshold:
                    stale_status_count += 1
                    days_stale = (today - lsc_parsed).days
                    issues.append(DealIssue(
                        row_index=int_idx,
                        issue_type="stale_status_date",
                        field="last_status_changed_date",
                        value=_truncate(lsc_raw),
                        detail=f"Status unchanged for {days_stale} day(s)",
                        severity="warning",
                    ))

        # SOW status missing / not started (normalized format only)
        if not netsuite and "sow_status" in df_norm.columns:
            sow_val = str(row.get("sow_status", "")).strip()
            sow_empty = not sow_val or sow_val.lower() in ("nan", "none", "not-started", "not_started", "")
            if sow_empty and close_date_parsed and close_date_parsed <= soon_threshold:
                issues.append(DealIssue(
                    row_index=int_idx,
                    issue_type="missing_sow",
                    field="sow_status",
                    value=_truncate(sow_val) if sow_val else "",
                    detail="SOW not started/missing with close date approaching",
                ))

        # LOE status missing / not started (normalized format only)
        if not netsuite and "loe_status" in df_norm.columns:
            loe_val = str(row.get("loe_status", "")).strip()
            loe_empty = not loe_val or loe_val.lower() in ("nan", "none", "not-started", "not_started", "")
            if loe_empty and close_date_parsed and close_date_parsed <= soon_threshold:
                issues.append(DealIssue(
                    row_index=int_idx,
                    issue_type="missing_loe",
                    field="loe_status",
                    value=_truncate(loe_val) if loe_val else "",
                    detail="LOE not started/missing with close date approaching",
                ))

        # No owner (technical_owner) — only for normalized format
        if not netsuite and "technical_owner" in df_norm.columns:
            owner_val = str(row.get("technical_owner", "")).strip()
            if not owner_val or owner_val.lower() in ("nan", "none", ""):
                issues.append(DealIssue(
                    row_index=int_idx,
                    issue_type="no_owner",
                    field="technical_owner",
                    value="",
                    detail="No technical owner assigned",
                ))

        # High-value deal without staffing info (normalized format only)
        if not netsuite:
            stage_val = str(row.get("stage", "")).strip().lower() if "stage" in df_norm.columns else ""
            in_late_stage = stage_val in _LATE_STAGES
            has_owner = (
                "technical_owner" in df_norm.columns
                and str(row.get("technical_owner", "")).strip()
                and str(row.get("technical_owner", "")).strip().lower() not in ("nan", "none", "")
            )
            has_feasibility = (
                "staffing_feasibility" in df_norm.columns
                and str(row.get("staffing_feasibility", "")).strip()
                and str(row.get("staffing_feasibility", "")).strip().lower() not in ("nan", "none", "")
            )
            if in_late_stage and not has_owner and not has_feasibility:
                stage_display = str(row.get("stage", "")).strip() if "stage" in df_norm.columns else ""
                issues.append(DealIssue(
                    row_index=int_idx,
                    issue_type="high_value_no_staffing",
                    field="stage",
                    value=_truncate(stage_display),
                    detail="Late-stage deal with no owner or staffing feasibility",
                ))

        # Malformed probability
        if "probability" in df_norm.columns:
            prob_raw = str(row.get("probability", "")).strip()
            if prob_raw and prob_raw.lower() not in ("nan", "none", ""):
                parsed_prob = _parse_probability(prob_raw)
                if parsed_prob is None:
                    malformed_probability_count += 1
                    issues.append(DealIssue(
                        row_index=int_idx,
                        issue_type="malformed_probability",
                        field="probability",
                        value=_truncate(prob_raw),
                        detail="Cannot parse probability as number",
                        severity="warning",
                    ))
                elif not (0.0 <= parsed_prob <= 1.0):
                    malformed_probability_count += 1
                    issues.append(DealIssue(
                        row_index=int_idx,
                        issue_type="malformed_probability",
                        field="probability",
                        value=_truncate(prob_raw),
                        detail=f"Parsed probability {parsed_prob:.4f} out of range [0, 1]",
                        severity="warning",
                    ))

        # Malformed services_amount
        if "services_amount" in df_norm.columns:
            svc_raw = str(row.get("services_amount", "")).strip()
            if svc_raw and svc_raw.lower() not in ("nan", "none", ""):
                parsed_svc = _parse_services_amount(svc_raw)
                if parsed_svc is None:
                    malformed_services_count += 1
                    issues.append(DealIssue(
                        row_index=int_idx,
                        issue_type="malformed_services_amount",
                        field="services_amount",
                        value=_truncate(svc_raw),
                        detail="Cannot parse services amount as number",
                        severity="warning",
                    ))

        # Missing next_steps (info-level only for NetSuite)
        if netsuite and "next_steps" in df_norm.columns:
            ns_val = str(row.get("next_steps", "")).strip()
            if not ns_val or ns_val.lower() in ("nan", "none", ""):
                no_next_steps_count += 1
                issues.append(DealIssue(
                    row_index=int_idx,
                    issue_type="no_next_steps",
                    field="next_steps",
                    value="",
                    detail="No next steps recorded",
                    severity="info",
                ))

    netsuite_summary: dict = {}
    if netsuite:
        netsuite_summary = {
            "derived_deal_names": derived_deal_name_count,
            "malformed_close_date": malformed_close_date_count,
            "malformed_probability": malformed_probability_count,
            "malformed_services_amount": malformed_services_count,
            "no_next_steps": no_next_steps_count,
            "close_date_soon": close_soon_count,
            "stale_status_date": stale_status_count,
        }

    return DealsProfile(
        path=csv_path,
        total_rows=total_rows,
        sample_size=min(sample_size, total_rows),
        raw_columns=raw_columns,
        normalized_columns=normalized_columns,
        column_mapping=column_mapping,
        fields_found=fields_found,
        fields_missing=fields_missing,
        sample_rows=sample_rows,
        issues=issues,
        can_ingest=can_ingest,
        detected_format=detected_format,
        derived_deal_name_count=derived_deal_name_count,
        netsuite_summary=netsuite_summary,
    )

