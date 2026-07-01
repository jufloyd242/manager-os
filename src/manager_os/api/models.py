"""Pydantic response models for the Manager OS API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from manager_os.command_center.models import CommandSpec


class HealthResponse(BaseModel):
    ok: bool
    service: str


class SourceHealth(BaseModel):
    name: str
    status: str
    count: int
    last_updated: str | None = None
    warnings: list[str] = []


class StatusResponse(BaseModel):
    ok: bool
    db_path: str
    workspace_enabled: bool
    sources: list[SourceHealth]
    warnings: list[str] = []


class DailyResponse(BaseModel):
    date: str
    people_staffing: list[dict[str, Any]]
    meetings: list[dict[str, Any]]
    projects_deals: list[dict[str, Any]]
    document_gaps: list[dict[str, Any]]
    feedback_learning: list[dict[str, Any]]
    recommended_actions: list[dict[str, Any]]
    warnings: list[str]


class PeopleResponse(BaseModel):
    people: list[dict[str, Any]]
    warnings: list[str] = []


class MeetingsResponse(BaseModel):
    date: str
    meetings: list[dict[str, Any]]
    warnings: list[str] = []


class ProjectsResponse(BaseModel):
    projects: list[dict[str, Any]]
    warnings: list[str] = []


class FeedbackResponse(BaseModel):
    candidates: list[dict[str, Any]]
    warnings: list[str] = []


class CommandSpecResponse(CommandSpec):
    """API response shape for a single command spec.

    Reuses `command_center.models.CommandSpec` directly (same fields:
    command_id, label, description, category, risk_level,
    external_call_risk, parameters, supports_dry_run, supports_print_prompt,
    requires_confirmation, dry_run_required_before_live,
    default_timeout_seconds, estimated_prompt_chars, estimated_input_tokens,
    max_scope, bounded_param, writes_tables, reads_tables) rather than
    duplicating the field list.
    """


class CommandParamsRequest(BaseModel):
    params: dict[str, Any] = {}


class CommandRunRequestBody(BaseModel):
    params: dict[str, Any] = {}
    confirm: bool = False


class CommandValidateResponse(BaseModel):
    ok: bool
    command_id: str | None = None
    argv_preview: list[str] | None = None
    risk_level: str
    external_call_risk: str
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    warnings: list[str] = []
    requires_confirmation: bool = False
    dry_run_required_before_live: bool = False


class CommandRunResponse(BaseModel):
    ok: bool
    run_id: str
    status: str
    command_id: str
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None


class CommandRunRecordResponse(BaseModel):
    run_id: str
    command_id: str
    status: str
    risk_level: str
    external_call_risk: str
    dry_run: bool
    argv: list[str] = []
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    affected_tables: list[str] = []


class RunListResponse(BaseModel):
    runs: list[CommandRunRecordResponse]


class RunLogsResponse(BaseModel):
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
