"""Tests for wide-format sectioned forecast parsing."""

from pathlib import Path

from manager_os.ingest.forecast_wide import parse_wide_forecast, is_wide_format


def test_is_wide_format_sectioned():
    fixture_path = Path(__file__).parent.parent / "fixtures" / "wide_forecast_sectioned.csv"
    assert is_wide_format(str(fixture_path)) is True


def test_parse_wide_sectioned_format():
    fixture_path = Path(__file__).parent.parent / "fixtures" / "wide_forecast_sectioned.csv"
    result = parse_wide_forecast(str(fixture_path))

    assert result.format_detected is True
    assert "AI" in result.sections
    assert "ML" in result.sections

    # Check engineer rows
    alice_records = [r for r in result.person_forecast if r.person_name == "Alice"]
    assert len(alice_records) == 12  # 12 weeks
    assert alice_records[0].target_hours == 40.0
    assert alice_records[0].planned_hours == 40.0

    # Check overallocated person (Bob: 45/40 = 112.5%)
    bob_records = [r for r in result.person_forecast if r.person_name == "Bob"]
    assert len(bob_records) == 12
    assert bob_records[0].target_hours == 40.0
    assert bob_records[0].planned_hours == 45.0

    # Check blank weekly cell = 0 planned hours (Charlie)
    charlie_records = [r for r in result.person_forecast if r.person_name == "Charlie"]
    # Charlie has a blank cell in week 1 (6/15/2026) based on CSV alignment
    charlie_week1 = [r for r in charlie_records if str(r.week_start) == "2026-06-15"]
    assert len(charlie_week1) == 1
    assert charlie_week1[0].planned_hours == 0.0
    assert charlie_week1[0].target_hours == 40.0

    # Check pipeline rows with scheduled demand
    mty_demand = [r for r in result.pipeline_demand if r.prospect_or_deal == "MTY"]
    assert len(mty_demand) > 0
    assert mty_demand[0].probability == 75.0
    assert mty_demand[0].requested_allocation == 25.0
    assert mty_demand[0].skillset == "ML"
    assert mty_demand[0].duration_weeks == 8
    assert mty_demand[0].candidate_people == ["Satya", "Zheng"]
    assert mty_demand[0].expected_start_week is not None
    assert mty_demand[0].expected_end_week is not None

    # Check unscheduled pipeline opportunity
    future_deal_opp = [r for r in result.pipeline_opportunities if r.prospect_or_deal == "Future Deal"]
    assert len(future_deal_opp) == 1
    assert future_deal_opp[0].probability == 60.0
    assert future_deal_opp[0].requested_allocation == 75.0
    assert future_deal_opp[0].duration_weeks == 16
    assert future_deal_opp[0].candidate_people == ["Alice", "Bob"]
    assert future_deal_opp[0].status == "unscheduled"
    assert future_deal_opp[0].estimated_weighted_weekly_hours == 18.0  # 0.6 * 0.75 * 40
    
    # Check candidate splitting
    ella_candidates = [r for r in result.pipeline_demand if r.prospect_or_deal == "SEI GECX Ella chatbot"][0].candidate_people
    assert "Bob" in ella_candidates
    assert "Charlie" in ella_candidates

    # Check ambiguous candidate (?)
    geap_candidates = [r for r in result.pipeline_demand if r.prospect_or_deal == "SEI GEAP"][0].candidate_people
    assert geap_candidates == []

    # Check metric parsing
    assert len(result.summary_metrics) > 0
    # We can check if mismatches are recorded (they might be due to my simplified fixture)
    # The parser should not crash.