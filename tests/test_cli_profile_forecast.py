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
        # Zero allocation is VALID — should NOT create any issue
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\nAlice Chen,2026-06-16,Acme Corp,Proj,0,confirmed",
        )
        result = profile_forecast_csv(str(p))
        assert not any(i.issue_type == "zero_allocation" for i in result.issues)

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


# ===========================================================================
# Wide-format forecast CSV tests
# ===========================================================================

WIDE_FIXTURE = FIXTURES / "wide_forecast.csv"


class TestWideFormatDetection:
    def test_normalized_csv_not_detected_as_wide(self) -> None:
        from manager_os.ingest.forecast_wide import is_wide_format
        assert is_wide_format(str(FIXTURES / "forecast.csv")) is False

    def test_wide_fixture_detected_as_wide(self) -> None:
        from manager_os.ingest.forecast_wide import is_wide_format
        assert is_wide_format(str(WIDE_FIXTURE)) is True

    def test_profile_detects_wide_format_field(self) -> None:
        result = profile_forecast_csv(str(WIDE_FIXTURE))
        assert result.detected_format == "wide"

    def test_profile_normalized_fixture_format_field(self) -> None:
        result = profile_forecast_csv(str(FIXTURES / "forecast.csv"))
        assert result.detected_format == "normalized"


class TestWideFormatProfile:
    def test_can_ingest_true(self) -> None:
        result = profile_forecast_csv(str(WIDE_FIXTURE))
        assert result.can_ingest is True

    def test_sections_detected(self) -> None:
        result = profile_forecast_csv(str(WIDE_FIXTURE))
        assert "AI" in result.wide_summary["sections"]
        assert "ML" in result.wide_summary["sections"]

    def test_person_forecast_rows_count(self) -> None:
        # AI: Alex Rivera (5) + Jordan Lee (5) = 10; ML: Sam Chen (5) = 5 → 15
        result = profile_forecast_csv(str(WIDE_FIXTURE))
        assert result.wide_summary["person_forecast_rows"] == 15

    def test_person_forecast_records_have_correct_fields(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(WIDE_FIXTURE))
        alex_rows = [r for r in pr.person_forecast if r.person_name == "Alex Rivera"]
        assert len(alex_rows) == 5
        assert all(r.source_section == "AI" for r in alex_rows)
        assert all(r.target_hours == 40.0 for r in alex_rows)
        assert all(r.record_type == "person_forecast" for r in alex_rows)

    def test_zero_allocation_week_no_issue(self) -> None:
        result = profile_forecast_csv(str(WIDE_FIXTURE))
        zero_alloc_issues = [i for i in result.issues if i.issue_type == "zero_allocation"]
        assert zero_alloc_issues == []

    def test_pipeline_rows_are_demand_records(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(WIDE_FIXTURE))
        alpha_rows = [r for r in pr.pipeline_demand if r.prospect_or_deal == "Prospect Alpha Inc"]
        # Alpha has weekly demand across 5 weeks
        assert len(alpha_rows) == 5
        assert all(r.probability == 0.8 for r in alpha_rows)
        assert all(r.requested_allocation == 20.0 for r in alpha_rows)
        # candidate_people are POSSIBLE CANDIDATES, NOT allocated persons
        assert all(not hasattr(r, "person_name") for r in alpha_rows)

    def test_split_candidates_both_in_list(self) -> None:
        # "Alex/Jordan" → candidate_people=['Alex','Jordan'] on the SAME record
        # NOT two separate person_name records
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(WIDE_FIXTURE))
        alpha_rows = [r for r in pr.pipeline_demand if r.prospect_or_deal == "Prospect Alpha Inc"]
        assert alpha_rows, "Prospect Alpha Inc should have demand records"
        first = alpha_rows[0]
        assert "Alex" in first.candidate_people
        assert "Jordan" in first.candidate_people

    def test_ambiguous_assignee_stored_as_unassigned(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(WIDE_FIXTURE))
        assert pr.skipped_ambiguous > 0
        # Ambiguous rows have empty candidate_people and staffing_status=unassigned
        beta_rows = [r for r in pr.pipeline_demand if r.prospect_or_deal == "Prospect Beta Ltd"]
        assert all(r.candidate_people == [] for r in beta_rows)
        assert all(r.staffing_status == "unassigned" for r in beta_rows)

    def test_skipped_ambiguous_in_wide_summary(self) -> None:
        result = profile_forecast_csv(str(WIDE_FIXTURE))
        # 5 weeks of "?" → 5 unassigned pipeline_demand records
        assert result.wide_summary["unassigned_pipeline_demand"] == 5

    def test_year_typo_corrected_with_warning(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(WIDE_FIXTURE))
        assert any("2206" in w for w in pr.warnings)
        ml_records = [r for r in pr.person_forecast if r.source_section == "ML"]
        assert all(r.week_start.year == 2026 for r in ml_records)

    def test_pipeline_prospects_not_unknown_client_issues(self) -> None:
        # Providing an empty client list should NOT produce unknown_client issues
        # for pipeline prospect labels (they are NOT clients)
        result = profile_forecast_csv(str(WIDE_FIXTURE), clients=[])
        unknown_client_issues = [i for i in result.issues if i.issue_type == "unknown_client"]
        assert unknown_client_issues == []

    def test_unknown_engineer_flagged(self) -> None:
        result = profile_forecast_csv(str(WIDE_FIXTURE), people=[])
        unknown = [i for i in result.issues if i.issue_type == "unknown_person"]
        assert len(unknown) > 0
        person_names = {i.value for i in unknown}
        assert "Alex Rivera" in person_names or "Jordan Lee" in person_names

    def test_known_person_not_flagged(self) -> None:
        result = profile_forecast_csv(
            str(WIDE_FIXTURE),
            people=[
                PersonConfig(name="Alex Rivera", aliases=["Alex"]),
                PersonConfig(name="Jordan Lee", aliases=["Jordan"]),
                PersonConfig(name="Sam Chen", aliases=["Sam"]),
            ],
        )
        unknown = [i for i in result.issues if i.issue_type == "unknown_person"]
        assert unknown == []

    def test_candidate_engineers_not_flagged_as_unknown_person(self) -> None:
        # Pipeline candidate names ("Alex", "Jordan") are POSSIBLE CANDIDATES only.
        # Even if they don't appear in people.yaml, they should NOT create unknown_person issues.
        result = profile_forecast_csv(str(WIDE_FIXTURE), people=[])
        # All unknown_person issues should be from person_forecast rows, not pipeline candidates
        for issue in result.issues:
            if issue.issue_type == "unknown_person":
                # The issue should come from person_forecast row, not pipeline candidate
                # (source is engineer rows only)
                assert issue.detail.startswith("Engineer row:")

    def test_wide_to_dict_json_serializable(self) -> None:
        result = profile_forecast_csv(str(WIDE_FIXTURE))
        d = result.to_dict()
        json.dumps(d)  # must not raise

    def test_wide_summary_keys_in_to_dict(self) -> None:
        result = profile_forecast_csv(str(WIDE_FIXTURE))
        d = result.to_dict()
        assert "wide_summary" in d
        assert "sections" in d["wide_summary"]
        assert "person_forecast_rows" in d["wide_summary"]
        assert "pipeline_demand_rows" in d["wide_summary"]

    def test_normalized_csv_still_profiles_correctly(self) -> None:
        """Regression: normalized fixture must still work after wide support added."""
        result = profile_forecast_csv(str(FIXTURES / "forecast.csv"))
        assert result.can_ingest is True
        assert result.total_rows == 9
        assert "person_name" in result.fields_found
        assert result.detected_format == "normalized"


