"""Tests for ingest/gws_client.py (Issue #23)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.gws_client import (
    ingest_gws_snapshots,
    _ingest_calendar_file,
    _ingest_gmail_file,
    _ingest_chat_file,
    _parse_gws_date,
    _parse_gws_datetime,
)

# Path to fixtures
_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "gws_snapshots"
_TARGET_DATE = date(2026, 6, 13)


@pytest.fixture()
def conn():
    return get_connection(":memory:")


# ------------------------------------------------------------------
# Date parsing helpers
# ------------------------------------------------------------------


def test_parse_gws_datetime_with_offset() -> None:
    dt = _parse_gws_datetime("2026-06-13T10:00:00-07:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.hour == 10


def test_parse_gws_datetime_dict_form() -> None:
    dt = _parse_gws_datetime({"dateTime": "2026-06-13T14:00:00+00:00"})
    assert dt is not None


def test_parse_gws_datetime_date_only_dict() -> None:
    dt = _parse_gws_datetime({"date": "2026-06-13"})
    assert dt is not None
    assert dt.year == 2026


def test_parse_gws_datetime_z_suffix() -> None:
    dt = _parse_gws_datetime("2026-06-13T09:15:00Z")
    assert dt is not None


def test_parse_gws_date_extracts_date() -> None:
    d = _parse_gws_date({"dateTime": "2026-06-13T10:00:00-07:00"})
    assert d == date(2026, 6, 13)


def test_parse_gws_datetime_invalid_returns_none() -> None:
    assert _parse_gws_datetime("not-a-date") is None


# ------------------------------------------------------------------
# Calendar ingestion
# ------------------------------------------------------------------


def test_calendar_ingests_meetings(conn) -> None:
    snap = _FIXTURE_DIR / "calendar" / "2026-06-13.json"
    result = _ingest_calendar_file(snap, conn, force=False)
    # 3 events in fixture; all have valid start dates
    assert result.ingested == 3
    count = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    assert count == 3


def test_calendar_meeting_has_correct_title(conn) -> None:
    snap = _FIXTURE_DIR / "calendar" / "2026-06-13.json"
    _ingest_calendar_file(snap, conn, force=False)
    titles = [r[0] for r in conn.execute("SELECT title FROM meetings").fetchall()]
    assert "1:1 with Alice Chen" in titles
    assert "Acme Corp Weekly Sync" in titles


def test_calendar_meeting_has_attendees(conn) -> None:
    snap = _FIXTURE_DIR / "calendar" / "2026-06-13.json"
    _ingest_calendar_file(snap, conn, force=False)
    row = conn.execute(
        "SELECT attendees FROM meetings WHERE title = '1:1 with Alice Chen'"
    ).fetchone()
    attendees = json.loads(row[0])
    assert "Alice Chen" in attendees


def test_calendar_meeting_has_start_time(conn) -> None:
    snap = _FIXTURE_DIR / "calendar" / "2026-06-13.json"
    _ingest_calendar_file(snap, conn, force=False)
    row = conn.execute(
        "SELECT start_time FROM meetings WHERE title = '1:1 with Alice Chen'"
    ).fetchone()
    assert row[0] == "10:00"


def test_calendar_ingests_raw_document(conn) -> None:
    snap = _FIXTURE_DIR / "calendar" / "2026-06-13.json"
    _ingest_calendar_file(snap, conn, force=False)
    count = conn.execute(
        "SELECT COUNT(*) FROM raw_documents WHERE source_type = 'gws'"
    ).fetchone()[0]
    assert count >= 2  # events with non-empty description


def test_calendar_idempotent(conn) -> None:
    snap = _FIXTURE_DIR / "calendar" / "2026-06-13.json"
    _ingest_calendar_file(snap, conn, force=False)
    result2 = _ingest_calendar_file(snap, conn, force=False)
    assert result2.skipped == 3
    assert result2.ingested == 0


def test_calendar_force_re_ingests(conn) -> None:
    snap = _FIXTURE_DIR / "calendar" / "2026-06-13.json"
    _ingest_calendar_file(snap, conn, force=False)
    result2 = _ingest_calendar_file(snap, conn, force=True)
    assert result2.ingested == 3


def test_calendar_missing_file_returns_failed(conn, tmp_path: Path) -> None:
    result = _ingest_calendar_file(tmp_path / "missing.json", conn, force=False)
    assert result.failed == 1


def test_calendar_malformed_json(conn, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    result = _ingest_calendar_file(bad, conn, force=False)
    assert result.failed == 1


def test_calendar_non_list_json(conn, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"key": "value"}', encoding="utf-8")
    result = _ingest_calendar_file(bad, conn, force=False)
    assert result.failed == 1


# ------------------------------------------------------------------
# Gmail ingestion
# ------------------------------------------------------------------


def test_gmail_ingests_threads(conn) -> None:
    snap = _FIXTURE_DIR / "gmail" / "2026-06-13.json"
    result = _ingest_gmail_file(snap, conn, force=False)
    assert result.ingested == 2
    count = conn.execute(
        "SELECT COUNT(*) FROM raw_documents WHERE source_type = 'gmail'"
    ).fetchone()[0]
    assert count == 2


def test_gmail_raw_document_contains_subject(conn) -> None:
    snap = _FIXTURE_DIR / "gmail" / "2026-06-13.json"
    _ingest_gmail_file(snap, conn, force=False)
    row = conn.execute(
        "SELECT content FROM raw_documents WHERE source_type = 'gmail' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert "Subject:" in row[0]


def test_gmail_idempotent(conn) -> None:
    snap = _FIXTURE_DIR / "gmail" / "2026-06-13.json"
    _ingest_gmail_file(snap, conn, force=False)
    result2 = _ingest_gmail_file(snap, conn, force=False)
    assert result2.skipped == 2
    assert result2.ingested == 0


def test_gmail_metadata_has_thread_id(conn) -> None:
    snap = _FIXTURE_DIR / "gmail" / "2026-06-13.json"
    _ingest_gmail_file(snap, conn, force=False)
    row = conn.execute(
        "SELECT metadata FROM raw_documents WHERE source_type = 'gmail' LIMIT 1"
    ).fetchone()
    meta = json.loads(row[0])
    assert meta.get("type") == "gmail_thread"


# ------------------------------------------------------------------
# Chat ingestion
# ------------------------------------------------------------------


def test_chat_ingests_by_space(conn) -> None:
    snap = _FIXTURE_DIR / "chat" / "2026-06-13.json"
    result = _ingest_chat_file(snap, conn, force=False)
    # 2 spaces: "Acme Delivery Team" and "Manager DMs"
    assert result.ingested == 2
    count = conn.execute("SELECT COUNT(*) FROM raw_documents WHERE source_type = 'gws'").fetchone()[0]
    assert count == 2


def test_chat_body_contains_messages(conn) -> None:
    snap = _FIXTURE_DIR / "chat" / "2026-06-13.json"
    _ingest_chat_file(snap, conn, force=False)
    row = conn.execute(
        "SELECT content FROM raw_documents WHERE content LIKE '%Acme%' LIMIT 1"
    ).fetchone()
    assert row is not None
    assert "alice@example.com" in row[0]


def test_chat_idempotent(conn) -> None:
    snap = _FIXTURE_DIR / "chat" / "2026-06-13.json"
    _ingest_chat_file(snap, conn, force=False)
    result2 = _ingest_chat_file(snap, conn, force=False)
    assert result2.skipped == 2
    assert result2.ingested == 0


# ------------------------------------------------------------------
# ingest_gws_snapshots (top-level)
# ------------------------------------------------------------------


def test_ingest_gws_snapshots_full_run(conn) -> None:
    result = ingest_gws_snapshots(_FIXTURE_DIR, conn, target_date=_TARGET_DATE)
    assert result.ingested > 0
    assert result.failed == 0


def test_ingest_gws_snapshots_missing_dir(conn, tmp_path: Path) -> None:
    result = ingest_gws_snapshots(tmp_path / "nonexistent", conn, target_date=_TARGET_DATE)
    assert result.ingested == 0
    assert result.failed == 0


def test_ingest_gws_snapshots_missing_date_files(conn, tmp_path: Path) -> None:
    # Dir exists but no files for the target date
    (tmp_path / "calendar").mkdir()
    result = ingest_gws_snapshots(tmp_path, conn, target_date=date(2099, 1, 1))
    assert result.ingested == 0
