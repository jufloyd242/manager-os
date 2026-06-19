"""Project sheet parser - deterministic parsing of NetSuite Closed-Won Opportunities CSV."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from manager_os.db import content_hash
from manager_os.utils import normalize_opp_id

logger = logging.getLogger(__name__)

# Technology keyword map for extraction from text fields
TECH_KEYWORDS = {
    "ADK": ["ADK", "Agent Development Kit", "agentic"],
    "GenAI": ["GenAI", "Gemini", "LLM", "RAG", "chatbot", "agent"],
    "CES": ["CCAI", "Contact Center", "Dialogflow", "Dialogflow CX", "support automation"],
    "ML": ["ML", "machine learning", "Vertex AI", "embeddings", "XGBoost", "recommendation"],
    "Search": ["Vertex AI Search", "enterprise search", "semantic search"],
    "Media Rec": ["Recommendations AI", "media recommendations"],
    "Retail Rec": ["Recommendations AI", "retail recommendations"],
    "DocAI": ["Document AI", "Doc AI"],
    "BigQuery": ["BigQuery"],
    "Apigee": ["Apigee"],
    "IAM": ["IAM"],
    "GKE": ["GKE", "Kubernetes"],
    "Cloud Run": ["Cloud Run"],
    "Terraform": ["Terraform"],
    "Looker": ["Looker"],
    "dbt": ["dbt"],
    "Python": ["Python"],
    "FastAPI": ["FastAPI"],
    "LangChain": ["LangChain"],
    "Dataflow": ["Dataflow"],
    "Pub/Sub": ["Pub/Sub"],
    "Cloud SQL": ["Cloud SQL"],
    "Security Command Center": ["Security Command Center"],
    "AlloyDB": ["AlloyDB"],
    "Cloud Functions": ["Cloud Functions"],
    "Cloud Build": ["Cloud Build"],
    "Cloud Deploy": ["Cloud Deploy"],
    "MLOps": ["MLOps"],
}


@dataclass
class ProjectRecord:
    """Canonical project record from NetSuite sheet."""
    opportunity_number: str
    project_name: str
    client: str
    year: int | None = None
    month: int | None = None
    services_amount: float | None = None
    close_date: str | None = None
    sales_rep: str = ""
    services_delivery_team: str = ""
    solution_pillar: str = ""
    project_type: str = ""
    industry: str = ""
    short_description: str = ""
    summary: str = ""
    technologies: list[str] = field(default_factory=list)
    source_row: int = 0
    summary_is_generated: bool = False


@dataclass
class ParseResult:
    """Result of parsing project sheet CSV."""
    projects: list[ProjectRecord] = field(default_factory=list)
    skipped_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class MetadataInfo:
    """Metadata validation result."""
    valid: bool = False
    sheet_id: str = ""
    gid: str = ""
    retrieved_at: str = ""
    content_hash: str = ""
    row_count: int = 0
    error: str = ""


def validate_metadata(csv_path: str, expected_sheet_id: str, expected_gid: str, stale_hours: int = 24) -> MetadataInfo:
    """Validate project index CSV metadata.
    
    Args:
        csv_path: Path to the CSV file
        expected_sheet_id: Expected Google Sheet ID
        expected_gid: Expected Google Sheet GID
        stale_hours: Hours after which metadata is considered stale
        
    Returns:
        MetadataInfo with validation result
    """
    meta_path = f"{csv_path}.meta.json"
    
    if not Path(meta_path).exists():
        return MetadataInfo(
            valid=False,
            error=f"Metadata file not found: {meta_path}. Run 'manager-os project-index-fetch' first."
        )
    
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        
        # Check sheet_id and gid
        if meta.get("sheet_id") != expected_sheet_id:
            return MetadataInfo(
                valid=False,
                sheet_id=meta.get("sheet_id", ""),
                gid=meta.get("gid", ""),
                error=f"Sheet ID mismatch. Expected {expected_sheet_id}, got {meta.get('sheet_id')}"
            )
        
        if meta.get("gid") != expected_gid:
            return MetadataInfo(
                valid=False,
                sheet_id=meta.get("sheet_id", ""),
                gid=meta.get("gid", ""),
                error=f"GID mismatch. Expected {expected_gid}, got {meta.get('gid')}"
            )
        
        # Check freshness
        retrieved_at_str = meta.get("retrieved_at", "")
        if retrieved_at_str:
            retrieved_at_str = retrieved_at_str.replace("Z", "+00:00")
            try:
                retrieved_at = datetime.fromisoformat(retrieved_at_str)
                if retrieved_at.tzinfo is None:
                    retrieved_at = retrieved_at.replace(tzinfo=datetime.now().astimezone().tzinfo)
                
                now = datetime.now(retrieved_at.tzinfo)
                age_hours = (now - retrieved_at).total_seconds() / 3600
                
                if age_hours > stale_hours:
                    return MetadataInfo(
                        valid=False,
                        sheet_id=meta.get("sheet_id", ""),
                        gid=meta.get("gid", ""),
                        retrieved_at=retrieved_at_str,
                        error=f"Metadata is stale (retrieved {age_hours:.1f}h ago, stale after {stale_hours}h). Run 'manager-os project-index-fetch --force' to refresh."
                    )
            except ValueError:
                pass
        
        # Check content hash
        if not Path(csv_path).exists():
            return MetadataInfo(
                valid=False,
                sheet_id=meta.get("sheet_id", ""),
                gid=meta.get("gid", ""),
                retrieved_at=retrieved_at_str,
                error=f"CSV file not found: {csv_path}"
            )
        
        with open(csv_path, "rb") as f:
            actual_hash = hashlib.sha256(f.read()).hexdigest()
        
        expected_hash = meta.get("content_hash", "")
        if actual_hash != expected_hash:
            return MetadataInfo(
                valid=False,
                sheet_id=meta.get("sheet_id", ""),
                gid=meta.get("gid", ""),
                retrieved_at=retrieved_at_str,
                content_hash=actual_hash,
                error=f"Content hash mismatch. CSV may have been modified. Run 'manager-os project-index-fetch --force' to refresh."
            )
        
        return MetadataInfo(
            valid=True,
            sheet_id=meta.get("sheet_id", ""),
            gid=meta.get("gid", ""),
            retrieved_at=retrieved_at_str,
            content_hash=actual_hash,
            row_count=meta.get("row_count", 0)
        )
    
    except Exception as e:
        return MetadataInfo(
            valid=False,
            error=f"Failed to validate metadata: {str(e)}"
        )


def record_indexing_run(
    conn,
    source: str,
    sheet_id: str,
    gid: str,
    source_url: str,
    local_csv_path: str,
    row_count: int,
    valid_project_count: int,
    skipped_row_count: int,
    warning_count: int,
    csv_content_hash: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    error: str = ""
) -> str:
    """Record a project indexing run in the project_index_runs table.
    
    Args:
        conn: DuckDB connection
        source: Source type (e.g., "google_sheet_project_index")
        sheet_id: Google Sheet ID
        gid: Google Sheet GID
        source_url: Source URL
        local_csv_path: Local CSV path
        row_count: Total rows in CSV
        valid_project_count: Number of valid projects parsed
        skipped_row_count: Number of skipped rows
        warning_count: Number of warnings
        csv_content_hash: Content hash of CSV
        started_at: Run start time
        finished_at: Run finish time
        status: Run status ("success" or "failed")
        error: Error message if failed
        
    Returns:
        Run ID
    """
    run_id = content_hash(f"project_index_run::{started_at.isoformat()}::{sheet_id}")
    
    conn.execute(
        """
        INSERT INTO project_index_runs (
            id, source, sheet_id, gid, source_url, local_csv_path,
            row_count, valid_project_count, skipped_row_count, warning_count,
            content_hash, started_at, finished_at, status, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id, source, sheet_id, gid, source_url, local_csv_path,
            row_count, valid_project_count, skipped_row_count, warning_count,
            csv_content_hash, started_at.isoformat(), finished_at.isoformat(),
            status, error
        ]
    )
    
    return run_id


