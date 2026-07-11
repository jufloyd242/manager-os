"""Deals dashboard query functions.

Provides enriched deal data for the Deals API and React view.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from manager_os.build.dashboard_data import get_deal_rows


def get_deals_list(
    conn,
    *,
    search: str | None = None,
    attention_only: bool = False,
    stage: str | None = None,
    owner: str | None = None,
    limit: int = 200,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Return enriched deal list with attention classification.

    Args:
        conn: Open DuckDB connection.
        search: Optional text search across account/deal_name.
        attention_only: If True, only return deals needing attention.
        stage: Optional stage filter.
        owner: Optional technical_owner filter.
        limit: Max results (default 200, not capped at 5).
        as_of: Reference date (default today).

    Returns:
        Dict with keys: deals, total, attention_count, counts_by_severity,
        freshness, last_updated, warnings.
    """
    if as_of is None:
        as_of = date.today()

    warnings: list[str] = []
    try:
        rows = get_deal_rows(conn, as_of=as_of)
    except Exception as exc:
        warnings.append(f"deals: {exc}")
        return {
            "deals": [],
            "total": 0,
            "attention_count": 0,
            "counts_by_severity": {},
            "freshness": "missing",
            "last_updated": None,
            "warnings": warnings,
        }

    # Enrich with attention classification
    enriched = []
    for r in rows:
        d = r.model_dump(mode="json")
        d["attention_level"], d["attention_reasons"] = _classify_attention(r, as_of)
        d["days_until_close"] = r.days_to_close
        d["freshness"] = _deal_freshness(r)
        d["freshness_explanation"] = _deal_freshness_explanation(r)
        enriched.append(d)

    # Apply filters
    if search:
        search_lower = search.lower()
        enriched = [
            d for d in enriched
            if search_lower in (d.get("account") or "").lower()
            or search_lower in (d.get("deal_name") or "").lower()
        ]

    if attention_only:
        enriched = [d for d in enriched if d.get("attention_level") in ("critical", "high")]

    if stage:
        enriched = [d for d in enriched if (d.get("stage") or "").lower() == stage.lower()]

    if owner:
        owner_lower = owner.lower()
        enriched = [d for d in enriched if owner_lower in (d.get("technical_owner") or "").lower()]

    # Sort by attention level then close date
    _attention_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "none": 4}
    enriched.sort(key=lambda d: (_attention_order.get(d.get("attention_level", "none"), 4), d.get("days_until_close") or 9999))

    # Count attention
    attention_count = sum(1 for d in enriched if d.get("attention_level") in ("critical", "high"))
    counts_by_severity: dict[str, int] = {}
    for d in enriched:
        sev = d.get("attention_level", "none")
        counts_by_severity[sev] = counts_by_severity.get(sev, 0) + 1

    # Freshness
    freshness, last_updated = _source_freshness(conn)

    return {
        "deals": enriched[:limit],
        "total": len(enriched),
        "attention_count": attention_count,
        "counts_by_severity": counts_by_severity,
        "freshness": freshness,
        "last_updated": last_updated,
        "warnings": warnings,
    }


def _classify_attention(deal_row, as_of: date) -> tuple[str, list[str]]:
    """Classify deal attention level and return reasons."""
    reasons: list[str] = []

    # Critical: past close or closes within 7 days with missing/blocked SOW
    if deal_row.days_to_close is not None and deal_row.days_to_close < 0:
        reasons.append(f"Past close date ({deal_row.close_date})")
        return "critical", reasons

    if deal_row.days_to_close is not None and deal_row.days_to_close <= 7:
        if deal_row.sow_status in ("missing", "blocked", ""):
            reasons.append(f"Closes in {deal_row.days_to_close}d with {deal_row.sow_status or 'unknown'} SOW")
            return "critical", reasons

    # High: explicit blocker, staffing infeasibility, escalation
    if deal_row.blockers:
        reasons.append(f"Blocker: {deal_row.blockers[:100]}")
        return "high", reasons

    if deal_row.staffing_feasibility == "blocked":
        reasons.append("Staffing infeasibility")
        return "high", reasons

    if deal_row.days_to_close is not None and deal_row.days_to_close <= 7:
        reasons.append(f"Closes in {deal_row.days_to_close}d")
        return "high", reasons

    # Medium: closes within 14 days, missing LOE, missing next step, stale status, missing owner
    if deal_row.days_to_close is not None and deal_row.days_to_close <= 14:
        reasons.append(f"Closes in {deal_row.days_to_close}d")
        return "medium", reasons

    if not deal_row.loe_status or deal_row.loe_status in ("missing", ""):
        reasons.append("Missing LOE")
        return "medium", reasons

    if not deal_row.next_action:
        reasons.append("Missing next step")
        return "medium", reasons

    if not deal_row.technical_owner:
        reasons.append("Missing technical owner")
        return "medium", reasons

    if deal_row.staffing_feasibility in ("unknown", ""):
        reasons.append("Unknown staffing feasibility")
        return "medium", reasons

    return "low", reasons


def _deal_freshness(deal_row) -> str:
    """Return freshness label for a deal."""
    if deal_row.days_to_close is not None and deal_row.days_to_close < 0:
        return "overdue"
    if deal_row.days_to_close is not None and deal_row.days_to_close <= 7:
        return "urgent"
    return "current"


def _deal_freshness_explanation(deal_row) -> str:
    """Return explanation for deal freshness."""
    if deal_row.days_to_close is not None:
        if deal_row.days_to_close < 0:
            return f"Closed {abs(deal_row.days_to_close)} days ago"
        return f"{deal_row.days_to_close} days until close"
    return "No close date set"


def _source_freshness(conn) -> tuple[str, str | None]:
    """Determine deals source freshness from DB."""
    try:
        row = conn.execute(
            "SELECT MAX(ingested_at) FROM raw_documents WHERE source_type = 'deals'"
        ).fetchone()
        if row and row[0]:
            ts = row[0]
            if isinstance(ts, datetime):
                return "fresh", ts.isoformat()
            return "fresh", str(ts)
        return "missing", None
    except Exception:
        return "missing", None
