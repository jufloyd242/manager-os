"""Tests for db.seed_from_config and manager-os closeout CLI (Issue #22)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from manager_os.db import get_connection, seed_from_config, content_hash
from manager_os.config import PersonConfig, ClientConfig


# ------------------------------------------------------------------
# Minimal settings stub
# ------------------------------------------------------------------

class _FakeSettings:
    config_dir = "./config"


# ------------------------------------------------------------------
# seed_from_config
# ------------------------------------------------------------------


@pytest.fixture()
def conn():
    return get_connection(":memory:")


def test_seed_from_config_inserts_people(conn, monkeypatch) -> None:
    people = [
        PersonConfig(name="Alice Chen", aliases=["Alice"], role="ML Engineer", level="L5"),
        PersonConfig(name="Bob Martinez", aliases=["Bob"], role="Data Engineer", level="L4"),
    ]
    clients = []
    monkeypatch.setattr("manager_os.config.load_people", lambda s: people)
    monkeypatch.setattr("manager_os.config.load_clients", lambda s: clients)

    result = seed_from_config(conn, _FakeSettings())
    assert result["people"] == 2
    assert result["clients"] == 0

    names = [r[0] for r in conn.execute("SELECT name FROM people").fetchall()]
    assert "Alice Chen" in names
    assert "Bob Martinez" in names


def test_seed_from_config_inserts_clients(conn, monkeypatch) -> None:
    people = []
    clients = [
        ClientConfig(name="Acme Corp", aliases=["Acme"]),
        ClientConfig(name="FinServ Partners", aliases=["FinServ"]),
    ]
    monkeypatch.setattr("manager_os.config.load_people", lambda s: people)
    monkeypatch.setattr("manager_os.config.load_clients", lambda s: clients)

    result = seed_from_config(conn, _FakeSettings())
    assert result["clients"] == 2
    names = [r[0] for r in conn.execute("SELECT name FROM clients").fetchall()]
    assert "Acme Corp" in names


def test_seed_from_config_idempotent(conn, monkeypatch) -> None:
    people = [PersonConfig(name="Alice Chen", aliases=["Alice"])]
    monkeypatch.setattr("manager_os.config.load_people", lambda s: people)
    monkeypatch.setattr("manager_os.config.load_clients", lambda s: [])

    seed_from_config(conn, _FakeSettings())
    result2 = seed_from_config(conn, _FakeSettings())
    # Second call should not insert duplicates
    assert result2["people"] == 0
    count = conn.execute("SELECT COUNT(*) FROM people WHERE name='Alice Chen'").fetchone()[0]
    assert count == 1


def test_seed_from_config_does_not_overwrite_existing(conn, monkeypatch) -> None:
    """Existing people rows (enriched by ingest) must not be overwritten."""
    # Manually seed a row with enriched data
    pid = content_hash("config::person::Alice Chen")
    from datetime import datetime
    conn.execute(
        """
        INSERT INTO people
            (id, name, aliases, role, level, current_client, allocation_pct,
             next_availability_date, last_1on1_date, morale_signal, growth_topic, blockers, updated_at)
        VALUES (?, 'Alice Chen', '[]', 'Staff SWE', 'L6', 'Acme Corp', 80.0,
                NULL, NULL, 'yellow', 'leadership', 'none', ?)
        """,
        [pid, datetime.utcnow()],
    )

    people = [PersonConfig(name="Alice Chen", aliases=["Alice"], role="ML Engineer", level="L5")]
    monkeypatch.setattr("manager_os.config.load_people", lambda s: people)
    monkeypatch.setattr("manager_os.config.load_clients", lambda s: [])

    result = seed_from_config(conn, _FakeSettings())
    assert result["people"] == 0  # not re-inserted

    row = conn.execute("SELECT role, current_client FROM people WHERE name='Alice Chen'").fetchone()
    assert row[0] == "Staff SWE"  # enriched data preserved
    assert row[1] == "Acme Corp"


def test_seed_from_config_handles_missing_config_gracefully(conn, monkeypatch) -> None:
    def _raise(s):
        raise FileNotFoundError("config not found")

    monkeypatch.setattr("manager_os.config.load_people", _raise)
    monkeypatch.setattr("manager_os.config.load_clients", _raise)

    result = seed_from_config(conn, _FakeSettings())
    assert result == {"people": 0, "clients": 0}


def test_seed_from_config_sets_default_morale_green(conn, monkeypatch) -> None:
    people = [PersonConfig(name="Elena Torres", aliases=[])]
    monkeypatch.setattr("manager_os.config.load_people", lambda s: people)
    monkeypatch.setattr("manager_os.config.load_clients", lambda s: [])

    seed_from_config(conn, _FakeSettings())
    row = conn.execute("SELECT morale_signal FROM people WHERE name='Elena Torres'").fetchone()
    assert row[0] == "green"


def test_seed_from_config_sets_default_client_health_green(conn, monkeypatch) -> None:
    clients = [ClientConfig(name="MedTech Solutions", aliases=[])]
    monkeypatch.setattr("manager_os.config.load_people", lambda s: [])
    monkeypatch.setattr("manager_os.config.load_clients", lambda s: clients)

    seed_from_config(conn, _FakeSettings())
    row = conn.execute("SELECT health FROM clients WHERE name='MedTech Solutions'").fetchone()
    assert row[0] == "green"


# ------------------------------------------------------------------
# Closeout CLI smoke test (uses typer.testing)
# ------------------------------------------------------------------


def test_closeout_cli_runs(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner
    from manager_os.cli import app as cli_app

    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    monkeypatch.setenv("MANAGER_OS_VAULT_PATH", "")

    runner = CliRunner()
    result = runner.invoke(
        cli_app, ["closeout", "--date", date.today().isoformat(), "--no-weekly"]
    )
    assert result.exit_code == 0
    assert "Closeout written" in result.output


def test_closeout_cli_with_force_weekly(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner
    from manager_os.cli import app as cli_app

    db_path = str(tmp_path / "test.duckdb")
    monkeypatch.setenv("MANAGER_OS_DB_PATH", db_path)
    monkeypatch.setenv("MANAGER_OS_VAULT_PATH", "")

    runner = CliRunner()
    result = runner.invoke(
        cli_app, ["closeout", "--date", date.today().isoformat(), "--weekly",
                  "--output", str(tmp_path / "out")]
    )
    assert result.exit_code == 0
    assert "Weekly exec update" in result.output
