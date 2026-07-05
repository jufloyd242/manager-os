"""Google Drive document enrichment for projects using Gemini CLI."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from manager_os.db import content_hash
from manager_os.ingest.workspace_gemini import _run_gemini_retrieval, _parse_retrieval_json
from manager_os.utils import normalize_opp_id

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
    metadata_json: dict = field(default_factory=dict)


# Document type detection patterns
# Order matters: more specific patterns must come before general ones
DOCUMENT_TYPE_PATTERNS = {
    "int_sow": ["int sow", "internal sow"],
    "sow": ["sow", "statement of work"],
    "deal_sheet": ["deal sheet", "deal summary"],
    "closure_deck": ["closure deck", "project closure deck"],
    "closure_presentation": ["closure presentation", "project closure"],
    "closeout": ["closeout", "project closeout"],
    "retrospective": ["retro", "retrospective", "lessons learned", "post-mortem"],
    "project_plan": ["project plan", "implementation plan"],
    "architecture": ["architecture", "arch design", "technical architecture"],
    "design_doc": ["design doc", "design document", "technical design"],
    "delivery_plan": ["delivery plan", "implementation timeline"],
    "runbook": ["runbook", "operations guide", "ops guide"],
    "handoff": ["handoff", "hand-off", "transition"],
    "executive_update": ["exec update", "executive update", "exec summary"],
    "proposal": ["proposal", "rfp response"],
    "estimate": ["estimate", "sizing", "effort estimate"],
    "loe": ["loe", "level of effort"],
}


def detect_document_type(title: str, url: str = "") -> str:
    """Detect document type from title and URL.
    
    Args:
        title: Document title
        url: Document URL (optional)
        
    Returns:
        Document type string
    """
    text = f"{title} {url}".lower()
    
    for doc_type, patterns in DOCUMENT_TYPE_PATTERNS.items():
        for pattern in patterns:
            if pattern in text:
                return doc_type
    
    return "other"


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
    retrieved_at = datetime.utcnow().isoformat()
    return f"""[Read-only. Metadata only]
Find Drive docs for OPP={opportunity_number}; client={client}; project={project_name}.
Search by OPP#, client, or name. Need: SOW, deal sheet, project plan, architecture, runbook, proposal/LOE.

Return ONLY JSON:
{{"ok":true,"source":"google_drive_project_docs","retrieved_at":"{retrieved_at}","documents":[{{"document_type":"sow|deal_sheet|project_plan|architecture|runbook|other","title":"str","url":"str","confidence":0.9,"why_matched":"str"}}]}}
Fail: {{"ok":false,"source":"google_drive_project_docs","error":"str"}}"""


def _build_batch_drive_search_prompt(projects: list[dict]) -> str:
    """Build a single Gemini CLI prompt that searches Drive docs for multiple projects.

    Amortizes the read-only/metadata-only/JSON-schema boilerplate across all
    projects in the batch instead of paying for it once per project.
    """
    project_lines = "\n".join(
        f"- {normalize_opp_id(p.get('opportunity_number', ''))} | {p.get('client', '')} | {p.get('project_name', '')}"
        for p in projects
    )
    return f"""[Read-only. Metadata only]
Find Drive docs for these projects (OPP | client | name):
{project_lines}

Need: SOW, deal sheet, project plan, architecture, runbook, proposal/LOE.

