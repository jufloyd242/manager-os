"""Wide-format forecast CSV parser.

Handles planning spreadsheet exports shaped like:

    Section row:  AI   (or ML, Engineering, etc.)
    Date sub-row: ,,,,2026-06-16,2026-06-23,...
    Engineer hdr: Engineer,Target,,,2026-06-16,...,Average
    Capacity row: Alex Rivera,40,,,40,40,0,40,40,36
    ...
    Pipeline hdr: Pipeline,Probability,Requested Alloc,Skillset,<dates>,Assignee
    Pipeline row: Prospect Alpha Inc,0.8,20,ML Engineering,20,20,...,Alex/Jordan
    ...
    Summary rows: Total Demand / Total Capacity / Weekly Gap / Team Utilization / Hire Status

Multiple sections (AI, ML) may appear in one file.

Key guarantees:
- Pipeline prospect/deal labels are preserved as-is; NOT validated against clients.yaml.
- Assignees like "Satya/Zheng" are split; "?" or blank → skipped with info log.
- Year typos like "2206" → "2026" are auto-corrected with a warning.
- Zero-allocation weeks are stored as-is; NOT flagged as issues.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# First-cell values that identify a new team/group section
_SECTION_LABELS: frozenset[str] = frozenset({
    "ai", "ml", "data engineering", "data science", "engineering",
    "platform", "infrastructure", "infra", "mlops", "mlplatform",
})

# First-cell values that identify the start of an engineer capacity block
_ENGINEER_LABELS: frozenset[str] = frozenset({
    "engineer", "engineers", "team", "capacity",
})

# First-cell values that identify the start of a pipeline block
_PIPELINE_LABELS: frozenset[str] = frozenset({
    "pipeline", "prospects", "deals", "pipeline demand",
})

# First-cell prefixes for summary rows to skip
_SUMMARY_PREFIXES: tuple[str, ...] = (
    "total demand",
    "total capacity",
    "weekly gap",
    "team utilization",
    "hire status",
    "pipeline demand",
    "capacity summary",
    "net capacity",
    "bench",
)

# Planning year range — dates outside this range trigger typo-correction logic
_YEAR_MIN = 2020
_YEAR_MAX = 2040


# ---------------------------------------------------------------------------
# Result data structures
# ---------------------------------------------------------------------------


@dataclass
class WideParsedRecord:
    """One normalized record produced from a wide-format forecast CSV."""

    person_name: str        # engineer name (capacity) or assignee (pipeline)
    week_start: date
    section: str            # AI | ML | etc.
    forecast_type: str      # "capacity" | "pipeline"
    allocation: float       # hours that week

    # Capacity-only fields
    target_hours: Optional[float] = None

    # Pipeline-only fields
    prospect_label: str = ""        # raw account / deal / prospect name
    probability: Optional[float] = None
    requested_alloc: Optional[float] = None
    skillset: str = ""


@dataclass
class WideParseResult:
    """Aggregate result from :func:`parse_wide_forecast`."""

    format_detected: bool = False
    total_rows: int = 0
    sections: list[str] = field(default_factory=list)
    capacity_records: list[WideParsedRecord] = field(default_factory=list)
    pipeline_records: list[WideParsedRecord] = field(default_factory=list)
    skipped_ambiguous: int = 0           # pipeline rows skipped due to no/? assignee
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _first_nonempty(row: pd.Series) -> str:
    """Return the first non-empty, non-NaN cell value in a Series as str."""
    for val in row:
        if pd.notna(val) and str(val).strip():
            return str(val).strip()
    return ""


def _maybe_fix_year_typo(date_str: str) -> tuple[str, bool]:
    """Attempt to fix a year typo by swapping adjacent digits.

    Example: '2206-06-16' -> ('2026-06-16', True)

    Returns (possibly_fixed_string, was_fixed).
    """
    s = date_str.strip()
    m = re.match(r"^(\d{4})([-/].+)$", s)
    if not m:
        return s, False

    year_digits = m.group(1)
    rest = m.group(2)
    year = int(year_digits)

    # Already plausible
    if _YEAR_MIN <= year <= _YEAR_MAX:
        return s, False

    # Try all adjacent-digit swaps to find a plausible year
    for i in range(len(year_digits) - 1):
        chars = list(year_digits)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
        candidate_str = "".join(chars)
        if _YEAR_MIN <= int(candidate_str) <= _YEAR_MAX:
            return f"{candidate_str}{rest}", True

    return s, False


def _try_parse_date(val: str) -> tuple[Optional[date], bool]:
    """Parse a date string with optional year-typo correction.

    Returns (date_or_None, was_year_corrected).
    """
    s = str(val).strip() if val else ""
    if not s or s.lower() in ("nan", "none", ""):
        return None, False

    try:
        d = pd.to_datetime(s).date()
        # Year out of planning range — try auto-correction
        if not (_YEAR_MIN <= d.year <= _YEAR_MAX):
            fixed, was_fixed = _maybe_fix_year_typo(s)
            if was_fixed:
                try:
                    corrected = pd.to_datetime(fixed).date()
                    return corrected, True
                except Exception:
                    pass
            return None, False
        return d, False
    except Exception:
        # Direct parse failed; try typo correction
        fixed, was_fixed = _maybe_fix_year_typo(s)
        if was_fixed:
            try:
                return pd.to_datetime(fixed).date(), True
            except Exception:
                pass
        return None, False


def _extract_date_columns(row: pd.Series) -> dict[int, date]:
    """Return {col_index: date} for all date-parseable cells in the row."""
    result: dict[int, date] = {}
    for i, val in enumerate(row):
        if pd.isna(val):
            continue
        d, _ = _try_parse_date(str(val))
        if d is not None:
            result[i] = d
    return result


def _find_assignee_col(header_row: pd.Series) -> int:
    """Find the column index of an 'Assignee' cell in a pipeline header row.

    Falls back to the last non-empty, non-date column.
    """
    for i, val in enumerate(header_row):
        if pd.notna(val) and "assignee" in str(val).strip().lower():
            return i

    # Fallback: last non-empty column
    for i in range(len(header_row) - 1, -1, -1):
        v = str(header_row.iloc[i]).strip()
        if v and v.lower() not in ("nan", ""):
            return i

    return -1


def _split_assignees(raw: str) -> list[str]:
    """Split 'Satya/Zheng' → ['Satya', 'Zheng']; return [] for '?' / blank."""
    if not raw:
        return []
    stripped = raw.strip()
    if stripped.lower() in ("", "nan", "none", "?", "tbd", "n/a"):
        return []
    if "?" in stripped:
        return []  # Any '?' makes the assignee ambiguous
    parts = re.split(r"[/,]", stripped)
    return [p.strip() for p in parts if p.strip()]


def _parse_float(s: str) -> Optional[float]:
    """Parse a numeric cell; return None if empty or unparseable."""
    cleaned = str(s).strip() if s else ""
    if not cleaned or cleaned.lower() in ("nan", "none", ""):
        return None
    cleaned = cleaned.replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _is_summary_row(first_cell: str) -> bool:
    fc = first_cell.strip().lower()
    return any(fc.startswith(prefix) for prefix in _SUMMARY_PREFIXES)


# ---------------------------------------------------------------------------
# Row parsers
# ---------------------------------------------------------------------------


def _parse_capacity_row(
    row: pd.Series,
    row_idx: int,
    name: str,
    date_cols: dict[int, date],
    target_col: int,
    section: str,
    result: WideParseResult,
) -> None:
    target_str = str(row.iloc[target_col]).strip() if len(row) > target_col else ""
    target_hours = _parse_float(target_str)

    for col_idx, week_date in date_cols.items():
        if col_idx >= len(row):
            continue
        val_str = str(row.iloc[col_idx]).strip()
        alloc = _parse_float(val_str)
        if alloc is None:
            result.warnings.append(
                f"Row {row_idx}: Cannot parse capacity hours for '{name}' on {week_date!s}; skipping"
            )
            continue

        result.capacity_records.append(WideParsedRecord(
            person_name=name,
            week_start=week_date,
            section=section,
            forecast_type="capacity",
            allocation=alloc,
            target_hours=target_hours,
        ))


def _parse_pipeline_row(
    row: pd.Series,
    row_idx: int,
    label: str,
    date_cols: dict[int, date],
    prob_col: int,
    alloc_col: int,
    skillset_col: int,
    assignee_col: int,
    section: str,
    result: WideParseResult,
) -> None:
    probability = _parse_float(str(row.iloc[prob_col]).strip() if len(row) > prob_col else "")
    requested_alloc = _parse_float(str(row.iloc[alloc_col]).strip() if len(row) > alloc_col else "")
    skillset = str(row.iloc[skillset_col]).strip() if len(row) > skillset_col else ""
    if skillset.lower() in ("nan", "none", ""):
        skillset = ""

    if assignee_col >= 0 and len(row) > assignee_col:
        assignee_raw = str(row.iloc[assignee_col]).strip()
    else:
        assignee_raw = ""

    assignees = _split_assignees(assignee_raw)

    for col_idx, week_date in date_cols.items():
        if col_idx >= len(row):
            continue
        val_str = str(row.iloc[col_idx]).strip()
        demand = _parse_float(val_str)
        if demand is None:
            result.warnings.append(
                f"Row {row_idx}: Cannot parse demand hours for '{label}' on {week_date!s}; skipping"
            )
            continue

        if not assignees:
            result.infos.append(
                f"Row {row_idx}: Pipeline '{label}' on {week_date!s} "
                f"has no/ambiguous assignee ('{assignee_raw}'); skipping person record"
            )
            result.skipped_ambiguous += 1
            # Store a record with empty person_name so pipeline data is preserved
            result.pipeline_records.append(WideParsedRecord(
                person_name="",
                week_start=week_date,
                section=section,
                forecast_type="pipeline",
                allocation=demand,
                prospect_label=label,
                probability=probability,
                requested_alloc=requested_alloc,
                skillset=skillset,
            ))
        else:
            for assignee in assignees:
                result.pipeline_records.append(WideParsedRecord(
                    person_name=assignee,
                    week_start=week_date,
                    section=section,
                    forecast_type="pipeline",
                    allocation=demand,
                    prospect_label=label,
                    probability=probability,
                    requested_alloc=requested_alloc,
                    skillset=skillset,
                ))


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def is_wide_format(csv_path: str) -> bool:
    """Return True if the CSV looks like a wide planning spreadsheet.

    Heuristic:
    - If the first non-empty header row contains 'person' or 'week_start',
      it is normalized long format → return False.
    - If any of the first 30 rows have a known section label (AI, ML, …) or
      engineer/pipeline block header in the first cell, it is wide → return True.
    """
    try:
        df = pd.read_csv(csv_path, header=None, dtype=str, nrows=30)
    except Exception:
        return False

    # Normalized format guard: first row has canonical headers
    first_row_lower = {
        str(v).strip().lower()
        for v in df.iloc[0]
        if pd.notna(v) and str(v).strip()
    }
    if any(h in first_row_lower for h in ("person", "person_name", "week_start", "week start")):
        return False

    for _, row in df.iterrows():
        fc = _first_nonempty(row).lower()
        if fc in _SECTION_LABELS or fc in _ENGINEER_LABELS:
            return True

    return False


def parse_wide_forecast(csv_path: str) -> WideParseResult:
    """Parse a wide-format forecast CSV into normalized :class:`WideParsedRecord` objects.

    Does not write to any database.  Returns a :class:`WideParseResult`.
    """
    result = WideParseResult(format_detected=True)

    try:
        df = pd.read_csv(csv_path, header=None, dtype=str)
    except Exception as exc:
        result.format_detected = False
        result.warnings.append(f"Could not read CSV: {exc}")
        return result

    result.total_rows = len(df)

    # ── State machine ──────────────────────────────────────────────────────
    # States: SCAN | AWAIT_ENGINEER | IN_ENGINEER | IN_PIPELINE
    state = "SCAN"
    current_section = ""
    date_cols: dict[int, date] = {}        # col_idx → date for current block
    pending_dates: dict[int, date] = {}    # dates from a pre-header date row
    engineer_target_col = 1               # default: col 1 = target hours
    pipeline_prob_col = 1
    pipeline_alloc_col = 2
    pipeline_skillset_col = 3
    pipeline_assignee_col = -1

    for row_idx, row in df.iterrows():
        int_idx = int(row_idx)  # type: ignore[arg-type]
        first_cell = _first_nonempty(row)
        first_cell_lower = first_cell.lower()

        # ── Section transition ─────────────────────────────────────────────
        if first_cell_lower in _SECTION_LABELS:
            current_section = first_cell
            if current_section not in result.sections:
                result.sections.append(current_section)
            state = "AWAIT_ENGINEER"
            pending_dates = {}
            continue

        # ── Skip blank rows ────────────────────────────────────────────────
        if not first_cell:
            # Could be a "date sub-row" (dates starting ~col 4)
            dc = _extract_date_columns(row)
            if dc:
                pending_dates = dc
                # Warn about any corrected year typos
                for val in row:
                    if pd.notna(val) and str(val).strip():
                        _, corrected = _try_parse_date(str(val))
                        if corrected:
                            result.warnings.append(
                                f"Row {int_idx}: Year typo detected in '{str(val).strip()}'; "
                                f"auto-corrected to 20xx"
                            )
            continue

        # ── Skip summary rows ──────────────────────────────────────────────
        if _is_summary_row(first_cell):
            continue

        # ── Engineer block header ──────────────────────────────────────────
        if first_cell_lower in _ENGINEER_LABELS:
            # Extract dates from this header row (or fall back to pending_dates)
            dc = _extract_date_columns(row)
            if dc:
                date_cols = dc
                # Check for year typos
                for val in row:
                    s = str(val).strip() if pd.notna(val) else ""
                    _, corrected = _try_parse_date(s)
                    if corrected:
                        result.warnings.append(
                            f"Row {int_idx}: Year typo in '{s}'; auto-corrected"
                        )
            elif pending_dates:
                date_cols = pending_dates
            pending_dates = {}
            state = "IN_ENGINEER"
            continue

        # ── Pipeline block header ──────────────────────────────────────────
        if first_cell_lower in _PIPELINE_LABELS:
            # Update dates if pipeline header row has them
            dc = _extract_date_columns(row)
            if dc:
                date_cols = dc
                for val in row:
                    s = str(val).strip() if pd.notna(val) else ""
                    _, corrected = _try_parse_date(s)
                    if corrected:
                        result.warnings.append(
                            f"Row {int_idx}: Year typo in '{s}'; auto-corrected"
                        )
            elif pending_dates:
                date_cols = pending_dates

            pipeline_assignee_col = _find_assignee_col(row)
            # Determine prob/alloc/skillset column indices from header
            # Defaults are 1/2/3; override if we find explicit labels
            for i, val in enumerate(row):
                if pd.isna(val):
                    continue
                vl = str(val).strip().lower()
                if vl in ("probability", "prob", "probability %"):
                    pipeline_prob_col = i
                elif vl in ("requested alloc", "alloc", "allocation", "requested allocation", "hours"):
                    pipeline_alloc_col = i
                elif vl in ("skillset", "skill", "skills", "role"):
                    pipeline_skillset_col = i

            pending_dates = {}
            state = "IN_PIPELINE"
            continue

        # ── Engineer capacity data row ─────────────────────────────────────
        if state == "IN_ENGINEER":
            if not date_cols:
                result.warnings.append(
                    f"Row {int_idx}: Capacity row '{first_cell}' has no date columns; skipping"
                )
                continue
            _parse_capacity_row(
                row, int_idx, first_cell, date_cols,
                engineer_target_col, current_section, result
            )

        # ── Pipeline data row ──────────────────────────────────────────────
        elif state == "IN_PIPELINE":
            if not date_cols:
                result.warnings.append(
                    f"Row {int_idx}: Pipeline row '{first_cell}' has no date columns; skipping"
                )
                continue
            _parse_pipeline_row(
                row, int_idx, first_cell, date_cols,
                pipeline_prob_col, pipeline_alloc_col,
                pipeline_skillset_col, pipeline_assignee_col,
                current_section, result
            )

        # ── SCAN / AWAIT_ENGINEER state: treat date-like rows as pending ───
        elif state in ("SCAN", "AWAIT_ENGINEER"):
            dc = _extract_date_columns(row)
            if dc:
                pending_dates = dc

    return result
