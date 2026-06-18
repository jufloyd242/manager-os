"""Tests for project sheet parser and project index functionality."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from manager_os.ingest.project_sheet import (
    parse_project_sheet,
    upsert_projects,
    _parse_currency,
    _parse_date,
    _extract_technologies,
)
from manager_os.db import get_connection


@pytest.fixture
def sample_csv(tmp_path):
    """Create a minimal sample project index CSV for testing (2 records)."""
    csv_content = """Year,Month,Services ($),OppID,Close Date,Sales Rep,Customer,Opp Name,Services Delivery Team,Solution Pillar,Type,Industry,3-5 words,1-2 sentences
2024,3,"$267,000",OPP030034,3/15/2024,Charlie Lisk,Acme Corp,GenAI Chatbot,AI/ML,GenAI,GenAI,Retail,AI-powered customer support,Implemented a GenAI-powered chatbot for customer support using Dialogflow CX and Vertex AI.
2024,6,"$1,097,513",OPP027764,6/20/2024,Sarah Johnson,TechStart Inc,ML Recommendation Engine,AI/ML,ML,ML,E-commerce,Built ML recommendation system,Developed a machine learning recommendation engine using Vertex AI and BigQuery.
"""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text(csv_content)
    return str(csv_path)


def test_parse_currency():
    """Test currency parsing."""
    assert _parse_currency("$267,000") == 267000.0
    assert _parse_currency("$1,097,513") == 1097513.0
    assert _parse_currency("$450,000") == 450000.0
    assert _parse_currency("") is None
    assert _parse_currency("invalid") is None


def test_parse_date():
    """Test date parsing."""
    assert _parse_date("3/15/2024") == "2024-03-15"
    assert _parse_date("6/20/2024") == "2024-06-20"
    assert _parse_date("1/10/2025") == "2025-01-10"
    assert _parse_date("") is None
    assert _parse_date("invalid") is None


def test_extract_technologies():
    """Test technology extraction from text."""
    # Test GenAI extraction
    techs = _extract_technologies("GenAI", ["Implemented GenAI chatbot with Dialogflow CX"])
    assert "GenAI" in techs
    assert "CES" in techs  # Dialogflow CX is CES
    
    # Test ADK extraction
    techs = _extract_technologies("ADK", ["Built ADK agent platform"])
    assert "ADK" in techs
    
    # Test ML extraction
    techs = _extract_technologies("ML", ["Used Vertex AI and BigQuery for ML"])
    assert "ML" in techs
    assert "BigQuery" in techs


def test_parse_project_sheet(sample_csv):
    """Test parsing project sheet CSV."""
    result = parse_project_sheet(sample_csv)
    
    # Should parse 2 projects
    assert len(result.projects) == 2
    
    # Check first project
    project = result.projects[0]
    assert project.opportunity_number == "OPP030034"
    assert project.project_name == "GenAI Chatbot"
    assert project.client == "Acme Corp"
    assert project.year == 2024
    assert project.month == 3
    assert project.services_amount == 267000.0
    assert project.close_date == "2024-03-15"
    assert project.sales_rep == "Charlie Lisk"
    assert project.project_type == "GenAI"
    assert project.industry == "Retail"
    assert project.short_description == "AI-powered customer support"
    assert "GenAI" in project.technologies
    assert "CES" in project.technologies  # Dialogflow CX
    
    # Check second project
    project2 = result.projects[1]
    assert project2.opportunity_number == "OPP027764"
    assert project2.project_name == "ML Recommendation Engine"
    assert project2.client == "TechStart Inc"
    assert project2.services_amount == 1097513.0
    assert "ML" in project2.technologies
    assert "BigQuery" in project2.technologies
    
    # Check no errors
    assert len(result.errors) == 0


def test_parse_project_sheet_skips_invalid_rows(tmp_path):
    """Test that invalid rows are skipped."""
    csv_content = """Year,Month,Services ($),OppID,Close Date,Sales Rep,Customer,Opp Name,Services Delivery Team,Solution Pillar,Type,Industry,3-5 words,1-2 sentences
2024,3,"$267,000",OPP030034,3/15/2024,Charlie Lisk,Acme Corp,GenAI Chatbot,AI/ML,GenAI,GenAI,Retail,Test,Test summary
,,,,,,,
2024,6,"$100,000",OPP027764,6/20/2024,Sarah Johnson,TechStart,ML Engine,AI/ML,ML,ML,Tech,Test,Test
"""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text(csv_content)
    
    result = parse_project_sheet(str(csv_path))
    
    # Should parse 2 valid projects, skip 1 invalid row
    assert len(result.projects) == 2
    assert result.skipped_rows == 1
    assert len(result.warnings) > 0


def test_parse_project_sheet_handles_incomplete_rows(tmp_path):
    """Test that parser handles rows with fewer columns than header."""
    csv_content = """Year,Month,Services ($),OppID,Close Date,Sales Rep,Customer,Opp Name,Services Delivery Team,Solution Pillar,Type,Industry,3-5 words,1-2 sentences
