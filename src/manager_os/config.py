"""Config loading and validation for Manager OS.

Loads YAML config files and .env settings. All config is loaded once
at startup and passed as dependencies to ingest/extract modules.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Settings (from .env)
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_prefix="MANAGER_OS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vault_path: str = ""
    db_path: str = "./data/processed/manager_os.duckdb"
    forecast_csv: str = "./data/raw/forecast.csv"
    deals_csv: str = "./data/raw/deals.csv"
    workspace_summary_dir: str = "./data/raw/summaries"
    gws_snapshot_dir: str = "./data/raw/gws_snapshots"
    config_dir: str = "./config"
    gemini_model: str = "gemini-2.0-flash"

    # LLM extraction (Gemini CLI provider)
    llm_enabled: bool = True
    llm_provider: str = "gemini_cli"
    gemini_cli_bin: str = "gemini"
    gemini_cli_model: str = "gemini-2.0-flash"
    gemini_cli_timeout_seconds: int = 120
    gemini_cli_workdir: str = ""
    gemini_cli_args: str = ""
    gemini_cli_yolo: bool = False
    gemini_cli_yolo_args: str = "-y"
    llm_max_candidates: int = 25
    llm_max_chars_per_note: int = 6000

    # Workspace retrieval
    workspace_retrieval_enabled: bool = False
    workspace_retrieval_provider: str = "gemini_cli"
    workspace_retrieval_yolo: bool = True
    retrieve_forecast_with_gemini: bool = True
    retrieve_calendar_with_gemini: bool = True
    retrieve_workspace_activity_with_gemini: bool = True
    forecast_query: str = ""
    calendar_lookahead_days: int = 2
    calendar_lookback_days: int = 1
    workspace_activity_lookback_days: int = 1
    workspace_activity_source: str = "google_chat_space"
    workspace_activity_chat_url: str = "https://chat.google.com/u/0/app/chat/AAQA61WgdSs"

    # Forecast source configuration
    forecast_source: str = "google_sheet_gemini"
    forecast_sheet_url: str = ""
    forecast_sheet_id: str = ""
    forecast_sheet_gid: str = ""
    forecast_export_url: str = ""
    forecast_local_csv: str = ""
    forecast_download_timeout_seconds: int = 120
    forecast_stale_after_hours: int = 24

    # Project index source configuration
    project_index_source: str = "google_sheet_gemini"
    project_index_sheet_url: str = ""
    project_index_sheet_id: str = ""
    project_index_sheet_gid: str = ""
    project_index_export_url: str = ""
    project_index_local_csv: str = ""
    project_index_download_timeout_seconds: int = 180
    project_index_stale_after_hours: int = 24
    project_index_require_exact_source: bool = True
    project_doc_search_enabled: bool = True
    project_doc_search_limit_per_project: int = 10


def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# Config YAML models
# ---------------------------------------------------------------------------


class PersonConfig(BaseModel):
    model_config = ConfigDict(strict=False)

    name: str
    aliases: list[str] = []
    role: str = ""
    level: str = ""
    track: bool = True  # False = hide from dashboard / people-health tracking

    @field_validator("aliases", mode="before")
    @classmethod
    def ensure_name_in_aliases(cls, v: list[str], info: Any) -> list[str]:
        return v


class ClientConfig(BaseModel):
    model_config = ConfigDict(strict=False)

    name: str
    aliases: list[str] = []
    engagement: str = ""


class SourcePriorityConfig(BaseModel):
    model_config = ConfigDict(strict=False)

    confidence_weights: dict[str, float] = {}
    conflict_resolution_order: list[str] = []
    forecast_column_aliases: dict[str, str] = {}
    deal_column_aliases: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def _config_dir(settings: Settings | None = None) -> Path:
    if settings:
        return Path(settings.config_dir)
    env_dir = os.environ.get("MANAGER_OS_CONFIG_DIR", "./config")
    return Path(env_dir)


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    return data


def load_people(settings: Settings | None = None) -> list[PersonConfig]:
    """Load and validate people.yaml."""
    path = _config_dir(settings) / "people.yaml"
    raw = _load_yaml(path)
    if not isinstance(raw, list):
        raise ValueError(f"people.yaml must be a YAML list, got {type(raw).__name__}")
    return [PersonConfig.model_validate(item) for item in raw]


def load_clients(settings: Settings | None = None) -> list[ClientConfig]:
    """Load and validate clients.yaml."""
    path = _config_dir(settings) / "clients.yaml"
    raw = _load_yaml(path)
    if not isinstance(raw, list):
        raise ValueError(f"clients.yaml must be a YAML list, got {type(raw).__name__}")
    return [ClientConfig.model_validate(item) for item in raw]


def load_deal_aliases(settings: Settings | None = None) -> dict[str, str]:
    """Load deal_aliases.yaml. Returns {raw_name: canonical_name}."""
    path = _config_dir(settings) / "deal_aliases.yaml"
    raw = _load_yaml(path)
    if not isinstance(raw, dict):
        raise ValueError(
            f"deal_aliases.yaml must be a YAML mapping, got {type(raw).__name__}"
        )
    return {str(k): str(v) for k, v in raw.items()}


def load_source_priority(settings: Settings | None = None) -> SourcePriorityConfig:
    """Load source_priority.yaml."""
    path = _config_dir(settings) / "source_priority.yaml"
    raw = _load_yaml(path)
    if not isinstance(raw, dict):
        raise ValueError(
            f"source_priority.yaml must be a YAML mapping, got {type(raw).__name__}"
        )
    return SourcePriorityConfig.model_validate(raw)


def load_meeting_prep_rules(settings: Settings | None = None) -> dict:
    """Load meeting_prep_rules.yaml.

    Returns the parsed dict with a top-level "rules" key containing a list
    of rule dicts. Falls back to an empty rules list with a generic fallback
    if the file doesn't exist, so the system is always operable.
    """
    path = _config_dir(settings) / "meeting_prep_rules.yaml"
    try:
        raw = _load_yaml(path)
    except FileNotFoundError:
        return {"rules": [{"id": "generic_fallback", "name": "Generic Meeting", "match": {}, "prep_required": True}]}
    if not isinstance(raw, dict) or "rules" not in raw:
        return {"rules": [{"id": "generic_fallback", "name": "Generic Meeting", "match": {}, "prep_required": True}]}
    return raw


# ---------------------------------------------------------------------------
# Entity resolution helpers
# ---------------------------------------------------------------------------


def get_person_by_alias(
    text: str, people: list[PersonConfig]
) -> str | None:
    """Return the canonical person name for a given alias string, or None."""
    text_lower = text.lower().strip()
    for person in people:
        for alias in person.aliases:
            if alias.lower().strip() == text_lower:
                return person.name
    return None


def get_client_by_alias(
    text: str, clients: list[ClientConfig]
) -> str | None:
    """Return the canonical client name for a given alias string, or None."""
    text_lower = text.lower().strip()
    for client in clients:
        for alias in client.aliases:
            if alias.lower().strip() == text_lower:
                return client.name
    return None
