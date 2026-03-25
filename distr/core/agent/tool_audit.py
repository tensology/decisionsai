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
) -> None:
    """Record a tool execution to the Step Runner audit log for the chat.
    If event_queue is provided, puts ('step_runner_updated', {}) so main app can refresh UI."""
    if not chat_id:
        return
    try:
        from distr.core.step_runner.service import append_audit_step

        inst = instruction_hint or f"Executed {tool_name}"
        append_audit_step(
            chat_id=chat_id,
            tool_name=tool_name,
            instruction=inst,
            result=result,
            status=status,
            user_text=user_text,
            routing_path=routing_path,
        )
        if event_queue:
            try:
                event_queue.put(("step_runner_updated", {}), block=False)
            except Exception:
                pass
    except Exception as e:
        logger.debug("record_tool_execution failed: %s", e)
