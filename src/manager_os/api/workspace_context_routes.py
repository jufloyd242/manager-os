"""Workspace Context API routes."""

from __future__ import annotations

from datetime import date

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from manager_os.api.deps import get_db_connection, get_fresh_settings
from manager_os.build.workspace_context import get_workspace_context
from manager_os.config import Settings

router = APIRouter(prefix="/api", tags=["workspace_context"])


@router.get("/workspace-context")
def workspace_context(
    date_param: str | None = Query(default=None, alias="date"),
    lookback_days: int = Query(default=0, ge=0, le=365),
    entity_type: str | None = Query(default=None),
    entity: str | None = Query(default=None),
    attention_only: bool = Query(default=False, alias="attention_only"),
    limit: int = Query(default=100, ge=1, le=500),
    conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    settings: Settings = Depends(get_fresh_settings),
) -> dict:
    """Return workspace context for a given date with optional lookback."""
    try:
        target_date = date.fromisoformat(date_param) if date_param else date.today()
        result = get_workspace_context(
            conn,
            target_date,
            lookback_days=lookback_days,
            entity_type=entity_type,
            entity=entity,
            attention_only=attention_only,
            limit=limit,
        )
        return result
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_param}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))