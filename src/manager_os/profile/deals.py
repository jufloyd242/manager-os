"""Deals CSV profiler — read-only validation before ingest.

Reads headers and a sample of rows from a deals/SOW CSV, applies the
same column normalisation used by the ingestor, and returns a
:class:`DealsProfile` describing what was found.

No data is written to DuckDB and no files are modified.
"""

from __future__ import annotations

import json as _json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from manager_os.config import ClientConfig, SourcePriorityConfig
from manager_os.ingest.deals import _normalize_columns

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Required canonical columns — the ingestor will raise without these.
_REQUIRED_CANONICAL: list[str] = ["account", "deal_name"]

# Optional canonical columns that are useful for signal extraction.
_OPTIONAL_CANONICAL: list[str] = [
    "stage",
    "close_date",
    "technical_owner",
    "ae_name",
    "loe_status",
    "sow_status",
    "staffing_feasibility",
    "blockers",
    "next_action",
]

# Display labels for canonical names (shown in the output table).
_FIELD_DISPLAY: dict[str, str] = {
    "account": "account/client",
    "deal_name": "deal name",
    "stage": "stage",
    "close_date": "close date",
    "technical_owner": "owner",
    "ae_name": "AE/ECA",
    "loe_status": "LOE status",
    "sow_status": "SOW status",
    "staffing_feasibility": "staffing feasibility",
    "blockers": "blockers",
    "next_action": "next action",
}

# SOW/LOE values that count as "present/resolved" for the profiler.
_SIGNED_VALUES: frozenset[str] = frozenset({"signed"})

# Sample clients considered "high-value" by default close-to-deadline heuristic
# when staffing info is missing.  We don't know actual dollar amounts from the
# CSV alone, so we use a stage-based proxy.
_LATE_STAGES: frozenset[str] = frozenset({
    "sow review", "sow_review", "proposal",
    "negotiation", "commit", "closed",
})

# How many days ahead to flag an imminent close date.
_CLOSE_DATE_WARN_DAYS = 14

# Maximum characters for cell values before truncating.
_MAX_VAL_LEN = 50


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
                      # missing_close_date | missing_deal_name | missing_account
    field: str
    value: str
    detail: str = ""


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

    try:
        df = pd.read_csv(csv_path, dtype=str)
    except Exception as exc:
        raise RuntimeError(
            f"Could not read deals CSV at '{csv_path}': {exc}"
        ) from exc

    raw_columns = list(df.columns)
    total_rows = len(df)

    df_norm = _normalize_columns(df.copy(), extra_aliases)
    normalized_columns = list(df_norm.columns)

    column_mapping: dict[str, str] = {
        raw: norm for raw, norm in zip(raw_columns, normalized_columns)
    }

    all_tracked = _REQUIRED_CANONICAL + _OPTIONAL_CANONICAL
    fields_found = [f for f in all_tracked if f in normalized_columns]
    fields_missing = [f for f in _REQUIRED_CANONICAL if f not in normalized_columns]
    can_ingest = len(fields_missing) == 0

    today = reference_date or date.today()
    soon_threshold = today + timedelta(days=_CLOSE_DATE_WARN_DAYS)

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
            if pd.notna(val) and str(val).strip()
        }
        for _, row in sample_df.iterrows()
    ]

    # Per-row issue detection (full dataset)
    issues: list[DealIssue] = []

    for idx, row in df_norm.iterrows():
        int_idx = int(idx)  # type: ignore[arg-type]

        # Unknown client
        if "account" in df_norm.columns and clients is not None:
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
        close_date_parsed: date | None = None

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
                try:
                    close_date_parsed = pd.to_datetime(close_raw).date()
                    if close_date_parsed <= soon_threshold:
                        days_left = (close_date_parsed - today).days
                        if days_left >= 0:
                            issues.append(DealIssue(
                                row_index=int_idx,
                                issue_type="close_date_soon",
                                field="close_date",
                                value=_truncate(close_raw),
                                detail=f"{days_left} day(s) until close",
                            ))
                        # Past close dates are not separately flagged here;
                        # they will surface as stale signals after ingest.
                except Exception:
                    issues.append(DealIssue(
                        row_index=int_idx,
                        issue_type="malformed_close_date",
                        field="close_date",
                        value=_truncate(close_raw),
                        detail="Cannot parse as date",
                    ))

        # SOW status missing / not started
        if "sow_status" in df_norm.columns:
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

        # LOE status missing / not started
        if "loe_status" in df_norm.columns:
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

        # No owner (technical_owner)
        if "technical_owner" in df_norm.columns:
            owner_val = str(row.get("technical_owner", "")).strip()
            if not owner_val or owner_val.lower() in ("nan", "none", ""):
                issues.append(DealIssue(
                    row_index=int_idx,
                    issue_type="no_owner",
                    field="technical_owner",
                    value="",
                    detail="No technical owner assigned",
                ))

        # High-value deal without staffing info
        # Proxy: deal is in a late stage but has no technical_owner or
        # staffing_feasibility info.
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
            deal_name_val = str(row.get("deal_name", "")).strip() if "deal_name" in df_norm.columns else ""
            issues.append(DealIssue(
                row_index=int_idx,
                issue_type="high_value_no_staffing",
                field="stage",
                value=_truncate(stage_val),
                detail="Late-stage deal with no owner or staffing feasibility",
            ))

        # Malformed probability (if column present)
        if "probability" in df_norm.columns:
            prob_raw = str(row.get("probability", "")).strip()
            if prob_raw and prob_raw.lower() not in ("nan", "none", ""):
                try:
                    prob = float(prob_raw.replace("%", "").strip())
                    if prob < 0 or prob > 100:
                        issues.append(DealIssue(
                            row_index=int_idx,
                            issue_type="malformed_probability",
                            field="probability",
                            value=_truncate(prob_raw),
                            detail=f"Probability {prob} out of range [0, 100]",
                        ))
                except ValueError:
                    issues.append(DealIssue(
                        row_index=int_idx,
                        issue_type="malformed_probability",
                        field="probability",
                        value=_truncate(prob_raw),
                        detail="Cannot parse probability as number",
                    ))

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
    )
