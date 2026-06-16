"""All Pydantic v2 models for Manager OS.

One source of truth for data shapes used in ingest, extract, build, and dashboard.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


def _today() -> date:
    return date.today()


# ---------------------------------------------------------------------------
# Literal type aliases
# ---------------------------------------------------------------------------

SeverityType = Literal["critical", "high", "medium", "low"]
SignalStatusType = Literal["open", "acknowledged", "resolved", "dismissed"]
ActionItemStatusType = Literal[
    "open", "completed", "stale", "dismissed", "snoozed", "not_mine", "done"
]
DecisionStatusType = Literal["open", "made", "blocked"]
EntityType = Literal["person", "client", "deal", "team", "practice"]
SignalType = Literal[
    "risk",
    "blocker",
    "ask",
    "decision",
    "staffing_change",
    "deal_change",
    "client_update",
    "follow_up",
    "stale_item",
    "meeting_prep",
    "people_health",
    "utilization_risk",
    "sow_loe_review",
]
SourceType = Literal[
    "obsidian",
    "forecast",
    "deals",
    "workspace_summary",
    "gws",
    "rule",
    "llm",
    "manager_os_write",
    "gmail",
    "chat",
    "drive",
    "calendar",
]
NoteType = Literal["1on1", "client", "deal", "meeting", "team", "practice"]
ForecastType = Literal["confirmed", "likely", "pipeline", "capacity"]
HealthType = Literal["green", "yellow", "red"]
MoraleType = Literal["green", "yellow", "red"]
StaffingFeasibilityType = Literal["feasible", "at-risk", "blocked"]


# ---------------------------------------------------------------------------
# Config models (from YAML)
# ---------------------------------------------------------------------------


class PersonConfig(BaseModel):
    model_config = ConfigDict(strict=False)

    name: str
    aliases: list[str] = []
    role: str = ""
    level: str = ""
    track: bool = True  # False = hide from dashboard / people-health tracking


class ClientConfig(BaseModel):
    model_config = ConfigDict(strict=False)

    name: str
    aliases: list[str] = []
    engagement: str = ""


class DealAliasConfig(BaseModel):
    """Wraps the deal_aliases dict for typed access."""

    model_config = ConfigDict(strict=False)

    aliases: dict[str, str] = {}


class SourcePriorityConfig(BaseModel):
    model_config = ConfigDict(strict=False)

    confidence_weights: dict[str, float] = {}
    conflict_resolution_order: list[str] = []
    forecast_column_aliases: dict[str, str] = {}
    deal_column_aliases: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Ingest models
# ---------------------------------------------------------------------------


class RawDocument(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    ingested_at: datetime = Field(default_factory=_now)
    source_type: SourceType
    source_path: str
    file_modified_at: datetime | None = None
    content_hash: str
    content: str
    metadata: dict[str, Any] = {}


class NoteRecord(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    raw_document_id: str
    note_date: date | None = None
    note_type: str = ""  # flexible; validated against NoteType where possible
    entity_type: str = ""
    entity_name: str = ""
    title: str = ""
    body: str = ""
    tags: list[str] = []
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Core signal model (most important)
# ---------------------------------------------------------------------------


class Signal(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    signal_date: date = Field(default_factory=_today)
    source: SourceType
    source_path: str = ""
    entity_type: EntityType
    entity_name: str
    signal_type: SignalType
    severity: SeverityType
    summary: str
    why_it_matters: str = ""
    requires_manager_attention: bool = False
    owner: str = ""
    due_date: date | None = None
    confidence: float = 1.0
    status: SignalStatusType = "open"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Action items and decisions
# ---------------------------------------------------------------------------


class ActionItem(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    signal_id: str | None = None
    source_note_id: str | None = None
    assigned_to: str
    description: str
    due_date: date | None = None
    status: ActionItemStatusType = "open"
    feedback_rating: str | None = None
    feedback_reason: str | None = None
    snooze_until: date | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Decision(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    entity_type: str = ""
    entity_name: str = ""
    description: str
    decision_date: date | None = None
    status: DecisionStatusType = "open"
    owner: str = ""
    source_note_id: str | None = None
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# CSV row models
# ---------------------------------------------------------------------------


class StaffingForecastRow(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    person_id: str | None = None
    person_name: str
    week_start: date
    client: str = ""
    project: str = ""
    allocation_pct: float = 0.0
    forecast_type: ForecastType = "confirmed"
    notes: str = ""
    ingested_at: datetime = Field(default_factory=_now)


class DealRow(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    account: str
    deal_name: str
    deal_id: str = ""
    stage: str = ""
    close_date: date | None = None
    technical_owner: str = ""
    ae_name: str = ""
    requested_roles: list[str] = []
    loe_status: str = ""
    sow_status: str = ""
    staffing_feasibility: StaffingFeasibilityType = "feasible"
    blockers: str = ""
    next_action: str = ""
    # NetSuite-specific fields
    next_steps: str = ""
    delivery_comment: str = ""
    forecast_category: str = ""
    probability: float | None = None
    services_amount: float | None = None
    last_status_changed_date: date | None = None
    source_format: str = "normalized"
    updated_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Meeting model
# ---------------------------------------------------------------------------


class MeetingRecord(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    meeting_date: date
    start_time: str = ""
    title: str
    attendees: list[str] = []
    linked_entities: list[dict[str, str]] = []  # [{entity_type, entity_name}]
    source: str = ""
    external_id: str = ""
    updated_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class DailyBrief(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    brief_date: date = Field(default_factory=_today)
    content: str
    signal_ids: list[str] = []
    shown_signals: int = 0
    created_at: datetime = Field(default_factory=_now)


class MeetingPrepRecord(BaseModel):
    model_config = ConfigDict(strict=False)

    id: str = Field(default_factory=_new_id)
    meeting_id: str
    content: str
    generated_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Dashboard view models
# ---------------------------------------------------------------------------


class DashboardPeopleRow(BaseModel):
    model_config = ConfigDict(strict=False)

    name: str
    role: str = ""
    current_client: str = ""
    allocation_pct: float = 0.0
    next_availability_date: date | None = None
    last_1on1_date: date | None = None
    days_since_1on1: int | None = None
    morale: MoraleType = "green"
    blockers: str = ""
    open_signal_count: int = 0
    highest_severity: SeverityType | None = None
    growth_topic: str = ""
    next_action_for_me: str = ""


class DashboardClientRow(BaseModel):
    model_config = ConfigDict(strict=False)

    name: str
    engagement: str = ""
    health: HealthType = "green"
    current_team: list[str] = []
    last_update_date: date | None = None
    open_risk_count: int = 0
    client_sentiment: str = ""
    next_milestone: str = ""
    unresolved_decision_count: int = 0


class DashboardDealRow(BaseModel):
    model_config = ConfigDict(strict=False)

    account: str
    deal_name: str
    deal_id: str = ""
    stage: str = ""
    close_date: date | None = None
    days_to_close: int | None = None
    technical_owner: str = ""
    ae_name: str = ""
    loe_status: str = ""
    sow_status: str = ""
    staffing_feasibility: StaffingFeasibilityType = "feasible"
    staffing_feasibility_source: str = "deals_csv"  # 'deals_csv' | 'computed' | 'unknown'
    blockers: str = ""
    next_action: str = ""
    open_signal_count: int = 0
    highest_severity: SeverityType | None = None
    # Document links (from deal_documents table / Google Drive)
    sow_title: str = ""
    sow_url: str = ""
    deal_sheet_title: str = ""
    deal_sheet_url: str = ""


class DashboardForecastRow(BaseModel):
    model_config = ConfigDict(strict=False)

    person_name: str
    week_start: date
    client: str = ""
    project: str = ""
    allocation_pct: float = 0.0
    forecast_type: ForecastType = "confirmed"
    is_overallocated: bool = False
    is_underallocated: bool = False
