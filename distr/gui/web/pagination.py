"""Shared bounds for client-supplied pagination.

Every ``/api`` GET is reachable without the internal token, so a bare
``limit: int = 50`` query parameter lets any local caller ask for the whole
table (``?limit=999999999``). These helpers clamp a client value into a sane
range so list endpoints stay bounded regardless of what is requested.
"""

from __future__ import annotations

from typing import Any, Optional

MAX_PAGE_SIZE = 1000
DEFAULT_PAGE_SIZE = 100


def clamp_limit(value: Any, *, default: int = DEFAULT_PAGE_SIZE, maximum: int = MAX_PAGE_SIZE) -> int:
    """Coerce a client-supplied limit into ``1..maximum`` (falls back to ``default``)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    if n <= 0:
        n = int(default)
    return min(n, int(maximum))


def clamp_offset(value: Any, *, maximum: Optional[int] = None) -> int:
    """Coerce a client-supplied offset into ``>= 0`` (optionally capped)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 0
    if n < 0:
        n = 0
    if maximum is not None:
        n = min(n, int(maximum))
    return n
