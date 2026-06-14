"""Tests for ``manager-os readiness``.

Covers:
- All-ready scenario: all checks PASS or WARN, exit 0
- Missing vault path: FAIL + exit 1
- Missing CSV path: FAIL + exit 1
- Sample config warning: WARN for people.yaml / clients.yaml still having fixture names
- Unsafe DB path warning: WARN when DB is inside repo with non-.duckdb extension
- Missing .gitignore rule: FAIL via unit test of _check_gitignore()
- CLI exit codes: 0 for WARN-only, 1 for any FAIL
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from manager_os.cli import (
    app as cli_app,
    _Check,
    _SAMPLE_PERSON_NAMES,
    _SAMPLE_CLIENT_NAMES,
    _REQUIRED_GITIGNORE_RULES,
    _check_gitignore,
    _gitignore_lines,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config_dir(
    tmp_path: Path,
    *,
    people_names: list[str] | None = None,
    client_names: list[str] | None = None,
    with_deal_aliases: bool = True,
) -> Path:
    """Write minimal config YAML files to a temporary directory."""
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)

    if people_names is None:
        people_names = ["Real Person One", "Real Person Two"]
    people_yaml = "\n".join(
        f'- name: "{name}"\n  aliases: ["{name}"]\n  role: "Engineer"\n  level: "L4"'
        for name in people_names
    )
    (cfg / "people.yaml").write_text(people_yaml, encoding="utf-8")

    if client_names is None:
        client_names = ["Real Client Corp"]
    clients_yaml = "\n".join(
        f'- name: "{name}"\n  aliases: ["{name}"]\n  engagement: "Data Platform"'
        for name in client_names
    )
    (cfg / "clients.yaml").write_text(clients_yaml, encoding="utf-8")

    if with_deal_aliases:
        (cfg / "deal_aliases.yaml").write_text(
            '"Real Deal": "Real Client Corp — Data Platform"\n',
            encoding="utf-8",
        )

    return cfg


def _make_vault(tmp_path: Path) -> Path:
    """Create a minimal vault directory (without .obsidian — not needed for readiness)."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def _make_csvs(tmp_path: Path) -> tuple[Path, Path]:
    """Create minimal forecast and deals CSV files."""
    forecast = tmp_path / "forecast.csv"
    forecast.write_text("person,week_start,client,project,allocation_pct,forecast_type\n", encoding="utf-8")
    deals = tmp_path / "deals.csv"
    deals.write_text("account,deal_name,stage,close_date,technical_owner,ae_name,loe_status,sow_status\n", encoding="utf-8")
    return forecast, deals


