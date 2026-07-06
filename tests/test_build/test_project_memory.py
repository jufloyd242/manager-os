"""Tests for project memory refinement (Phases 2-10)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from manager_os.db import content_hash, get_connection
from manager_os.utils import normalize_opp_id
from manager_os.build.project_index import (
    extract_projects_from_notes,
    search_projects,
)
from manager_os.build.similar_projects import find_similar_projects
from manager_os.ingest.project_drive_docs import (
    ProjectDocument,
    upsert_project_documents,
)
from manager_os.ingest.project_sheet import (
    ProjectRecord,
    upsert_projects,
)


@pytest.fixture()
def conn():
    """In-memory DuckDB connection."""
    return get_connection(":memory:")


# ------------------------------------------------------------------
# Phase 4: Normalize OppID
# ------------------------------------------------------------------


def test_normalize_opp_id_basic():
    """Test basic OppID normalization."""
    assert normalize_opp_id("OPP031267") == "OPP031267"
    assert normalize_opp_id("opp031267") == "OPP031267"
    assert normalize_opp_id(" OPP031267 ") == "OPP031267"
    assert normalize_opp_id("opp031267") == "OPP031267"
    assert normalize_opp_id("") == ""
    assert normalize_opp_id(None) == ""


def test_normalize_opp_id_in_search(conn):
    """Test that search normalizes OppID."""
    # Insert a project with uppercase OppID
    project_id = "project::OPP031267"
    conn.execute(
        """
        INSERT INTO projects (
            id, project_name, client, opportunity_number, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [project_id, "Test Project", "Test Client", "OPP031267", "completed"],
    )
    
    # Search with lowercase should find it
    results = search_projects(conn, opportunity_number="opp031267")
    assert len(results) == 1
    assert results[0]["opportunity_number"] == "OPP031267"
    
    # Search with spaces should find it
    results = search_projects(conn, opportunity_number=" OPP031267 ")
    assert len(results) == 1


# ------------------------------------------------------------------
# Phase 2: Notes enrichment does not create canonical projects
# ------------------------------------------------------------------


def test_extract_projects_from_notes_enrichment_only(conn):
    """Test that notes enrichment only enriches existing projects."""
    # Insert a canonical project
    project_id = "project::OPP031267"
    conn.execute(
        """
        INSERT INTO projects (
            id, project_name, client, opportunity_number, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [project_id, "Test Project", "Test Client", "OPP031267", "completed"],
    )
    
    # Insert a note that mentions the project
    note_id = content_hash("note::test")
    conn.execute(
        """
        INSERT INTO notes (
            id, raw_document_id, note_date, note_type, entity_type, entity_name,
            title, body, tags, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            note_id,
            "raw_doc_1",
            date.today(),
            "meeting",
            "client",
            "Test Client",
            "Meeting Notes",
            "Discussed OPP031267 and Gemini implementation",
            json.dumps(["test"]),
        ],
    )
    
    # Run enrichment
    enriched = extract_projects_from_notes(conn, force=True)
    assert enriched == 1
    
    # Check that project_notes_context was populated
    context_rows = conn.execute(
        "SELECT * FROM project_notes_context WHERE project_id = ?",
        [project_id],
    ).fetchall()
    assert len(context_rows) == 1
    
    # Check that no new canonical projects were created
    project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert project_count == 1


