"""Forecast API routes."""

from __future__ import annotations

from datetime import date

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query

from manager_os.api.deps import get_db_connection, get_fresh_settings
from manager_os.build.forecast_dashboard import get_forecast_data
from manager_os.config import Settings

router = APIRouter(prefix="/api", tags=["forecast"])


@router.get("/forecast")
def forecast(
    week_start: str | None = Query(default=None),
    person: str | None = Query(default=None),
    client: str | None = Query(default=None),
    exceptions_only: bool = Query(default=False, alias="exceptions_only"),
    limit: int = Query(default=200, ge=1, le=500),
    conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    settings: Settings = Depends(get_fresh_settings),
) -> dict:
    """Return forecast data with classification."""
    try:
        result = get_forecast_data(
            conn,
            week_start=week_start,
            person=person,
            client=client,
            exceptions_only=exceptions_only,
            limit=limit,
            as_of=date.today(),
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))