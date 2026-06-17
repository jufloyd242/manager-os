"""Project index extraction and search."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from manager_os.db import content_hash

logger = logging.getLogger(__name__)

# Technology keyword map for deterministic extraction
_TECH_KEYWORDS = {
    "ADK", "Agent Development Kit", "Vertex AI", "Gemini", "GenAI", "BigQuery",
    "Apigee", "IAM", "GKE", "Cloud Run", "Terraform", "Looker", "dbt", "Python",
    "FastAPI", "LangChain", "Dataflow", "Pub/Sub", "Cloud SQL", "Security Command Center",
    "Dialogflow CX"
}


def extract_projects_from_notes(conn, force: bool = False, limit: int | None = None) -> int:
    """Extract project knowledge from notes and deals into the projects table."""
    rows = conn.execute(
        """
        SELECT id, title, body, entity_type, entity_name, tags, source_path
        FROM notes
        WHERE note_type IN ('client', 'deal', 'meeting') OR entity_type IN ('client', 'deal')
        ORDER BY created_at DESC
        """ + (f" LIMIT {limit}" if limit else "")
    ).fetchall()
    
    ingested = 0
    for row in rows:
        note_id, title, body, entity_type, entity_name, tags_raw, source_path = row
        tags = json.loads(tags_raw) if tags_raw else []
        
        # Deterministic extraction
        technologies = [kw for kw in _TECH_KEYWORDS if kw.lower() in body.lower() or kw.lower() in title.lower()]
        
        # Simple heuristic for opportunity number
        import re
        opp_match = re.search(r'(OPP-\d+|Opportunity\s*#?\s*\d+)', body, re.IGNORECASE)
        opp_number = opp_match.group(1) if opp_match else ""
        
        project_id = content_hash(f"project::{entity_name}::{note_id}")
        
        # Check if exists
        if not force:
            existing = conn.execute("SELECT id FROM projects WHERE id = ?", [project_id]).fetchone()
            if existing:
                continue
                
        conn.execute(
            """
            INSERT OR REPLACE INTO projects (
                id, project_name, client, opportunity_number, deal_id, status,
                technologies_json, skills_json, team_members_json, summary,
                lessons_learned, source_urls_json, source_note_ids_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            [
                project_id,
                title or entity_name or "Unknown Project",
                entity_name if entity_type == 'client' else "",
                opp_number,
                note_id if entity_type == 'deal' else "",
                "active" if "active" in body.lower() else "unknown",
                json.dumps(technologies),
                json.dumps([]), # skills
                json.dumps([]), # team
                body[:500] + "..." if len(body) > 500 else body, # summary
                "", # lessons_learned
                json.dumps([source_path] if source_path else []),
                json.dumps([note_id]),
            ]
        )
        ingested += 1
        
    return ingested


