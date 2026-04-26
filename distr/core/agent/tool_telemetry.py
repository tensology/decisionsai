"""
Structured logging for tool-selection gaps — primarily ``request_tool`` usage.

Parse logs with: grep TOOL_TELEMETRY or JSON-aware collectors on logger name
``distr.core.agent.tool_telemetry``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("distr.core.agent.tool_telemetry")


def log_request_tool_event(
    *,
    query: str,
    success: bool,
    matched_registry_class: Optional[str] = None,
    injected_tool_name: Optional[str] = None,
    fuzzy_score: Optional[int] = None,
    top_candidates: Optional[List[str]] = None,
    message: Optional[str] = None,
    model_name: Optional[str] = None,
    injection_performed: Optional[bool] = None,
) -> None:
    """Emit one INFO line per ``request_tool`` outcome for dashboards / log mining."""
    payload: Dict[str, Any] = {
        "event": "request_tool",
        "success": success,
        "query_preview": (query or "")[:500],
        "matched_registry_class": matched_registry_class,
        "injected_tool_name": injected_tool_name,
        "fuzzy_score": fuzzy_score,
        "top_candidates": (top_candidates or [])[:8],
        "model_name": model_name,
    }
    if injection_performed is not None:
        payload["injection_performed"] = injection_performed
    if message:
        payload["result_message_preview"] = message[:300]
    # Single line — easy to grep / ship to analytics
    logger.info("TOOL_TELEMETRY %s", json.dumps(payload, ensure_ascii=False))


def log_retrieval_summary(
    *,
    user_message_preview: str,
    tier: str,
    tool_count: int,
    tool_names_preview: List[str],
    backend: str,
) -> None:
    """Optional: log high-level retrieval shape (use sparingly to avoid noise)."""
    logger.debug(
        "TOOL_TELEMETRY retrieval tier=%s backend=%s count=%d preview_tools=%s msg=%r",
        tier,
        backend,
        tool_count,
        tool_names_preview[:12],
        (user_message_preview or "")[:120],
    )
