"""Tests for the Obsidian vault ingestor."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.obsidian import IngestResult, ingest_vault, _strip_frontmatter_block

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
    # Note with no frontmatter at all — valid and should be ingested cleanly
    (vault / "no_frontmatter.md").write_text("# Just a plain note\n\nSome content here.")
    result = ingest_vault(str(vault), conn)
    assert result.ingested == 2
    assert result.failed == 0


# ===========================================================================
# Bad frontmatter tolerance
# ===========================================================================

# YAML strings that python-frontmatter / pyyaml cannot parse:
# This one is now repaired by _sanitize_frontmatter_yaml (unquoted colon).
_MAPPING_VALUES_NOT_ALLOWED = "---\nkey: value: extra\n---\n\nBody text here."
# This one is genuinely unrepairable (unhashable mapping key).
_UNHASHABLE_KEY = "---\n{nested: key}: value\n---\n\nBody text here."
# Real-world-style unquoted colon in a title/client/type value.
_UNQUOTED_COLON_TITLE = (
    "---\n"
    "title: Decision Log: Giles Access Revocation\n"
    "date: 2026-06-05\n"
    "tags: [decision, legal, risk, giles]\n"
    "---\n\n"
    "# Body\n"
)


class TestMalformedFrontmatter:
    def test_malformed_note_is_ingested_not_failed(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text(_UNHASHABLE_KEY)
        result = ingest_vault(str(vault), conn)
        assert result.failed == 0
        assert result.ingested + result.ingested_with_warnings == 1

    def test_malformed_note_counted_as_ingested_with_warnings(
        self, tmp_path: Path, conn
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text(_UNHASHABLE_KEY)
        result = ingest_vault(str(vault), conn)
        assert result.ingested_with_warnings >= 1

    def test_malformed_note_body_is_preserved(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text(_UNHASHABLE_KEY)
        ingest_vault(str(vault), conn)
        rows = conn.execute("SELECT body FROM notes").fetchall()
        assert len(rows) == 1
        # Body text must be present
        assert "Body text here" in (rows[0][0] or "")

    def test_malformed_note_raw_doc_stored(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text(_UNHASHABLE_KEY)
        ingest_vault(str(vault), conn)
        count = conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
        assert count == 1

    def test_parse_error_recorded_as_warning(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text(_UNHASHABLE_KEY)
        result = ingest_vault(str(vault), conn)
        assert len(result.warnings) >= 1
        assert "bad.md" in result.warnings[0] or "frontmatter" in result.warnings[0].lower()

    def test_unhashable_key_also_tolerated(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "unhashable.md").write_text(_UNHASHABLE_KEY)
        result = ingest_vault(str(vault), conn)
        assert result.failed == 0
        assert result.ingested + result.ingested_with_warnings == 1

    def test_good_and_bad_both_ingested(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "good.md").write_text("---\ntype: team\n---\n\n# Good")
        (vault / "bad.md").write_text(_UNHASHABLE_KEY)
        result = ingest_vault(str(vault), conn)
        assert result.failed == 0
        total = result.ingested + result.ingested_with_warnings
        assert total == 2
        count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        assert count == 2

    def test_malformed_note_not_counted_in_ingested(self, tmp_path: Path, conn) -> None:
        """ingested should only count clean files; ingested_with_warnings covers bad ones."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "bad.md").write_text(_UNHASHABLE_KEY)
        result = ingest_vault(str(vault), conn)
        # ingested should be 0 (bad file went to warnings bucket)
        assert result.ingested == 0

    def test_unquoted_colon_title_is_repaired(self, tmp_path: Path, conn) -> None:
        """Real-world frontmatter with unquoted colons should parse cleanly."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "decision.md").write_text(_UNQUOTED_COLON_TITLE)
        result = ingest_vault(str(vault), conn)
        assert result.failed == 0
        assert result.ingested == 1
        assert result.ingested_with_warnings == 0
        rows = conn.execute(
            "SELECT title, note_date, tags FROM notes WHERE title = ?",
            ["Decision Log: Giles Access Revocation"],
        ).fetchall()
        assert len(rows) == 1
        assert str(rows[0][1]) == "2026-06-05"
        assert "decision" in rows[0][2]

    def test_unquoted_slash_type_is_repaired(self, tmp_path: Path, conn) -> None:
        """Real-world frontmatter with unquoted slashes should parse cleanly."""
        vault = tmp_path / "vault"
        vault.mkdir()
        meetings = vault / "meetings"
        meetings.mkdir()
        text = (
            "---\n"
            "date: 2026-05-11\n"
            "client: Molecule AI (internal codename: GSL AI)\n"
            "type: Pre-sales positioning / Approach alignment\n"
            "---\n\n"
            "# Meeting\n"
        )
        (meetings / "meeting.md").write_text(text)
        result = ingest_vault(str(vault), conn)
        assert result.failed == 0
        assert result.ingested == 1
        assert result.ingested_with_warnings == 0
        rows = conn.execute(
            "SELECT entity_name, note_type FROM notes WHERE entity_name = ?",
            ["Molecule AI (internal codename: GSL AI)"],
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "meeting"


# ===========================================================================
# Template directory skipping
# ===========================================================================


class TestTemplateSkipping:
    def test_templates_dir_is_skipped(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        templates = vault / "templates"
        templates.mkdir()
        (templates / "Meeting-note.md").write_text("---\ntype: meeting\n---\nBody")
        (templates / "Manager-up.md").write_text("---\ntype: team\n---\nBody")
        (vault / "real_note.md").write_text("---\ntype: team\n---\n\n# Real note")
        result = ingest_vault(str(vault), conn)
        # Only real_note.md should be ingested
        assert result.ingested == 1
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1

    def test_templates_not_in_raw_documents(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        templates = vault / "templates"
        templates.mkdir()
        (templates / "template.md").write_text("# Template\nSome template body.")
        ingest_vault(str(vault), conn)
        count = conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
        assert count == 0

    def test_nested_templates_also_skipped(self, tmp_path: Path, conn) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        nested = vault / "work" / "templates"
        nested.mkdir(parents=True)
        (nested / "deep_template.md").write_text("# Deep template")
        (vault / "work" / "note.md").write_text("---\ntype: team\n---\n# Note")
        result = ingest_vault(str(vault), conn)
        assert result.ingested == 1
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


# ===========================================================================
# _strip_frontmatter_block helper
# ===========================================================================


class TestStripFrontmatterBlock:
    def test_strips_opening_and_closing_delimiters(self) -> None:
        raw = "---\nkey: value\n---\n\nBody line."
        assert "Body line." in _strip_frontmatter_block(raw)
        assert "---" not in _strip_frontmatter_block(raw)

    def test_no_frontmatter_returns_raw_text(self) -> None:
        raw = "# Plain markdown\n\nNo frontmatter."
        assert _strip_frontmatter_block(raw) == raw.strip()

    def test_handles_unclosed_frontmatter(self) -> None:
        raw = "---\nkey: value\nno closing delimiter\nbody here"
        # Should still return something useful
        result = _strip_frontmatter_block(raw)
        assert isinstance(result, str)

    def test_empty_body_after_frontmatter(self) -> None:
        raw = "---\nkey: value\n---\n"
        result = _strip_frontmatter_block(raw)
        assert result == ""


def test_ingest_nonexistent_vault_raises() -> None:
    conn = get_connection(":memory:")
    with pytest.raises(FileNotFoundError):
        ingest_vault("/nonexistent/path/vault", conn)
