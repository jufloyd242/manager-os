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
    text = re.sub(r"^`{3}(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*`{3}$", "", text)

    # Fix trailing commas in objects and arrays (extremely common LLM artifact)
    text = re.sub(r',\s*([\]}])', r'\1', text)

    # Final strip of any lingering whitespace
    text = text.strip()

    # --- AUTO-REPAIR TRUNCATED JSON ---
    # 1. Strip trailing commas at the absolute end of the string
    text = re.sub(r',\s*$', '', text)
    
    # 2. Balance unclosed brackets and quotes to handle LLM token limit cut-offs
    stack = []
    in_string = False
    escape = False
    
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == '{':
                stack.append('}')
            elif char == '[':
                stack.append(']')
            elif char in '}]':
                if stack and stack[-1] == char:
                    stack.pop()
    
    # Close any hanging strings
    if in_string:
        text += '"'
        
    # Strip trailing commas one more time just in case the unclosed string 
    # was the only element after a comma
    text = re.sub(r',\s*$', '', text)
    
    # Append all missing closing brackets in the correct order
    while stack:
        text += stack.pop()
    # ----------------------------------

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Extract a context window around the precise position of the error
        start = max(0, e.pos - 100)
        end = min(len(text), e.pos + 100)
        context = text[start:end]
        
        # Ensure local logger instance for safety
        logger = logging.getLogger(__name__)
        logger.error(
            f"Failed to parse JSON at position {e.pos} (Line {e.lineno}, Col {e.colno}).\n"
            f"Context window around error:\n"
            f"...\n{context}\n..."
        )
        raise e
