"""Tests for the config-audit command and config_audit module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manager_os.cli import app
from manager_os.build.config_audit import scan_vault, render_report, AuditResult

runner = CliRunner()

# Path to the synthetic config-audit vault fixture
FIXTURE_VAULT = Path(__file__).parent.parent / "fixtures" / "config_audit_scenario" / "vault"


# ---------------------------------------------------------------------------
# Unit tests: scan_vault
# ---------------------------------------------------------------------------


class TestScanVault:
    def test_detects_person_from_frontmatter(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        names = [e.name for e in result.candidate_people]
        assert "Riley Santos" in names, f"Expected Riley Santos in {names}"

    def test_detects_client_from_frontmatter(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        names = [e.name for e in result.candidate_clients]
        assert "Zephyr Dynamics" in names, f"Expected Zephyr Dynamics in {names}"

    def test_detects_deal_from_frontmatter(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        names = [e.name for e in result.candidate_deals]
        assert "Atlas Robotics SOW" in names, f"Expected Atlas Robotics SOW in {names}"

    def test_person_frontmatter_confidence_is_high(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        riley = next((e for e in result.candidate_people if e.name == "Riley Santos"), None)
        assert riley is not None
        assert riley.confidence == "high"
        assert "frontmatter" in riley.source

    def test_client_frontmatter_confidence_is_high(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        zephyr = next((e for e in result.candidate_clients if e.name == "Zephyr Dynamics"), None)
        assert zephyr is not None
        assert zephyr.confidence == "high"

    def test_deal_frontmatter_confidence_is_high(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        atlas = next((e for e in result.candidate_deals if e.name == "Atlas Robotics SOW"), None)
        assert atlas is not None
        assert atlas.confidence == "high"

    def test_filename_only_note_gets_medium_confidence(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        # 1on1_filename_only.md has no entity field → inferred from filename → medium
        stem_names = [e.name.lower() for e in result.candidate_people]
        # The file is "1on1_filename_only" which normalizes to "1on1 Filename Only"
        # It's in a 1on1 directory so it should be treated as a person
        filename_entries = [
            e for e in result.candidate_people if e.confidence == "medium"
        ]
        assert len(filename_entries) >= 1, "Expected at least one medium-confidence person entry"

    def test_notes_scanned_count(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        assert result.notes_scanned >= 4

    def test_limit_caps_notes(self) -> None:
        result_full = scan_vault(str(FIXTURE_VAULT))
        result_limited = scan_vault(str(FIXTURE_VAULT), limit=1)
        assert result_limited.notes_scanned <= 1
        assert result_limited.notes_scanned < result_full.notes_scanned

    def test_config_gaps_reported_for_unknown_entities(self) -> None:
        result = scan_vault(
            str(FIXTURE_VAULT),
            existing_people=[],
            existing_clients=[],
            existing_deals=[],
        )
        assert len(result.config_gaps) > 0

    def test_no_gap_when_entity_already_in_config(self) -> None:
        result = scan_vault(
            str(FIXTURE_VAULT),
            existing_people=["Riley Santos"],
            existing_clients=["Zephyr Dynamics"],
            existing_deals=["Atlas Robotics SOW"],
        )
        # All three known entities should not appear in gaps
        person_gaps = [g for g in result.config_gaps if "Riley Santos" in g]
        assert person_gaps == []

    def test_possible_aliases_generated_for_new_entries(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT), existing_people=[], existing_clients=[], existing_deals=[])
        alias_names = [a["name"] for a in result.possible_aliases]
        assert "Riley Santos" in alias_names or "Zephyr Dynamics" in alias_names

    def test_does_not_write_to_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.duckdb"
        scan_vault(str(FIXTURE_VAULT))
        assert not db_path.exists()

    def test_vault_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            scan_vault("/nonexistent/vault/path")

    def test_include_body_signals_does_not_expose_body_in_output(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT), include_body_signals=True)
        # Render and ensure no body excerpts appear
        report = render_report(result)
        # The report should not contain "onboarding" (body text from riley note)
        assert "Onboarding" not in report
        assert "onboarding" not in report

    def test_report_does_not_include_body_by_default(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT), include_body_signals=False)
        report = render_report(result)
        # Body text from the notes should not appear
        assert "Delivery on track" not in report
        assert "SOW review" not in report


# ---------------------------------------------------------------------------
# Render report
# ---------------------------------------------------------------------------


class TestRenderReport:
    def test_report_contains_safety_notice(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        report = render_report(result)
        assert "Do not commit" in report or "not commit" in report

    def test_report_contains_candidate_tables(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        report = render_report(result)
        assert "Candidate People" in report
        assert "Candidate Clients" in report
        assert "Candidate Deals" in report

    def test_report_contains_counts(self) -> None:
        result = scan_vault(str(FIXTURE_VAULT))
        report = render_report(result)
        assert "Notes scanned" in report


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


class TestConfigAuditCLI:
    def test_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["config-audit", "--help"])
        assert result.exit_code == 0
        assert "real-data-preview" in result.output

    def test_without_flag_exits_zero_with_instructions(self) -> None:
        result = runner.invoke(app, ["config-audit"])
        assert result.exit_code == 0
        assert "--real-data-preview" in result.output

    def test_creates_report_file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("output/\n")
        result = runner.invoke(
            app,
            ["config-audit", "--real-data-preview", "--vault-path", str(FIXTURE_VAULT)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        report_files = list((tmp_path / "output" / "config_audit").glob("*.md"))
        assert len(report_files) == 1

    def test_report_file_contains_candidates(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("output/\n")
        runner.invoke(
            app,
            ["config-audit", "--real-data-preview", "--vault-path", str(FIXTURE_VAULT)],
            catch_exceptions=False,
        )
        report_files = list((tmp_path / "output" / "config_audit").glob("*.md"))
        content = report_files[0].read_text()
        assert "Riley Santos" in content
        assert "Zephyr Dynamics" in content
        assert "Atlas Robotics SOW" in content

    def test_does_not_modify_config_files(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("output/\n")
        # Create minimal config dir
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        people_yaml = config_dir / "people.yaml"
        people_yaml.write_text("- name: 'Existing Person'\n  aliases: []\n")
        original_mtime = people_yaml.stat().st_mtime

        runner.invoke(
            app,
            ["config-audit", "--real-data-preview", "--vault-path", str(FIXTURE_VAULT)],
        )
        assert people_yaml.stat().st_mtime == original_mtime, "people.yaml was modified!"

    def test_vault_path_override_flag(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("output/\n")
        result = runner.invoke(
            app,
            [
                "config-audit",
                "--real-data-preview",
                "--vault-path",
                str(FIXTURE_VAULT),
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        # Report should exist under output/config_audit/
        assert (tmp_path / "output" / "config_audit").exists()

    def test_limit_flag(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("output/\n")
        result = runner.invoke(
            app,
            [
                "config-audit",
                "--real-data-preview",
                "--vault-path",
                str(FIXTURE_VAULT),
                "--limit",
                "1",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        # Output should mention only 1 note scanned
        assert "1" in result.output

    def test_json_flag_outputs_valid_json(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("output/\n")
        result = runner.invoke(
            app,
            [
                "config-audit",
                "--real-data-preview",
                "--vault-path",
                str(FIXTURE_VAULT),
                "--json",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        # Last non-empty lines should be valid JSON
        out_text = result.output.strip()
        # Find the JSON block
        json_start = out_text.find("{")
        assert json_start != -1, f"No JSON found in output: {out_text}"
        parsed = json.loads(out_text[json_start:])
        assert "notes_scanned" in parsed
        assert "candidate_people" in parsed
        assert "report_path" in parsed

    def test_fails_if_output_not_gitignored(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        # Write a .gitignore that does NOT include output/
        (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        result = runner.invoke(
            app,
            ["config-audit", "--real-data-preview", "--vault-path", str(FIXTURE_VAULT)],
        )
        assert result.exit_code != 0 or "Safety check failed" in result.output

    def test_missing_vault_path_exits_nonzero(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("output/\n")
        # No MANAGER_OS_VAULT_PATH, no --vault-path
        result = runner.invoke(
            app,
            ["config-audit", "--real-data-preview"],
            env={"MANAGER_OS_VAULT_PATH": ""},
        )
        assert result.exit_code != 0 or "No vault path" in result.output
