"""Deals API routes."""

from __future__ import annotations

from datetime import date

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from manager_os.api.deps import get_db_connection, get_fresh_settings
from manager_os.build.deals_dashboard import get_deals_list
from manager_os.config import Settings

router = APIRouter(prefix="/api", tags=["deals"])


@router.get("/deals")
def deals(
    search: str | None = Query(default=None),
    attention_only: bool = Query(default=False, alias="attention_only"),
    stage: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    settings: Settings = Depends(get_fresh_settings),
) -> dict:
    """Return enriched deal list with attention classification."""
    try:
        result = get_deals_list(
            conn,
            search=search,
            attention_only=attention_only,
            stage=stage,
            owner=owner,
            limit=limit,
            as_of=date.today(),
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))