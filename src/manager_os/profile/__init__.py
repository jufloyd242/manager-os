"""Forecast CSV profiler — read-only validation before ingest.

Reads headers and a sample of rows from a staffing forecast CSV,
applies the same column normalisation used by the ingestor, and
returns a :class:`ForecastProfile` describing what was found.

No data is written to DuckDB and no files are modified.
"""

from __future__ import annotations

import json as _json
from dataclasses import asdict, dataclass, field
from typing import Any

import pandas as pd

from manager_os.config import ClientConfig, PersonConfig, SourcePriorityConfig
from manager_os.ingest.forecast import _normalize_columns

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# These must be present (after normalisation) for the ingestor to work.
_REQUIRED_CANONICAL: list[str] = ["person_name", "week_start"]

# Additional canonical fields that are useful but not strictly required.
_OPTIONAL_CANONICAL: list[str] = [
    "client",
    "project",
    "allocation_pct",
    "forecast_type",
    "notes",
]

# Maps internal canonical name → the display label the task specification
# uses so the output is intuitive for the end user.
_FIELD_DISPLAY: dict[str, str] = {
    "person_name": "person",
    "week_start": "start_date",
    "client": "client",
    "project": "engagement",
    "allocation_pct": "allocation",
    "forecast_type": "status",
    "notes": "notes",
}

# Maximum characters to show for any cell value before truncating.
_MAX_VAL_LEN = 50


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RowIssue:
    """A single per-row finding from the profiler."""

    row_index: int
    issue_type: str   # overallocated | zero_allocation | missing_date |
                      # malformed_date | missing_allocation | unknown_person |
                      # unknown_client
    field: str
    value: str
    detail: str = ""


