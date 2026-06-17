"""Project sheet parser - deterministic parsing of NetSuite Closed-Won Opportunities CSV."""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from manager_os.db import content_hash

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


def _parse_currency(value: str) -> float | None:
    """Parse currency string like '$267,000' to float."""
    if not value:
        return None
    # Remove $ and commas
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(value: str) -> str | None:
    """Parse date string like '8/4/2021' to ISO format."""
    if not value:
        return None
    try:
        # Try common formats
        for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"]:
            try:
                dt = datetime.strptime(value.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None
    except Exception:
        return None


def _extract_technologies(project_type: str, text_fields: list[str]) -> list[str]:
    """Extract technology keywords from project type and text fields."""
    technologies = set()
    
    # Add project type if it matches a known category
    if project_type in TECH_KEYWORDS:
        technologies.add(project_type)
    
    # Search text fields for technology keywords
    combined_text = " ".join(text_fields).lower()
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
            reader = csv.DictReader(f)
            
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
                opp_id = row.get("OppID", "").strip()
                customer = row.get("Customer", "").strip()
                opp_name = row.get("Opp Name", "").strip()
                
                # Skip rows missing all three key fields
                if not opp_id and not customer and not opp_name:
                    result.skipped_rows += 1
                    result.warnings.append(f"Row {row_idx}: Skipped - missing OppID, Customer, and Opp Name")
                    continue
                
                # Warn if missing customer or opp_name but have opp_id
                if opp_id and (not customer or not opp_name):
                    result.warnings.append(f"Row {row_idx}: OppID {opp_id} missing Customer or Opp Name")
                
                # Parse fields
                year_str = row.get("Year", "").strip()
                month_str = row.get("Month", "").strip()
                services_str = row.get("Services ($)", "").strip()
                close_date_str = row.get("Close Date", "").strip()
                sales_rep = row.get("Sales Rep", "").strip()
                delivery_team = row.get("Services Delivery Team", "").strip()
                solution_pillar = row.get("Solution Pillar", "").strip()
                project_type = row.get("Type", "").strip()
                industry = row.get("Industry", "").strip()
                short_desc = row.get("3-5 words", "").strip()
                summary = row.get("1-2 sentences", "").strip()
                
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
        project_id = f"project::{project.opportunity_number}"
        
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
