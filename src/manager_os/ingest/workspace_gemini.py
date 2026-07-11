"""Read-only retrieval helpers for Google Workspace data via Gemini CLI.

All retrieval is read-only — no create, edit, delete, move, or send.
Uses Gemini CLI YOLO mode (-y) for headless auto-approved access.

Safety:
- Every prompt includes read-only instructions.
- No writes to Google Workspace.
- Retrieved data is written to local snapshot files only.
- Snapshot directory is gitignored.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


WORKSPACE_RETRIEVAL_ENABLED = (
    _env("MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED", "false").lower() in ("true", "yes", "1")
)
RETRIEVAL_PROVIDER = _env("MANAGER_OS_WORKSPACE_RETRIEVAL_PROVIDER", "gemini_cli")
RETRIEVAL_YOLO = (
    _env("MANAGER_OS_WORKSPACE_RETRIEVAL_YOLO", "true").lower() in ("true", "yes", "1")
)

RETRIEVE_FORECAST = (
    _env("MANAGER_OS_RETRIEVE_FORECAST_WITH_GEMINI", "true").lower() in ("true", "yes", "1")
)
RETRIEVE_CALENDAR = (
    _env("MANAGER_OS_RETRIEVE_CALENDAR_WITH_GEMINI", "true").lower() in ("true", "yes", "1")
)
RETRIEVE_ACTIVITY = (
    _env("MANAGER_OS_RETRIEVE_WORKSPACE_ACTIVITY_WITH_GEMINI", "true").lower() in ("true", "yes", "1")
)

FORECAST_QUERY = _env(
    "MANAGER_OS_FORECAST_QUERY",
    "Find the latest people/staffing forecast for AI ML team and export the usable tabular data as CSV-like JSON.",
)
CALENDAR_LOOKAHEAD_DAYS = int(_env("MANAGER_OS_CALENDAR_LOOKAHEAD_DAYS", "2"))
CALENDAR_LOOKBACK_DAYS = int(_env("MANAGER_OS_CALENDAR_LOOKBACK_DAYS", "1"))
ACTIVITY_LOOKBACK_DAYS = int(_env("MANAGER_OS_WORKSPACE_ACTIVITY_LOOKBACK_DAYS", "1"))


# ------------------------------------------------------------------
# Read-only prompt prefix
# ------------------------------------------------------------------

_READ_ONLY_PREFIX = """
You are operating in read-only mode.
Do NOT create, edit, delete, send, move, or modify anything.
Retrieve only the requested information.
Return strict JSON only.
If you cannot retrieve the data, return:
{"ok": false, "error": "...", "source": "..."}
Do not guess.
Include source metadata and retrieved_at.
"""


# ------------------------------------------------------------------
# Retrieval result
# ------------------------------------------------------------------


@dataclass
class RetrievalResult:
    ok: bool = False
    error: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    source_title: str = ""
    source_url: str = ""
    retrieved_at: str = ""
    json_text: str = ""
    dry_run: bool = False
    written_to: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at or datetime.utcnow().isoformat(),
            "items": self.items,
        }


# ------------------------------------------------------------------
# Build Gemini command with optional yolo args
# ------------------------------------------------------------------


def _build_gemini_cmd(use_yolo: bool = True) -> list[str]:
    """Build base Gemini CLI command array."""
    from manager_os.llm.gemini_cli import GEMINI_CLI_BIN, GEMINI_CLI_MODEL, GEMINI_CLI_ARGS

    cmd = [GEMINI_CLI_BIN]
    if GEMINI_CLI_MODEL:
        cmd.extend(["--model", GEMINI_CLI_MODEL])
    if GEMINI_CLI_ARGS:
        cmd.extend(GEMINI_CLI_ARGS.split())
    if use_yolo:
        cmd.append("-y")
    return cmd


def _run_gemini_retrieval(
    prompt: str,
    use_yolo: bool = True,
    timeout: int = 180,
    dry_run: bool = False,
) -> tuple[str, list[str]]:
    """Run Gemini CLI for a retrieval prompt.

    Returns (stdout, cmd_array) on success.
    Raises RuntimeError on failure.
    """
    import subprocess

    if dry_run:
        return "", []

    from manager_os.llm.gemini_cli import GEMINI_CLI_TIMEOUT

    cmd = _build_gemini_cmd(use_yolo=use_yolo)
    full_prompt = f"{_READ_ONLY_PREFIX}\n\n{prompt}"
    effective_timeout = timeout if timeout else GEMINI_CLI_TIMEOUT

    proc = subprocess.run(
        cmd + ["--prompt", full_prompt],
        capture_output=True,
        text=True,
        timeout=effective_timeout,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"Gemini CLI exited with code {proc.returncode}: {proc.stderr.strip()[:500]}"
        )

    return proc.stdout.strip(), cmd


def _parse_retrieval_json(raw: str) -> dict[str, Any]:
    """Parse JSON from Gemini retrieval response."""
    from manager_os.llm.gemini_cli import _extract_json

    clean = _extract_json(raw)
    data = json.loads(clean)
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"items": data, "ok": True, "retrieved_at": datetime.utcnow().isoformat()}
    raise ValueError(f"Unexpected JSON type: {type(data).__name__}")


def _write_snapshot(
    data: dict[str, Any],
    subdir: str,
    target_date: date,
    output_dir: str | None = None,
) -> str:
    """Write a retrieval snapshot to disk."""
    if output_dir:
        snap_dir = Path(output_dir)
    else:
        snap_dir = Path("data/raw/workspace_snapshots") / subdir
    snap_dir.mkdir(parents=True, exist_ok=True)
    path = snap_dir / f"{target_date.isoformat()}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    return str(path)


# ------------------------------------------------------------------
# 1. Forecast retrieval
# ------------------------------------------------------------------

FORECAST_PROMPT_TEMPLATE = """\
[Read-only] Get latest AI/ML forecast.
Query: {query_hint}
Return ONLY JSON:
{{"ok":true,"source_title":"str","source_url":"str","retrieved_at":"ISO8601","rows":[{{"person":"str","week_start":"YYYY-MM-DD","allocation_pct":100,"project":"str","client":"str"}}]}}
Fail: {{"ok":false,"error":"str"}}
"""


def retrieve_forecast(
    target_date: date,
    use_yolo: bool = True,
    timeout: int = 180,
    dry_run: bool = False,
    output_dir: str | None = None,
    query_hint: str = "",
) -> RetrievalResult:
    """Retrieve latest forecasting data from Google Workspace via Gemini CLI.

    Use *query_hint* to guide Gemini to a specific spreadsheet name or URL.

    Note: this template no longer injects ``_READ_ONLY_PREFIX`` directly —
    ``_run_gemini_retrieval`` already prepends it for every live call, so
    injecting it here too would pay for it twice. The dry-run preview below
    is composed the same way so ``--print-prompt`` stays representative of
    what is actually sent live.
    """
    effective_hint = query_hint or FORECAST_QUERY or "Find the latest people/staffing forecast for AI ML team."
    prompt = FORECAST_PROMPT_TEMPLATE.format(
        query_hint=effective_hint,
    )

    result = RetrievalResult(dry_run=dry_run)

    if dry_run:
        result.json_text = f"{_READ_ONLY_PREFIX}\n\n{prompt}"
        return result

    try:
        raw, cmd = _run_gemini_retrieval(prompt, use_yolo=use_yolo, timeout=timeout)
        data = _parse_retrieval_json(raw)
        result.ok = data.get("ok", False)
        result.error = data.get("error", "")
        result.source_title = data.get("source_title", "")
        result.source_url = data.get("source_url", "")
        result.retrieved_at = data.get("retrieved_at", datetime.utcnow().isoformat())
        result.items = data.get("rows", data.get("items", []))
        result.json_text = raw
        if result.ok:
            path = _write_snapshot(data, "forecast", target_date, output_dir)
            result.written_to = path
    except Exception as exc:
        result.ok = False
        result.error = str(exc)

    return result


# ------------------------------------------------------------------
# 2. Calendar retrieval
# ------------------------------------------------------------------

CALENDAR_PROMPT_TEMPLATE = """\
[Read-only] Get calendar for {target_date}; lookback={lookback_days}d ahead={lookahead_days}d.
Return ONLY JSON:
{{"ok":true,"source":"google_calendar_gemini","retrieved_at":"ISO8601","events":[{{"title":"str","start_time":"ISO","end_time":"ISO","attendees":["str"],"location":"str","description_summary":"str","external_id":"str"}}]}}
Fail: {{"ok":false,"error":"str"}}
"""


def retrieve_calendar(
    target_date: date,
    use_yolo: bool = True,
    timeout: int = 180,
    dry_run: bool = False,
    output_dir: str | None = None,
    lookback_days: int | None = None,
    lookahead_days: int | None = None,
) -> RetrievalResult:
    """Retrieve calendar events from Google Workspace via Gemini CLI.

    Note: see ``retrieve_forecast`` docstring re: ``_READ_ONLY_PREFIX`` no
    longer being double-injected.
    """
    prompt = CALENDAR_PROMPT_TEMPLATE.format(
        target_date=target_date.isoformat(),
        lookback_days=lookback_days if lookback_days is not None else CALENDAR_LOOKBACK_DAYS,
        lookahead_days=lookahead_days if lookahead_days is not None else CALENDAR_LOOKAHEAD_DAYS,
    )

    result = RetrievalResult(dry_run=dry_run)

    if dry_run:
        result.json_text = f"{_READ_ONLY_PREFIX}\n\n{prompt}"
        return result

    try:
        raw, cmd = _run_gemini_retrieval(prompt, use_yolo=use_yolo, timeout=timeout)
        data = _parse_retrieval_json(raw)
        result.ok = data.get("ok", False)
        result.error = data.get("error", "")
        result.retrieved_at = data.get("retrieved_at", datetime.utcnow().isoformat())
        result.items = data.get("events", data.get("items", []))
        result.json_text = raw
        if result.ok:
            path = _write_snapshot(data, "calendar", target_date, output_dir)
            result.written_to = path
    except Exception as exc:
        result.ok = False
        result.error = str(exc)

    return result


# ------------------------------------------------------------------
# 3. Workspace activity retrieval (Google Chat summary)
# ------------------------------------------------------------------

ACTIVITY_PROMPT_TEMPLATE = """\
You are operating in read-only mode.
Do not send, edit, delete, or modify any Chat messages or Google Workspace data.