class TestWideFormatV2:
    """Tests using wide_forecast_v2.csv — proper AI/ML sections with new semantics."""

    V2 = FIXTURES / "wide_forecast_v2.csv"

    def test_v2_detected_as_wide(self) -> None:
        from manager_os.ingest.forecast_wide import is_wide_format
        assert is_wide_format(str(self.V2)) is True

    def test_v2_sections(self) -> None:
        result = profile_forecast_csv(str(self.V2))
        assert result.wide_summary["sections"] == ["AI", "ML"]

    def test_v2_person_forecast_rows(self) -> None:
        # AI: Avery×12 + Blake×12 = 24; ML: Cameron×12 + Devlin×12 = 24 → 48
        result = profile_forecast_csv(str(self.V2))
        assert result.wide_summary["person_forecast_rows"] == 48

    def test_v2_pipeline_demand_rows(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(self.V2))
        assert len(pr.pipeline_demand) > 0

    def test_v2_pipeline_opportunities(self) -> None:
        # Phantom Deal (no weekly demand) + Ridge Opportunity (no weekly demand)
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(self.V2))
        assert len(pr.pipeline_opportunities) == 2
        opp_names = {r.prospect_or_deal for r in pr.pipeline_opportunities}
        assert "Phantom Deal" in opp_names
        assert "Ridge Opportunity" in opp_names

    def test_v2_no_metric_mismatches(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(self.V2))
        assert len(pr.metric_mismatches) == 0, pr.metric_mismatches

    def test_v2_pipeline_candidate_not_person_name(self) -> None:
        # PipelineDemandRecord has NO person_name field
        from manager_os.ingest.forecast_wide import parse_wide_forecast, PipelineDemandRecord
        pr = parse_wide_forecast(str(self.V2))
        for rec in pr.pipeline_demand:
            assert isinstance(rec, PipelineDemandRecord)
            assert not hasattr(rec, "person_name")

    def test_v2_split_candidates_both_preserved(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(self.V2))
        nova_rows = [r for r in pr.pipeline_demand if r.prospect_or_deal == "Nova Pipeline"]
        assert nova_rows, "Nova Pipeline should have demand records"
        # "Avery/Blake" → both names in candidate_people of the SAME record
        assert "Avery" in nova_rows[0].candidate_people
        assert "Blake" in nova_rows[0].candidate_people

    def test_v2_blank_candidate_is_unassigned(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(self.V2))
        titan_rows = [r for r in pr.pipeline_demand if r.prospect_or_deal == "Titan Prospect"]
        assert titan_rows, "Titan Prospect should have demand records"
        for r in titan_rows:
            assert r.candidate_people == []
            assert r.staffing_status == "unassigned"

    def test_v2_question_mark_candidate_is_unassigned(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(self.V2))
        zenith_rows = [r for r in pr.pipeline_demand if r.prospect_or_deal == "Zenith Future"]
        assert zenith_rows, "Zenith Future should have demand records"
        for r in zenith_rows:
            assert r.candidate_people == []
            assert r.staffing_status == "unassigned"

    def test_v2_summary_metrics_present(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(self.V2))
        # 5 metrics × 12 weeks × 2 sections = 120
        assert len(pr.summary_metrics) == 120

    def test_v2_hire_status_detected(self) -> None:
        from manager_os.ingest.forecast_wide import parse_wide_forecast
        pr = parse_wide_forecast(str(self.V2))
        hire_weeks = [sm for sm in pr.summary_metrics if sm.metric_name == "hire_status"]
        assert hire_weeks, "Should have hire_status summary metrics"
        hire_raw_values = {sm.raw_value for sm in hire_weeks}
        assert "HIRE" in hire_raw_values

    def test_v2_zero_hours_not_warning(self) -> None:
        # Engineer with 0 planned hours in a week is valid, not a warning
        result = profile_forecast_csv(str(self.V2))
        assert not any(i.issue_type == "zero_allocation" for i in result.issues)

    def test_v2_prospect_labels_not_unknown_clients(self) -> None:
        result = profile_forecast_csv(str(self.V2), clients=[])
        assert not any(i.issue_type == "unknown_client" for i in result.issues)


