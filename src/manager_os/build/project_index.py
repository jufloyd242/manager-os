"""Project index extraction and search."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from manager_os.db import content_hash
from manager_os.utils import normalize_opp_id

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = ["normalize_opp_id", "extract_projects_from_notes", "search_projects"]

# Technology keyword map for deterministic extraction
_TECH_KEYWORDS = {
    "ADK", "Agent Development Kit", "Vertex AI", "Gemini", "GenAI", "BigQuery",
    "Apigee", "IAM", "GKE", "Cloud Run", "Terraform", "Looker", "dbt", "Python",
    "FastAPI", "LangChain", "Dataflow", "Pub/Sub", "Cloud SQL", "Security Command Center",
    "Dialogflow CX"
}


def extract_projects_from_notes(conn, force: bool = False, limit: int | None = None) -> int:
    """Extract project context from notes and enrich existing canonical projects.
    
    Notes CANNOT create new canonical projects. They only enrich existing
    Sheet-backed projects via the project_notes_context table.
    """
    import re
    
    rows = conn.execute(
        """
        SELECT n.id, n.title, n.body, n.entity_type, n.entity_name, n.tags, r.source_path
        FROM notes n
        LEFT JOIN raw_documents r ON n.raw_document_id = r.id
        WHERE n.note_type IN ('client', 'deal', 'meeting') OR n.entity_type IN ('client', 'deal')
        ORDER BY n.created_at DESC
        """ + (f" LIMIT {limit}" if limit else "")
    ).fetchall()
    
    enriched = 0
    skipped = 0
    for row in rows:
        note_id, title, body, entity_type, entity_name, tags_raw, source_path = row
        
        # Deterministic extraction
        technologies = [kw for kw in _TECH_KEYWORDS if kw.lower() in body.lower() or kw.lower() in title.lower()]
        
        # Simple heuristic for opportunity number
        opp_match = re.search(r'(OPP-\d+|Opportunity\s*#?\s*\d+)', body, re.IGNORECASE)
        opp_number = normalize_opp_id(opp_match.group(1)) if opp_match else ""
        
        # Find matching canonical project by OppID, client name, or entity_name
        project_id = None
        if opp_number:
            match = conn.execute(
                "SELECT id FROM projects WHERE UPPER(TRIM(opportunity_number)) = ?",
                [opp_number]
            ).fetchone()
            if match:
                project_id = match[0]
        
        if not project_id and entity_name:
            match = conn.execute(
                "SELECT id FROM projects WHERE LOWER(TRIM(client)) = LOWER(TRIM(?))",
                [entity_name]
            ).fetchone()
            if match:
                project_id = match[0]
        
        if not project_id:
            skipped += 1
            logger.debug(f"No canonical project found for note {note_id} (entity={entity_name}, opp={opp_number})")
            continue
        
        # Write to project_notes_context (enrichment only, never canonical)
        context_id = content_hash(f"project_note_context::{project_id}::{note_id}")
        
        if not force:
            existing = conn.execute(
                "SELECT id FROM project_notes_context WHERE id = ?", [context_id]
            ).fetchone()
            if existing:
                continue
        
        context_type = "technology" if technologies else "general"
        excerpt = body[:500] + "..." if len(body) > 500 else body
        
        conn.execute(
            """
            INSERT INTO project_notes_context (
                id, project_id, opportunity_number, source_note_id,
                source_path, context_type, excerpt, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                context_id,
                project_id,
                opp_number,
                note_id,
                source_path or "",
                context_type,
                excerpt,
                0.8 if technologies else 0.5,
            ]
        )
        enriched += 1
        
    return enriched


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
        conditions.append("UPPER(TRIM(opportunity_number)) = ?")
        params.append(normalize_opp_id(opportunity_number))
    
    # Filter out legacy empty projects unless status is explicitly queried
    if not status:
        conditions.append("(document_status IS NULL OR document_status != 'LEGACY_EMPTY')")
    
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
    
    # Score and rank results
    scored_results = []
    for row in rows:
        project_id = row[0]
        project_name = row[1]
        client_name = row[2]
        opp_num = row[3]
        
        # Get related documents
        docs = conn.execute(
            """
            SELECT document_type, title, url, confidence, why_matched
            FROM project_documents
            WHERE project_id = ?
            ORDER BY confidence DESC
            """,
            [project_id]
        ).fetchall()
        
        # Calculate score and match reasons
        score = 0
        match_reasons = []
        
        # Exact OppID match (highest priority)
        if opportunity_number and normalize_opp_id(opp_num) == normalize_opp_id(opportunity_number):
            score += 100
            match_reasons.append("exact opportunity number match")
        
        # Exact client match
        if client and client_name and client.lower() == client_name.lower():
            score += 50
            match_reasons.append("exact client match")
        
        # Exact project type match
        if project_type and row[17] and project_type.lower() == row[17].lower():
            score += 40
            match_reasons.append("exact project type match")
        
        # Exact technology match
        if technology:
            tech_list = json.loads(row[5]) if row[5] else []
            if technology in tech_list:
                score += 30
                match_reasons.append(f"technology: {technology}")
        
        # Document title match
        if query:
            for doc in docs:
                if doc[1] and query.lower() in doc[1].lower():
                    score += 20
                    match_reasons.append(f"document title match: {doc[1]}")
                    break
        
        # Free text match scoring
        if query:
            query_lower = query.lower()
            if project_name and query_lower in project_name.lower():
                score += 15
                match_reasons.append("project name match")
            if row[18] and query_lower in row[18].lower():  # short_description
                score += 10
                match_reasons.append("short description match")
            if row[7] and query_lower in row[7].lower():  # summary
                score += 5
                match_reasons.append("summary match")
        
        # If no specific filters matched, give base score for passing WHERE clause
        if score == 0:
            score = 1
            match_reasons.append("matches filter criteria")
        
        scored_results.append({
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
            "score": score,
            "match_reasons": match_reasons,
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
    
    # Sort by score descending, then by close_date descending
    scored_results.sort(key=lambda x: (-x["score"], x["close_date"] or ""), reverse=False)
    scored_results.sort(key=lambda x: x["close_date"] or "", reverse=True)
    scored_results.sort(key=lambda x: x["score"], reverse=True)
    
    return scored_results[:limit]
        
    return results