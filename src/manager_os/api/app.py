"""FastAPI app factory for the Manager OS read-only API."""

from __future__ import annotations

from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query
import duckdb

from manager_os.api import services
from manager_os.api.deps import get_db_connection, get_fresh_settings
from manager_os.api.models import (
    CommandParamsRequest,
    CommandRunRequestBody,
    CommandRunResponse,
    CommandSpecResponse,
    CommandValidateResponse,
    DailyResponse,
    FeedbackResponse,
    HealthResponse,
    MeetingsResponse,
    PeopleResponse,
    ProjectsResponse,
    RunListResponse,
    RunLogsResponse,
    StatusResponse,
)
from manager_os.build.daily_operating_loop import build_daily_operating_loop
from manager_os.command_center.errors import (
    CommandNotFoundError,
    InvalidArgumentError,
    ScopeExceededError,
)
from manager_os.config import Settings


def _parse_date(value: str | None, param_name: str = "date") -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid '{param_name}' value: {value!r}. Expected format YYYY-MM-DD.",
        ) from None


def create_app() -> FastAPI:
    app = FastAPI(title="manager-os-api", description="Local read-only Manager OS API")

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(ok=True, service="manager-os-api")

    @app.get("/api/status", response_model=StatusResponse)
    def status(
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
        settings: Settings = Depends(get_fresh_settings),
    ) -> StatusResponse:
        return StatusResponse(**services.build_status(conn, settings))

    @app.get("/api/daily", response_model=DailyResponse)
    def daily(
        date: str | None = Query(default=None),
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
        settings: Settings = Depends(get_fresh_settings),
    ) -> DailyResponse:
        target_date = _parse_date(date)
        loop = build_daily_operating_loop(conn, target_date, settings=settings)
        return DailyResponse(**loop)

    @app.get("/api/people", response_model=PeopleResponse)
    def people(
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
        settings: Settings = Depends(get_fresh_settings),
    ) -> PeopleResponse:
        return PeopleResponse(**services.build_people(conn, settings))

    @app.get("/api/meetings", response_model=MeetingsResponse)
    def meetings(
        date: str | None = Query(default=None),
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    ) -> MeetingsResponse:
        target_date = _parse_date(date)
        return MeetingsResponse(**services.build_meetings(conn, target_date))

    @app.get("/api/projects", response_model=ProjectsResponse)
    def projects(
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    ) -> ProjectsResponse:
        return ProjectsResponse(**services.build_projects(conn))

    @app.get("/api/feedback", response_model=FeedbackResponse)
    def feedback(
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    ) -> FeedbackResponse:
        return FeedbackResponse(**services.build_feedback(conn))

    @app.get("/api/commands", response_model=list[CommandSpecResponse])
    def list_commands() -> list[CommandSpecResponse]:
        return [CommandSpecResponse(**spec) for spec in services.list_commands()]

    @app.get("/api/commands/{command_id}", response_model=CommandSpecResponse)
    def get_command(command_id: str) -> CommandSpecResponse:
        try:
            spec = services.get_command_spec(command_id)
        except CommandNotFoundError:
            raise HTTPException(status_code=404, detail=f"Unknown command_id: {command_id}") from None
        return CommandSpecResponse(**spec)

    @app.post("/api/commands/{command_id}/validate", response_model=CommandValidateResponse)
    def validate_command(command_id: str, body: CommandParamsRequest) -> CommandValidateResponse:
        try:
            result = services.validate_command(command_id, body.params)
        except CommandNotFoundError:
            raise HTTPException(status_code=404, detail=f"Unknown command_id: {command_id}") from None
        except (InvalidArgumentError, ScopeExceededError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandValidateResponse(**result)

    @app.post("/api/commands/{command_id}/run", response_model=CommandRunResponse)
    def run_command(
        command_id: str,
        body: CommandRunRequestBody,
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    ) -> CommandRunResponse:
        try:
            result = services.run_command(conn, command_id, body.params, confirm=body.confirm)
        except CommandNotFoundError:
            raise HTTPException(status_code=404, detail=f"Unknown command_id: {command_id}") from None
        except (InvalidArgumentError, ScopeExceededError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CommandRunResponse(**result)

    @app.get("/api/runs", response_model=RunListResponse)
    def list_runs(
        limit: int = Query(default=50),
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    ) -> RunListResponse:
        return RunListResponse(runs=services.list_runs(conn, limit=limit))

    @app.get("/api/runs/{run_id}")
    def get_run(
        run_id: str,
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    ) -> dict:
        run = services.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        return run

    @app.get("/api/runs/{run_id}/logs", response_model=RunLogsResponse)
    def get_run_logs(
        run_id: str,
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    ) -> RunLogsResponse:
        run = services.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
        return RunLogsResponse(stdout=run["stdout"], stderr=run["stderr"], error=run["error"])

    return app


app = create_app()
