"""Shared utility functions."""

from __future__ import annotations


def normalize_opp_id(value: str) -> str:
    """Normalize opportunity ID to uppercase stripped form.
    
    Ensures OPP031267, opp031267, OPP031267 all resolve to the same value.
    """
    return str(value or "").strip().upper()
