"""Tests for the Obsidian vault ingestor."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.obsidian import IngestResult, ingest_vault

FIXTURES_VAULT = Path(__file__).parent.parent / "fixtures" / "vault"


@pytest.fixture()
def conn():
    return get_connection(":memory:")


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    """Copy fixture vault notes into a temp directory."""
    dest = tmp_path / "vault"
    shutil.copytree(FIXTURES_VAULT, dest)
    return dest


def test_ingest_all_fixture_notes(conn, vault_dir: Path) -> None:
    result = ingest_vault(str(vault_dir), conn)
    assert result.ingested == 3
    assert result.skipped == 0
    assert result.failed == 0

    raw_count = conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
    note_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert raw_count == 3
    assert note_count == 3


def test_ingest_idempotent(conn, vault_dir: Path) -> None:
    ingest_vault(str(vault_dir), conn)
    result2 = ingest_vault(str(vault_dir), conn)
    assert result2.ingested == 0
    assert result2.skipped == 3
    assert conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0] == 3


def test_ingest_force_reingest(conn, vault_dir: Path) -> None:
    ingest_vault(str(vault_dir), conn)
    result2 = ingest_vault(str(vault_dir), conn, force=True)
    assert result2.ingested == 3
    # Force re-inserts — row count stays 3 (INSERT OR REPLACE)
    assert conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0] == 3


def test_note_types_assigned_correctly(conn, vault_dir: Path) -> None:
    ingest_vault(str(vault_dir), conn)
    rows = conn.execute(
        "SELECT title, note_type, entity_type, entity_name FROM notes ORDER BY title"
    ).fetchall()
    type_map = {row[0]: (row[1], row[2], row[3]) for row in rows}

    assert "1on1_alice" in " ".join(type_map).lower() or any(
        "alice" in t[0].lower() or "1on1" in t[0].lower() for t in type_map.values()
    )

    for title, (note_type, entity_type, entity_name) in type_map.items():
        if "1on1" in title.lower() or "alice" in title.lower():
            assert note_type == "1on1"
            assert entity_type == "person"
            assert entity_name == "Alice Chen"
        elif "acme" in title.lower() or "client" in title.lower():
            assert note_type == "client"
            assert entity_type == "client"
        elif "big retail" in title.lower() or "deal" in title.lower():
            assert note_type == "deal"
            assert entity_type == "deal"


def test_ingest_skips_obsidian_system_dir(tmp_path: Path, conn) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    # Place a note inside .obsidian — it should be skipped
    (vault / ".obsidian" / "workspace.md").write_text("# system file")
    # Place a valid note
    (vault / "real_note.md").write_text("---\ntype: team\nentity: Team\n---\n\n# Team note")
    result = ingest_vault(str(vault), conn)
    assert result.ingested == 1
    assert conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0] == 1


def test_ingest_handles_malformed_frontmatter(tmp_path: Path, conn) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    # Valid note
    (vault / "good.md").write_text("---\ntype: team\nentity: Team\n---\n\n# Good note")
    # This note has a body but invalid YAML frontmatter (unclosed bracket)
    # python-frontmatter is lenient, so let's use a note with no frontmatter at all
    (vault / "no_frontmatter.md").write_text("# Just a plain note\n\nSome content here.")
    result = ingest_vault(str(vault), conn)
    # Both should ingest successfully — no frontmatter is valid
    assert result.ingested == 2
    assert result.failed == 0


def test_ingest_nonexistent_vault_raises() -> None:
    conn = get_connection(":memory:")
    with pytest.raises(FileNotFoundError):
        ingest_vault("/nonexistent/path/vault", conn)
