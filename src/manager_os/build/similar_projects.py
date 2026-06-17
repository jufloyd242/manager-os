"""Similar project matching for deal delivery intelligence."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def find_similar_projects(
    conn,
    deal_id: str = "",
    opportunity_number: str = "",
    limit: int = 5
) -> list[dict[str, Any]]:
    """Find similar past projects for a given deal."""
    
    # Get deal details
    deal = conn.execute(
        """
        SELECT account, deal_name, requested_roles, loe_status, sow_status
        FROM deals WHERE id = ? OR deal_id = ?
        """,
        [deal_id, opportunity_number]
    ).fetchone()
    
    if not deal:
        return []
        
    account, deal_name, roles_raw, loe_status, sow_status = deal
    roles = json.loads(roles_raw) if roles_raw else []
    
    # Keywords from deal name and account
    keywords = (deal_name + " " + account).lower().split()
    keywords = [k for k in keywords if len(k) > 3]
    
    # Search projects
    # We'll use a simple scoring mechanism:
    # +10 for client match
    # +5 for each keyword match in project name or summary
    # +5 for each technology match (if we had deal tech requirements, but we'll use roles as proxy)
    
    projects = conn.execute(
        """
        SELECT id, project_name, client, technologies_json, team_members_json, 
               summary, lessons_learned, source_urls_json
        FROM projects
        """
    ).fetchall()
    
    scored_projects = []
    for p in projects:
        p_id, p_name, p_client, tech_raw, team_raw, summary, lessons, urls_raw = p
        score = 0
        why_matched = []
        
        # Client match
        if p_client and account and p_client.lower() == account.lower():
            score += 10
            why_matched.append("client match")
            
        # Keyword match
        text_to_search = (p_name + " " + (summary or "")).lower()
        matched_keywords = [k for k in keywords if k in text_to_search]
        if matched_keywords:
            score += len(matched_keywords) * 5
            why_matched.append(f"keywords: {', '.join(matched_keywords[:3])}")
            
        # Role/Tech match (simplified)
        techs = json.loads(tech_raw) if tech_raw else []
        matched_techs = [r for r in roles if any(r.lower() in t.lower() for t in techs)]
        if matched_techs:
            score += len(matched_techs) * 5
            why_matched.append(f"skills: {', '.join(matched_techs[:3])}")
            
        if score > 0:
            scored_projects.append({
                "project_id": p_id,
                "project_name": p_name,
                "client": p_client,
                "score": score,
                "why_it_matched": "; ".join(why_matched),
                "matching_technologies": techs,
                "team_members_involved": json.loads(team_raw) if team_raw else [],
                "lessons_learned": lessons,
                "source_links": json.loads(urls_raw) if urls_raw else [],
            })
            
    # Sort by score descending
    scored_projects.sort(key=lambda x: x["score"], reverse=True)
    return scored_projects[:limit]