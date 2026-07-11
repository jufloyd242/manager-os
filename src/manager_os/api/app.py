"""FastAPI app factory for the Manager OS read-only API."""

from __future__ import annotations

from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import duckdb

from manager_os.analytics.balance_capacity import balance_staffing_capacity
from manager_os.api import services
from manager_os.api.deps import get_db_connection, get_fresh_settings
from manager_os.api.models import (
    CalendarSyncRequest,
    CalendarSyncResponse,
    CommandParamsRequest,
    CommandRunRequestBody,
    CommandRunResponse,
    CommandSpecResponse,
    CommandValidateResponse,
    DailyResponse,
    FeedbackResponse,
    HealthResponse,
    MeetingPrepResponse,
    MeetingsResponse,
    PeopleResponse,
    ProjectsResponse,
    RunListResponse,
    RunLogsResponse,
    StatusResponse,
    FeedbackRequestBody,
)
from manager_os.build.daily_operating_loop import build_daily_operating_loop
from manager_os.build.dashboard_data import get_people_rows
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    @app.get("/api/analytics/staffing-balance")
    def staffing_balance(
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
        settings: Settings = Depends(get_fresh_settings),
    ) -> dict:
        rows = get_people_rows(conn, settings=settings)
        allocations = {r.name: r.allocation_pct for r in rows}
        return balance_staffing_capacity(
            allocations,
            standard_capacity=100.0,
            overallocated_threshold=100.0,
            underallocated_threshold=80.0,
            max_receiver_capacity=80.0,
        )

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

    @app.post("/api/feedback")
    def post_feedback(
        body: FeedbackRequestBody,
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
    ) -> dict:
        from manager_os.build.feedback import mark
        try:
            event_id = mark(
                conn,
                item_id=body.item_id,
                rating=body.rating,
                reason=body.reason,
            )
            return {"ok": True, "event_id": event_id}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/refresh")
    def safe_refresh(
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
        settings: Settings = Depends(get_fresh_settings),
    ) -> dict:
        from pathlib import Path
        from manager_os.db import seed_from_config
        from manager_os.ingest.obsidian import ingest_vault
        from manager_os.ingest.deals import ingest_deals
        from manager_os.extract.signals import run_rule_extraction
        from manager_os.extract.action_items import extract_action_items_from_all_notes
        from manager_os.extract.decisions import extract_decisions_from_all_notes
        
        # 1. Seed from config
        seed_from_config(conn, settings)
        
        # 2. Ingest Obsidian vault (if path exists)
        if settings.vault_path and Path(settings.vault_path).exists():
            try:
                ingest_vault(settings.vault_path, conn)
            except Exception as exc:
                print(f"Failed to ingest vault: {exc}")
                
        # 3. Ingest Deals (if CSV exists)
        deals_path = Path("data/raw/deals.csv")
        if deals_path.exists():
            try:
                ingest_deals(str(deals_path), conn)
            except Exception as exc:
                print(f"Failed to ingest deals: {exc}")
                
        # 4. Run rule extraction
        try:
            run_rule_extraction(conn, date.today())
        except Exception as exc:
            print(f"Failed rule extraction: {exc}")
            
        # 5. Run action-items & decisions extraction
        try:
            extract_action_items_from_all_notes(conn, date.today())
            extract_decisions_from_all_notes(conn, date.today())
        except Exception as exc:
            print(f"Failed action items/decisions extraction: {exc}")
            
        return {"ok": True, "message": "Local refresh completed successfully."}

    # ------------------------------------------------------------------
    # Calendar sync — explicit per-date retrieval
    # ------------------------------------------------------------------

    @app.post("/api/meetings/sync-calendar", response_model=CalendarSyncResponse)
    def sync_calendar(
        body: CalendarSyncRequest,
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
        settings: Settings = Depends(get_fresh_settings),
    ) -> CalendarSyncResponse:
        target_date = _parse_date(body.date)
        result = services.sync_calendar_date(conn, target_date, settings)
        return CalendarSyncResponse(**result)

    # ------------------------------------------------------------------
    # Meeting prep — deterministic rule-driven preparation
    # ------------------------------------------------------------------

    @app.get("/api/meetings/{meeting_id}/prep", response_model=MeetingPrepResponse)
    def get_meeting_prep(
        meeting_id: str,
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
        settings: Settings = Depends(get_fresh_settings),
    ) -> MeetingPrepResponse:
        # Check for existing prep in DB — always the most recently
        # generated row for this meeting (defense-in-depth: prep_id is
        # deterministic per meeting_id so there should only ever be one,
        # but ORDER BY + LIMIT 1 guards against any legacy duplicates).
        row = conn.execute(
            "SELECT content, generated_at FROM meeting_prep WHERE meeting_id = ? "
            "ORDER BY generated_at DESC LIMIT 1",
            [meeting_id],
        ).fetchone()
        if row:
            import json
            try:
                data = json.loads(row[0])
                if isinstance(data, dict):
                    return MeetingPrepResponse(**data)
            except (json.JSONDecodeError, ValueError):
                pass

        # Generate fresh prep
        result = services.prep_meeting(conn, meeting_id, settings)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return MeetingPrepResponse(**result)

    @app.post("/api/meetings/{meeting_id}/prep", response_model=MeetingPrepResponse)
    def regenerate_meeting_prep(
        meeting_id: str,
        conn: duckdb.DuckDBPyConnection = Depends(get_db_connection),
        settings: Settings = Depends(get_fresh_settings),
    ) -> MeetingPrepResponse:
        result = services.prep_meeting(conn, meeting_id, settings)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])

        # Persist the prep as JSON in meeting_prep table. prep_id is
        # deterministic on meeting_id ALONE (not generated_at) so
        # INSERT OR REPLACE actually replaces the prior stored prep for
        # this meeting instead of accumulating a new row on every
        # regenerate call — previously hashing in generated_at meant every
        # call produced a distinct id, silently leaving stale duplicate
        # rows behind that a later GET could non-deterministically return.
        import json
        from manager_os.db import content_hash
        prep_json = json.dumps(result)
        prep_id = content_hash(f"meeting_prep::{meeting_id}")
        conn.execute(
            "DELETE FROM meeting_prep WHERE meeting_id = ?",
            [meeting_id],
        )
        conn.execute(
            "INSERT OR REPLACE INTO meeting_prep (id, meeting_id, content, generated_at) VALUES (?, ?, ?, ?)",
            [prep_id, meeting_id, prep_json, result.get("generated_at", "")],
        )

        return MeetingPrepResponse(**result)

    return app


app = create_app()
