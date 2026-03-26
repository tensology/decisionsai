"""Utility helpers for the Kanban module."""

from __future__ import annotations

from typing import Optional

MASK_CHAR = "•"


def mask_api_key(value: Optional[str]) -> str:
    """Mask an API key, showing only the last four characters.

    For strings of length >= 4, all characters except the last four are
    replaced with the mask character (``•``).  For strings shorter than
    4 characters the entire string is masked.  ``None`` and empty strings
    return an empty string.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) < 4:
        return MASK_CHAR * len(raw)
    return MASK_CHAR * (len(raw) - 4) + raw[-4:]