def _cell(row: dict, name: str) -> str:
    """Safely extract a cell value from a row, handling None values."""
    value = row.get(name, "")
    if value is None:
        return ""
    return str(value).strip()


def _parse_currency(value: object) -> float | None:
    """Parse currency string like '$267,000' to float."""
    value = "" if value is None else str(value).strip()
    if not value:
        return None
    # Remove $ and commas
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(value: object) -> str | None:
    """Parse date string like '8/4/2021' to ISO format."""
    value = "" if value is None else str(value).strip()
    if not value:
        return None
    try:
        # Try common formats
        for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]:
            try:
                dt = datetime.strptime(value, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None
    except Exception:
        return None


def _extract_technologies(project_type: str, text_fields: list[object]) -> list[str]:
    """Extract technology keywords from project type and text fields."""
    technologies = set()
    
    # Add project type if it matches a known category
    if project_type in TECH_KEYWORDS:
        technologies.add(project_type)
    
    # Search text fields for technology keywords
    safe_fields = ["" if v is None else str(v) for v in text_fields]
    combined_text = " ".join(safe_fields).lower()
    for tech, keywords in TECH_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in combined_text:
                technologies.add(tech)
                break
    
    return sorted(technologies)


def _generate_fallback_summary(project: ProjectRecord) -> str:
    """Generate a fallback summary from available fields."""
    parts = []
    if project.client:
        parts.append(project.client)
    if project.project_name:
        parts.append(project.project_name)
    if project.project_type:
        parts.append(project.project_type)
    if project.industry:
        parts.append(project.industry)
    
    if parts:
        return " | ".join(parts)
    return ""


def parse_project_sheet(csv_path: str) -> ParseResult:
    """Parse the NetSuite Closed-Won Opportunities CSV.
    
    Args:
        csv_path: Path to the CSV file
        
    Returns:
        ParseResult with projects, skipped rows, warnings, and errors
    """
    result = ParseResult()
    
    if not Path(csv_path).exists():
        result.errors.append(f"CSV file not found: {csv_path}")
        return result
    
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, restval="")
            
            # Validate required columns
            required_columns = {
                "OppID", "Customer", "Opp Name", "Year", "Month",
                "Services ($)", "Close Date", "Sales Rep",
                "Services Delivery Team", "Solution Pillar",
                "Type", "Industry", "3-5 words", "1-2 sentences"
            }
            
            if not reader.fieldnames:
                result.errors.append("CSV file has no headers")
                return result
            
            actual_columns = set(reader.fieldnames)
            missing_columns = required_columns - actual_columns
            if missing_columns:
                result.warnings.append(f"Missing columns: {', '.join(sorted(missing_columns))}")
            
            for row_idx, row in enumerate(reader, start=2):  # Start at 2 (1 is header)
                # Check for extra fields beyond expected headers
                if None in row:
                    result.warnings.append(f"Row {row_idx}: Ignored extra cells beyond expected headers")
                
                # Safely extract all fields using _cell helper
                opp_id = normalize_opp_id(_cell(row, "OppID"))
                customer = _cell(row, "Customer")
                opp_name = _cell(row, "Opp Name")
                
                # Skip rows missing all three key fields
                if not opp_id and not customer and not opp_name:
                    result.skipped_rows += 1
                    result.warnings.append(f"Row {row_idx}: Skipped - missing OppID, Customer, and Opp Name")
                    continue
                
                # Warn if missing customer or opp_name but have opp_id
                if opp_id and (not customer or not opp_name):
                    result.warnings.append(f"Row {row_idx}: OppID {opp_id} missing Customer or Opp Name")
                
                # Parse fields using _cell helper
                year_str = _cell(row, "Year")
                month_str = _cell(row, "Month")
                services_str = _cell(row, "Services ($)")
                close_date_str = _cell(row, "Close Date")
                sales_rep = _cell(row, "Sales Rep")
                delivery_team = _cell(row, "Services Delivery Team")
                solution_pillar = _cell(row, "Solution Pillar")
                project_type = _cell(row, "Type")
                industry = _cell(row, "Industry")
                short_desc = _cell(row, "3-5 words")
                summary = _cell(row, "1-2 sentences")
                
                # Parse numeric fields
                year = None
                if year_str:
                    try:
                        year = int(year_str)
                    except ValueError:
                        result.warnings.append(f"Row {row_idx}: Invalid year '{year_str}'")
                
                month = None
                if month_str:
                    try:
                        month = int(month_str)
                    except ValueError:
                        result.warnings.append(f"Row {row_idx}: Invalid month '{month_str}'")
                
                services_amount = _parse_currency(services_str)
                close_date = _parse_date(close_date_str)
                
                # Extract technologies
                text_fields = [opp_name, short_desc, summary, project_type]
                technologies = _extract_technologies(project_type, text_fields)
                
                # Generate fallback summary if needed
                summary_is_generated = False
                if not summary:
                    summary = _generate_fallback_summary(ProjectRecord(
                        opportunity_number=opp_id,
                        project_name=opp_name,
                        client=customer,
                        project_type=project_type,
                        industry=industry,
                    ))
                    summary_is_generated = True
                
                # Create project record
                project = ProjectRecord(
                    opportunity_number=opp_id,
                    project_name=opp_name,
                    client=customer,
                    year=year,
                    month=month,
                    services_amount=services_amount,
                    close_date=close_date,
                    sales_rep=sales_rep,
                    services_delivery_team=delivery_team,
                    solution_pillar=solution_pillar,
                    project_type=project_type,
                    industry=industry,
                    short_description=short_desc,
                    summary=summary,
                    technologies=technologies,
                    source_row=row_idx,
                    summary_is_generated=summary_is_generated,
                )
                
                result.projects.append(project)
    
    except Exception as e:
        result.errors.append(f"Failed to parse CSV: {str(e)}")
    
    return result


