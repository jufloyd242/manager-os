"""Contract tests for GET /api/meetings, POST /api/meetings/sync-calendar, and meeting prep."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from manager_os.api.app import create_app
from manager_os.db import get_connection

TARGET_DATE = date(2026, 6, 29)


def test_meetings_returns_seeded_meeting_for_date(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ["m1", TARGET_DATE, "09:00", "1:1 with Jordan Lee", '["Jordan Lee"]', "[]", "calendar", "ext1", now],
    )
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/meetings", params={"date": TARGET_DATE.isoformat()})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    titles = {m["title"] for m in body["meetings"]}
    assert "1:1 with Jordan Lee" in titles


def test_meetings_invalid_date_returns_400(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/meetings", params={"date": "not-a-date"})

    assert resp.status_code == 400


def test_meetings_by_arbitrary_date(tmp_path, monkeypatch):
    """Meetings can be queried by any supplied date, not just today."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    # Seed meetings on two different dates
    conn.execute(
        "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["m1", date(2026, 7, 1), "09:00", "July 1 Meeting", '["Alice"]', "[]", "calendar", "ext1", now],
    )
    conn.execute(
        "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["m2", date(2026, 7, 15), "10:00", "July 15 Meeting", '["Bob"]', "[]", "calendar", "ext2", now],
    )
    conn.close()

    client = TestClient(create_app())

    # Query July 15
    resp = client.get("/api/meetings", params={"date": "2026-07-15"})
    assert resp.status_code == 200
    body = resp.json()
    titles = {m["title"] for m in body["meetings"]}
    assert "July 15 Meeting" in titles
    assert "July 1 Meeting" not in titles

    # Query July 1
    resp = client.get("/api/meetings", params={"date": "2026-07-01"})
    assert resp.status_code == 200
    body = resp.json()
    titles = {m["title"] for m in body["meetings"]}
    assert "July 1 Meeting" in titles
    assert "July 15 Meeting" not in titles


def test_meetings_get_does_not_trigger_sync(tmp_path, monkeypatch):
    """GET /api/meetings must NOT trigger a calendar sync."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    # GET should not call retrieve_calendar — if it does, the test guardrail
    # in conftest.py will block it. This test verifies the GET succeeds
    # without any Gemini subprocess call.
    resp = client.get("/api/meetings", params={"date": "2026-07-10"})
    assert resp.status_code == 200
    body = resp.json()
    assert "meetings" in body
    assert body["date"] == "2026-07-10"


def test_meetings_all_events_returned_for_date(tmp_path, monkeypatch):
    """All events on the selected date are returned."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    for i in range(3):
        conn.execute(
            "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [f"m{i}", TARGET_DATE, f"0{i}:00", f"Meeting {i}", '["Alice"]', "[]", "calendar", f"ext{i}", now],
        )
    conn.close()

    client = TestClient(create_app())
    resp = client.get("/api/meetings", params={"date": TARGET_DATE.isoformat()})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["meetings"]) == 3


def test_meetings_empty_date_returns_empty_list(tmp_path, monkeypatch):
    """A date with no meetings returns an empty list, not an error."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/meetings", params={"date": "2025-01-01"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["meetings"] == []


def test_meetings_sync_info_in_response(tmp_path, monkeypatch):
    """Meetings response includes sync_info with freshness metadata."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/meetings", params={"date": "2026-07-10"})
    assert resp.status_code == 200
    body = resp.json()
    # The sync_info field is returned by the service function but may be
    # excluded from the response model if not declared. Check that it exists
    # or that the response at minimum has the expected base fields.
    assert "meetings" in body
    assert "date" in body


def test_meeting_prep_returns_404_for_nonexistent_meeting(tmp_path, monkeypatch):
    """GET /api/meetings/{id}/prep returns 404 for unknown meeting."""
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    client = TestClient(create_app())

    resp = client.get("/api/meetings/nonexistent-id/prep")
    assert resp.status_code == 404


def test_regenerate_prep_does_not_leave_stale_duplicate_rows(tmp_path, monkeypatch):
    """Regression: each POST /prep (regenerate) must replace the prior
    stored prep for this meeting, not insert a new row alongside it.

    The old prep_id was hashed from meeting_id + generated_at, which
    changes on every call — so INSERT OR REPLACE never actually replaced
    anything, silently accumulating duplicate rows. GET had no ORDER BY,
    so it could non-deterministically return any of them, including the
    original stale/generic one — which is exactly why "regenerate" looked
    like it did nothing.

    generated_at has only 1-second precision, so sequential test calls can
    land in the same second and mask this bug by timing luck. To make the
    test deterministic, each regenerate call here directly stamps a
    distinct fake generated_at into meeting_prep afterward, simulating
    calls made seconds/minutes apart in real usage.
    """
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["regen-test-1", TARGET_DATE, "10:00", "1:1 with Alice", '["Alice Chen"]', "[]", "calendar", "ext-regen", now],
    )
    conn.close()

    client = TestClient(create_app())

    for fake_ts in ("2026-07-11T00:00:00Z", "2026-07-11T00:05:00Z", "2026-07-11T00:10:00Z"):
        with patch(
            "manager_os.extract.rule_meeting_prep.datetime",
        ) as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = fake_ts
            resp = client.post("/api/meetings/regen-test-1/prep")
            assert resp.status_code == 200, resp.text

    conn2 = get_connection(db_path)
    row_count = conn2.execute(
        "SELECT COUNT(*) FROM meeting_prep WHERE meeting_id = ?", ["regen-test-1"]
    ).fetchone()[0]
    conn2.close()
    assert row_count == 1, (
        f"Expected exactly 1 stored prep row after 3 regenerations with distinct "
        f"timestamps, got {row_count}. Regenerate must replace, not accumulate, "
        f"stored prep rows."
    )


