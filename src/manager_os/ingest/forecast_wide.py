"""Wide-format forecast CSV parser.

Product semantics
-----------------
- Pipeline rows are POSSIBLE FUTURE DEMAND, not person allocations.
- Candidate Engineer(s) are POSSIBLE STAFFING CANDIDATES only.
  They are NOT allocated, soft-held, or committed.
  They are stored in candidate_people only, NEVER in person_name.
- Target on engineer rows means weekly capacity in hours.
- Engineer weekly cells mean planned allocation hours.
- Summary rows are team/week metrics, not people or clients.
- Pipeline prospect/deal labels are NOT validated against clients.yaml.
- Zero allocation hours are VALID — not a warning condition.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_SECTION_LABELS: frozenset = frozenset({
    "ai", "ml", "data engineering", "data science", "engineering",
    "platform", "infrastructure", "infra", "mlops", "mlplatform",
    "machine learning", "artificial intelligence",
})
_ENGINEER_LABELS: frozenset = frozenset({"engineer", "engineers", "team", "capacity"})
_PIPELINE_LABELS: frozenset = frozenset({"pipeline", "prospects", "deals", "pipeline demand"})
_METRIC_MAP: dict = {
    "total demand": "total_demand",
    "total capacity": "total_capacity",
    "weekly gap": "weekly_gap",
    "team utilization": "team_utilization",
    "hire status": "hire_status",
}
_YEAR_MIN = 2020
_YEAR_MAX = 2040
_COMPARISON_TOLERANCE = 0.5


@dataclass
class PersonForecastRecord:
    record_type: str = "person_forecast"
    forecast_type: str = "engineer_allocation"
    source_section: str = ""
    person_name: str = ""
    week_start: Optional[date] = None
    target_hours: Optional[float] = None
    planned_hours: float = 0.0
    status: str = "planned"
    source_row: int = 0


@dataclass
class PipelineDemandRecord:
    record_type: str = "pipeline_demand"
    forecast_type: str = "pipeline_demand"
    source_section: str = ""
    week_start: Optional[date] = None
    prospect_or_deal: str = ""
    probability: Optional[float] = None
    requested_allocation: Optional[float] = None
    skillset: str = ""
    demand_hours: float = 0.0
    candidate_people: list = field(default_factory=list)
    staffing_status: str = "unassigned"
    source_row: int = 0


@dataclass
class PipelineOpportunityRecord:
    record_type: str = "pipeline_opportunity"
    forecast_type: str = "pipeline_opportunity"
    source_section: str = ""
    prospect_or_deal: str = ""
    probability: Optional[float] = None
    requested_allocation: Optional[float] = None
    skillset: str = ""
    candidate_people: list = field(default_factory=list)
    status: str = "unscheduled"
    source_row: int = 0


@dataclass
class SummaryMetricRecord:
    record_type: str = "summary_metric"
    source_section: str = ""
    week_start: Optional[date] = None
    metric_name: str = ""
    metric_value: Optional[float] = None
    raw_value: str = ""
    source_row: int = 0


@dataclass
class MetricMismatch:
    source_section: str
    week_start: date
    metric_name: str
    spreadsheet_value: Optional[float]
    calculated_value: Optional[float]
    difference: Optional[float]


@dataclass
class WideParseResult:
    format_detected: bool = False
    total_rows: int = 0
    sections: list = field(default_factory=list)
    person_forecast: list = field(default_factory=list)
    pipeline_demand: list = field(default_factory=list)
    pipeline_opportunities: list = field(default_factory=list)
    summary_metrics: list = field(default_factory=list)
    metric_mismatches: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    infos: list = field(default_factory=list)

    @property
    def skipped_ambiguous(self) -> int:
        return sum(1 for r in self.pipeline_demand if r.staffing_status == "unassigned")

    @property
    def candidate_people_total(self) -> int:
        return sum(len(r.candidate_people) for r in self.pipeline_demand)


def _first_nonempty(row: "pd.Series") -> str:
    for val in row:
        if pd.notna(val) and str(val).strip():
            return str(val).strip()
    return ""


def _maybe_fix_year_typo(date_str: str) -> tuple:
    s = date_str.strip()
    m = re.match(r"^(\d{4})([-/].+)$", s)
    if not m:
        return s, False
    year_digits = m.group(1)
    rest = m.group(2)
    year = int(year_digits)
    if _YEAR_MIN <= year <= _YEAR_MAX:
        return s, False
    for i in range(len(year_digits) - 1):
        chars = list(year_digits)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
        candidate_str = "".join(chars)
        if _YEAR_MIN <= int(candidate_str) <= _YEAR_MAX:
            return f"{candidate_str}{rest}", True
    return s, False


def _try_parse_date(val: str) -> tuple:
    s = str(val).strip() if val else ""
    if not s or s.lower() in ("nan", "none", ""):
        return None, False
    try:
        d = pd.to_datetime(s).date()
        if not (_YEAR_MIN <= d.year <= _YEAR_MAX):
            fixed, was_fixed = _maybe_fix_year_typo(s)
            if was_fixed:
                try:
                    return pd.to_datetime(fixed).date(), True
                except Exception:
                    pass
            return None, False
        return d, False
    except Exception:
        fixed, was_fixed = _maybe_fix_year_typo(s)
        if was_fixed:
            try:
                return pd.to_datetime(fixed).date(), True
            except Exception:
                pass
        return None, False


def _extract_date_columns(row: "pd.Series") -> dict:
    result: dict = {}
    for i, val in enumerate(row):
        if pd.isna(val):
            continue
        s = str(val).strip()
        if not s or s.lower() in ("average", "avg", "total", "notes", ""):
            continue
        d, _ = _try_parse_date(s)
        if d is not None:
            result[i] = d
    return result


def _parse_float(s) -> Optional[float]:
    cleaned = str(s).strip() if s is not None else ""
    if not cleaned or cleaned.lower() in ("nan", "none", ""):
        return None
    cleaned = cleaned.replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_utilization(s) -> tuple:
    raw = str(s).strip() if s is not None else ""
    if not raw or raw.lower() in ("nan", "none", ""):
        return None, raw
    cleaned = raw.replace("%", "").strip()
    try:
        pct = float(cleaned)
        return pct / 100.0, raw
    except ValueError:
        return None, raw


def _split_candidates(raw: str) -> tuple:
    if not raw:
        return [], True
    stripped = raw.strip()
    if stripped.lower() in ("", "nan", "none"):
        return [], True
    if stripped == "?" or "?" in stripped:
        return [], True
    parts = re.split(r"[/,&]", stripped)
    names = [p.strip() for p in parts if p.strip()]
    return names, False


def _is_metric_row(first_cell: str) -> bool:
    return first_cell.strip().lower() in _METRIC_MAP


def _metric_canonical(first_cell: str) -> Optional[str]:
    return _METRIC_MAP.get(first_cell.strip().lower())


def is_wide_format(csv_path: str) -> bool:
    try:
        import csv as _csv
        with open(csv_path, newline="", encoding="utf-8-sig") as _f:
            rows = list(_csv.reader(_f))[:30]
        if not rows:
            return False
        first_row_lower = {c.strip().lower() for c in rows[0] if c.strip()}
        if any(h in first_row_lower for h in ("person", "person_name", "week_start", "week start")):
            return False
        for r in rows:
            fc = next((c.strip().lower() for c in r if c.strip()), "")
            if fc in _SECTION_LABELS or fc in _ENGINEER_LABELS:
                return True
    except Exception:
        pass
    return False


def parse_wide_forecast(csv_path: str) -> WideParseResult:
    result = WideParseResult(format_detected=True)
    try:
        # Pre-compute max column count to handle variable-width rows
        with open(csv_path, newline="", encoding="utf-8-sig") as _f:
            import csv as _csv
            max_cols = max((len(r) for r in _csv.reader(_f)), default=1)
        df = pd.read_csv(
            csv_path, header=None, dtype=str,
            names=range(max_cols), engine="python",
        )
    except Exception as exc:
        result.format_detected = False
        result.warnings.append(f"Could not read CSV: {exc}")
        return result

    result.total_rows = len(df)
    state = "SCAN"
    current_section = ""
    date_cols: dict = {}
    pending_dates: dict = {}
    pipeline_prob_col = 1
    pipeline_alloc_col = 2
    pipeline_skillset_col = 3
    pipeline_candidate_col = -1
    _acc: dict = {}

    def _get_acc(section: str, week: date) -> dict:
        return _acc.setdefault(section, {}).setdefault(
            week, {"eng_planned": 0.0, "eng_target": 0.0, "pip_demand": 0.0}
        )

    for row_idx, row in df.iterrows():
        int_idx = int(row_idx)  # type: ignore[arg-type]
        first_cell = _first_nonempty(row)
        first_cell_lower = first_cell.lower()

        if first_cell_lower in _SECTION_LABELS:
            current_section = first_cell
            if current_section not in result.sections:
                result.sections.append(current_section)
            state = "AWAIT_ENGINEER"
            pending_dates = {}
            continue

        if not first_cell:
            dc = _extract_date_columns(row)
            if dc:
                for val in row:
                    if pd.notna(val) and str(val).strip():
                        _, corrected = _try_parse_date(str(val))
                        if corrected:
                            result.warnings.append(
                                f"Row {int_idx}: Year typo auto-corrected in '{str(val).strip()}'"
                            )
                pending_dates = dc
            continue

        if _is_metric_row(first_cell):
            metric_name = _metric_canonical(first_cell)
            if metric_name and date_cols:
                for col_idx, week_date in date_cols.items():
                    if col_idx >= len(row):
                        continue
                    cell = row.iloc[col_idx]
                    raw_val = str(cell).strip() if pd.notna(cell) else ""
                    if not raw_val or raw_val.lower() in ("nan", ""):
                        continue
                    if metric_name == "team_utilization":
                        m_val, raw_str = _parse_utilization(raw_val)
                    elif metric_name == "hire_status":
                        m_val = None
                        raw_str = raw_val
                    else:
                        m_val = _parse_float(raw_val)
                        raw_str = raw_val
                    result.summary_metrics.append(SummaryMetricRecord(
                        source_section=current_section,
                        week_start=week_date,
                        metric_name=metric_name,
                        metric_value=m_val,
                        raw_value=raw_str,
                        source_row=int_idx,
                    ))
            continue

        if first_cell_lower in _ENGINEER_LABELS:
            dc = _extract_date_columns(row)
            if dc:
                date_cols = dc
            elif pending_dates:
                date_cols = pending_dates
            pending_dates = {}
            state = "IN_ENGINEER"
            continue

        if first_cell_lower in _PIPELINE_LABELS:
            dc = _extract_date_columns(row)
            if dc:
                date_cols = dc
            elif pending_dates:
                date_cols = pending_dates
            pipeline_candidate_col = -1
            for i, val in enumerate(row):
                if pd.isna(val):
                    continue
                vl = str(val).strip().lower()
                if "candidate" in vl:
                    pipeline_candidate_col = i
                elif vl == "assignee":
                    pipeline_candidate_col = i
                elif vl in ("probability", "prob"):
                    pipeline_prob_col = i
                elif vl in ("allocation", "alloc", "requested alloc", "requested allocation"):
                    pipeline_alloc_col = i
                elif vl in ("skillset", "skill", "skills", "role"):
                    pipeline_skillset_col = i
            if pipeline_candidate_col == -1:
                for i in range(len(row) - 1, -1, -1):
                    v_cell = row.iloc[i]
                    v = str(v_cell).strip() if pd.notna(v_cell) else ""
                    if v and v.lower() not in ("nan", ""):
                        d, _ = _try_parse_date(v)
                        if d is None:
                            pipeline_candidate_col = i
                            break
            pending_dates = {}
            state = "IN_PIPELINE"
            continue

        if state == "IN_ENGINEER":
            if not date_cols:
                result.warnings.append(
                    f"Row {int_idx}: Engineer row '{first_cell}' has no date columns; skipping"
                )
                continue
            target_cell = row.iloc[1] if len(row) > 1 else None
            target_str = str(target_cell).strip() if pd.notna(target_cell) else ""
            target_hours = _parse_float(target_str)
            for col_idx, week_date in date_cols.items():
                if col_idx >= len(row):
                    continue
                cell = row.iloc[col_idx]
                planned_raw = _parse_float(str(cell).strip() if pd.notna(cell) else "")
                # A blank weekly cell means 0 planned hours — the engineer still
                # has capacity for this week.  Do NOT skip; target must be counted.
                planned = planned_raw if planned_raw is not None else 0.0
                result.person_forecast.append(PersonForecastRecord(
                    source_section=current_section,
                    person_name=first_cell,
                    week_start=week_date,
                    target_hours=target_hours,
                    planned_hours=planned,
                    source_row=int_idx,
                ))
                acc = _get_acc(current_section, week_date)
                acc["eng_planned"] += planned
                if target_hours is not None:
                    acc["eng_target"] += target_hours

        elif state == "IN_PIPELINE":
            if not date_cols:
                result.warnings.append(
                    f"Row {int_idx}: Pipeline row '{first_cell}' has no date columns; skipping"
                )
                continue

            def _cell_str(col: int) -> str:
                if col < 0 or col >= len(row):
                    return ""
                c = row.iloc[col]
                return str(c).strip() if pd.notna(c) else ""

            prob = _parse_float(_cell_str(pipeline_prob_col))
            req_alloc = _parse_float(_cell_str(pipeline_alloc_col))
            skillset = _cell_str(pipeline_skillset_col)
            if skillset.lower() in ("nan", "none", ""):
                skillset = ""
            candidate_raw = _cell_str(pipeline_candidate_col)
            if candidate_raw.lower() in ("nan", "none"):
                candidate_raw = ""
            candidates, is_ambiguous = _split_candidates(candidate_raw)
            if is_ambiguous and candidate_raw:
                result.infos.append(
                    f"Row {int_idx}: Pipeline '{first_cell}' Candidate Engineer(s) "
                    f"'{candidate_raw}' is ambiguous; treating as unassigned"
                )
            elif not candidate_raw:
                result.infos.append(
                    f"Row {int_idx}: Pipeline '{first_cell}' has no Candidate Engineer(s)"
                )
            has_demand = any(
                _parse_float(str(row.iloc[ci]).strip() if pd.notna(row.iloc[ci]) else "") is not None
                for ci in date_cols
                if ci < len(row)
            )
            if not has_demand:
                result.pipeline_opportunities.append(PipelineOpportunityRecord(
                    source_section=current_section,
                    prospect_or_deal=first_cell,
                    probability=prob,
                    requested_allocation=req_alloc,
                    skillset=skillset,
                    candidate_people=candidates,
                    status="unscheduled",
                    source_row=int_idx,
                ))
                result.infos.append(
                    f"Row {int_idx}: Pipeline '{first_cell}' has no weekly demand; "
                    "stored as pipeline_opportunity"
                )
            else:
                for col_idx, week_date in date_cols.items():
                    if col_idx >= len(row):
                        continue
                    cell = row.iloc[col_idx]
                    demand = _parse_float(str(cell).strip() if pd.notna(cell) else "")
                    if demand is None:
                        continue
                    staffing_status = "candidate" if candidates else "unassigned"
                    result.pipeline_demand.append(PipelineDemandRecord(
                        source_section=current_section,
                        week_start=week_date,
                        prospect_or_deal=first_cell,
                        probability=prob,
                        requested_allocation=req_alloc,
                        skillset=skillset,
                        demand_hours=demand,
                        candidate_people=candidates,
                        staffing_status=staffing_status,
                        source_row=int_idx,
                    ))
                    acc = _get_acc(current_section, week_date)
                    acc["pip_demand"] += demand

        elif state in ("SCAN", "AWAIT_ENGINEER"):
            dc = _extract_date_columns(row)
            if dc:
                pending_dates = dc
                # Warn about year typo corrections in header rows
                for val in row:
                    if pd.notna(val) and str(val).strip():
                        _, corrected = _try_parse_date(str(val))
                        if corrected:
                            result.warnings.append(
                                f"Row {int_idx}: Year typo auto-corrected "
                                f"in '{str(val).strip()}'"
                            )

    _compare_metrics(result, _acc)
    return result


def _compare_metrics(result: WideParseResult, acc: dict) -> None:
    sheet: dict = {}
    for sm in result.summary_metrics:
        if sm.week_start is None:
            continue
        sheet[(sm.source_section, sm.week_start, sm.metric_name)] = sm.metric_value

    for section, weeks in acc.items():
        for week_date, data in weeks.items():
            eng_planned = data["eng_planned"]
            eng_target = data["eng_target"]
            pip_demand = data["pip_demand"]
            # Total Demand = engineer planned hours + pipeline demand hours
            # Total Capacity = sum of engineer Target values for this section/week
            calc_demand = eng_planned + pip_demand
            calc_capacity = eng_target
            calc_gap = calc_capacity - calc_demand
            calc_util = (calc_demand / calc_capacity) if calc_capacity > 0 else 0.0
            calcs = {
                "total_demand": calc_demand,
                "total_capacity": calc_capacity,
                "weekly_gap": calc_gap,
                "team_utilization": calc_util,
            }
            for metric_name, calc_val in calcs.items():
                key = (section, week_date, metric_name)
                if key not in sheet:
                    continue
                sheet_val = sheet[key]
                if sheet_val is None:
                    continue
                # Normalize both values to the same scale before comparing.
                # team_utilization is stored as a decimal fraction (105.81% → 1.0581)
                # after _parse_utilization.  The calculated value is already a fraction.
                # Other metrics are in hours.
                diff = abs(calc_val - sheet_val)
                if diff > _COMPARISON_TOLERANCE:
                    result.metric_mismatches.append(MetricMismatch(
                        source_section=section,
                        week_start=week_date,
                        metric_name=metric_name,
                        spreadsheet_value=sheet_val,
                        calculated_value=calc_val,
                        difference=diff,
                    ))
                    result.warnings.append(
                        f"Metric mismatch [{section}] {week_date} {metric_name}: "
                        f"spreadsheet={sheet_val:.4f} calculated={calc_val:.4f} "
                        f"diff={diff:.4f}"
                    )
