"""Tests for ``manager-os profile-deals`` and the deals profile module.

Covers:
- valid CSV: correct column detection, no issues, can_ingest=True
- missing required columns: account, deal_name → can_ingest=False, exit 1
- close date soon (within 14 days): issue recorded
- missing SOW/LOE status with close date approaching: issue recorded
- no owner: issue recorded
- unknown client (config provided): issue recorded
- high-value (late-stage) deal without staffing info: issue recorded
- malformed close date: issue recorded
- malformed probability: issue recorded
- --json output: valid JSON, correct fields
- --path override: uses supplied path, not settings default
- command exits 0 for warnings-only
- command exits 1 for missing required columns
- command exits 1 for unreadable file
- sample_size limits displayed rows without affecting issue detection
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from manager_os.cli import app as cli_app
from manager_os.config import ClientConfig
from manager_os.profile.deals import (
    DealsProfile,
    DealIssue,
    profile_deals_csv,
    _CLOSE_DATE_WARN_DAYS,
    _FIELD_DISPLAY,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_HEADER = "account,deal_name,stage,close_date,technical_owner,ae_name,loe_status,sow_status"


def _future_date(days: int) -> str:
    return (date(2026, 6, 14) + timedelta(days=days)).isoformat()


# A "safe" close date well outside the 14-day warning window.
_SAFE_CLOSE = _future_date(60)
# A "soon" close date inside the 14-day warning window.
_SOON_CLOSE = _future_date(7)

_VALID_ROW = f"Acme Corp,ACME Deal One,Proposal,{_SAFE_CLOSE},Alice Chen,Bob Kim,in-review,pending"


def _csv(tmp_path: Path, content: str, name: str = "deals.csv") -> Path:
    p = tmp_path / name
    p.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return p


def _clients() -> list[ClientConfig]:
    return [
        ClientConfig(name="Acme Corp", aliases=["Acme", "acme", "Acme Corp"]),
        ClientConfig(name="Nexus Inc", aliases=["Nexus", "nexus"]),
    ]


def _env(csv_path: str) -> dict[str, str]:
    return {
        "MANAGER_OS_DEALS_CSV": csv_path,
        "MANAGER_OS_FORECAST_CSV": str(FIXTURES / "forecast.csv"),
        "MANAGER_OS_VAULT_PATH": str(FIXTURES / "vault"),
        "MANAGER_OS_DB_PATH": "/tmp/profile_deals_test.duckdb",
        "MANAGER_OS_WORKSPACE_SUMMARY_DIR": str(FIXTURES / "summaries"),
        "MANAGER_OS_GWS_SNAPSHOT_DIR": str(FIXTURES / "gws_snapshots"),
        "MANAGER_OS_CONFIG_DIR": str(REPO_ROOT / "config"),
    }


def _run(args: list[str], env: dict[str, str]) -> object:
    return CliRunner().invoke(cli_app, args, env=env)


# Reference date pinned so tests don't drift as time passes.
_REF = date(2026, 6, 14)


# ===========================================================================
# Unit tests — profile_deals_csv()
# ===========================================================================


class TestDealsProfileValidCSV:
    def test_basic_fields_found(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert "account" in result.fields_found
        assert "deal_name" in result.fields_found
        assert result.can_ingest is True

    def test_total_row_count(self, tmp_path: Path) -> None:
        rows = "\n".join([_VALID_ROW] * 4)
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{rows}")
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert result.total_rows == 4

    def test_column_mapping_canonical_headers(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert result.column_mapping["account"] == "account"
        assert result.column_mapping["deal_name"] == "deal_name"
        assert result.column_mapping["sow_status"] == "sow_status"

    def test_no_issues_for_clean_row(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_deals_csv(str(p), clients=_clients(), reference_date=_REF)
        assert result.issues == []

    def test_sample_rows_limited_by_sample_size(self, tmp_path: Path) -> None:
        rows = "\n".join([_VALID_ROW] * 15)
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{rows}")
        result = profile_deals_csv(str(p), sample_size=5, reference_date=_REF)
        assert result.sample_size == 5
        assert len(result.sample_rows) == 5

    def test_fixture_csv_passes(self) -> None:
        result = profile_deals_csv(
            str(FIXTURES / "deals.csv"),
            reference_date=date(2025, 1, 1),  # far past so no close-date alerts
        )
        assert result.can_ingest is True
        assert result.total_rows == 5

    def test_to_dict_is_serialisable(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_deals_csv(str(p), reference_date=_REF)
        d = result.to_dict()
        json.dumps(d)  # must not raise

    def test_file_not_found_raises_runtime_error(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="Could not read"):
            profile_deals_csv(str(tmp_path / "nope.csv"), reference_date=_REF)


class TestDealsProfileMissingRequiredColumns:
    def test_missing_account_column(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"deal_name,stage\nDeal One,Proposal")
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert result.can_ingest is False
        assert "account" in result.fields_missing

    def test_missing_deal_name_column(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"account,stage\nAcme Corp,Proposal")
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert result.can_ingest is False
        assert "deal_name" in result.fields_missing

    def test_both_required_present_can_ingest_true(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"account,deal_name\nAcme Corp,Deal One")
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert result.can_ingest is True


class TestDealsProfileCloseDateSoon:
    def test_close_date_within_14_days_flagged(self, tmp_path: Path) -> None:
        soon = _future_date(7)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,{soon},Alice,Bob,in-review,pending",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "close_date_soon" for i in result.issues)

    def test_close_date_far_future_not_flagged(self, tmp_path: Path) -> None:
        far = _future_date(60)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,{far},Alice,Bob,in-review,pending",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        soon = [i for i in result.issues if i.issue_type == "close_date_soon"]
        assert soon == []

    def test_missing_close_date_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "account,deal_name,close_date\nAcme Corp,Deal One,",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "missing_close_date" for i in result.issues)

    def test_malformed_close_date_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,NOT-A-DATE,Alice,Bob,in-review,pending",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "malformed_close_date" for i in result.issues)

    def test_14_day_boundary(self, tmp_path: Path) -> None:
        # Exactly 14 days away is within the threshold → flagged
        exactly_14 = _future_date(_CLOSE_DATE_WARN_DAYS)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,{exactly_14},Alice,Bob,in-review,pending",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "close_date_soon" for i in result.issues)


class TestDealsProfileSOWLOE:
    def test_missing_sow_with_soon_close_date_flagged(self, tmp_path: Path) -> None:
        soon = _future_date(5)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,SOW Review,{soon},Alice,Bob,signed,not-started",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "missing_sow" for i in result.issues)

    def test_signed_sow_not_flagged(self, tmp_path: Path) -> None:
        soon = _future_date(5)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,SOW Review,{soon},Alice,Bob,signed,signed",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        sow = [i for i in result.issues if i.issue_type == "missing_sow"]
        assert sow == []

    def test_missing_loe_with_soon_close_date_flagged(self, tmp_path: Path) -> None:
        soon = _future_date(5)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,SOW Review,{soon},Alice,Bob,not-started,pending",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "missing_loe" for i in result.issues)

    def test_sow_missing_far_date_not_flagged(self, tmp_path: Path) -> None:
        # SOW not started but close date is far away → no flag
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,{_SAFE_CLOSE},Alice,Bob,signed,not-started",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        sow = [i for i in result.issues if i.issue_type == "missing_sow"]
        assert sow == []


class TestDealsProfileNoOwner:
    def test_empty_owner_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,{_SAFE_CLOSE},,Bob,in-review,pending",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "no_owner" for i in result.issues)

    def test_owner_present_no_issue(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_deals_csv(str(p), reference_date=_REF)
        no_owner = [i for i in result.issues if i.issue_type == "no_owner"]
        assert no_owner == []

    def test_owner_column_absent_no_issue(self, tmp_path: Path) -> None:
        # If the column doesn't exist at all, no issue is raised
        p = _csv(tmp_path, "account,deal_name,stage\nAcme Corp,Deal One,Proposal")
        result = profile_deals_csv(str(p), reference_date=_REF)
        no_owner = [i for i in result.issues if i.issue_type == "no_owner"]
        assert no_owner == []


class TestDealsProfileUnknownClient:
    def test_unknown_client_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Unknown Co,Deal One,Proposal,{_SAFE_CLOSE},Alice,Bob,in-review,pending",
        )
        result = profile_deals_csv(str(p), clients=_clients(), reference_date=_REF)
        assert any(i.issue_type == "unknown_client" for i in result.issues)

    def test_known_client_no_issue(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = profile_deals_csv(str(p), clients=_clients(), reference_date=_REF)
        unknown = [i for i in result.issues if i.issue_type == "unknown_client"]
        assert unknown == []

    def test_alias_resolves_no_issue(self, tmp_path: Path) -> None:
        # "Acme" is an alias for "Acme Corp"
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme,Deal One,Proposal,{_SAFE_CLOSE},Alice,Bob,in-review,pending",
        )
        result = profile_deals_csv(str(p), clients=_clients(), reference_date=_REF)
        unknown = [i for i in result.issues if i.issue_type == "unknown_client"]
        assert unknown == []

    def test_no_clients_config_skips_check(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Anyone At All Corp,Deal,Proposal,{_SAFE_CLOSE},Alice,Bob,signed,signed",
        )
        result = profile_deals_csv(str(p), clients=None, reference_date=_REF)
        unknown = [i for i in result.issues if i.issue_type == "unknown_client"]
        assert unknown == []


class TestDealsProfileHighValueNoStaffing:
    def test_late_stage_no_owner_no_feasibility_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "account,deal_name,stage,close_date\n"
            f"Acme Corp,Deal One,Proposal,{_SAFE_CLOSE}",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "high_value_no_staffing" for i in result.issues)

    def test_late_stage_with_owner_no_flag(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "account,deal_name,stage,close_date,technical_owner\n"
            f"Acme Corp,Deal One,Proposal,{_SAFE_CLOSE},Alice Chen",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        hv = [i for i in result.issues if i.issue_type == "high_value_no_staffing"]
        assert hv == []

    def test_early_stage_no_owner_no_flag(self, tmp_path: Path) -> None:
        # "Discovery" is not in _LATE_STAGES
        p = _csv(
            tmp_path,
            "account,deal_name,stage,close_date\n"
            f"Acme Corp,Deal One,Discovery,{_SAFE_CLOSE}",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        hv = [i for i in result.issues if i.issue_type == "high_value_no_staffing"]
        assert hv == []

    def test_sow_review_stage_without_staffing_flagged(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "account,deal_name,stage\n"
            "Acme Corp,Deal One,SOW Review",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "high_value_no_staffing" for i in result.issues)


class TestDealsProfileProbability:
    def test_malformed_probability_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"account,deal_name,probability\n"
            f"Acme Corp,Deal One,high",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "malformed_probability" for i in result.issues)

    def test_out_of_range_probability_creates_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "account,deal_name,probability\n"
            "Acme Corp,Deal One,150",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        assert any(i.issue_type == "malformed_probability" for i in result.issues)

    def test_valid_probability_no_issue(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "account,deal_name,probability\n"
            "Acme Corp,Deal One,75",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        prob = [i for i in result.issues if i.issue_type == "malformed_probability"]
        assert prob == []

    def test_probability_as_pct_string_ok(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            "account,deal_name,probability\n"
            "Acme Corp,Deal One,75%",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        prob = [i for i in result.issues if i.issue_type == "malformed_probability"]
        assert prob == []


class TestDealIssueDataclass:
    def test_fields_populated(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,NOT-A-DATE,Alice,Bob,signed,signed",
        )
        result = profile_deals_csv(str(p), reference_date=_REF)
        issue = next(i for i in result.issues if i.issue_type == "malformed_close_date")
        assert issue.field == "close_date"
        assert "NOT-A-DATE" in issue.value
        assert issue.row_index == 0


# ===========================================================================
# CLI tests
# ===========================================================================


class TestProfileDealsCLI:
    def test_help_exits_0(self) -> None:
        result = CliRunner().invoke(cli_app, ["profile-deals", "--help"])
        assert result.exit_code == 0
        assert "deals" in result.output.lower()

    def test_valid_csv_exits_0(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(["profile-deals", "--path", str(p)], _env(str(p)))
        assert result.exit_code == 0, result.output

    def test_valid_csv_shows_no_issues_message(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(["profile-deals", "--path", str(p)], _env(str(p)))
        assert "No issues" in result.output or "Safe to run" in result.output

    def test_missing_required_column_exits_1(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, "stage,close_date\nProposal,2026-12-01")
        result = _run(["profile-deals", "--path", str(p)], _env(str(p)))
        assert result.exit_code == 1

    def test_unreadable_file_exits_1(self, tmp_path: Path) -> None:
        nonexistent = str(tmp_path / "no_such_file.csv")
        result = _run(["profile-deals", "--path", nonexistent], _env(nonexistent))
        assert result.exit_code == 1

    def test_warnings_only_exits_0(self, tmp_path: Path) -> None:
        soon = _future_date(5)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,{soon},,Bob,in-review,pending",
        )
        result = _run(["profile-deals", "--path", str(p)], _env(str(p)))
        assert result.exit_code == 0

    def test_path_override_used(self, tmp_path: Path) -> None:
        p1 = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}", "d1.csv")
        result = _run(
            ["profile-deals", "--path", str(p1)],
            _env(str(tmp_path / "other.csv")),   # env points elsewhere
        )
        assert result.exit_code == 0, result.output

    def test_output_shows_file_name(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}", "my_deals.csv")
        result = _run(["profile-deals", "--path", str(p)], _env(str(p)))
        assert "my_deals.csv" in result.output

    def test_output_shows_row_count(self, tmp_path: Path) -> None:
        rows = "\n".join([_VALID_ROW] * 3)
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{rows}")
        result = _run(["profile-deals", "--path", str(p)], _env(str(p)))
        assert "3" in result.output

    def test_sample_size_flag(self, tmp_path: Path) -> None:
        rows = "\n".join([_VALID_ROW] * 20)
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{rows}")
        result = _run(
            ["profile-deals", "--path", str(p), "--sample-size", "3"],
            _env(str(p)),
        )
        assert result.exit_code == 0

    def test_close_date_soon_shows_in_output(self, tmp_path: Path) -> None:
        soon = _future_date(5)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,{soon},Alice,Bob,signed,signed",
        )
        result = _run(["profile-deals", "--path", str(p)], _env(str(p)))
        assert "close date" in result.output.lower() or "soon" in result.output.lower()

    def test_malformed_close_date_shows_in_output(self, tmp_path: Path) -> None:
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,BADDATE,Alice,Bob,signed,signed",
        )
        result = _run(["profile-deals", "--path", str(p)], _env(str(p)))
        assert "malformed" in result.output.lower() or "date" in result.output.lower()

    def test_fixture_csv_exits_0(self) -> None:
        result = _run(
            ["profile-deals", "--path", str(FIXTURES / "deals.csv")],
            _env(str(FIXTURES / "deals.csv")),
        )
        assert result.exit_code == 0, result.output


class TestProfileDealsJSONOutput:
    def test_json_flag_produces_valid_json(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(
            ["profile-deals", "--path", str(p), "--json"],
            _env(str(p)),
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_json_contains_expected_keys(self, tmp_path: Path) -> None:
        p = _csv(tmp_path, f"{_VALID_HEADER}\n{_VALID_ROW}")
        result = _run(
            ["profile-deals", "--path", str(p), "--json"],
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
            ["profile-deals", "--path", str(p), "--json"],
            _env(str(p)),
        )
        parsed = json.loads(result.output)
        assert parsed["can_ingest"] is True

    def test_json_exits_1_for_unreadable_file(self, tmp_path: Path) -> None:
        nonexistent = str(tmp_path / "gone.csv")
        result = _run(
            ["profile-deals", "--path", nonexistent, "--json"],
            _env(nonexistent),
        )
        assert result.exit_code == 1
        parsed = json.loads(result.output)
        assert parsed["can_ingest"] is False
        assert "error" in parsed

    def test_json_issues_list_for_problematic_csv(self, tmp_path: Path) -> None:
        soon = _future_date(5)
        p = _csv(
            tmp_path,
            f"{_VALID_HEADER}\n"
            f"Acme Corp,Deal One,Proposal,{soon},,Bob,in-review,not-started",
        )
        result = _run(
            ["profile-deals", "--path", str(p), "--json"],
            _env(str(p)),
        )
        parsed = json.loads(result.output)
        assert len(parsed["issues"]) >= 1
        issue = parsed["issues"][0]
        assert "issue_type" in issue
        assert "field" in issue