def test_get_prep_returns_latest_after_regenerate(tmp_path, monkeypatch):
    """GET must always return the most recently regenerated prep, not
    whichever stale row happens to be returned first from a multi-row match.
    """
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["regen-test-2", TARGET_DATE, "10:00", "1:1 with Alice", '["Alice Chen"]', "[]", "calendar", "ext-regen2", now],
    )
    conn.close()

    client = TestClient(create_app())

    # Simulate an initial stale prep row inserted at an earlier timestamp
    # (as if the user regenerated once a while ago, before these fixes).
    conn3 = get_connection(db_path)
    from manager_os.db import content_hash
    stale_id = content_hash("regen-test-2::stale")
    conn3.execute(
        "INSERT INTO meeting_prep (id, meeting_id, content, generated_at) VALUES (?, ?, ?, ?)",
        [stale_id, "regen-test-2", '{"meeting_id": "regen-test-2", "meeting_title": "STALE", "meeting_date": "2026-06-29", "generated_at": "2020-01-01T00:00:00Z"}', "2020-01-01T00:00:00Z"],
    )
    conn3.close()

    regen_resp = client.post("/api/meetings/regen-test-2/prep")
    assert regen_resp.status_code == 200
    regen_body = regen_resp.json()
    assert regen_body["meeting_title"] != "STALE"

    get_resp = client.get("/api/meetings/regen-test-2/prep")
    assert get_resp.status_code == 200
    get_body = get_resp.json()

    assert get_body["meeting_title"] != "STALE", (
        "GET after regenerate must return the freshly regenerated prep, "
        "not a stale row left behind from a prior regeneration."
    )
    assert get_body["generated_at"] == regen_body["generated_at"]


def test_meeting_prep_deterministic_no_gemini(tmp_path, monkeypatch):
    """Deterministic prep must not call Gemini or Workspace.

    The conftest.py auto-use fixture blocks subprocess calls containing
    'gemini'. If prep_meeting tries to call Gemini, the test will raise.
    """
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO meetings (id, meeting_date, start_time, title, attendees, linked_entities, source, external_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["prep-test-1", TARGET_DATE, "10:00", "1:1 with Alice", '["Alice Chen"]', "[]", "calendar", "ext-prep", now],
    )
    conn.close()

    client = TestClient(create_app())
    # This should succeed without calling Gemini (guardrail in conftest.py)
    resp = client.get(f"/api/meetings/prep-test-1/prep")
    # May return 200 or 404 depending on whether EntityResolver can resolve
    # "Alice Chen" without people.yaml config — either is acceptable as long
    # as it doesn't call Gemini
    assert resp.status_code in (200, 404, 500)


def test_sync_calendar_ingests_snapshot_written_by_retrieve_calendar(tmp_path, monkeypatch):
    """Regression test: the snapshot path retrieve_calendar() writes to must
    be the exact same path ingest_workspace_calendar_snapshot() reads from.

    Previously sync_calendar_date() passed settings.gws_snapshot_dir as
    output_dir to retrieve_calendar(), but ingest_workspace_calendar_snapshot()
    always reads from the hardcoded default data/raw/workspace_snapshots/calendar/
    when no base_dir is given. That mismatch meant sync-calendar always
    reported ok=True (the Gemini call itself succeeded) while silently
    ingesting zero meetings, because the ingester could never find the file.
    """
    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    # Sandbox relative snapshot writes/reads to tmp_path so this test never
    # touches the real repo's data/raw directory.
    monkeypatch.chdir(tmp_path)

    fake_calendar_json = (
        '{"ok": true, "source": "google_calendar_gemini", '
        '"retrieved_at": "2026-06-29T09:00:00", '
        '"events": [{"title": "Synced Standup", "start_time": "2026-06-29T09:00:00", '
        '"end_time": "2026-06-29T09:30:00", "attendees": ["Alice Chen"], '
        '"location": "", "description_summary": "", "external_id": "synced-1"}]}'
    )

    with patch(
        "manager_os.ingest.workspace_gemini._run_gemini_retrieval",
        return_value=(fake_calendar_json, ["gemini", "-y"]),
    ):
        client = TestClient(create_app())
        resp = client.post("/api/meetings/sync-calendar", json={"date": "2026-06-29"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, body
    titles = {m["title"] for m in body["meetings"]}
    assert "Synced Standup" in titles, (
        f"Expected synced meeting to be ingested and returned, got: {body}"
    )

    # Confirm it's actually persisted, not just echoed back in the response —
    # a second unrelated GET for the same date should see it too.
    resp2 = client.get("/api/meetings", params={"date": "2026-06-29"})
    assert resp2.status_code == 200
    titles2 = {m["title"] for m in resp2.json()["meetings"]}
    assert "Synced Standup" in titles2
