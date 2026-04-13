"""
Tool execution audit logging.

Records every tool execution as a step in the Step Runner audit session for the current chat.
This provides a visible audit log in Settings > Step Runner.
"""

import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


def record_tool_execution(
    chat_id: Optional[int],
    tool_name: str,
    result: str,
    status: str = "completed",
    instruction_hint: Optional[str] = None,
    event_queue: Optional[Any] = None,
    user_text: Optional[str] = None,
    routing_path: Optional[str] = None,
    routing_hint: Optional[str] = None,
) -> None:
    """Record a tool execution to the Step Runner audit log for the chat.
    If event_queue is provided, puts ('step_runner_updated', {}) so main app can refresh UI."""
    if not chat_id:
        return
    try:
        from distr.core.workflow.service import append_audit_step

        inst = instruction_hint or f"Executed {tool_name}"
        # routing_hint is an alias for routing_path (text_extraction path uses routing_hint)
        effective_routing = routing_hint if routing_hint is not None else routing_path
        append_audit_step(
            chat_id=chat_id,
            tool_name=tool_name,
            instruction=inst,
            result=result,
            status=status,
            user_text=user_text,
            routing_path=effective_routing,
        )
        if event_queue:
            try:
                event_queue.put(("step_runner_updated", {}), block=False)
            except Exception:
                pass
    except Exception as e:
        logger.debug("record_tool_execution failed: %s", e)
