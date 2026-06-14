"""Tests for schemas.py — Pydantic model validation and round-trips."""

from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from manager_os.schemas import (
    ActionItem,
    DailyBrief,
    DashboardClientRow,
    DashboardDealRow,
    DashboardForecastRow,
    DashboardPeopleRow,
    DealRow,
    Decision,
    MeetingPrepRecord,
    MeetingRecord,
    NoteRecord,
    RawDocument,
    Signal,
    StaffingForecastRow,
)


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


def test_signal_valid() -> None:
    s = Signal(
        source="rule",
        entity_type="client",
        entity_name="Acme Corp",
        signal_type="risk",
        severity="high",
        summary="Delivery risk detected",
        why_it_matters="Client escalation possible",
    )
    assert s.status == "open"
    assert s.confidence == 1.0
    assert isinstance(s.id, str)
    assert uuid.UUID(s.id)  # valid UUID


def test_signal_invalid_severity() -> None:
    with pytest.raises(ValidationError):
        Signal(
            source="rule",
            entity_type="client",
            entity_name="Acme",
            signal_type="risk",
            severity="catastrophic",  # invalid
            summary="test",
        )


def test_signal_invalid_signal_type() -> None:
    with pytest.raises(ValidationError):
        Signal(
            source="rule",
            entity_type="client",
            entity_name="Acme",
            signal_type="unknown_type",  # invalid
            severity="high",
            summary="test",
        )


def test_signal_invalid_status() -> None:
    with pytest.raises(ValidationError):
        Signal(
            source="rule",
            entity_type="client",
            entity_name="Acme",
            signal_type="risk",
            severity="high",
            summary="test",
            status="pending",  # invalid
        )


def test_signal_invalid_entity_type() -> None:
    with pytest.raises(ValidationError):
        Signal(
            source="rule",
            entity_type="invoice",  # invalid
            entity_name="Acme",
            signal_type="risk",
            severity="high",
            summary="test",
        )


def test_signal_round_trip() -> None:
    s = Signal(
        source="obsidian",
        entity_type="person",
        entity_name="Alice Chen",
        signal_type="people_health",
        severity="medium",
        summary="Stale 1:1",
        due_date=date(2026, 6, 20),
    )
    data = s.model_dump()
    s2 = Signal.model_validate(data)
    assert s2.id == s.id
    assert s2.due_date == date(2026, 6, 20)


def test_signal_defaults() -> None:
    s = Signal(
        source="rule",
        entity_type="deal",
        entity_name="Big Deal",
        signal_type="sow_loe_review",
        severity="high",
        summary="SOW unsigned",
    )
    assert s.status == "open"
    assert s.requires_manager_attention is False
    assert s.due_date is None
    assert s.signal_date == date.today()


# ---------------------------------------------------------------------------
# ActionItem
# ---------------------------------------------------------------------------


def test_action_item_valid() -> None:
    ai = ActionItem(assigned_to="manager", description="Follow up with Alice")
    assert ai.status == "open"
    assert isinstance(ai.id, str)


def test_action_item_invalid_status() -> None:
    with pytest.raises(ValidationError):
        ActionItem(assigned_to="manager", description="test", status="cancelled")


# ---------------------------------------------------------------------------
# RawDocument
# ---------------------------------------------------------------------------


def test_raw_document_valid() -> None:
    doc = RawDocument(
        source_type="obsidian",
        source_path="/vault/notes/1on1_alice.md",
        content_hash="abc123",
        content="# Meeting notes",
    )
    assert doc.metadata == {}
    assert isinstance(doc.ingested_at, datetime)


def test_raw_document_invalid_source_type() -> None:
    with pytest.raises(ValidationError):
        RawDocument(
            source_type="unknown_source",
            source_path="/vault/test.md",
            content_hash="abc",
            content="test",
        )


# ---------------------------------------------------------------------------
# StaffingForecastRow
# ---------------------------------------------------------------------------


def test_staffing_forecast_row_valid() -> None:
    row = StaffingForecastRow(
        person_name="Alice Chen",
        week_start=date(2026, 6, 16),
        client="Acme Corp",
        allocation_pct=80.0,
        forecast_type="confirmed",
    )
    assert row.allocation_pct == 80.0


def test_staffing_forecast_row_invalid_forecast_type() -> None:
    with pytest.raises(ValidationError):
        StaffingForecastRow(
            person_name="Alice",
            week_start=date(2026, 6, 16),
            forecast_type="maybe",  # invalid
        )


# ---------------------------------------------------------------------------
# DealRow
# ---------------------------------------------------------------------------


def test_deal_row_valid() -> None:
    row = DealRow(
        account="Acme Corp",
        deal_name="ML Platform Q3",
        stage="Proposal",
        sow_status="pending",
    )
    assert row.staffing_feasibility == "feasible"


def test_deal_row_optional_close_date() -> None:
    row = DealRow(account="Acme", deal_name="Deal X")
    assert row.close_date is None


# ---------------------------------------------------------------------------
# UUID default generation
# ---------------------------------------------------------------------------


def test_all_models_generate_unique_ids() -> None:
    s1 = Signal(source="rule", entity_type="client", entity_name="A", signal_type="risk", severity="low", summary="x")
    s2 = Signal(source="rule", entity_type="client", entity_name="A", signal_type="risk", severity="low", summary="x")
    assert s1.id != s2.id


# ---------------------------------------------------------------------------
# Dashboard view models — basic construction
# ---------------------------------------------------------------------------


def test_dashboard_people_row_defaults() -> None:
    row = DashboardPeopleRow(name="Alice Chen")
    assert row.morale == "green"
    assert row.open_signal_count == 0
    assert row.highest_severity is None


def test_dashboard_deal_row_defaults() -> None:
    row = DashboardDealRow(account="Acme", deal_name="Deal")
    assert row.staffing_feasibility == "feasible"
    assert row.days_to_close is None
