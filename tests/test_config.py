"""Tests for config.py loaders and entity resolution helpers."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from manager_os.config import (
    ClientConfig,
    PersonConfig,
    SourcePriorityConfig,
    get_client_by_alias,
    get_person_by_alias,
    load_clients,
    load_deal_aliases,
    load_people,
    load_source_priority,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Create a temp config directory with valid YAML files."""
    people_data = [
        {"name": "Alice Chen", "aliases": ["Alice", "alice", "Alice Chen"], "role": "Senior ML Engineer", "level": "L5"},
        {"name": "Bob Martinez", "aliases": ["Bob", "bob", "Bob Martinez"], "role": "ML Engineer", "level": "L4"},
    ]
    clients_data = [
        {"name": "Acme Corp", "aliases": ["Acme", "acme", "Acme Corp"], "engagement": "ML Platform"},
        {"name": "Big Retail Co", "aliases": ["Big Retail", "big retail", "BRC"], "engagement": "Recs"},
    ]
    deal_aliases_data = {
        "ACME ML Platform Q3": "Acme Corp — ML Platform Build",
        "Big Retail Recs v2": "Big Retail Co — Recommendation Engine Phase 2",
    }
    source_priority_data = {
        "confidence_weights": {"obsidian": 0.9, "deals": 1.0},
        "conflict_resolution_order": ["obsidian", "deals"],
        "forecast_column_aliases": {"Person": "person"},
        "deal_column_aliases": {"Account": "account"},
    }

    (tmp_path / "people.yaml").write_text(yaml.dump(people_data))
    (tmp_path / "clients.yaml").write_text(yaml.dump(clients_data))
    (tmp_path / "deal_aliases.yaml").write_text(yaml.dump(deal_aliases_data))
    (tmp_path / "source_priority.yaml").write_text(yaml.dump(source_priority_data))
    return tmp_path


@pytest.fixture()
def mock_settings(config_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(config_dir))


# ---------------------------------------------------------------------------
# load_people
# ---------------------------------------------------------------------------


def test_load_people_success(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(config_dir))
    people = load_people()
    assert len(people) == 2
    assert people[0].name == "Alice Chen"
    assert "alice" in people[0].aliases
    assert isinstance(people[0], PersonConfig)


def test_load_people_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="people.yaml"):
        load_people()


def test_load_people_not_a_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "people.yaml").write_text("name: Alice Chen\nrole: Engineer\n")
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="must be a YAML list"):
        load_people()


# ---------------------------------------------------------------------------
# load_clients
# ---------------------------------------------------------------------------


def test_load_clients_success(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(config_dir))
    clients = load_clients()
    assert len(clients) == 2
    assert clients[0].name == "Acme Corp"
    assert isinstance(clients[0], ClientConfig)


def test_load_clients_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="clients.yaml"):
        load_clients()


# ---------------------------------------------------------------------------
# load_deal_aliases
# ---------------------------------------------------------------------------


def test_load_deal_aliases_success(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(config_dir))
    aliases = load_deal_aliases()
    assert isinstance(aliases, dict)
    assert aliases["ACME ML Platform Q3"] == "Acme Corp — ML Platform Build"


def test_load_deal_aliases_not_a_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "deal_aliases.yaml").write_text("- item1\n- item2\n")
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_deal_aliases()


# ---------------------------------------------------------------------------
# load_source_priority
# ---------------------------------------------------------------------------


def test_load_source_priority_success(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANAGER_OS_CONFIG_DIR", str(config_dir))
    sp = load_source_priority()
    assert isinstance(sp, SourcePriorityConfig)
    assert sp.confidence_weights["obsidian"] == 0.9
    assert "obsidian" in sp.conflict_resolution_order


# ---------------------------------------------------------------------------
# get_person_by_alias
# ---------------------------------------------------------------------------


def test_get_person_by_alias_exact_match() -> None:
    people = [
        PersonConfig(name="Alice Chen", aliases=["Alice", "alice", "Alice Chen"]),
    ]
    assert get_person_by_alias("alice", people) == "Alice Chen"
    assert get_person_by_alias("Alice", people) == "Alice Chen"
    assert get_person_by_alias("ALICE", people) == "Alice Chen"


def test_get_person_by_alias_no_match() -> None:
    people = [PersonConfig(name="Alice Chen", aliases=["Alice"])]
    assert get_person_by_alias("Bob", people) is None


def test_get_person_by_alias_empty_list() -> None:
    assert get_person_by_alias("Alice", []) is None


# ---------------------------------------------------------------------------
# get_client_by_alias
# ---------------------------------------------------------------------------


def test_get_client_by_alias_exact_match() -> None:
    clients = [
        ClientConfig(name="Acme Corp", aliases=["Acme", "acme", "ACME"]),
    ]
    assert get_client_by_alias("acme", clients) == "Acme Corp"
    assert get_client_by_alias("ACME", clients) == "Acme Corp"


def test_get_client_by_alias_no_match() -> None:
    clients = [ClientConfig(name="Acme Corp", aliases=["Acme"])]
    assert get_client_by_alias("FinServ", clients) is None
