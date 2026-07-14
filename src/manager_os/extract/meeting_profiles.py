"""Meeting classification and exact profile matching.

Two layers:
1. Exact profiles — deterministic matches for known recurring meetings
2. Heuristic/LLM classification — for meetings without an exact profile

No-prep detection (focus time, lunch, OOO) is checked BEFORE broad rules.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# No-prep detection — checked FIRST, before any broad rules
# ---------------------------------------------------------------------------

NO_PREP_PATTERNS = [
    "focus time", "focus block", "focus", "deep work",
    "lunch", "personal", "out of office", "ooo", "o.o.o.",
    "reminder", "appointment", "blocked", "travel",
    "doctor", "dentist", "birthday", "holiday", "vacation",
    "solo block", "private", "do not disturb",
]


def is_no_prep(meeting: dict[str, Any]) -> bool:
    """Detect obvious no-prep events before broad rules.

    A broad "team" or "manager" match must not override an obvious no-prep event.
    """
    title = (meeting.get("title") or "").lower().strip()
    attendees = meeting.get("attendees") or []
    attendee_count = len(attendees) if isinstance(attendees, list) else 0

    for pattern in NO_PREP_PATTERNS:
        if pattern in title:
            # No-prep events typically have 0-1 attendees
            if attendee_count <= 1:
                return True
    return False


# ---------------------------------------------------------------------------
# Exact profiles
# ---------------------------------------------------------------------------

EXACT_PROFILES: list[dict[str, Any]] = [
    {
        "profile_id": "data_leaders_standup",
        "meeting_type": "upward_daily_status",
        "name": "Data Leaders Standup",
        "title_variants": [
            "data leaders standup",
            "data leadership standup",
            "data leaders daily",
        ],
        "objective": "Give my manager a concise daily leadership update.",
        "time_horizon": "since_previous_occurrence",
        "retrieval_plan": "upward_daily_status",
        "output_schema": "upward_daily_status",
        "prep_required": True,
    },
]


def match_exact_profile(meeting: dict[str, Any]) -> dict[str, Any] | None:
    """Match a meeting against exact profiles.

    Matches by normalized title variants or recurring_event_id.
    Returns the matching profile dict or None.
    """
    title = (meeting.get("title") or "").lower().strip()
    recurring_id = (meeting.get("recurring_event_id") or "").lower().strip()

    for profile in EXACT_PROFILES:
        # Check title variants
        for variant in profile.get("title_variants", []):
            if variant.lower() == title:
                return profile
        # Check recurring event ID if profile specifies one
        if recurring_id and profile.get("recurring_event_id"):
            if profile["recurring_event_id"].lower() == recurring_id:
                return profile
    return None


# ---------------------------------------------------------------------------
# Heuristic classification (fallback when no exact profile and no LLM)
# ---------------------------------------------------------------------------

def _heuristic_classify(meeting: dict[str, Any]) -> dict[str, Any]:
    """Classify a meeting using simple heuristics.

    This is a fallback when LLM classification is unavailable.
    """
    title = (meeting.get("title") or "").lower().strip()
    attendees = meeting.get("attendees") or []
    attendee_count = len(attendees) if isinstance(attendees, list) else 0

    # Check for 1:1 patterns
    one_on_one_patterns = ["1:1", "1-1", "one-on-one", "one on one", "1-2-1", "check-in"]
    if any(p in title for p in one_on_one_patterns) and attendee_count <= 2:
        return {
            "meeting_type": "direct_report_1on1",
            "confidence": 0.7,
            "reasoning_summary": ["Title contains 1:1 pattern", "Small attendee count"],
            "recommended_profile": "direct_report_1on1",
            "classification_source": "heuristic",
            "prep_required": True,
        }

    # Check for standup patterns
    standup_patterns = ["standup", "daily sync", "team sync", "daily"]
    if any(p in title for p in standup_patterns):
        return {
            "meeting_type": "team_standup",
            "confidence": 0.7,
            "reasoning_summary": ["Title contains standup pattern"],
            "recommended_profile": "team_standup",
            "classification_source": "heuristic",
            "prep_required": True,
        }

    # Default to generic
    return {
        "meeting_type": "generic",
        "confidence": 0.5,
        "reasoning_summary": ["No specific pattern matched"],
        "recommended_profile": "generic",
        "classification_source": "heuristic",
        "prep_required": True,
    }


# ---------------------------------------------------------------------------
# Main classification entry point
# ---------------------------------------------------------------------------


def classify_meeting(meeting: dict[str, Any]) -> dict[str, Any]:
    """Classify a meeting using exact profiles, no-prep detection, then LLM/heuristic.

    Order of precedence:
    1. No-prep detection (focus time, lunch, OOO) — checked FIRST
    2. Exact profile match (Data Leaders Standup, etc.)
    3. LLM classification (if available) or heuristic fallback

    Returns a dict with:
        meeting_type, confidence, reasoning_summary, recommended_profile,
        classification_source, prep_required, profile_id (if exact match)
    """
    # 1. No-prep detection FIRST
    if is_no_prep(meeting):
        return {
            "meeting_type": "no_prep",
            "confidence": 1.0,
            "reasoning_summary": ["Detected as no-prep event (focus/lunch/OOO/etc.)"],
            "recommended_profile": "no_prep",
            "classification_source": "no_prep_detector",
            "prep_required": False,
            "profile_id": None,
        }

    # 2. Exact profile match
    profile = match_exact_profile(meeting)
    if profile:
        return {
            "meeting_type": profile["meeting_type"],
            "confidence": 1.0,
            "reasoning_summary": [f"Exact profile match: {profile['name']}"],
            "recommended_profile": profile["profile_id"],
            "classification_source": "exact_profile",
            "prep_required": profile.get("prep_required", True),
            "profile_id": profile["profile_id"],
            "objective": profile.get("objective", ""),
            "retrieval_plan": profile.get("retrieval_plan", ""),
            "output_schema": profile.get("output_schema", ""),
        }

    # 3. Heuristic classification (LLM would go here in production)
    result = _heuristic_classify(meeting)
    result["profile_id"] = None
    return result