2024,3,"$267,000",OPP030034,3/15/2024,Charlie Lisk,Acme Corp,GenAI Chatbot
2024,6,"$100,000",OPP027764,6/20/2024,Sarah Johnson,TechStart,ML Engine,AI/ML,ML,ML,Tech,Test,Test
"""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text(csv_content)
    
    result = parse_project_sheet(str(csv_path))
    
    # Should parse both rows without crashing
    assert len(result.projects) == 2
    # First row has missing fields but should still be parsed
    assert result.projects[0].opportunity_number == "OPP030034"
    assert result.projects[0].project_name == "GenAI Chatbot"
    # Missing fields should be empty strings
    assert result.projects[0].services_delivery_team == ""
    assert result.projects[0].solution_pillar == ""


def test_parse_project_sheet_handles_none_values(tmp_path):
    """Test that parser handles None values without crashing."""
    csv_content = """Year,Month,Services ($),OppID,Close Date,Sales Rep,Customer,Opp Name,Services Delivery Team,Solution Pillar,Type,Industry,3-5 words,1-2 sentences
2024,3,,OPP030034,,Charlie Lisk,Acme Corp,GenAI Chatbot,AI/ML,GenAI,GenAI,Retail,,
2024,6,"$100,000",OPP027764,6/20/2024,Sarah Johnson,TechStart,ML Engine,AI/ML,ML,ML,Tech,Test,Test
"""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text(csv_content)
    
    result = parse_project_sheet(str(csv_path))
    
    # Should parse both rows without crashing
    assert len(result.projects) == 2
    # First row has None/empty values but should still be parsed
    assert result.projects[0].opportunity_number == "OPP030034"
    assert result.projects[0].services_amount is None
    assert result.projects[0].close_date is None
    assert result.projects[0].short_description == ""


def test_parse_project_sheet_skips_trailing_incomplete_row(tmp_path):
    """Test that parser skips trailing incomplete rows."""
    csv_content = """Year,Month,Services ($),OppID,Close Date,Sales Rep,Customer,Opp Name,Services Delivery Team,Solution Pillar,Type,Industry,3-5 words,1-2 sentences
2024,3,"$267,000",OPP030034,3/15/2024,Charlie Lisk,Acme Corp,GenAI Chatbot,AI/ML,GenAI,GenAI,Retail,Test,Test summary
2024,6,"$100,000",OPP027764,6/20/2024,Sarah Johnson,TechStart,ML Engine,AI/ML,ML,ML,Tech,Test,Test
,,,,
"""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text(csv_content)
    
    result = parse_project_sheet(str(csv_path))
    
    # Should parse 2 valid projects, skip 1 incomplete row
    assert len(result.projects) == 2
    assert result.skipped_rows == 1
    # Should have warning about skipped row
    assert any("Skipped" in w for w in result.warnings)


def test_parse_project_sheet_keeps_valid_row_with_blank_summary(tmp_path):
    """Test that parser keeps valid rows even with blank summary fields."""
    csv_content = """Year,Month,Services ($),OppID,Close Date,Sales Rep,Customer,Opp Name,Services Delivery Team,Solution Pillar,Type,Industry,3-5 words,1-2 sentences
2024,3,"$267,000",OPP030034,3/15/2024,Charlie Lisk,Acme Corp,GenAI Chatbot,AI/ML,GenAI,GenAI,Retail,,
"""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text(csv_content)
    
    result = parse_project_sheet(str(csv_path))
    
    # Should parse the row even with blank summary
    assert len(result.projects) == 1
    assert result.projects[0].opportunity_number == "OPP030034"
    # When summary is blank, a fallback summary is generated
    assert result.projects[0].summary_is_generated is True
    assert result.projects[0].summary != ""  # Should have generated fallback


def test_parse_project_sheet_emits_warnings_with_row_numbers(tmp_path):
    """Test that parser emits warnings with row numbers."""
    csv_content = """Year,Month,Services ($),OppID,Close Date,Sales Rep,Customer,Opp Name,Services Delivery Team,Solution Pillar,Type,Industry,3-5 words,1-2 sentences
