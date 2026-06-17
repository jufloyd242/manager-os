"""Google Drive document enrichment for projects using Gemini CLI."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from manager_os.db import content_hash

logger = logging.getLogger(__name__)


@dataclass
class ProjectDocument:
    """Document found in Google Drive related to a project."""
    project_id: str
    opportunity_number: str
    client: str
    project_name: str
    document_type: str
    title: str
    url: str
    source: str = "google_drive"
    retrieved_at: str = ""
    search_status: str = "success"
    confidence: float = 0.0
    why_matched: str = ""
    error: str = ""


@dataclass
class DriveSearchResult:
    """Result of searching Google Drive for project documents."""
    documents: list[ProjectDocument] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _build_drive_search_prompt(
    opportunity_number: str,
    client: str,
    project_name: str,
) -> str:
    """Build the Gemini CLI prompt for searching Google Drive."""
    return f"""You are operating in read-only mode.
Do not create, edit, delete, move, send, or modify anything.

Search Google Drive for documents related to this project:
Opportunity Number: {opportunity_number}
Customer: {client}
Opportunity Name: {project_name}

Search primarily by exact opportunity number.
Also consider customer and opportunity name.

Return metadata only. Do not download full documents.

Find documents such as:
- SOW / INT SOW
- Deal Sheet
- Project Closure Presentation / Closure Deck
- Retrospective
- Project Plan
- Architecture / Design Doc
- Runbook / Handoff
- Proposal / LOE / Estimate

Return strict JSON only with:
{{
  "ok": true,
  "source": "google_drive_project_docs",
  "retrieved_at": "{datetime.utcnow().isoformat()}",
  "documents": [
    {{
      "document_type": "sow|deal_sheet|closure_presentation|retro|project_plan|architecture|design_doc|runbook|handoff|proposal|loe|other",
      "title": "...",
      "url": "...",
      "confidence": 0.0-1.0,
      "why_matched": "matched exact OPP number / customer / opportunity name"
    }}
  ]
}}

If you cannot find any documents, return:
{{
  "ok": true,
  "source": "google_drive_project_docs",
  "retrieved_at": "{datetime.utcnow().isoformat()}",
  "documents": []
}}

If you encounter an error, return:
{{
  "ok": false,
  "source": "google_drive_project_docs",
  "error": "..."
}}"""


def search_drive_for_project_docs(
    opportunity_number: str,
    client: str,
    project_name: str,
    timeout: int = 120,
) -> DriveSearchResult:
    """Search Google Drive for documents related to a project.
    
    Args:
        opportunity_number: The OPP number
        client: Customer/client name
        project_name: Opportunity/project name
        timeout: Timeout in seconds for Gemini CLI
        
    Returns:
        DriveSearchResult with found documents
    """
    result = DriveSearchResult()
    
    if not opportunity_number:
        result.warnings.append("No opportunity number provided, skipping Drive search")
        return result
    
    prompt = _build_drive_search_prompt(opportunity_number, client, project_name)
    
    try:
        # Build Gemini CLI command
        from manager_os.llm.gemini_cli import GEMINI_CLI_BIN, GEMINI_CLI_MODEL, GEMINI_CLI_ARGS
        
        cmd = [GEMINI_CLI_BIN]
        if GEMINI_CLI_MODEL:
            cmd.extend(["--model", GEMINI_CLI_MODEL])
        if GEMINI_CLI_ARGS:
            cmd.extend(GEMINI_CLI_ARGS.split())
        cmd.append("-y")  # YOLO mode for headless auto-approval
        
        # Run Gemini CLI
        proc = subprocess.run(
            cmd + ["--prompt", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        
        if proc.returncode != 0:
            result.errors.append(f"Gemini CLI failed: {proc.stderr}")
            return result
        
        # Parse JSON output
        output_text = proc.stdout.strip()
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]
        
        try:
            response = json.loads(output_text)
        except json.JSONDecodeError as e:
            result.errors.append(f"Failed to parse Gemini response: {e}")
            return result
        
        if not response.get("ok"):
            result.errors.append(f"Gemini reported error: {response.get('error', 'Unknown error')}")
            return result
        
        # Extract documents
        retrieved_at = response.get("retrieved_at", datetime.utcnow().isoformat())
        for doc_data in response.get("documents", []):
            doc = ProjectDocument(
                project_id="",  # Will be set by caller
                opportunity_number=opportunity_number,
                client=client,
                project_name=project_name,
                document_type=doc_data.get("document_type", "other"),
                title=doc_data.get("title", ""),
                url=doc_data.get("url", ""),
                source="google_drive",
                retrieved_at=retrieved_at,
                search_status="success",
                confidence=float(doc_data.get("confidence", 0.0)),
                why_matched=doc_data.get("why_matched", ""),
            )
            result.documents.append(doc)
    
    except subprocess.TimeoutExpired:
        result.errors.append(f"Gemini CLI timed out after {timeout} seconds")
    except Exception as e:
        result.errors.append(f"Drive search failed: {str(e)}")
    
    return result


def upsert_project_documents(
    conn,
    documents: list[ProjectDocument],
    force: bool = False,
) -> tuple[int, int]:
    """Upsert project documents into the project_documents table.
    
    Args:
        conn: DuckDB connection
        documents: List of ProjectDocument objects
        force: If True, overwrite existing records
        
    Returns:
        Tuple of (inserted_count, updated_count)
    """
    inserted = 0
    updated = 0
    
    for doc in documents:
        # Generate stable document ID
        doc_id = content_hash(f"project_document::{doc.project_id}::{doc.document_type}::{doc.url}")
        
        # Check if exists
        existing = conn.execute(
            "SELECT id FROM project_documents WHERE id = ?",
            [doc_id]
        ).fetchone()
        
        if existing and not force:
            updated += 1
            # Update existing record
            conn.execute(
                """
                UPDATE project_documents SET
                    title = ?,
                    confidence = ?,
                    why_matched = ?,
                    retrieved_at = ?,
                    search_status = ?
                WHERE id = ?
                """,
                [
                    doc.title,
                    doc.confidence,
                    doc.why_matched,
                    doc.retrieved_at,
                    doc.search_status,
                    doc_id,
                ]
            )
        else:
            inserted += 1
            # Insert new record
            conn.execute(
                """
                INSERT INTO project_documents (
                    id, project_id, opportunity_number, client, project_name,
                    document_type, title, url, source, retrieved_at,
                    search_status, confidence, why_matched, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    doc_id,
                    doc.project_id,
                    doc.opportunity_number,
                    doc.client,
                    doc.project_name,
                    doc.document_type,
                    doc.title,
                    doc.url,
                    doc.source,
                    doc.retrieved_at,
                    doc.search_status,
                    doc.confidence,
                    doc.why_matched,
                    doc.error,
                ]
            )
    
    return inserted, updated