def _env(
    tmp_path: Path,
    *,
    vault: str | None = None,
    forecast_csv: str | None = None,
    deals_csv: str | None = None,
    db_path: str | None = None,
    config_dir: str | None = None,
) -> dict[str, str]:
    """Build env-var dict for CliRunner.invoke()."""
    return {
        "MANAGER_OS_VAULT_PATH": vault or "",
        "MANAGER_OS_DB_PATH": db_path or str(tmp_path / "test.duckdb"),
        "MANAGER_OS_FORECAST_CSV": forecast_csv or "",
        "MANAGER_OS_DEALS_CSV": deals_csv or "",
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(tmp_path / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(tmp_path / "gws"),
        "MANAGER_OS_CONFIG_DIR": config_dir or str(REPO_ROOT / "config"),
    }


def _run_readiness(env: dict[str, str]) -> object:
    runner = CliRunner()
    return runner.invoke(cli_app, ["readiness"], env=env)


# ===========================================================================
# Unit tests for _check_gitignore
# ===========================================================================


class TestCheckGitignoreUnit:
    def test_real_gitignore_passes_all_patterns(self) -> None:
        """The actual repo .gitignore must cover all required patterns."""
        real_gi = REPO_ROOT / ".gitignore"
        checks = _check_gitignore(real_gi)
        fails = [c for c in checks if c.status == "FAIL"]
        assert fails == [], f"Unexpected FAIL for patterns: {[c.label for c in fails]}"

    def test_missing_gitignore_file_returns_all_fail(self, tmp_path: Path) -> None:
        missing = tmp_path / ".gitignore"  # does not exist
        checks = _check_gitignore(missing)
        assert all(c.status == "FAIL" for c in checks)
        assert len(checks) == len(_REQUIRED_GITIGNORE_RULES)

    def test_missing_single_pattern_returns_fail_for_that_entry(self, tmp_path: Path) -> None:
        # Write a gitignore that covers everything except *.duckdb
        gi = tmp_path / ".gitignore"
        lines = [pat for _, pat in _REQUIRED_GITIGNORE_RULES if pat != "*.duckdb"]
        gi.write_text("\n".join(lines) + "\n", encoding="utf-8")

        checks = _check_gitignore(gi)
        statuses = {c.label: c.status for c in checks}
        assert statuses[".gitignore: *.duckdb"] == "FAIL"
        # All others should pass
        for label, _ in _REQUIRED_GITIGNORE_RULES:
            if label != "*.duckdb":
                assert statuses[f".gitignore: {label}"] == "PASS"

    def test_data_raw_star_glob_accepted(self, tmp_path: Path) -> None:
        """'data/raw/*' is acceptable as a substitute for 'data/raw/'."""
        gi = tmp_path / ".gitignore"
        lines = [pat for _, pat in _REQUIRED_GITIGNORE_RULES if pat != "data/raw/"]
        lines.append("data/raw/*")
        gi.write_text("\n".join(lines) + "\n", encoding="utf-8")

        checks = _check_gitignore(gi)
        statuses = {c.label: c.status for c in checks}
        assert statuses[".gitignore: data/raw/"] == "PASS"

    def test_gitignore_lines_ignores_comments(self, tmp_path: Path) -> None:
        gi = tmp_path / ".gitignore"
        gi.write_text("# this is a comment\n.env\n# another\n", encoding="utf-8")
        lines = _gitignore_lines(gi)
        assert ".env" in lines
        assert "# this is a comment" not in lines

    def test_gitignore_lines_empty_file(self, tmp_path: Path) -> None:
        gi = tmp_path / ".gitignore"
        gi.write_text("", encoding="utf-8")
        assert _gitignore_lines(gi) == frozenset()


# ===========================================================================
# Unit tests for _Check class
# ===========================================================================


class TestCheckClass:
    def test_check_stores_label_status_note(self) -> None:
        c = _Check("my label", "PASS", "all good")
        assert c.label == "my label"
        assert c.status == "PASS"
        assert c.note == "all good"

    def test_check_default_note_is_empty_string(self) -> None:
        c = _Check("x", "FAIL")
        assert c.note == ""


# ===========================================================================
# All-ready scenario
# ===========================================================================


class TestReadinessAllReady:
    """When vault, CSVs, and config all look real, exit 0 with no FAILs."""

    def test_exits_0_all_pass(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert result.exit_code == 0, result.output

    def test_output_contains_pass_rows(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert "PASS" in result.output

    def test_output_shows_readiness_header(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert "Readiness" in result.output


# ===========================================================================
# Missing vault path
# ===========================================================================


class TestReadinessMissingVaultPath:
    def test_vault_not_set_shows_fail_and_exits_1(self, tmp_path: Path) -> None:
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)

        result = _run_readiness(_env(
            tmp_path,
            vault="",           # explicitly empty — not set
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "MANAGER_OS_VAULT_PATH" in result.output

    def test_vault_dir_not_found_shows_fail_and_exits_1(self, tmp_path: Path) -> None:
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)
        nonexistent = tmp_path / "no_such_vault"  # never created

        result = _run_readiness(_env(
            tmp_path,
            vault=str(nonexistent),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "vault" in result.output.lower()


# ===========================================================================
# Missing CSV paths
# ===========================================================================


class TestReadinessMissingCSV:
    def test_missing_forecast_csv_shows_fail(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        _, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)
        missing_forecast = tmp_path / "no_forecast.csv"  # never created

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(missing_forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "FORECAST" in result.output

    def test_missing_deals_csv_shows_fail(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, _ = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)
        missing_deals = tmp_path / "no_deals.csv"  # never created

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(missing_deals),
            config_dir=str(cfg),
        ))
        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "DEALS" in result.output


# ===========================================================================
# Sample config warning
# ===========================================================================


class TestReadinessSampleConfigWarning:
    def test_sample_person_name_shows_warn(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        # people.yaml contains a sample name
        cfg = _make_config_dir(tmp_path, people_names=["Alice Chen", "Real Person"])

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert "WARN" in result.output
        # Rich may wrap long names across lines; just check the name appears somewhere
        assert "alice" in result.output.lower()

    def test_sample_client_name_shows_warn(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path, client_names=["Acme Corp"])

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert "WARN" in result.output
        assert "acme" in result.output.lower()

    def test_sample_config_exits_0_because_warn_only(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path, people_names=["Alice Chen"])

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        # WARN-only → exit 0
        assert result.exit_code == 0

    def test_non_sample_names_shows_pass(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(
            tmp_path,
            people_names=["Jordan Lee", "Morgan Kim"],
            client_names=["Stellar Corp", "Nexus Inc"],
        )

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        # No FAIL, exit 0
        assert result.exit_code == 0

    def test_sample_names_set_constants_populated(self) -> None:
        """Sentinel name sets must include the fixture names."""
        assert "alice chen" in _SAMPLE_PERSON_NAMES
        assert "acme corp" in _SAMPLE_CLIENT_NAMES


# ===========================================================================
# DB path warning
# ===========================================================================


class TestReadinessDbPath:
    def test_duckdb_extension_shows_pass(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            db_path=str(tmp_path / "manager_os.duckdb"),
            config_dir=str(cfg),
        ))
        # .duckdb extension → PASS; no FAIL → exit 0
        assert result.exit_code == 0
        assert "DB path gitignored" in result.output

    def test_data_processed_path_shows_pass(self, tmp_path: Path) -> None:
        """data/processed/ path is gitignored even without .duckdb extension."""
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)

        # Relative path containing data/processed
        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            db_path="./data/processed/manager_os.duckdb",
            config_dir=str(cfg),
        ))
        assert result.exit_code == 0

    def test_inside_repo_no_duckdb_extension_warns(self, tmp_path: Path) -> None:
        """DB path inside the repo without .duckdb extension → WARN."""
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)

        # A path inside the repo root that doesn't end with .duckdb
        # and isn't in data/processed or data/demo
        from manager_os.cli import _REPO_ROOT
        unusual_db = str(_REPO_ROOT / "manager_os_unusual_db")

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            db_path=unusual_db,
            config_dir=str(cfg),
        ))
        assert "WARN" in result.output
        assert "DB path gitignored" in result.output


