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
    """Find similar past projects for a given deal using Sheet-backed project data.
    
    Scoring weights:
    - exact client/customer alias match: high weight (20)
    - technology/type overlap: high weight (15 per match)
    - industry match: medium (10)
    - keyword overlap in deal name and Opp Name: medium (5 per match)
    - keyword overlap in summaries/descriptions: medium (3 per match)
    - services delivery team / solution pillar match: medium (8)
    - document type match: medium (5 per match)
    - lessons/risks overlap: lower but useful (2 per match)
    """
    
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
    deal_keywords = (deal_name + " " + account).lower().split()
    deal_keywords = [k for k in deal_keywords if len(k) > 3]
    
    # Get all projects with full Sheet-backed data
    projects = conn.execute(
        """
        SELECT id, project_name, client, opportunity_number, close_date,
               services_amount, project_type, industry, sales_rep,
               services_delivery_team, solution_pillar, short_description,
               summary, technologies_json, lessons_learned, risks_json,
               source_urls_json
        FROM projects
        """
    ).fetchall()
    
    scored_projects = []
    for p in projects:
        (p_id, p_name, p_client, p_opp_num, p_close_date, p_services_amt,
         p_project_type, p_industry, p_sales_rep, p_delivery_team, p_solution_pillar,
         p_short_desc, p_summary, tech_raw, p_lessons, p_risks, urls_raw) = p
        
        score = 0
        why_matched = []
        matching_technologies = []
        matching_project_types = []
        matching_keywords = []
        
        # 1. Client/customer match (high weight: 20)
        if p_client and account and p_client.lower() == account.lower():
            score += 20
            why_matched.append(f"client match: {p_client}")
        
        # 2. Technology/type overlap (high weight: 15 per match)
        project_techs = json.loads(tech_raw) if tech_raw else []
        if roles and project_techs:
            matched_techs = [r for r in roles if any(r.lower() in t.lower() for t in project_techs)]
            if matched_techs:
                score += len(matched_techs) * 15
                matching_technologies = matched_techs
                why_matched.append(f"technology match: {', '.join(matched_techs[:3])}")
        
        # 3. Project type match (high weight: 15)
        if p_project_type and roles:
            if any(p_project_type.lower() in r.lower() for r in roles):
                score += 15
                matching_project_types.append(p_project_type)
                why_matched.append(f"project type match: {p_project_type}")
        
        # 4. Industry match (medium weight: 10)
        if p_industry and account:
            # Simple industry keyword matching
            industry_keywords = p_industry.lower().split()
            account_keywords = account.lower().split()
            if any(ind in ' '.join(account_keywords) for ind in industry_keywords):
                score += 10
                why_matched.append(f"industry match: {p_industry}")
        
        # 5. Keyword overlap in deal name and Opp Name (medium weight: 5 per match)
        if deal_keywords and p_name:
            opp_keywords = p_name.lower().split()
            opp_keywords = [k for k in opp_keywords if len(k) > 3]
            matched_keywords = [k for k in deal_keywords if k in opp_keywords]
            if matched_keywords:
                score += len(matched_keywords) * 5
                matching_keywords.extend(matched_keywords)
                why_matched.append(f"deal name keywords: {', '.join(matched_keywords[:3])}")
        
        # 6. Keyword overlap in summaries/descriptions (medium weight: 3 per match)
        if deal_keywords and (p_summary or p_short_desc):
            summary_text = ((p_summary or '') + ' ' + (p_short_desc or '')).lower()
            matched_summary_keywords = [k for k in deal_keywords if k in summary_text]
            if matched_summary_keywords:
                score += len(matched_summary_keywords) * 3
                matching_keywords.extend(matched_summary_keywords)
                why_matched.append(f"summary keywords: {', '.join(matched_summary_keywords[:3])}")
        
        # 7. Services delivery team / solution pillar match (medium weight: 8)
        if p_delivery_team and roles:
            if any(p_delivery_team.lower() in r.lower() for r in roles):
                score += 8
                why_matched.append(f"delivery team match: {p_delivery_team}")
        
        if p_solution_pillar and roles:
            if any(p_solution_pillar.lower() in r.lower() for r in roles):
                score += 8
                why_matched.append(f"solution pillar match: {p_solution_pillar}")
        
        # 8. Lessons/risks overlap (lower weight: 2 per match)
        if p_lessons and deal_keywords:
            lessons_text = p_lessons.lower()
            matched_lessons = [k for k in deal_keywords if k in lessons_text]
            if matched_lessons:
                score += len(matched_lessons) * 2
                why_matched.append(f"lessons overlap: {len(matched_lessons)} keyword(s)")
        
        if p_risks and deal_keywords:
            risks_text = json.loads(p_risks) if isinstance(p_risks, str) else p_risks
            if isinstance(risks_text, list):
                risks_text = ' '.join(str(r) for r in risks_text).lower()
            else:
                risks_text = str(risks_text).lower()
            matched_risks = [k for k in deal_keywords if k in risks_text]
            if matched_risks:
                score += len(matched_risks) * 2
                why_matched.append(f"risks overlap: {len(matched_risks)} keyword(s)")
        
        # Only include if score > 0
        if score > 0:
            # Get related documents
            related_docs = []
            try:
                docs = conn.execute(
                    """
                    SELECT document_type, title, url, confidence
                    FROM project_documents
                    WHERE project_id = ?
                    ORDER BY confidence DESC
                    LIMIT 5
                    """,
                    [p_id]
                ).fetchall()
                related_docs = [
                    {
                        "document_type": doc[0],
                        "title": doc[1],
                        "url": doc[2],
                        "confidence": doc[3]
                    }
                    for doc in docs
                ]
            except Exception:
                pass
            
            scored_projects.append({
                "project_id": p_id,
                "project_name": p_name,
                "client": p_client,
                "opportunity_number": p_opp_num,
                "close_date": p_close_date,
                "services_amount": p_services_amt,
                "project_type": p_project_type,
                "industry": p_industry,
                "score": score,
                "why_it_matched": "; ".join(why_matched),
                "matching_technologies": matching_technologies,
                "matching_project_types": matching_project_types,
                "matching_keywords": list(set(matching_keywords)),  # dedupe
                "sales_rep": p_sales_rep,
                "short_description": p_short_desc,
                "summary": p_summary,
                "lessons_learned": p_lessons,
                "related_docs": related_docs,
                "source_links": json.loads(urls_raw) if urls_raw else [],
            })
    
    # Sort by score descending
    scored_projects.sort(key=lambda x: x["score"], reverse=True)
    return scored_projects[:limit]