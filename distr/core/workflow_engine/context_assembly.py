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
class WorkflowRunContext:
    """Structured context passed from the parent session into a workflow run.

    Carries the conversational context the subagent needs to make informed
    decisions: the last few turns from the parent session, a summary of the
    user's intent, the active project, and any ticket/board linkage.

    This replaces the previous free-form ``context: Optional[str]`` pattern
    with a typed schema that downstream consumers can rely on.
    """

    last_conversation_turns: List[Dict[str, str]] = field(default_factory=list)
    user_intent_summary: str = ""
    active_project_id: Optional[int] = None
    active_project_path: Optional[str] = None
    ticket_id: Optional[int] = None
    board_id: Optional[int] = None

    def as_context_string(self) -> str:
        """Build a human-readable context string for injection into agent prompts."""
        parts: List[str] = []
        if self.user_intent_summary:
            parts.append(f"User intent: {self.user_intent_summary}")
        if self.active_project_path:
            parts.append(f"Active project: {self.active_project_path}")
        if self.last_conversation_turns:
            recent = self.last_conversation_turns[-5:]
            lines = []
            for turn in recent:
                role = turn.get("role", "?")
                content = (turn.get("content", "") or "")[:200]
                lines.append(f"  [{role}] {content}")
            parts.append("Recent conversation:\n" + "\n".join(lines))
        return "\n".join(parts)


@dataclass
class StepInputContext:
    """The assembled input context for a single step execution."""

    workflow_input: Optional[WorkflowInput] = None
    workflow_rules: str = ""
    previous_results: List[Dict[str, Any]] = field(default_factory=list)
    step_config: Dict[str, Any] = field(default_factory=dict)
    resolved_variables: Dict[str, str] = field(default_factory=dict)
    workspace_slice: str = ""


def build_workspace_slice(
    *,
    project_id: Optional[int] = None,
    board_id: Optional[int] = None,
    workflow_id: Optional[int] = None,
    run_id: Optional[int] = None,
    ticket_id: Optional[int] = None,
    folder_location: str = "",
) -> str:
    """Compact filesystem workspace block for step/run context."""
    if not any((project_id, board_id, workflow_id, run_id, ticket_id)):
        return ""
    try:
        from distr.core.workspace_memory.reader import load_workspace_context

        ctx = load_workspace_context(
            project_id=project_id,
            board_id=board_id,
            workflow_id=workflow_id,
            run_id=run_id,
            ticket_id=ticket_id,
            folder_location=folder_location,
            ensure=True,
        )
    except Exception:
        return ""
    lines = ["## Workspace memory"]
    if ctx.projection_path:
        lines.append(f"- projection: `{ctx.projection_path}`")
    if ctx.handoff_preview:
        lines.append(f"- handoff: {ctx.handoff_preview}")
    for key, path in (ctx.companion_paths or {}).items():
        lines.append(f"- {key}: `{path}`")
    if ctx.references_index:
        lines.append(f"- references: {', '.join(ctx.references_index[:8])}")
    return "\n".join(lines) if len(lines) > 1 else ""


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
    *,
    run_id: Optional[int] = None,
    ticket_id: Optional[int] = None,
    project_id: Optional[int] = None,
    board_id: Optional[int] = None,
    workflow_id: Optional[int] = None,
    folder_location: str = "",
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

    ctx.workspace_slice = build_workspace_slice(
        project_id=project_id,
        board_id=board_id,
        workflow_id=workflow_id or getattr(step, "workflow_id", None),
        run_id=run_id,
        ticket_id=ticket_id,
        folder_location=folder_location,
    )
    if ctx.workspace_slice:
        rules = (ctx.workflow_rules or "").strip()
        ctx.workflow_rules = f"{rules}\n\n{ctx.workspace_slice}".strip() if rules else ctx.workspace_slice

    return ctx