Open this Google Chat space/app URL:
{chat_url}

Retrieve the daily activity summary and action items for {target_date} (or the most recent within {lookback_days} day(s) if not available).

Return ONLY JSON:
{{"ok":true,"source":"google_chat_activity_summary","source_url":"{chat_url}","retrieved_at":"ISO8601","summary_date":"YYYY-MM-DD","summary":"str","items":[{{"type":"action_item|mention|doc_update|other","title":"str","description":"str","source_url":"str","requires_attention":true,"assigned_to":"str","due_date":"YYYY-MM-DD|null","entity_type":"str","entity_name":"str","confidence":1.0}}],"action_items":[]}}
Fail: {{"ok":false,"source":"google_chat_activity_summary","error":"str"}}
Do not guess.
"""


def retrieve_activity(
    target_date: date,
    use_yolo: bool = True,
    timeout: int = 180,
    dry_run: bool = False,
    output_dir: str | None = None,
    lookback_days: int | None = None,
    chat_url: str | None = None,
) -> RetrievalResult:
    """Retrieve workspace activity summary from configured Google Chat space via Gemini CLI."""
    from manager_os.config import get_settings
    settings = get_settings()
    url = chat_url or settings.workspace_activity_chat_url
    days = lookback_days or settings.workspace_activity_lookback_days

    if not url:
        result = RetrievalResult(dry_run=dry_run)
        result.ok = False
        result.error = "MANAGER_OS_WORKSPACE_ACTIVITY_CHAT_URL is not configured."
        return result

    prompt = ACTIVITY_PROMPT_TEMPLATE.format(
        target_date=target_date.isoformat(),
        lookback_days=days,
        chat_url=url,
    )

    result = RetrievalResult(dry_run=dry_run)

    if dry_run:
        result.json_text = prompt
        return result

    try:
        raw, cmd = _run_gemini_retrieval(prompt, use_yolo=use_yolo, timeout=timeout)
        data = _parse_retrieval_json(raw)
        result.ok = data.get("ok", False)
        result.error = data.get("error", "")
        result.retrieved_at = data.get("retrieved_at", datetime.utcnow().isoformat())
        result.items = data.get("items", [])
        result.json_text = raw
        if result.ok:
            # Enrich snapshot metadata for better tracking
            data["source"] = "google_chat_activity_summary"
            data["source_url"] = url
            data["action_items_count"] = len(data.get("action_items", []))
            data["requires_attention_count"] = sum(
                1 for i in data.get("items", []) if i.get("requires_attention")
            )
            path = _write_snapshot(data, "activity", target_date, output_dir)
            result.written_to = path
    except Exception as exc:
        result.ok = False
        result.error = str(exc)

    return result


# ------------------------------------------------------------------
# Doctor
# ------------------------------------------------------------------


@dataclass
class WorkspaceDoctorResult:
    gemini_available: bool = False
    yolo_configured: bool = False
    retrieval_enabled: bool = False
    forecast_enabled: bool = False
    calendar_enabled: bool = False
    activity_enabled: bool = False
    errors: list[str] = field(default_factory=list)


def workspace_doctor() -> WorkspaceDoctorResult:
    """Check workspace retrieval configuration without running any retrieval."""
    from manager_os.llm.gemini_cli import is_gemini_available

    result = WorkspaceDoctorResult(
        gemini_available=is_gemini_available(),
        yolo_configured=RETRIEVAL_YOLO,
        retrieval_enabled=WORKSPACE_RETRIEVAL_ENABLED,
        forecast_enabled=RETRIEVE_FORECAST,
        calendar_enabled=RETRIEVE_CALENDAR,
        activity_enabled=RETRIEVE_ACTIVITY,
    )

    if not is_gemini_available():
        result.errors.append("Gemini CLI not available. Set MANAGER_OS_GEMINI_CLI_BIN.")
    if not WORKSPACE_RETRIEVAL_ENABLED:
        result.errors.append("Workspace retrieval disabled. Set MANAGER_OS_WORKSPACE_RETRIEVAL_ENABLED=true.")

    return result