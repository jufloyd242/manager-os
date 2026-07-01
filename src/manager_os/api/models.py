"""Pydantic response models for the Manager OS API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