@dataclass
class ForecastProfile:
    """Full profile result from :func:`profile_forecast_csv`."""

    path: str
    total_rows: int
    sample_size: int                           # actual rows included in sample
    raw_columns: list[str]
    normalized_columns: list[str]
    column_mapping: dict[str, str]             # raw col name → normalised col name
    fields_found: list[str]                    # canonical names present in CSV
    fields_missing: list[str]                  # required canonical names absent
    sample_rows: list[dict[str, str]]          # truncated sample of data rows
    issues: list[RowIssue]
    can_ingest: bool                           # False when required cols missing

    # Wide-format metadata (populated only when detected_format == "wide")
    detected_format: str = "normalized"        # "normalized" | "wide"
    wide_summary: dict = field(default_factory=dict)

    # Convenience grouping for rendering
    @property
    def unknown_people(self) -> list[RowIssue]:
        return [i for i in self.issues if i.issue_type == "unknown_person"]

    @property
    def unknown_clients(self) -> list[RowIssue]:
        return [i for i in self.issues if i.issue_type == "unknown_client"]

    @property
    def overallocated(self) -> list[RowIssue]:
        return [i for i in self.issues if i.issue_type == "overallocated"]

    @property
    def zero_allocation(self) -> list[RowIssue]:
        return [i for i in self.issues if i.issue_type == "zero_allocation"]

    @property
    def missing_dates(self) -> list[RowIssue]:
        return [i for i in self.issues if i.issue_type == "missing_date"]

    @property
    def malformed_dates(self) -> list[RowIssue]:
        return [i for i in self.issues if i.issue_type == "malformed_date"]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of the full profile."""
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truncate(val: Any, max_len: int = _MAX_VAL_LEN) -> str:
    s = str(val) if val is not None else ""
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def _person_known(
    name: str,
    names_lower: set[str],
    aliases_lower: set[str],
) -> bool:
    n = name.strip().lower()
    return n in names_lower or n in aliases_lower


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


def profile_forecast_csv(
    csv_path: str,
    *,
    people: list[PersonConfig] | None = None,
    clients: list[ClientConfig] | None = None,
    source_priority: SourcePriorityConfig | None = None,
    sample_size: int = 10,
) -> ForecastProfile:
    """Profile a forecast CSV without writing to the database.

    Auto-detects whether the CSV is in normalized long format or wide
    planning-spreadsheet format, and profiles accordingly.

    Args:
        csv_path:        Path to the CSV file.
        people:          Team member config for entity resolution (optional).
        clients:         Client config for entity resolution (optional).
        source_priority: Column alias overrides from source_priority.yaml.
        sample_size:     Maximum number of rows to include in the sample.

    Returns:
        A :class:`ForecastProfile` describing what was found.

    Raises:
        RuntimeError: If the CSV file cannot be read.
    """
    from manager_os.ingest.forecast_wide import is_wide_format

    if is_wide_format(csv_path):
        return _profile_wide_forecast(
            csv_path,
            people=people,
            sample_size=sample_size,
        )

    return _profile_normalized_forecast(
        csv_path,
        people=people,
        clients=clients,
        source_priority=source_priority,
        sample_size=sample_size,
    )


def _profile_wide_forecast(
    csv_path: str,
    *,
    people: list[PersonConfig] | None = None,
    sample_size: int = 10,
) -> ForecastProfile:
    """Profile a wide-format planning spreadsheet CSV."""
    from manager_os.ingest.forecast_wide import parse_wide_forecast

    parse_result = parse_wide_forecast(csv_path)

    person_names_lower: set[str] = set()
    person_aliases_lower: set[str] = set()
    if people:
        for p in people:
            person_names_lower.add(p.name.lower())
            for alias in p.aliases:
                person_aliases_lower.add(alias.lower())

    issues: list[RowIssue] = []
    seen_unknown_people: set[str] = set()

    # Validate engineer names from PersonForecastRecord rows (warning-level)
    if people is not None:
        for record in parse_result.person_forecast:
            pn = record.person_name.strip()
            if pn and pn.lower() not in seen_unknown_people:
                if not _person_known(pn, person_names_lower, person_aliases_lower):
                    seen_unknown_people.add(pn.lower())
                    issues.append(RowIssue(
                        row_index=record.source_row,
                        issue_type="unknown_person",
                        field="person_name",
                        value=_truncate(pn),
                        detail=f"Engineer row: not in config/people.yaml (section={record.source_section})",
                    ))

    # Candidate engineers from pipeline rows are info-level only — NOT unknown_person warnings.
    # They are POSSIBLE STAFFING CANDIDATES, not allocated resources.

    normalized_columns = [
        "person_name", "week_start", "planned_hours", "target_hours",
        "source_section", "record_type",
    ]

    sample_rows: list[dict[str, str]] = [
        {
            "person_name": r.person_name,
            "week_start": str(r.week_start),
            "planned_hours": str(r.planned_hours),
            "target_hours": str(r.target_hours),
            "source_section": r.source_section,
            "record_type": r.record_type,
        }
        for r in parse_result.person_forecast[:sample_size]
    ]

    hire_status_weeks = [
        f"{sm.raw_value} ({sm.week_start})"
        for sm in parse_result.summary_metrics
        if sm.metric_name == "hire_status"
    ]

    has_records = bool(
        parse_result.person_forecast
        or parse_result.pipeline_demand
        or parse_result.pipeline_opportunities
    )

    return ForecastProfile(
        path=csv_path,
        total_rows=parse_result.total_rows,
        sample_size=len(sample_rows),
        raw_columns=[],
        normalized_columns=normalized_columns,
        column_mapping={},
        fields_found=["person_name", "week_start", "planned_hours", "target_hours"],
        fields_missing=[],
        sample_rows=sample_rows,
        issues=issues,
        can_ingest=parse_result.format_detected and has_records,
        detected_format="wide",
        wide_summary={
            "sections": parse_result.sections,
            "person_forecast_rows": len(parse_result.person_forecast),
            "pipeline_demand_rows": len(parse_result.pipeline_demand),
            "pipeline_opportunity_rows": len(parse_result.pipeline_opportunities),
            "summary_metric_rows": len(parse_result.summary_metrics),
            "candidate_people_total": parse_result.candidate_people_total,
            "unassigned_pipeline_demand": parse_result.skipped_ambiguous,
            "metric_mismatches": len(parse_result.metric_mismatches),
            "hire_status_weeks": hire_status_weeks,
            "warnings": parse_result.warnings,
            "infos": parse_result.infos,
        },
    )


def _profile_normalized_forecast(
    csv_path: str,
    *,
    people: list[PersonConfig] | None = None,
    clients: list[ClientConfig] | None = None,
    source_priority: SourcePriorityConfig | None = None,
    sample_size: int = 10,
) -> ForecastProfile:
    """Profile a normalized long-format forecast CSV (original behaviour)."""
    extra_aliases: dict[str, str] = {}
    if source_priority:
        extra_aliases = source_priority.forecast_column_aliases

    try:
        df = pd.read_csv(csv_path, dtype=str)
    except Exception as exc:
        raise RuntimeError(f"Could not read forecast CSV at '{csv_path}': {exc}") from exc

    raw_columns = list(df.columns)
    total_rows = len(df)

    df_norm = _normalize_columns(df.copy(), extra_aliases)
    normalized_columns = list(df_norm.columns)

    # Build per-column mapping: raw → normalised
    column_mapping: dict[str, str] = {
        raw: norm for raw, norm in zip(raw_columns, normalized_columns)
    }

    all_tracked = _REQUIRED_CANONICAL + _OPTIONAL_CANONICAL
    fields_found = [f for f in all_tracked if f in normalized_columns]
    fields_missing = [f for f in _REQUIRED_CANONICAL if f not in normalized_columns]
    can_ingest = len(fields_missing) == 0

    # Resolve person / client name sets (lowercase for case-insensitive check)
    person_names_lower: set[str] = set()
    person_aliases_lower: set[str] = set()
    if people:
        for p in people:
            person_names_lower.add(p.name.lower())
            for alias in p.aliases:
                person_aliases_lower.add(alias.lower())

    client_names_lower: set[str] = set()
    client_aliases_lower: set[str] = set()
    if clients:
        for c in clients:
            client_names_lower.add(c.name.lower())
            for alias in c.aliases:
                client_aliases_lower.add(alias.lower())

    # Sample rows (truncated)
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
    issues: list[RowIssue] = []

    for idx, row in df_norm.iterrows():
        int_idx = int(idx)  # type: ignore[arg-type]

        # Unknown person
        if "person_name" in df_norm.columns and people is not None:
            person_val = str(row.get("person_name", "")).strip()
            if (
                person_val
                and person_val.lower() not in ("nan", "none", "")
                and not _person_known(person_val, person_names_lower, person_aliases_lower)
            ):
                issues.append(RowIssue(
                    row_index=int_idx,
                    issue_type="unknown_person",
                    field="person_name",
                    value=_truncate(person_val),
                    detail="Not found in config/people.yaml",
                ))

        # Unknown client — skip for pipeline/capacity rows (client field = prospect label)
        if "client" in df_norm.columns and clients is not None:
            forecast_type_val = str(row.get("forecast_type", "")).strip().lower()
            if forecast_type_val not in ("pipeline", "capacity"):
                client_val = str(row.get("client", "")).strip()
                if (
                    client_val
                    and client_val.lower() not in ("nan", "none", "")
                    and not _client_known(client_val, client_names_lower, client_aliases_lower)
                ):
                    issues.append(RowIssue(
                        row_index=int_idx,
                        issue_type="unknown_client",
                        field="client",
                        value=_truncate(client_val),
                        detail="Not found in config/clients.yaml",
                    ))

        # Allocation checks
        if "allocation_pct" in df_norm.columns:
            alloc_raw = str(row.get("allocation_pct", "")).strip()
            if not alloc_raw or alloc_raw.lower() in ("nan", "none", ""):
                issues.append(RowIssue(
                    row_index=int_idx,
                    issue_type="missing_allocation",
                    field="allocation_pct",
                    value="",
                    detail="Allocation value is empty",
                ))
            else:
                try:
                    alloc = float(alloc_raw.replace("%", "").strip())
                    if alloc > 100:
                        issues.append(RowIssue(
                            row_index=int_idx,
                            issue_type="overallocated",
                            field="allocation_pct",
                            value=_truncate(alloc_raw),
                            detail=f"{alloc:.0f}% > 100%",
                        ))
                except ValueError:
                    issues.append(RowIssue(
                        row_index=int_idx,
                        issue_type="malformed_allocation",
                        field="allocation_pct",
                        value=_truncate(alloc_raw),
                        detail="Cannot parse allocation as number",
                    ))

        # Date checks (week_start)
        if "week_start" in df_norm.columns:
            date_raw = str(row.get("week_start", "")).strip()
            if not date_raw or date_raw.lower() in ("nan", "none", ""):
                issues.append(RowIssue(
                    row_index=int_idx,
                    issue_type="missing_date",
                    field="week_start",
                    value="",
                    detail="week_start is empty",
                ))
            else:
                try:
                    pd.to_datetime(date_raw)
                except Exception:
                    issues.append(RowIssue(
                        row_index=int_idx,
                        issue_type="malformed_date",
                        field="week_start",
                        value=_truncate(date_raw),
                        detail="Cannot parse as date",
                    ))

    return ForecastProfile(
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
