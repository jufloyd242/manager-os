"""Shared utility functions."""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def normalize_opp_id(value: str) -> str:
    """Normalize opportunity ID to uppercase stripped form.
    
    Ensures OPP031267, opp031267, OPP031267 all resolve to the same value.
    """
    return str(value or "").strip().upper()


def clean_and_parse_json(text: str) -> dict:
    """Strips markdown code fences and parses JSON securely."""
    # Strip leading/trailing whitespace
    text = text.strip()
    
    # Strip markdown code fences using regex
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    
    # Final strip just in case
    text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        snippet = text[:200] + ("..." if len(text) > 200 else "")
        logger.error(f"Failed to parse JSON. Snippet of malformed text:\n{snippet}")
        raise e