# ===========================================================================
# Missing .gitignore rule (unit-level only — CLI always uses real gitignore)
# ===========================================================================


class TestReadinessMissingGitignoreRule:
    def test_missing_duckdb_rule_returns_fail_check(self, tmp_path: Path) -> None:
        gi = tmp_path / ".gitignore"
        lines = [pat for _, pat in _REQUIRED_GITIGNORE_RULES if pat != "*.duckdb"]
        gi.write_text("\n".join(lines) + "\n", encoding="utf-8")

        checks = _check_gitignore(gi)
        statuses = {c.label: c.status for c in checks}
        assert statuses[".gitignore: *.duckdb"] == "FAIL"

    def test_missing_env_rule_returns_fail_check(self, tmp_path: Path) -> None:
        gi = tmp_path / ".gitignore"
        lines = [pat for _, pat in _REQUIRED_GITIGNORE_RULES if pat != ".env"]
        gi.write_text("\n".join(lines) + "\n", encoding="utf-8")

        checks = _check_gitignore(gi)
        statuses = {c.label: c.status for c in checks}
        assert statuses[".gitignore: .env"] == "FAIL"

    def test_missing_output_rule_returns_fail_check(self, tmp_path: Path) -> None:
        gi = tmp_path / ".gitignore"
        lines = [pat for _, pat in _REQUIRED_GITIGNORE_RULES if pat != "output/"]
        gi.write_text("\n".join(lines) + "\n", encoding="utf-8")

        checks = _check_gitignore(gi)
        statuses = {c.label: c.status for c in checks}
        assert statuses[".gitignore: output/"] == "FAIL"


# ===========================================================================
# CLI exit codes
# ===========================================================================


class TestReadinessCLIExitCodes:
    def test_exits_0_with_only_warns(self, tmp_path: Path) -> None:
        """WARN-only run exits 0."""
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        # Use sample config → WARN, not FAIL
        cfg = _make_config_dir(tmp_path, people_names=["Alice Chen"])

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert result.exit_code == 0

    def test_exits_1_with_fail(self, tmp_path: Path) -> None:
        """Any FAIL → exit 1."""
        forecast, deals = _make_csvs(tmp_path)
        cfg = _make_config_dir(tmp_path)

        # vault not set → FAIL
        result = _run_readiness(_env(
            tmp_path,
            vault="",
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert result.exit_code == 1

    def test_missing_deal_aliases_exits_1(self, tmp_path: Path) -> None:
        vault = _make_vault(tmp_path)
        forecast, deals = _make_csvs(tmp_path)
        # config dir without deal_aliases.yaml
        cfg = _make_config_dir(tmp_path, with_deal_aliases=False)

        result = _run_readiness(_env(
            tmp_path,
            vault=str(vault),
            forecast_csv=str(forecast),
            deals_csv=str(deals),
            config_dir=str(cfg),
        ))
        assert result.exit_code == 1
        assert "deal_aliases" in result.output
