"""Step input context assembly for the workflow engine.

Assembles the execution context for each step based on its type. Different step
types receive different slices of the available context (workflow input, rules,
previous step outputs, step config) according to the context assembly matrix
defined in the design document.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .variable_resolver import _build_variable_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WorkflowInput:
    """The trigger/input that started the workflow."""

    source_type: str  # "instruction", "ticket_board", "api", "scheduled"
    text: str = ""
    title: str = ""
    images: List[str] = field(default_factory=list)
    attachments: List[Dict[str, str]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StepInputContext:
    """The assembled input context for a single step execution."""

    workflow_input: Optional[WorkflowInput] = None
    workflow_rules: str = ""
    previous_results: List[Dict[str, Any]] = field(default_factory=list)
    step_config: Dict[str, Any] = field(default_factory=dict)
    resolved_variables: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_workflow_input(session) -> Optional[WorkflowInput]:
    """Deserialize the session's ``workflow_input`` JSON into a WorkflowInput.

    Returns ``None`` when the session has no workflow input stored.
    """
    raw = getattr(session, "workflow_input", None)
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse workflow_input JSON on session")
        return None

    if not isinstance(data, dict):
        return None

    return WorkflowInput(
        source_type=data.get("source_type", "instruction"),
        text=data.get("text", ""),
        title=data.get("title", ""),
        images=data.get("images", []),
        attachments=data.get("attachments", []),
        metadata=data.get("metadata", {}),
    )


# ---------------------------------------------------------------------------
# Main assembly function
# ---------------------------------------------------------------------------

def assemble_step_context(
    session,
    step,
    prior_results: List[Dict[str, Any]],
) -> StepInputContext:
    """Assemble the full input context for a step based on its type.

    Parameters
    ----------
    session:
        Object with ``context_rules`` (str | None) and ``workflow_input``
        (JSON str | None) attributes.
    step:
        Object with ``step_type`` (str) and ``config`` (JSON str | None)
        attributes.
    prior_results:
        Ordered list of completed step outputs.  Each entry is a dict
        (e.g. ``{"result": "...", "title": "...", "step_type": "..."}``)

    Returns
    -------
    StepInputContext
        Context populated according to the step type's assembly rules.
    """
    step_type = getattr(step, "step_type", None) or "agent_instruction"
    # Any step_type not in the 5 new types is treated as "agent_instruction"
    _NEW_STEP_TYPES = {"run_command", "play_recording", "http_request", "execute_code", "playwright"}
    if step_type not in _NEW_STEP_TYPES:
        step_type = "agent_instruction"
    config_raw = getattr(step, "config", None)

    try:
        step_config = json.loads(config_raw) if config_raw else {}
    except (json.JSONDecodeError, TypeError):
        step_config = {}

    ctx = StepInputContext(step_config=step_config)

    if step_type == "agent_instruction":
        # Agent Instruction: full context — everything available
        ctx.workflow_input = _load_workflow_input(session)
        ctx.workflow_rules = getattr(session, "context_rules", None) or ""
        ctx.previous_results = list(prior_results)

    elif step_type in ("execute_code", "playwright"):
        # Text-only workflow input (strip images for code-gen steps)
        wi = _load_workflow_input(session)
        if wi is not None:
            wi.images = []
            wi.attachments = []
        ctx.workflow_input = wi
        ctx.workflow_rules = getattr(session, "context_rules", None) or ""
        ctx.resolved_variables = _build_variable_context(prior_results, {})
        # Playwright gets previous_results for screenshot references
        if step_type == "playwright":
            ctx.previous_results = list(prior_results)

    elif step_type in ("run_command", "http_request"):
        # Variables only — no workflow input, no rules
        ctx.resolved_variables = _build_variable_context(prior_results, {})

    elif step_type == "play_recording":
        # Self-contained — only step config (already set above)
        pass

    return ctx