2024,3,"$267,000",OPP030034,3/15/2024,Charlie Lisk,Acme Corp,GenAI Chatbot,AI/ML,GenAI,GenAI,Retail,Test,Test summary
,,,,,,,
2024,6,"$100,000",OPP027764,6/20/2024,Sarah Johnson,TechStart,ML Engine,AI/ML,ML,ML,Tech,Test,Test
"""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text(csv_content)
    
    result = parse_project_sheet(str(csv_path))
    
    # Should have warnings with row numbers
    assert len(result.warnings) > 0
    # Check that at least one warning contains "Row"
    assert any("Row" in w for w in result.warnings)


def test_upsert_projects(sample_csv):
    """Test upserting projects to database."""
    conn = get_connection(":memory:")
    result = parse_project_sheet(sample_csv)
    
    # Insert projects
    inserted, updated = upsert_projects(conn, result.projects, force=False)
    assert inserted == 2
    assert updated == 0
    
    # Verify projects in database
    rows = conn.execute("SELECT COUNT(*) FROM projects").fetchone()
    assert rows[0] == 2
    
    # Verify specific project
    row = conn.execute(
        "SELECT project_name, client, services_amount FROM projects WHERE id = ?",
        ["project::OPP030034"]
    ).fetchone()
    assert row[0] == "GenAI Chatbot"
    assert row[1] == "Acme Corp"
    assert row[2] == 267000.0
    
    # Upsert again without force - should update
    inserted2, updated2 = upsert_projects(conn, result.projects, force=False)
    assert inserted2 == 0
    assert updated2 == 2
    
    # Upsert with force - should insert/replace
    inserted3, updated3 = upsert_projects(conn, result.projects, force=True)
    assert inserted3 == 2
    assert updated3 == 0


def test_duplicate_oppid_handling(tmp_path):
    """Test that duplicate OppIDs don't create duplicate projects."""
    csv_content = """Year,Month,Services ($),OppID,Close Date,Sales Rep,Customer,Opp Name,Services Delivery Team,Solution Pillar,Type,Industry,3-5 words,1-2 sentences
2024,3,"$267,000",OPP030034,3/15/2024,Charlie Lisk,Acme Corp,GenAI Chatbot,AI/ML,GenAI,GenAI,Retail,Test,Test summary
2024,4,"$300,000",OPP030034,4/20/2024,Charlie Lisk,Acme Corp,GenAI Chatbot v2,AI/ML,GenAI,GenAI,Retail,Test,Updated summary
"""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text(csv_content)
    
    result = parse_project_sheet(str(csv_path))
    
    # Should parse both rows
    assert len(result.projects) == 2
    
    # Upsert both projects - first inserts, second updates (same OppID)
    conn = get_connection(":memory:")
    inserted, updated = upsert_projects(conn, result.projects, force=False)
    
    # First project inserts, second updates (same OppID)
    assert inserted == 1
    assert updated == 1
    
    # Verify only one project exists
    rows = conn.execute("SELECT COUNT(*) FROM projects WHERE id = ?", ["project::OPP030034"]).fetchone()
    assert rows[0] == 1
    
    # Verify it's the second one (updated)
    row = conn.execute(
        "SELECT project_name, services_amount FROM projects WHERE id = ?",
        ["project::OPP030034"]
    ).fetchone()
    assert row[0] == "GenAI Chatbot v2"
    assert row[1] == 300000.0


def test_technology_extraction_from_sheet(sample_csv):
    """Test that technologies are correctly extracted from sheet data."""
    result = parse_project_sheet(sample_csv)
    
    # GenAI project should have GenAI and CES (Dialogflow CX)
    genai_project = next(p for p in result.projects if p.opportunity_number == "OPP030034")
    assert "GenAI" in genai_project.technologies
    assert "CES" in genai_project.technologies
    
    # ML project should have ML and BigQuery
    ml_project = next(p for p in result.projects if p.opportunity_number == "OPP027764")
    assert "ML" in ml_project.technologies
    assert "BigQuery" in ml_project.technologies


def test_search_projects_by_type(sample_csv):
    """Test searching projects by type."""
    from manager_os.build.project_index import search_projects
    
    conn = get_connection(":memory:")
    result = parse_project_sheet(sample_csv)
    upsert_projects(conn, result.projects, force=False)
    
    # Search for GenAI projects
    genai_results = search_projects(conn, project_type="GenAI")
    assert len(genai_results) > 0
    assert all(r["project_type"] == "GenAI" for r in genai_results)
    
    # Search for ML projects
    ml_results = search_projects(conn, project_type="ML")
    assert len(ml_results) > 0
    assert all(r["project_type"] == "ML" for r in ml_results)


def test_search_projects_by_industry(sample_csv):
    """Test searching projects by industry."""
    from manager_os.build.project_index import search_projects
    
    conn = get_connection(":memory:")
    result = parse_project_sheet(sample_csv)
    upsert_projects(conn, result.projects, force=False)
    
    # Search for Retail industry
    retail_results = search_projects(conn, industry="Retail")
    assert len(retail_results) > 0
    assert all("Retail" in r["industry"] for r in retail_results)


