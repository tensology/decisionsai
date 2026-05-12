"""
Initiative LLM response parsing — no Qt imports (safe for headless tests).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from distr.core.initiative.orchestration_allowlist import normalize_suggested_tool
from distr.core.initiative.rubric import RubricScore

logger = logging.getLogger(__name__)

VALID_ACTION_TYPES = frozenset(
    {
        "suggestion",
        "routine_task",
        "board_triage",
        "ticket_lane_move",
        "workflow_start",
        "project_cli_task",
        "message_triage",
        "email_triage",
        "external_comms",
        "file_change",
        "sensitive",
        "none",
    }
)


@dataclass
class ProposedAction:
    action_type: str = "none"
    description: str = "No description provided"
    payload: dict = field(default_factory=dict)
    draft: str = ""
    telegram_message: str = ""
    requires_confirmation: bool = False
    suggested_tool: Optional[Dict[str, Any]] = None
    rubric: Optional[RubricScore] = None


def serialize(action: ProposedAction) -> dict:
    return dataclasses.asdict(action)


def deserialize(data: dict) -> ProposedAction:
    description = data.get("description", "No description provided")
    if not description:
        description = "No description provided"
    rubric = RubricScore.from_payload(data.get("rubric"))
    return ProposedAction(
        action_type=data.get("action_type", "none"),
        description=description,
        payload=data.get("payload") or {},
        draft=data.get("draft") or "",
        telegram_message=data.get("telegram_message") or "",
        requires_confirmation=data.get("requires_confirmation", False),
        suggested_tool=data.get("suggested_tool"),
        rubric=rubric,
    )


def parse_llm_response(raw: str) -> ProposedAction:
    """Parse a JSON action proposal from the LLM response."""
    text = raw.strip()
    if text.startswith("```json"):
        text = text[len("```json") :]
    elif text.startswith("```"):
        text = text[len("```") :]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("parse_llm_response: failed to parse JSON from LLM response")
        return ProposedAction(action_type="none")

    action_type = data.get("action_type", "none")
    if action_type not in VALID_ACTION_TYPES:
        logger.warning(
            "parse_llm_response: invalid action_type %r, defaulting to 'none'", action_type
        )
        data["action_type"] = "none"

    action = deserialize(data)
    normalized = normalize_suggested_tool(data.get("suggested_tool"))
    if data.get("suggested_tool") is not None and normalized is None:
        logger.info(
            "parse_llm_response: dropped invalid suggested_tool %r",
            data.get("suggested_tool"),
        )
    action.suggested_tool = normalized
    return action
