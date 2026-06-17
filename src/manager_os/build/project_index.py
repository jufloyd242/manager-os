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
    status: str = "",
    limit: int = 20
) -> list[dict[str, Any]]:
    """Search the project index."""
    conditions = []
    params = []
    
    if query:
        conditions.append("(project_name LIKE ? OR summary LIKE ? OR lessons_learned LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
    if client:
        conditions.append("client LIKE ?")
        params.append(f"%{client}%")
    if technology:
        conditions.append("technologies_json LIKE ?")
        params.append(f"%{technology}%")
    if status:
        conditions.append("status = ?")
        params.append(status)
        
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    rows = conn.execute(
        f"""
        SELECT id, project_name, client, opportunity_number, status,
               technologies_json, team_members_json, summary, lessons_learned, source_urls_json
        FROM projects
        WHERE {where_clause}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        params + [limit]
    ).fetchall()
    
    results = []
    for row in rows:
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
        })
        
    return results