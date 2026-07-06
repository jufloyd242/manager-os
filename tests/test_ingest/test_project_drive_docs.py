"""Tests for project Drive document enrichment."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from manager_os.db import get_connection
from manager_os.ingest.project_drive_docs import (
    ProjectDocument,
    DriveSearchResult,
    detect_document_type,
    upsert_project_documents,
)


def test_detect_document_type_sow():
    """Test SOW document type detection."""
    assert detect_document_type("Project SOW - AI Platform") == "sow"
    assert detect_document_type("Statement of Work for ML Project") == "sow"
    assert detect_document_type("INT SOW - GenAI Chatbot") == "int_sow"


def test_detect_document_type_deal_sheet():
    """Test deal sheet document type detection."""
    assert detect_document_type("Deal Sheet - Retail Customer") == "deal_sheet"
    assert detect_document_type("Deal Summary - Q4 2024") == "deal_sheet"


def test_detect_document_type_closure():
    """Test closure document type detection."""
    assert detect_document_type("Project Closure Presentation") == "closure_presentation"
    assert detect_document_type("Closure Deck - AI Platform") == "closure_deck"
    assert detect_document_type("Project Closeout Report") == "closeout"


def test_detect_document_type_retrospective():
    """Test retrospective document type detection."""
    assert detect_document_type("Project Retrospective") == "retrospective"
    assert detect_document_type("Lessons Learned - Q3") == "retrospective"
    assert detect_document_type("Post-Mortem Analysis") == "retrospective"


def test_detect_document_type_technical():
    """Test technical document type detection."""
    assert detect_document_type("Architecture Design Document") == "architecture"
    assert detect_document_type("Technical Design Doc") == "design_doc"
    assert detect_document_type("Project Plan - Phase 1") == "project_plan"


def test_detect_document_type_operations():
    """Test operations document type detection."""
    assert detect_document_type("Operations Runbook") == "runbook"
    assert detect_document_type("Handoff Document") == "handoff"
    assert detect_document_type("Executive Update - Q4") == "executive_update"


def test_detect_document_type_proposal():
    """Test proposal document type detection."""
    assert detect_document_type("Project Proposal") == "proposal"
    assert detect_document_type("RFP Response - AI Platform") == "proposal"
    assert detect_document_type("Effort Estimate - GenAI") == "estimate"
    assert detect_document_type("Level of Effort Analysis") == "loe"


def test_detect_document_type_other():
    """Test unknown document type detection."""
    assert detect_document_type("Random Document") == "other"
    assert detect_document_type("Meeting Notes") == "other"


def test_upsert_project_documents_insert():
    """Test inserting new project documents."""
    conn = get_connection(":memory:")
    
    docs = [
        ProjectDocument(
            project_id="project::OPP032106",
            opportunity_number="OPP032106",
            client="Acme Corp",
            project_name="AI Platform",
            document_type="sow",
            title="Project SOW",
            url="https://docs.google.com/document/d/abc123",
            source="google_drive",
            retrieved_at=datetime.utcnow().isoformat(),
            search_status="success",
            confidence=0.95,
            why_matched="matched exact OPP number",
            metadata_json={"mimeType": "application/vnd.google-apps.document"},
        ),
        ProjectDocument(
            project_id="project::OPP032106",
            opportunity_number="OPP032106",
            client="Acme Corp",
            project_name="AI Platform",
            document_type="deal_sheet",
            title="Deal Sheet",
            url="https://docs.google.com/spreadsheets/d/def456",
            source="google_drive",
            retrieved_at=datetime.utcnow().isoformat(),
            search_status="success",
            confidence=0.90,
            why_matched="matched exact OPP number",
            metadata_json={"mimeType": "application/vnd.google-apps.spreadsheet"},
        ),
    ]
    
    inserted, updated, skipped = upsert_project_documents(conn, docs, force=False)
    
    assert inserted == 2
    assert updated == 0
    assert skipped == 0
    
    # Verify documents in database
    rows = conn.execute(
        "SELECT id, document_type, title, url, metadata_json FROM project_documents"
    ).fetchall()
    
    assert len(rows) == 2
    assert rows[0][1] == "sow"
    assert rows[0][2] == "Project SOW"
    assert rows[1][1] == "deal_sheet"
    assert rows[1][2] == "Deal Sheet"
    
    # Verify metadata_json is stored correctly
    metadata = json.loads(rows[0][4])
    assert metadata["mimeType"] == "application/vnd.google-apps.document"


def test_upsert_project_documents_update():
    """Test updating existing project documents."""
    conn = get_connection(":memory:")
    
    # Insert initial document
    doc1 = ProjectDocument(
        project_id="project::OPP032106",
        opportunity_number="OPP032106",
        client="Acme Corp",
        project_name="AI Platform",
        document_type="sow",
        title="Project SOW v1",
        url="https://docs.google.com/document/d/abc123",
        source="google_drive",
        retrieved_at=datetime.utcnow().isoformat(),
        search_status="success",
        confidence=0.90,
        why_matched="matched exact OPP number",
    )
    
    inserted, updated, skipped = upsert_project_documents(conn, [doc1], force=False)
    assert inserted == 1
    assert updated == 0
    assert skipped == 0
    
    # Update same document with force=False (should skip)
    doc2 = ProjectDocument(
        project_id="project::OPP032106",
        opportunity_number="OPP032106",
        client="Acme Corp",
        project_name="AI Platform",
        document_type="sow",
        title="Project SOW v2",
        url="https://docs.google.com/document/d/abc123",
        source="google_drive",
        retrieved_at=datetime.utcnow().isoformat(),
        search_status="success",
        confidence=0.95,
        why_matched="matched exact OPP number",
    )
    
    inserted, updated, skipped = upsert_project_documents(conn, [doc2], force=False)
    assert inserted == 0
    assert updated == 0
    assert skipped == 1
    
    # Verify not updated in db since force=False
    row = conn.execute(
        "SELECT title, confidence FROM project_documents WHERE document_type = 'sow'"
    ).fetchone()
    assert row[0] == "Project SOW v1"
    
    # Update same document with force=True (should update)
    inserted, updated, skipped = upsert_project_documents(conn, [doc2], force=True)
    assert inserted == 0
    assert updated == 1
    assert skipped == 0
    
    # Verify updated document
    row = conn.execute(
        "SELECT title, confidence FROM project_documents WHERE document_type = 'sow'"
    ).fetchone()
    
    assert row[0] == "Project SOW v2"
    assert row[1] == pytest.approx(0.95, abs=1e-4)


def test_upsert_project_documents_force():
    """Test force overwrite of existing project documents."""
    conn = get_connection(":memory:")
    
    # Insert initial document
    doc1 = ProjectDocument(
        project_id="project::OPP032106",
        opportunity_number="OPP032106",
        client="Acme Corp",
        project_name="AI Platform",
        document_type="sow",
        title="Project SOW v1",
        url="https://docs.google.com/document/d/abc123",
        source="google_drive",
        retrieved_at=datetime.utcnow().isoformat(),
        search_status="success",
        confidence=0.90,
        why_matched="matched exact OPP number",
    )
    
    inserted, updated, skipped = upsert_project_documents(conn, [doc1], force=False)
    assert inserted == 1
    assert updated == 0
    assert skipped == 0
    
    # Force overwrite (existing is True and force is True -> updated is 1)
    doc2 = ProjectDocument(
        project_id="project::OPP032106",
        opportunity_number="OPP032106",
        client="Acme Corp",
        project_name="AI Platform",
        document_type="sow",
        title="Project SOW v2",
        url="https://docs.google.com/document/d/abc123",
        source="google_drive",
        retrieved_at=datetime.utcnow().isoformat(),
        search_status="success",
        confidence=0.95,
        why_matched="matched exact OPP number",
    )
    
    inserted, updated, skipped = upsert_project_documents(conn, [doc2], force=True)
    assert inserted == 0
    assert updated == 1
    assert skipped == 0
    
    # Verify overwritten document
    row = conn.execute(
        "SELECT title, confidence FROM project_documents WHERE document_type = 'sow'"
    ).fetchone()
    
    assert row[0] == "Project SOW v2"
    assert row[1] == pytest.approx(0.95, abs=1e-4)


def test_project_document_metadata_json():
    """Test that metadata_json field is properly stored and retrieved."""
    conn = get_connection(":memory:")
    
    metadata = {
        "mimeType": "application/vnd.google-apps.document",
        "createdTime": "2024-01-15T10:30:00Z",
        "modifiedTime": "2024-06-15T14:20:00Z",
        "owners": ["user@example.com"],
    }
    
    doc = ProjectDocument(
        project_id="project::OPP032106",
        opportunity_number="OPP032106",
        client="Acme Corp",
        project_name="AI Platform",
        document_type="sow",
        title="Project SOW",
        url="https://docs.google.com/document/d/abc123",
        source="google_drive",
        retrieved_at=datetime.utcnow().isoformat(),
        search_status="success",
        confidence=0.95,
        why_matched="matched exact OPP number",
        metadata_json=metadata,
    )
    
    inserted, updated, skipped = upsert_project_documents(conn, [doc], force=False)
    assert inserted == 1
    
    # Retrieve and verify metadata
    row = conn.execute(
        "SELECT metadata_json FROM project_documents WHERE document_type = 'sow'"
    ).fetchone()
    
    retrieved_metadata = json.loads(row[0])
    assert retrieved_metadata["mimeType"] == "application/vnd.google-apps.document"
    assert retrieved_metadata["owners"] == ["user@example.com"]
