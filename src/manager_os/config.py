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