def upsert_projects(conn, projects: list[ProjectRecord], force: bool = False) -> tuple[int, int]:
    """Upsert project records into the projects table.
    
    Args:
        conn: DuckDB connection
        projects: List of ProjectRecord objects
        force: If True, use INSERT OR REPLACE (counts as insert). If False, update existing records.
        
    Returns:
        Tuple of (inserted_count, updated_count)
    """
    inserted = 0
    updated = 0
    
    for project in projects:
        project_id = f"project::{normalize_opp_id(project.opportunity_number)}"
        
        if force:
            # Use INSERT OR REPLACE - counts as insert
            inserted += 1
            conn.execute(
                """
                INSERT OR REPLACE INTO projects (
                    id, project_name, client, opportunity_number,
                    year, month, services_amount, close_date,
                    sales_rep, services_delivery_team, solution_pillar,
                    project_type, industry, short_description, summary,
                    technologies_json, source_row, summary_is_generated,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                [
                    project_id,
                    project.project_name,
                    project.client,
                    project.opportunity_number,
                    project.year,
                    project.month,
                    project.services_amount,
                    project.close_date,
                    project.sales_rep,
                    project.services_delivery_team,
                    project.solution_pillar,
                    project.project_type,
                    project.industry,
                    project.short_description,
                    project.summary,
                    json.dumps(project.technologies),
                    project.source_row,
                    project.summary_is_generated,
                    "completed",  # Closed-won opportunities are completed
                ]
            )
        else:
            # Check if exists
            existing = conn.execute(
                "SELECT id FROM projects WHERE id = ?",
                [project_id]
            ).fetchone()
            
            if existing:
                updated += 1
                # Update existing record
                conn.execute(
                    """
                    UPDATE projects SET
                        project_name = ?,
                        client = ?,
                        year = ?,
                        month = ?,
                        services_amount = ?,
                        close_date = ?,
                        sales_rep = ?,
                        services_delivery_team = ?,
                        solution_pillar = ?,
                        project_type = ?,
                        industry = ?,
                        short_description = ?,
                        summary = ?,
                        technologies_json = ?,
                        source_row = ?,
                        summary_is_generated = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    [
                        project.project_name,
                        project.client,
                        project.year,
                        project.month,
                        project.services_amount,
                        project.close_date,
                        project.sales_rep,
                        project.services_delivery_team,
                        project.solution_pillar,
                        project.project_type,
                        project.industry,
                        project.short_description,
                        project.summary,
                        json.dumps(project.technologies),
                        project.source_row,
                        project.summary_is_generated,
                        project_id,
                    ]
                )
            else:
                inserted += 1
                # Insert new record
                conn.execute(
                    """
                    INSERT INTO projects (
                        id, project_name, client, opportunity_number,
                        year, month, services_amount, close_date,
                        sales_rep, services_delivery_team, solution_pillar,
                        project_type, industry, short_description, summary,
                        technologies_json, source_row, summary_is_generated,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    [
                        project_id,
                        project.project_name,
                        project.client,
                        project.opportunity_number,
                        project.year,
                        project.month,
                        project.services_amount,
                        project.close_date,
                        project.sales_rep,
                        project.services_delivery_team,
                        project.solution_pillar,
                        project.project_type,
                        project.industry,
                        project.short_description,
                        project.summary,
                        json.dumps(project.technologies),
                        project.source_row,
                        project.summary_is_generated,
                        "completed",  # Closed-won opportunities are completed
                    ]
                )
    
    return inserted, updated