Return ONLY JSON mapping each OPP to its docs:
{{"ok":true,"retrieved_at":"ISO8601","results":{{"OPP123":[{{"document_type":"sow|deal_sheet|project_plan|architecture|runbook|other","title":"str","url":"str","confidence":0.9,"why_matched":"str"}}]}}}}
Fail: {{"ok":false,"error":"str","results":{{}}}}"""


def _doc_data_to_project_document(
    doc_data: dict,
    opportunity_number: str,
    client: str,
    project_name: str,
    retrieved_at: str,
) -> ProjectDocument:
    """Map a single document dict from a Gemini response to a ProjectDocument."""
    doc_type = doc_data.get("document_type", "other")
    title = doc_data.get("title", "")
    url = doc_data.get("url", "")
    if doc_type == "other" or not doc_type:
        doc_type = detect_document_type(title, url)
    return ProjectDocument(
        project_id="",
        opportunity_number=opportunity_number,
        client=client,
        project_name=project_name,
        document_type=doc_type,
        title=title,
        url=url,
        source="google_drive",
        retrieved_at=retrieved_at,
        search_status="success",
        confidence=float(doc_data.get("confidence", 0.0)),
        why_matched=doc_data.get("why_matched", ""),
        metadata_json=doc_data.get("metadata", {}),
    )


def batch_search_drive_for_projects(
    projects: list[dict],
    *,
    batch_size: int = 5,
    use_yolo: bool = True,
    timeout: int = 300,
    dry_run: bool = False,
) -> dict[str, "DriveSearchResult"]:
    """Search Google Drive for documents for multiple projects in as few Gemini
    CLI calls as possible, bounded by *batch_size* per call.

    Every requested opportunity number is guaranteed to appear in the
    returned dict, even if Gemini found no documents (or omitted it from its
    response entirely, in which case a warning is attached).

    Read-only safety comes from `_run_gemini_retrieval`, which always
    prepends `_READ_ONLY_PREFIX` before sending to the Gemini CLI.
    """
    if not projects:
        return {}

    normalized = [
        {
            "opportunity_number": normalize_opp_id(p.get("opportunity_number", "")),
            "client": p.get("client", ""),
            "project_name": p.get("project_name", ""),
        }
        for p in projects
    ]

    chunks = [
        normalized[i : i + batch_size] for i in range(0, len(normalized), batch_size)
    ]

    results_map: dict[str, DriveSearchResult] = {}

    if dry_run:
        for chunk in chunks:
            prompt = _build_batch_drive_search_prompt(chunk)
            for project in chunk:
                result = DriveSearchResult()
                result.warnings.append(f"dry_run: would search via batch prompt ({len(prompt)} chars)")
                results_map[project["opportunity_number"]] = result
        return results_map

    for chunk in chunks:
        prompt = _build_batch_drive_search_prompt(chunk)
        raw, _cmd = _run_gemini_retrieval(prompt, use_yolo=use_yolo, timeout=timeout)
        data = _parse_retrieval_json(raw)

        if not data.get("ok"):
            raise RuntimeError(f"Batch Drive search failed: {data.get('error', 'unknown error')}")

        retrieved_at = data.get("retrieved_at", datetime.utcnow().isoformat())
        chunk_results = data.get("results", {})

        for project in chunk:
            opp_id = project["opportunity_number"]
            result = DriveSearchResult()
            if opp_id in chunk_results:
                for doc_data in chunk_results[opp_id]:
                    result.documents.append(
                        _doc_data_to_project_document(
                            doc_data,
                            opp_id,
                            project["client"],
                            project["project_name"],
                            retrieved_at,
                        )
                    )
            else:
                result.warnings.append(f"OPP {opp_id} omitted from batch Drive search response")
            results_map[opp_id] = result

    return results_map


def search_drive_for_project_docs(
    opportunity_number: str,
    client: str,
    project_name: str,
    conn: Any = None,
    force: bool = False,
    limit: int = 10,
    project_id: str = "",
    timeout: int = 120,
) -> dict[str, Any]:
    """Search Google Drive for documents related to a project and return stats.
    
    Args:
        opportunity_number: The OPP number
        client: Customer/client name
        project_name: Opportunity/project name
        conn: Optional DuckDB connection to perform upsert
        force: If True, overwrite existing records when upserting
        limit: Max documents to upsert
        project_id: Optional project ID prefix
        timeout: Timeout in seconds for Gemini CLI
        
    Returns:
        Dict of stats: {
            "status": "success" | "error" | "empty",
            "raw_count": int,
            "parsed_count": int,
            "inserted": int,
            "updated": int,
            "skipped": int,
            "errors": list[str]
        }
    """
    stats = {
        "status": "success",
        "raw_count": 0,
        "parsed_count": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }
    
    if not opportunity_number:
        stats["status"] = "empty"
        stats["errors"].append("No opportunity number provided, skipping Drive search")
        return stats
    
    prompt = _build_drive_search_prompt(opportunity_number, client, project_name)
    documents = []
    
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
            stats["status"] = "error"
            stats["errors"].append(f"Gemini CLI failed: {proc.stderr}")
            return stats
        
        # Parse JSON output
        output_text = proc.stdout.strip()
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]
        
        try:
            response = json.loads(output_text)
        except json.JSONDecodeError as e:
            stats["status"] = "error"
            stats["errors"].append(f"Failed to parse Gemini response: {e}")
            return stats
        
        if not response.get("ok"):
            stats["status"] = "error"
            stats["errors"].append(f"Gemini reported error: {response.get('error', 'Unknown error')}")
            return stats
        
        # Extract documents
        retrieved_at = response.get("retrieved_at", datetime.utcnow().isoformat())
        response_docs = response.get("documents", [])
        stats["raw_count"] = len(response_docs)
        
        for doc_data in response_docs:
            # Detect document type if not provided or if it's "other"
            doc_type = doc_data.get("document_type", "other")
            title = doc_data.get("title", "")
            url = doc_data.get("url", "")
            
            if doc_type == "other" or not doc_type:
                doc_type = detect_document_type(title, url)
            
            doc = ProjectDocument(
                project_id=project_id or f"project::{opportunity_number}",
                opportunity_number=opportunity_number,
                client=client,
                project_name=project_name,
                document_type=doc_type,
                title=title,
                url=url,
                source="google_drive",
                retrieved_at=retrieved_at,
                search_status="success",
                confidence=float(doc_data.get("confidence", 0.0)),
                why_matched=doc_data.get("why_matched", ""),
                metadata_json=doc_data.get("metadata", {}),
            )
            documents.append(doc)
            
        stats["parsed_count"] = len(documents)
        
        if stats["parsed_count"] == 0:
            stats["status"] = "empty"
        
        # Upsert documents if DB connection is provided
        if conn and documents:
            docs_to_upsert = documents[:limit]
            inserted, updated, skipped = upsert_project_documents(conn, docs_to_upsert, force=force)
            stats["inserted"] = inserted
            stats["updated"] = updated
            stats["skipped"] = skipped
            
    except subprocess.TimeoutExpired:
        stats["status"] = "error"
        stats["errors"].append(f"Gemini CLI timed out after {timeout} seconds")
    except Exception as e:
        stats["status"] = "error"
        stats["errors"].append(f"Drive search failed: {str(e)}")
    
    return stats


def upsert_project_documents(
    conn,
    documents: list[ProjectDocument],
    force: bool = False,
) -> tuple[int, int, int]:
    """Upsert project documents into the project_documents table.
    
    Args:
        conn: DuckDB connection
        documents: List of ProjectDocument objects
        force: If True, overwrite existing records
        
    Returns:
        Tuple of (inserted_count, updated_count, skipped_count)
    """
    inserted = 0
    updated = 0
    skipped = 0
    
    for doc in documents:
        # Generate stable document ID
        doc_id = content_hash(f"project_document::{doc.project_id}::{doc.document_type}::{doc.url}")
        
        # Check if exists
        existing = conn.execute(
            "SELECT id FROM project_documents WHERE id = ?",
            [doc_id]
        ).fetchone()
        
        if existing:
            if force:
                updated += 1
                # Update existing record (no DELETE/REPLACE)
                conn.execute(
                    """
                    UPDATE project_documents SET
                        title = ?,
                        confidence = ?,
                        why_matched = ?,
                        retrieved_at = ?,
                        search_status = ?,
                        metadata_json = ?
                    WHERE id = ?
                    """,
                    [
                        doc.title,
                        doc.confidence,
                        doc.why_matched,
                        doc.retrieved_at,
                        doc.search_status,
                        json.dumps(doc.metadata_json) if doc.metadata_json else None,
                        doc_id,
                    ]
                )
            else:
                skipped += 1
        else:
            inserted += 1
            # Insert new record only
            conn.execute(
                """
                INSERT INTO project_documents (
                    id, project_id, opportunity_number, client, project_name,
                    document_type, title, url, source, retrieved_at,
                    search_status, confidence, why_matched, error, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(doc.metadata_json) if doc.metadata_json else None,
                ]
            )
    
    return inserted, updated, skipped
