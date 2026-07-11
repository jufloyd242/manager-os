"""Unified local refresh API route.

Refreshes local data sources without invoking external systems.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException

from manager_os.api.deps import get_db_connection, get_fresh_settings
from manager_os.config import Settings
from manager_os.extract.action_items import extract_action_items_from_all_notes
from manager_os.extract.decisions import extract_decisions_from_all_notes
from manager_os.extract.signals import run_rule_extraction
from manager_os.ingest.deals import ingest_deals
from manager_os.ingest.forecast import ingest_forecast
from manager_os.ingest.obsidian import ingest_vault
from manager_os.ingest.workspace_snapshot import ingest_workspace_activity_snapshot
from manager_os.ingest.workspace_summary import ingest_summary

router = APIRouter(prefix="/api", tags=["refresh"])

_refresh_in_progress = False


@router.post("/refresh")
def refresh(
    body: dict | None = None,
    conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    settings: Settings = Depends(get_fresh_settings),
) -> dict:
    """Refresh local data sources.

    Request body (optional):
    {
        "date": "YYYY-MM-DD",
        "sources": ["obsidian", "deals", "forecast", "workspace_summary", "workspace_activity"],
        "run_extraction": true
    }

    Defaults: date=today, sources=all local-safe sources, run_extraction=true.
    Never invokes Calendar, Gemini, Workspace retrieval, Drive, Chat, or Sheets.
    """
    global _refresh_in_progress

    if _refresh_in_progress:
        raise HTTPException(status_code=409, detail="Refresh already in progress")

    try:
        _refresh_in_progress = True

        if body is None:
            body = {}

        raw_date = body.get("date")
        target_date = date.fromisoformat(raw_date) if raw_date else date.today()
        sources = body.get("sources", ["obsidian", "deals", "forecast", "workspace_summary", "workspace_activity"])
        run_extraction = body.get("run_extraction", True)

        source_results: dict[str, dict[str, Any]] = {}
        overall_ok = True

        for source in sources:
            result = _refresh_source(source, conn, settings, target_date)
            source_results[source] = result
            if result.get("status") != "ok":
                overall_ok = False

        extraction_result = None
        if run_extraction:
            extraction_result = _run_extraction(conn, target_date)

        return {
            "ok": overall_ok,
            "date": target_date.isoformat(),
            "sources": source_results,
            "extraction": extraction_result,
        }
    finally:
        _refresh_in_progress = False


def _refresh_source(
    source: str,
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    target_date: date,
) -> dict[str, Any]:
    """Refresh a single source. Returns per-source result dict."""
    try:
        if source == "obsidian":
            result = ingest_vault(settings.vault_path, conn)
            return {
                "status": "ok",
                "source": source,
                "ingested": result.ingested,
                "skipped": result.skipped,
                "failed": result.failed,
                "warnings": result.errors[:5],
            }

        elif source == "deals":
            if not settings.deals_csv:
                return {"status": "skipped", "source": source, "reason": "No deals_csv configured"}
            result = ingest_deals(settings.deals_csv, conn)
            return {
                "status": "ok",
                "source": source,
                "ingested": result.ingested,
                "skipped": result.skipped,
                "failed": result.failed,
                "warnings": result.errors[:5],
            }

        elif source == "forecast":
            if not settings.forecast_csv:
                return {"status": "skipped", "source": source, "reason": "No forecast_csv configured"}
            result = ingest_forecast(settings.forecast_csv, conn)
            return {
                "status": "ok",
                "source": source,
                "ingested": result.ingested,
                "skipped": result.skipped,
                "failed": result.failed,
                "warnings": result.errors[:5],
            }

        elif source == "workspace_summary":
            if not settings.workspace_summary_dir:
                return {"status": "skipped", "source": source, "reason": "No workspace_summary_dir configured"}
            result = ingest_summary(settings.workspace_summary_dir, target_date, conn)
            return {
                "status": "ok",
                "source": source,
                "ingested": result.ingested,
                "skipped": result.skipped,
                "failed": result.failed,
                "warnings": result.errors[:5],
            }

        elif source == "workspace_activity":
            result = ingest_workspace_activity_snapshot(conn, target_date)
            return {
                "status": "ok",
                "source": source,
                "ingested": result.ingested,
                "skipped": result.skipped,
                "failed": result.failed,
                "warnings": result.errors[:5],
            }

        else:
            return {"status": "skipped", "source": source, "reason": f"Unknown source: {source}"}

    except Exception as exc:
        return {
            "status": "error",
            "source": source,
            "error": str(exc),
        }


def _run_extraction(
    conn: duckdb.DuckDBPyConnection,
    target_date: date,
) -> dict[str, Any]:
    """Run extraction pipeline."""
    results: dict[str, Any] = {}
    overall_ok = True

    try:
        sig_result = run_rule_extraction(conn, target_date)
        results["signals"] = {
            "status": "ok",
            "extracted": sig_result.written,
            "skipped": sig_result.skipped,
        }
    except Exception as exc:
        results["signals"] = {"status": "error", "error": str(exc)}
        overall_ok = False

    try:
        ai_result = extract_action_items_from_all_notes(conn, run_date=target_date)
        results["action_items"] = {
            "status": "ok",
            "extracted": ai_result.written,
            "skipped": ai_result.skipped,
        }
    except Exception as exc:
        results["action_items"] = {"status": "error", "error": str(exc)}
        overall_ok = False

    try:
        dec_result = extract_decisions_from_all_notes(conn)
        results["decisions"] = {
            "status": "ok",
            "extracted": dec_result.written,
            "skipped": dec_result.skipped,
        }
    except Exception as exc:
        results["decisions"] = {"status": "error", "error": str(exc)}
        overall_ok = False

    return {
        "ok": overall_ok,
        "results": results,
    }
