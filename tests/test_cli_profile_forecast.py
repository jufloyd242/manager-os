"""Tests for ``manager-os profile-forecast`` and the profile module.

Covers:
- valid CSV: correct column detection, no issues, can_ingest=True
- missing required columns: person, week_start → can_ingest=False, exit 1
- unknown people (config provided): issue recorded
- unknown clients (config provided): issue recorded
- overallocated person (>100%): issue recorded
- zero allocation: issue recorded
- missing date: issue recorded
- malformed date: issue recorded
- malformed allocation: issue recorded
- --json output: valid JSON, correct fields
- --path override: uses supplied path, not settings default
- command exits 0 for warnings-only
- command exits 1 for missing required columns
- command exits 1 for unreadable file
- sample_size limits displayed rows without affecting issue detection
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from manager_os.cli import app as cli_app
from manager_os.config import PersonConfig, ClientConfig
from manager_os.profile import (
    ForecastProfile,
    RowIssue,
    profile_forecast_csv,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Synthetic CSV helpers
# ---------------------------------------------------------------------------

_VALID_HEADER = "person,week_start,client,project,allocation_pct,forecast_type"
_VALID_ROW = "Alice Chen,2026-06-16,Acme Corp,ML Platform Build,100,confirmed"


def _csv(tmp_path: Path, content: str, name: str = "forecast.csv") -> Path:
    p = tmp_path / name
    p.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return p


def _people() -> list[PersonConfig]:
    return [
        PersonConfig(name="Alice Chen", aliases=["Alice", "alice.chen", "Alice Chen"]),
        PersonConfig(name="Bob Kim", aliases=["Bob", "bob.kim", "Bob Kim"]),
    ]


def _clients() -> list[ClientConfig]:
    return [
        ClientConfig(name="Acme Corp", aliases=["Acme", "acme", "Acme Corp"]),
        ClientConfig(name="Nexus Inc", aliases=["Nexus", "nexus", "Nexus Inc"]),
    ]


def _env(csv_path: str) -> dict[str, str]:
    return {
        "MANAGER_OS_FORECAST_CSV": csv_path,
        "MANAGER_OS_DEALS_CSV": str(FIXTURES / "deals.csv"),
        "MANAGER_OS_VAULT_PATH": str(FIXTURES / "vault"),
        "MANAGER_OS_DB_PATH": "/tmp/profile_test.duckdb",
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        "MANAGER_OS_CONFIG_DIR": str(REPO_ROOT / "config"),
    }


def _run(args: list[str], env: dict[str, str]) -> object:
    return CliRunner().invoke(cli_app, args, env=env)


# ===========================================================================
# Unit tests — profile_forecast_csv()
# ===========================================================================


class TestProfileValidCSV:
    def test_basic_fields_found(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_forecast_csv(str(p))
        assert "person_name" in result.fields_found
        assert "week_start" in result.fields_found
        assert result.can_ingest is True

    def test_total_row_count(self, tmp_path: Path) -> None:
        rows = "\n".join([_VALID_ROW] * 5)
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{rows}")
        result = profile_forecast_csv(str(p))
        assert result.total_rows == 5

    def test_column_mapping_identity_for_canonical_headers(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_forecast_csv(str(p))
        # canonical headers map to themselves after stage-1 (no alias needed)
        assert result.column_mapping["person"] == "person_name"
        assert result.column_mapping["week_start"] == "week_start"
        assert result.column_mapping["allocation_pct"] == "allocation_pct"

    def test_no_issues_for_clean_fixture(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_forecast_csv(str(p), people=_people(), clients=_clients())
        assert result.issues == []

    def test_sample_rows_limited_by_sample_size(self, tmp_path: Path) -> None:
        rows = "\n".join([_VALID_ROW] * 20)
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{rows}")
        result = profile_forecast_csv(str(p), sample_size=5)
        assert result.sample_size == 5
        assert len(result.sample_rows) == 5

    def test_fixture_csv_passes(self) -> None:
        result = profile_forecast_csv(str(FIXTURES / "forecast.csv"))
        assert result.can_ingest is True
        assert result.total_rows == 9

    def test_to_dict_is_serialisable(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_forecast_csv(str(p))
        d = result.to_dict()
        assert isinstance(d, dict)
        json.dumps(d)  # must not raise


class TestProfileMissingRequiredColumns:
    def test_missing_person_column(self, tmp_path: Path) -> None:
        # Profiler returns can_ingest=False for missing required columns (does not raise).
        p = _csv(tmp_path, "week_start,client,allocation_pct\n2026-06-16,Acme,100")
        result = profile_forecast_csv(str(p))
        assert result.can_ingest is False
        assert "person_name" in result.fields_missing

    def test_missing_week_start_column(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, "person,client,allocation_pct\nAlice Chen,Acme,100")
        result = profile_forecast_csv(str(p))
        assert result.can_ingest is False
        assert "week_start" in result.fields_missing

    def test_can_ingest_false_only_when_required_missing(self, tmp_path: Path) -> None:
        # All required present → can_ingest True even with issues
        p = _csv(
            tmp_path,
            "person,week_start\nUnknown Person,NOT-A-DATE",
        )
        result = profile_forecast_csv(str(p), people=_people())
        assert result.can_ingest is True

    def test_file_not_found_raises_runtime_error(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="Could not read"):
            profile_forecast_csv(str(tmp_path / "nonexistent.csv"))


class TestProfileUnknownPeople:
    def test_unknown_person_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nStranger Danger,2026-06-16,Acme Corp,Proj,100,confirmed",
        )
        result = profile_forecast_csv(str(p), people=_people())
        assert any(i.issue_type == "unknown_person" for i in result.issues)

    def test_known_person_no_issue(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_forecast_csv(str(p), people=_people())
        unknown = [i for i in result.issues if i.issue_type == "unknown_person"]
        assert unknown == []

    def test_alias_resolves_no_issue(self, tmp_path: Path) -> None:
        # "Alice" is an alias for "Alice Chen" in _people()
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice,2026-06-16,Acme Corp,Proj,80,confirmed",
        )
        result = profile_forecast_csv(str(p), people=_people())
        unknown = [i for i in result.issues if i.issue_type == "unknown_person"]
        assert unknown == []

    def test_no_people_config_skips_check(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAnyone At All,2026-06-16,Acme,Proj,80,confirmed",
        )
        result = profile_forecast_csv(str(p), people=None)
        unknown = [i for i in result.issues if i.issue_type == "unknown_person"]
        assert unknown == []


class TestProfileUnknownClients:
    def test_unknown_client_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Unknown Co,Proj,100,confirmed",
        )
        result = profile_forecast_csv(str(p), clients=_clients())
        assert any(i.issue_type == "unknown_client" for i in result.issues)

    def test_known_client_no_issue(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_forecast_csv(str(p), clients=_clients())
        unknown = [i for i in result.issues if i.issue_type == "unknown_client"]
        assert unknown == []

    def test_no_clients_config_skips_check(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Any Corp,Proj,100,confirmed",
        )
        result = profile_forecast_csv(str(p), clients=None)
        unknown = [i for i in result.issues if i.issue_type == "unknown_client"]
        assert unknown == []


class TestProfileAllocationChecks:
    def test_overallocated_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Acme Corp,Proj,120,confirmed",
        )
        result = profile_forecast_csv(str(p))
        assert any(i.issue_type == "overallocated" for i in result.issues)

    def test_100_percent_is_not_overallocated(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_forecast_csv(str(p))
        over = [i for i in result.issues if i.issue_type == "overallocated"]
        assert over == []

    def test_zero_allocation_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Acme Corp,Proj,0,confirmed",
        )
        result = profile_forecast_csv(str(p))
        assert any(i.issue_type == "zero_allocation" for i in result.issues)

    def test_missing_allocation_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "person,week_start,client,allocation_pct\nAlice Chen,2026-06-16,Acme,",
        )
        result = profile_forecast_csv(str(p))
        assert any(i.issue_type == "missing_allocation" for i in result.issues)

    def test_percentage_string_not_malformed(self, tmp_path: Path) -> None:
        # "80%" should parse correctly — NOT flagged as malformed
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Acme Corp,Proj,80%,confirmed",
        )
        result = profile_forecast_csv(str(p))
        malformed = [i for i in result.issues if i.issue_type == "malformed_allocation"]
        assert malformed == []

    def test_non_numeric_allocation_malformed(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Acme,Proj,full-time,confirmed",
        )
        result = profile_forecast_csv(str(p))
        assert any(i.issue_type == "malformed_allocation" for i in result.issues)


class TestProfileDateChecks:
    def test_missing_date_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "person,week_start\nAlice Chen,",
        )
        result = profile_forecast_csv(str(p))
        assert any(i.issue_type == "missing_date" for i in result.issues)

    def test_malformed_date_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,NOT-A-DATE,Acme,Proj,100,confirmed",
        )
        result = profile_forecast_csv(str(p))
        assert any(i.issue_type == "malformed_date" for i in result.issues)

    def test_valid_iso_date_no_issue(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_forecast_csv(str(p))
        date_issues = [i for i in result.issues if "date" in i.issue_type]
        assert date_issues == []

    def test_slash_date_format_parses_ok(self, tmp_path: Path) -> None:
        # MM/DD/YYYY is accepted by pandas
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,06/16/2026,Acme Corp,Proj,100,confirmed",
        )
        result = profile_forecast_csv(str(p))
        date_issues = [i for i in result.issues if "date" in i.issue_type]
        assert date_issues == []


class TestProfileRowIssue:
    def test_row_issue_fields(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,BAD-DATE,Acme Corp,Proj,120,confirmed",
        )
        result = profile_forecast_csv(str(p))
        date_issue = next(i for i in result.issues if i.issue_type == "malformed_date")
        assert date_issue.field == "week_start"
        assert date_issue.row_index == 0
        assert "BAD-DATE" in date_issue.value

    def test_multiple_issues_on_one_row(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nStranger,BAD-DATE,Unknown Corp,Proj,150,confirmed",
        )
        result = profile_forecast_csv(
            str(p), people=_people(), clients=_clients()
        )
        types = {i.issue_type for i in result.issues}
        assert "malformed_date" in types
        assert "overallocated" in types
        assert "unknown_person" in types
        assert "unknown_client" in types


# ===========================================================================
# CLI tests
# ===========================================================================


class TestProfileForecastCLI:
    def test_help_exits_0(self) -> None:
        result = CliRunner().invoke(cli_app, ["profile-forecast", "--help"])
        assert result.exit_code == 0
        assert "profile-forecast" in result.output.lower() or "forecast" in result.output.lower()

    def test_valid_csv_exits_0(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(["profile-forecast", "--path", str(p)], _env(str(p)))
        assert result.exit_code == 0, result.output

    def test_valid_csv_shows_pass_message(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(["profile-forecast", "--path", str(p)], _env(str(p)))
        assert "No issues" in result.output or "Safe to run" in result.output

    def test_missing_required_column_exits_1(self, tmp_path: Path) -> None:
        # Missing person column — raises ValueError from ingestor
        p = _csv(tmp_path, "week_start,client\n2026-06-16,Acme")
        result = _run(["profile-forecast", "--path", str(p)], _env(str(p)))
        assert result.exit_code == 1

    def test_unreadable_file_exits_1(self, tmp_path: Path) -> None:
        nonexistent = str(tmp_path / "no_such_file.csv")
        result = _run(["profile-forecast", "--path", nonexistent], _env(nonexistent))
        assert result.exit_code == 1

    def test_warnings_only_exits_0(self, tmp_path: Path) -> None:
        # Overallocated row → warning, but not a blocking failure
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Acme Corp,Proj,150,confirmed",
        )
        result = _run(["profile-forecast", "--path", str(p)], _env(str(p)))
        assert result.exit_code == 0

    def test_path_override_flag_used(self, tmp_path: Path) -> None:
        p1 = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}", "f1.csv")
        # Set env to a different (missing) file; --path must override it
        result = _run(
            ["profile-forecast", "--path", str(p1)],
            _env(str(tmp_path / "other.csv")),
        )
        assert result.exit_code == 0, result.output

    def test_output_shows_file_path(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(["profile-forecast", "--path", str(p)], _env(str(p)))
        # Rich may wrap long paths; check a unique segment of the filename
        assert "forecast.csv" in result.output

    def test_output_shows_row_count(self, tmp_path: Path) -> None:
        rows = "\n".join([_VALID_ROW] * 3)
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{rows}")
        result = _run(["profile-forecast", "--path", str(p)], _env(str(p)))
        assert "3" in result.output

    def test_sample_size_flag(self, tmp_path: Path) -> None:
        rows = "\n".join([_VALID_ROW] * 20)
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{rows}")
        result = _run(
            ["profile-forecast", "--path", str(p), "--sample-size", "3"],
            _env(str(p)),
        )
        assert result.exit_code == 0

    def test_overallocated_shows_in_output(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Acme Corp,Proj,150,confirmed",
        )
        result = _run(["profile-forecast", "--path", str(p)], _env(str(p)))
        assert "overalloc" in result.output.lower() or "150" in result.output

    def test_malformed_date_shows_in_output(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,BADDATE,Acme Corp,Proj,100,confirmed",
        )
        result = _run(["profile-forecast", "--path", str(p)], _env(str(p)))
        assert "malformed" in result.output.lower() or "date" in result.output.lower()


class TestProfileForecastJSONOutput:
    def test_json_flag_produces_valid_json(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(
            ["profile-forecast", "--path", str(p), "--json"],
            _env(str(p)),
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_json_contains_expected_keys(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(
            ["profile-forecast", "--path", str(p), "--json"],
            _env(str(p)),
        )
        parsed = json.loads(result.output)
        assert "total_rows" in parsed
        assert "can_ingest" in parsed
        assert "issues" in parsed
        assert "column_mapping" in parsed

    def test_json_can_ingest_true_for_valid_csv(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(
            ["profile-forecast", "--path", str(p), "--json"],
            _env(str(p)),
        )
        parsed = json.loads(result.output)
        assert parsed["can_ingest"] is True

    def test_json_exits_1_for_unreadable_file(self, tmp_path: Path) -> None:
        nonexistent = str(tmp_path / "gone.csv")
        result = _run(
            ["profile-forecast", "--path", nonexistent, "--json"],
            _env(nonexistent),
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["can_ingest"] is False
        assert "error" in parsed

    def test_json_issues_list(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Acme Corp,Proj,150,confirmed",
        )
        result = _run(
            ["profile-forecast", "--path", str(p), "--json"],
            _env(str(p)),
        )
        parsed = json.loads(result.output)
        assert len(parsed["issues"]) >= 1
        issue = parsed["issues"][0]
        assert "issue_type" in issue
        assert "field" in issue