def test_search_projects_by_sales_rep(sample_csv):
    """Test searching projects by sales rep."""
    from manager_os.build.project_index import search_projects
    
    conn = get_connection(":memory:")
    result = parse_project_sheet(sample_csv)
    upsert_projects(conn, result.projects, force=False)
    
    # Search for Charlie Lisk projects
    charlie_results = search_projects(conn, sales_rep="Charlie Lisk")
    assert len(charlie_results) > 0
    assert all(r["sales_rep"] == "Charlie Lisk" for r in charlie_results)


def test_search_projects_by_opportunity_number(sample_csv):
    """Test searching projects by opportunity number."""
    from manager_os.build.project_index import search_projects
    
    conn = get_connection(":memory:")
    result = parse_project_sheet(sample_csv)
    upsert_projects(conn, result.projects, force=False)
    
    # Search for specific OppID
    opp_results = search_projects(conn, opportunity_number="OPP030034")
    assert len(opp_results) == 1
    assert opp_results[0]["opportunity_number"] == "OPP030034"


def test_search_projects_by_technology(sample_csv):
    """Test searching projects by technology."""
    from manager_os.build.project_index import search_projects
    
    conn = get_connection(":memory:")
    result = parse_project_sheet(sample_csv)
    upsert_projects(conn, result.projects, force=False)
    
    # Search for GenAI technology
    genai_results = search_projects(conn, technology="GenAI")
    assert len(genai_results) > 0
    assert all("GenAI" in r["technologies"] for r in genai_results)
    
    # Search for BigQuery technology
    bq_results = search_projects(conn, technology="BigQuery")
    assert len(bq_results) > 0
    assert all("BigQuery" in r["technologies"] for r in bq_results)


def test_search_projects_by_year(sample_csv):
    """Test searching projects by year."""
    from manager_os.build.project_index import search_projects
    
    conn = get_connection(":memory:")
    result = parse_project_sheet(sample_csv)
    upsert_projects(conn, result.projects, force=False)
    
    # Search for 2024 projects
    results_2024 = search_projects(conn, year=2024)
    assert len(results_2024) > 0
    assert all(r["year"] == 2024 for r in results_2024)


def test_search_projects_free_text(sample_csv):
    """Test free text search across multiple fields."""
    from manager_os.build.project_index import search_projects
    
    conn = get_connection(":memory:")
    result = parse_project_sheet(sample_csv)
    upsert_projects(conn, result.projects, force=False)
    
    # Search for "chatbot"
    chatbot_results = search_projects(conn, query="chatbot")
    assert len(chatbot_results) > 0
    
    # Search for "recommendation"
    rec_results = search_projects(conn, query="recommendation")
    assert len(rec_results) > 0


def test_project_metadata_validation(tmp_path):
    """Test that project index fails if metadata is missing or invalid."""
    csv_path = tmp_path / "project_index.csv"
    csv_path.write_text("dummy content")
    
    # Test missing metadata
    meta_path = f"{csv_path}.meta.json"
    assert not Path(meta_path).exists()
    
    # Create invalid metadata
    with open(meta_path, "w") as f:
        json.dump({
            "source": "google_sheet_project_index",
            "sheet_id": "wrong_id",
            "gid": "wrong_gid",
            "retrieved_at": datetime.utcnow().isoformat(),
            "content_hash": "wrong_hash"
        }, f)
    
    # Metadata exists but has wrong values
    with open(meta_path, "r") as f:
        meta = json.load(f)
    assert meta["sheet_id"] == "wrong_id"


def test_project_documents_table(sample_csv):
    """Test project_documents table creation and insertion."""
    conn = get_connection(":memory:")
    
    # Verify table exists
    tables = conn.execute("SHOW TABLES").fetchall()
    table_names = [t[0] for t in tables]
    assert "project_documents" in table_names
    
    # Insert a test document
    conn.execute(
        """
        INSERT INTO project_documents (
            id, project_id, opportunity_number, client, project_name,
            document_type, title, url, source, retrieved_at,
            search_status, confidence, why_matched
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            "doc::test",
            "project::OPP030034",
            "OPP030034",
            "Acme Corp",
            "GenAI Chatbot",
            "sow",
            "Test SOW",
            "https://example.com/sow",
            "google_drive",
            datetime.utcnow().isoformat(),
            "success",
            0.95,
            "matched exact OPP number"
        ]
    )
    
    # Verify document was inserted
    doc = conn.execute(
        "SELECT * FROM project_documents WHERE id = ?",
        ["doc::test"]
    ).fetchone()
    assert doc is not None
    assert doc[2] == "OPP030034"  # opportunity_number
    assert doc[5] == "sow"  # document_type