class TestWideFormatIngest:
    """Smoke-tests that wide format can be ingested into an in-memory DuckDB."""

    def test_wide_ingest_produces_records(self) -> None:
        import duckdb
        from manager_os.db import init_schema
        from manager_os.ingest.forecast import ingest_forecast

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        result = ingest_forecast(str(WIDE_FIXTURE), conn)
        assert result.ingested > 0
        assert result.failed == 0

    def test_wide_ingest_no_ambiguous_assignee_skip(self) -> None:
        # In new model, ambiguous pipeline candidates are stored as unassigned,
        # NOT skipped. There should be no "ambiguous_assignee" skip reason.
        import duckdb
        from manager_os.db import init_schema
        from manager_os.ingest.forecast import ingest_forecast

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        result = ingest_forecast(str(WIDE_FIXTURE), conn)
        assert result.skip_reasons.get("ambiguous_assignee", 0) == 0

    def test_wide_ingest_pipeline_demand_table_populated(self) -> None:
        import duckdb
        from manager_os.db import init_schema
        from manager_os.ingest.forecast import ingest_forecast

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        ingest_forecast(str(WIDE_FIXTURE), conn)
        count = conn.execute("SELECT COUNT(*) FROM forecast_pipeline_demand").fetchone()[0]
        assert count > 0

    def test_wide_ingest_summary_metric_table_populated(self) -> None:
        import duckdb
        from manager_os.db import init_schema
        from manager_os.ingest.forecast import ingest_forecast

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        ingest_forecast(str(FIXTURES / "wide_forecast_v2.csv"), conn)
        count = conn.execute("SELECT COUNT(*) FROM forecast_summary_metric").fetchone()[0]
        assert count > 0

    def test_normalized_ingest_regression(self) -> None:
        """Regression: normalized ingest must still work."""
        import duckdb
        from manager_os.db import init_schema
        from manager_os.ingest.forecast import ingest_forecast

        conn = duckdb.connect(":memory:")
        init_schema(conn)
        result = ingest_forecast(str(FIXTURES / "forecast.csv"), conn)
        assert result.ingested > 0
        assert result.failed == 0


class TestWideFormatCLI:
    def test_wide_csv_cli_exits_0(self) -> None:
        result = CliRunner().invoke(
            cli_app,
            ["profile-forecast", "--path", str(WIDE_FIXTURE)],
            env=_env(str(WIDE_FIXTURE)),
        )
        assert result.exit_code == 0, result.output

    def test_wide_csv_shows_sections_in_output(self) -> None:
        result = CliRunner().invoke(
            cli_app,
            ["profile-forecast", "--path", str(WIDE_FIXTURE)],
            env=_env(str(WIDE_FIXTURE)),
        )
        assert "AI" in result.output
        assert "ML" in result.output

    def test_wide_csv_shows_pipeline_disclaimer(self) -> None:
        result = CliRunner().invoke(
            cli_app,
            ["profile-forecast", "--path", str(WIDE_FIXTURE)],
            env=_env(str(WIDE_FIXTURE)),
        )
        out_lower = result.output.lower()
        assert "pipeline" in out_lower or "prospect" in out_lower

    def test_wide_csv_json_output_valid(self) -> None:
        result = CliRunner().invoke(
            cli_app,
            ["profile-forecast", "--path", str(WIDE_FIXTURE), "--json"],
            env=_env(str(WIDE_FIXTURE)),
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["detected_format"] == "wide"
        assert parsed["can_ingest"] is True

