"""Similar project matching for deal delivery intelligence."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Stopwords to remove from keyword extraction
_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "project", "projects",
    "service", "services", "phase", "phases", "implementation", "delivery",
    "customer", "client", "opportunity", "deal", "contract", "agreement",
}


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from text.
    
    - Normalize punctuation
    - Remove stopwords
    - Remove generic terms
    - Return unique keywords
    """
    if not text:
        return []
    
    # Normalize punctuation to spaces
    normalized = re.sub(r"[^\w\s]", " ", text.lower())
    
    # Split and filter
    words = normalized.split()
    keywords = [
        w for w in words
        if len(w) > 3 and w not in _STOPWORDS
    ]
    
    return list(set(keywords))


def find_similar_projects(
    conn,
    deal_id: str = "",
    opportunity_number: str = "",
    limit: int = 5
) -> list[dict[str, Any]]:
    """Find similar past projects for a given deal using Sheet-backed project data.
    
    Returns scored matches with detailed breakdown of why each project matched.
    
    Scoring components:
    - client_match: exact client/customer match (20 points)
    - technology_match: technology overlap (15 points per match)
    - project_type_match: project type match (15 points)
    - industry_match: industry keyword overlap (10 points)
    - deal_name_keywords: keyword overlap in deal/project names (5 points per match)
    - summary_keywords: keyword overlap in descriptions (3 points per match)
    - delivery_team_match: services delivery team match (8 points)
    - solution_pillar_match: solution pillar match (8 points)
    - lessons_overlap: lessons learned overlap (2 points per match)
    - risks_overlap: risks overlap (2 points per match)
    - document_types: related document types found (5 points per type)
    """
    
    # Get deal details with all active deal fields
    deal = conn.execute(
        """
        SELECT account, deal_name, requested_roles, stage, loe_status, sow_status,
               staffing_feasibility, close_date
        FROM deals WHERE id = ? OR deal_id = ?
        """,
        [deal_id, opportunity_number]
    ).fetchone()
    
    if not deal:
        return []
        
    (account, deal_name, roles_raw, stage, loe_status, sow_status,
     staffing_feasibility, deal_close_date) = deal
    roles = json.loads(roles_raw) if roles_raw else []
    
    # Extract keywords from deal name and account
    deal_keywords = _extract_keywords(f"{deal_name} {account}")
    
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
        
        # Score breakdown by component
        score_breakdown = {}
        why_matched = []
        matching_technologies = []
        matching_project_types = []
        matching_keywords = []
        
        # 1. Client/customer match (20 points)
        if p_client and account and p_client.lower() == account.lower():
            score_breakdown["client_match"] = 20
            why_matched.append(f"client match: {p_client}")
        
        # 2. Technology/type overlap (15 points per match)
        project_techs = json.loads(tech_raw) if tech_raw else []
        if roles and project_techs:
            matched_techs = [r for r in roles if any(r.lower() in t.lower() for t in project_techs)]
            if matched_techs:
                score_breakdown["technology_match"] = len(matched_techs) * 15
                matching_technologies = matched_techs
                why_matched.append(f"technology match: {', '.join(matched_techs[:3])}")
        
        # 3. Project type match (15 points)
        if p_project_type and roles:
            if any(p_project_type.lower() in r.lower() for r in roles):
                score_breakdown["project_type_match"] = 15
                matching_project_types.append(p_project_type)
                why_matched.append(f"project type match: {p_project_type}")
        
        # 4. Industry match (10 points)
        if p_industry and account:
            industry_keywords = _extract_keywords(p_industry)
            account_keywords = _extract_keywords(account)
            if any(ind in account_keywords for ind in industry_keywords):
                score_breakdown["industry_match"] = 10
                why_matched.append(f"industry match: {p_industry}")
        
        # 5. Keyword overlap in deal name and Opp Name (5 points per match)
        if deal_keywords and p_name:
            opp_keywords = _extract_keywords(p_name)
            matched_keywords = [k for k in deal_keywords if k in opp_keywords]
            if matched_keywords:
                score_breakdown["deal_name_keywords"] = len(matched_keywords) * 5
                matching_keywords.extend(matched_keywords)
                why_matched.append(f"deal name keywords: {', '.join(matched_keywords[:3])}")
        
        # 6. Keyword overlap in summaries/descriptions (3 points per match)
        if deal_keywords and (p_summary or p_short_desc):
            summary_text = f"{p_summary or ''} {p_short_desc or ''}"
            summary_keywords = _extract_keywords(summary_text)
            matched_summary_keywords = [k for k in deal_keywords if k in summary_keywords]
            if matched_summary_keywords:
                score_breakdown["summary_keywords"] = len(matched_summary_keywords) * 3
                matching_keywords.extend(matched_summary_keywords)
                why_matched.append(f"summary keywords: {', '.join(matched_summary_keywords[:3])}")
        
        # 7. Services delivery team match (8 points)
        if p_delivery_team and roles:
            if any(p_delivery_team.lower() in r.lower() for r in roles):
                score_breakdown["delivery_team_match"] = 8
                why_matched.append(f"delivery team match: {p_delivery_team}")
        
        # 8. Solution pillar match (8 points)
        if p_solution_pillar and roles:
            if any(p_solution_pillar.lower() in r.lower() for r in roles):
                score_breakdown["solution_pillar_match"] = 8
                why_matched.append(f"solution pillar match: {p_solution_pillar}")
        
        # 9. Lessons/risks overlap (2 points per match)
        if p_lessons and deal_keywords:
            lessons_keywords = _extract_keywords(p_lessons)
            matched_lessons = [k for k in deal_keywords if k in lessons_keywords]
            if matched_lessons:
                score_breakdown["lessons_overlap"] = len(matched_lessons) * 2
                why_matched.append(f"lessons overlap: {len(matched_lessons)} keyword(s)")
        
        if p_risks and deal_keywords:
            risks_text = json.loads(p_risks) if isinstance(p_risks, str) else p_risks
            if isinstance(risks_text, list):
                risks_text = ' '.join(str(r) for r in risks_text)
            else:
                risks_text = str(risks_text)
            risks_keywords = _extract_keywords(risks_text)
            matched_risks = [k for k in deal_keywords if k in risks_keywords]
            if matched_risks:
                score_breakdown["risks_overlap"] = len(matched_risks) * 2
                why_matched.append(f"risks overlap: {len(matched_risks)} keyword(s)")
        
        # Only include if score > 0
        total_score = sum(score_breakdown.values())
        if total_score > 0:
            # Get related documents
            related_docs = []
            doc_types_found = []
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
                doc_types_found = list(set(doc[0] for doc in docs))
                
                # Add document type score (5 points per type)
                if doc_types_found:
                    score_breakdown["document_types"] = len(doc_types_found) * 5
                    why_matched.append(f"documents found: {', '.join(doc_types_found[:3])}")
            except Exception:
                pass
            
            # Recalculate total score with document types
            total_score = sum(score_breakdown.values())
            
            scored_projects.append({
                "project_id": p_id,
                "project_name": p_name,
                "client": p_client,
                "opportunity_number": p_opp_num,
                "close_date": p_close_date,
                "services_amount": p_services_amt,
                "project_type": p_project_type,
                "industry": p_industry,
                "score": total_score,
                "score_breakdown": score_breakdown,
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