def search_projects(
    conn,
    query: str = "",
    client: str = "",
    person: str = "",
    technology: str = "",
    project_type: str = "",
    industry: str = "",
    sales_rep: str = "",
    status: str = "",
    year: int | None = None,
    close_after: str = "",
    close_before: str = "",
    opportunity_number: str = "",
    document_type: str = "",
    limit: int = 20
) -> list[dict[str, Any]]:
    """Search the project index with comprehensive filters.
    
    Args:
        conn: DuckDB connection
        query: Free text search across multiple fields
        client: Filter by client name
        person: Filter by team member (in team_members_json)
        technology: Filter by technology
        project_type: Filter by project type (ADK, GenAI, CES, etc.)
        industry: Filter by industry
        sales_rep: Filter by sales rep
        status: Filter by status
        year: Filter by year
        close_after: Filter by close date (YYYY-MM-DD)
        close_before: Filter by close date (YYYY-MM-DD)
        opportunity_number: Filter by exact opportunity number
        document_type: Filter by related document type
        limit: Maximum results to return
        
    Returns:
        List of project dictionaries
    """
    conditions = []
    params = []
    
    # Free text search across multiple fields
    if query:
        conditions.append("""(
            project_name LIKE ? OR 
            summary LIKE ? OR 
            short_description LIKE ? OR
            lessons_learned LIKE ? OR
            client LIKE ? OR
            opportunity_number LIKE ?
        )""")
        params.extend([f"%{query}%"] * 6)
    
    if client:
        conditions.append("client LIKE ?")
        params.append(f"%{client}%")
    
    if person:
        conditions.append("team_members_json LIKE ?")
        params.append(f"%{person}%")
    
    if technology:
        conditions.append("technologies_json LIKE ?")
        params.append(f"%{technology}%")
    
    if project_type:
        conditions.append("project_type = ?")
        params.append(project_type)
    
    if industry:
        conditions.append("industry LIKE ?")
        params.append(f"%{industry}%")
    
    if sales_rep:
        conditions.append("sales_rep LIKE ?")
        params.append(f"%{sales_rep}%")
    
    if status:
        conditions.append("status = ?")
        params.append(status)
    
    if year is not None:
        conditions.append("year = ?")
        params.append(year)
    
    if close_after:
        conditions.append("close_date >= ?")
        params.append(close_after)
    
    if close_before:
        conditions.append("close_date <= ?")
        params.append(close_before)
    
    if opportunity_number:
        conditions.append("opportunity_number = ?")
        params.append(opportunity_number)
    
    # Document type filter requires join with project_documents
    needs_doc_join = bool(document_type)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    if needs_doc_join:
        # Join with project_documents to filter by document type
        query_sql = f"""
            SELECT DISTINCT p.id, p.project_name, p.client, p.opportunity_number, p.status,
                   p.technologies_json, p.team_members_json, p.summary, p.lessons_learned, 
                   p.source_urls_json, p.year, p.month, p.services_amount, p.close_date,
                   p.sales_rep, p.services_delivery_team, p.solution_pillar, p.project_type,
                   p.industry, p.short_description, p.source_row, p.summary_is_generated
            FROM projects p
            LEFT JOIN project_documents pd ON p.id = pd.project_id
            WHERE {where_clause}
            {"AND pd.document_type = ?" if document_type else ""}
            ORDER BY p.close_date DESC
            LIMIT ?
        """
        if document_type:
            params.append(document_type)
        params.append(limit)
    else:
        query_sql = f"""
            SELECT id, project_name, client, opportunity_number, status,
                   technologies_json, team_members_json, summary, lessons_learned, 
                   source_urls_json, year, month, services_amount, close_date,
                   sales_rep, services_delivery_team, solution_pillar, project_type,
                   industry, short_description, source_row, summary_is_generated
            FROM projects
            WHERE {where_clause}
            ORDER BY close_date DESC
            LIMIT ?
        """
        params.append(limit)
    
    rows = conn.execute(query_sql, params).fetchall()
    
    results = []
    for row in rows:
        # Get related documents for this project
        project_id = row[0]
        docs = conn.execute(
            """
            SELECT document_type, title, url, confidence, why_matched
            FROM project_documents
            WHERE project_id = ?
            ORDER BY confidence DESC
            """,
            [project_id]
        ).fetchall()
        
        results.append({
            "id": row[0],
            "project_name": row[1],
            "client": row[2],
            "opportunity_number": row[3],
            "status": row[4],
            "technologies": json.loads(row[5]) if row[5] else [],
            "team_members": json.loads(row[6]) if row[6] else [],
            "summary": row[7],
            "lessons_learned": row[8],
            "source_urls": json.loads(row[9]) if row[9] else [],
            "year": row[10],
            "month": row[11],
            "services_amount": row[12],
            "close_date": row[13],
            "sales_rep": row[14],
            "services_delivery_team": row[15],
            "solution_pillar": row[16],
            "project_type": row[17],
            "industry": row[18],
            "short_description": row[19],
            "source_row": row[20],
            "summary_is_generated": row[21],
            "related_documents": [
                {
                    "document_type": doc[0],
                    "title": doc[1],
                    "url": doc[2],
                    "confidence": doc[3],
                    "why_matched": doc[4],
                }
                for doc in docs
            ],
        })
        
    return results