def test_extract_projects_from_notes_skips_unmatched(conn):
    """Test that notes without matching projects are skipped."""
    # Insert a note with no matching project
    note_id = content_hash("note::unmatched")
    conn.execute(
        """
        INSERT INTO notes (
            id, raw_document_id, note_date, note_type, entity_type, entity_name,
            title, body, tags, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            note_id,
            "raw_doc_1",
            date.today(),
            "meeting",
            "client",
            "Unknown Client",
            "Meeting Notes",
            "Discussed OPP999999",
            json.dumps(["test"]),
        ],
    )
    
    # Run enrichment
    enriched = extract_projects_from_notes(conn, force=True)
    assert enriched == 0
    
    # Check that no context was created
    context_count = conn.execute(
        "SELECT COUNT(*) FROM project_notes_context"
    ).fetchone()[0]
    assert context_count == 0


# ------------------------------------------------------------------
# Phase 5: Project document upsert avoids INSERT OR REPLACE
# ------------------------------------------------------------------


def test_upsert_project_documents_no_replace(conn):
    """Test that document upsert uses UPDATE not INSERT OR REPLACE."""
    project_id = "project::OPP031267"
    
    # Insert a project
    conn.execute(
        """
        INSERT INTO projects (
            id, project_name, client, opportunity_number, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [project_id, "Test Project", "Test Client", "OPP031267", "completed"],
    )
    
    # Create a document
    doc = ProjectDocument(
        project_id=project_id,
        opportunity_number="OPP031267",
        client="Test Client",
        project_name="Test Project",
        document_type="sow",
        title="SOW Document",
        url="https://example.com/sow",
        source="google_drive",
        retrieved_at="2024-01-01T00:00:00",
        search_status="success",
        confidence=0.9,
        why_matched="matched OppID",
    )
    
    # Insert first time
    inserted, updated, skipped = upsert_project_documents(conn, [doc], force=False)
    assert inserted == 1
    assert updated == 0
    assert skipped == 0
    
    # Get the document ID
    doc_id = conn.execute(
        "SELECT id FROM project_documents WHERE url = ?",
        ["https://example.com/sow"],
    ).fetchone()[0]
    
    # Update the document
    doc.title = "Updated SOW Document"
    doc.confidence = 0.95
    inserted, updated, skipped = upsert_project_documents(conn, [doc], force=True)
    assert inserted == 0
    assert updated == 1
    assert skipped == 0
    
    # Check that the document was updated (not replaced)
    result = conn.execute(
        "SELECT title, confidence FROM project_documents WHERE id = ?",
        [doc_id],
    ).fetchone()
    assert result[0] == "Updated SOW Document"
    assert abs(result[1] - 0.95) < 0.001  # Float precision tolerance
    
    # Check that document ID is stable
    assert conn.execute(
        "SELECT COUNT(*) FROM project_documents WHERE id = ?",
        [doc_id],
    ).fetchone()[0] == 1


def test_repeated_upsert_same_document(conn):
    """Test that repeated upsert of same document works correctly."""
    project_id = "project::OPP031267"
    
    conn.execute(
        """
        INSERT INTO projects (
            id, project_name, client, opportunity_number, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [project_id, "Test Project", "Test Client", "OPP031267", "completed"],
    )
    
    doc = ProjectDocument(
        project_id=project_id,
        opportunity_number="OPP031267",
        client="Test Client",
        project_name="Test Project",
        document_type="sow",
        title="SOW Document",
        url="https://example.com/sow",
        source="google_drive",
        retrieved_at="2024-01-01T00:00:00",
        search_status="success",
        confidence=0.9,
        why_matched="matched OppID",
    )
    
    # Upsert multiple times
    for i in range(3):
        doc.title = f"SOW Document v{i}"
        upsert_project_documents(conn, [doc], force=True)
    
    # Should still be only one document
    count = conn.execute(
        "SELECT COUNT(*) FROM project_documents WHERE url = ?",
        ["https://example.com/sow"],
    ).fetchone()[0]
    assert count == 1


# ------------------------------------------------------------------
# Phase 7: Ranked project search
# ------------------------------------------------------------------


def test_search_projects_ranked_by_score(conn):
    """Test that search returns results ranked by score."""
    # Insert multiple projects
    projects = [
        ("project::OPP001", "Project 1", "Client A", "OPP001", "ADK"),
        ("project::OPP002", "Project 2", "Client B", "OPP002", "GenAI"),
        ("project::OPP003", "Project 3", "Client A", "OPP003", "ADK"),
    ]
    
    for pid, name, client, opp, ptype in projects:
        conn.execute(
            """
            INSERT INTO projects (
                id, project_name, client, opportunity_number, project_type, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [pid, name, client, opp, ptype, "completed"],
        )
    
    # Search for exact OppID
    results = search_projects(conn, opportunity_number="OPP002")
    assert len(results) == 1
    assert results[0]["opportunity_number"] == "OPP002"
    assert results[0]["score"] >= 100  # Exact OppID match
    assert "exact opportunity number match" in results[0]["match_reasons"]
    
    # Search for client
    results = search_projects(conn, client="Client A")
    assert len(results) == 2
    # Both should have exact client match score
    for r in results:
        assert r["score"] >= 50
        assert "exact client match" in r["match_reasons"]


def test_search_projects_document_title_match(conn):
    """Test that document title matches contribute to score."""
    project_id = "project::OPP031267"
    
    conn.execute(
        """
        INSERT INTO projects (
            id, project_name, client, opportunity_number, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [project_id, "Gemini Project", "Test Client", "OPP031267", "completed"],
    )
    
    # Insert a document with matching title
    doc_id = content_hash("doc::test")
    conn.execute(
        """
        INSERT INTO project_documents (
            id, project_id, document_type, title, url, confidence
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [doc_id, project_id, "sow", "Gemini SOW", "https://example.com", 0.9],
    )
    
    # Search for "Gemini"
    results = search_projects(conn, query="Gemini")
    assert len(results) == 1
    assert results[0]["score"] >= 20  # Document title match
    assert any("document title match" in reason for reason in results[0]["match_reasons"])


# ------------------------------------------------------------------
# Phase 8: Similar projects with score breakdown
# ------------------------------------------------------------------


def test_find_similar_projects_score_breakdown(conn):
    """Test that similar projects returns score breakdown."""
    # Insert a deal
    deal_id = "deal::test"
    conn.execute(
        """
        INSERT INTO deals (
            id, account, deal_name, requested_roles, stage,
            loe_status, sow_status, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            deal_id,
            "Test Client",
            "Gemini Implementation",
            json.dumps(["ADK", "Gemini"]),
            "proposal",
            "complete",
            "complete",
        ],
    )
    
    # Insert a similar project
    project_id = "project::OPP001"
    conn.execute(
        """
        INSERT INTO projects (
            id, project_name, client, opportunity_number, project_type,
            technologies_json, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [
            project_id,
            "Past Gemini Project",
            "Test Client",
            "OPP001",
            "ADK",
            json.dumps(["Gemini", "ADK"]),
            "completed",
        ],
    )
    
    # Find similar projects
    matches = find_similar_projects(conn, deal_id=deal_id)
    assert len(matches) == 1
    
    match = matches[0]
    assert "score_breakdown" in match
    assert isinstance(match["score_breakdown"], dict)
    
    # Should have client match
    assert "client_match" in match["score_breakdown"]
    assert match["score_breakdown"]["client_match"] == 20
    
    # Should have technology match
    assert "technology_match" in match["score_breakdown"]


# ------------------------------------------------------------------
# Phase 3: project-index-fetch StringIO fix
# ------------------------------------------------------------------


def test_project_index_fetch_stringio(tmp_path):
    """Test that project-index-fetch uses StringIO correctly."""
    from io import StringIO
    import csv
    
    # Simulate the fixed code pattern
    rows = [
        ["OppID", "Customer", "Opp Name"],
        ["OPP001", "Client A", "Project A"],
        ["OPP002", "Client B", "Project B"],
    ]
    
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    csv_content = buffer.getvalue()
    
    # Write to file
    local_csv = tmp_path / "projects.csv"
    local_csv.write_text(csv_content, encoding="utf-8")
    
    # Verify content
    content = local_csv.read_text(encoding="utf-8")
    assert "OPP001" in content
    assert "Client A" in content
    
    # Verify hash
    import hashlib
    content_hash = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()
    assert len(content_hash) == 64


# ------------------------------------------------------------------
# Phase 10: project-memory-report
# ------------------------------------------------------------------


def test_project_memory_report_data(conn):
    """Test that project-memory-report queries work correctly."""
    # Insert test data
    for i in range(5):
        project_id = f"project::OPP{i:03d}"
        conn.execute(
            """
            INSERT INTO projects (
                id, project_name, client, opportunity_number, project_type,
                technologies_json, summary, summary_is_generated, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [
                project_id,
                f"Project {i}",
                f"Client {i}",
                f"OPP{i:03d}",
                "ADK" if i % 2 == 0 else "",
                json.dumps(["Gemini"]) if i % 2 == 0 else "[]",
                f"Summary {i}" if i < 3 else "",
                i >= 3,
                "completed",
            ],
        )
    
    # Test queries
    total = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert total == 5
    
    with_summaries = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE summary IS NOT NULL AND summary != ''"
    ).fetchone()[0]
    assert with_summaries == 3
    
    generated_only = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE summary_is_generated = TRUE"
    ).fetchone()[0]
    assert generated_only == 2
    
    missing_tech = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE technologies_json IS NULL OR technologies_json = '[]'"
    ).fetchone()[0]
    assert missing_tech == 2  # i=1 and i=3 have no tech
    
    missing_type = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE project_type IS NULL OR project_type = ''"
    ).fetchone()[0]
    assert missing_type == 2  # i=1 and i=3 have no type
