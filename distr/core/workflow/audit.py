"""
Workflow Audit — audit trail append/get for chat tool executions.

Extracted from service.py as part of the module decomposition.
"""
import logging
from typing import Optional

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

logger = logging.getLogger(__name__)


# ── Audit trail ──


def get_or_create_audit_workflow(chat_id: int) -> Optional[int]:
    """Get or create an audit workflow for a chat.

    Returns the workflow id (int) to avoid passing detached ORM objects
    across session boundaries.
    """
    with get_session() as db:
        wf = (
            db.query(AutoWorkflow)
            .filter(
                AutoWorkflow.chat_id == chat_id,
                AutoWorkflow.workflow_type == "audit",
            )
            .order_by(AutoWorkflow.modified_date.desc())
            .first()
        )
        if wf:
            return wf.id
        wf = AutoWorkflow(
            name=f"Audit log for chat {chat_id}",
            description=f"Audit log for chat {chat_id}",
            status="active",
            chat_id=chat_id,
            workflow_type="audit",
        )
        db.add(wf)
        db.commit()
        db.refresh(wf)
        return wf.id


def append_audit_step(
    chat_id: int,
    tool_name: str,
    instruction: str,
    result: str,
    status: str = "completed",
    user_text: str = None,
    routing_path: str = None,
) -> bool:
    """Append a tool execution as a step to the chat's audit workflow.

    Creates the audit workflow if it doesn't exist yet.
    Truncates *instruction* to 500 chars and *result* to 2000 chars.
    Stores *routing_path* in its own field without truncation.
    """
    try:
        workflow_id = get_or_create_audit_workflow(chat_id)
        if not workflow_id:
            return False
        with get_session() as db:
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
            if not wf:
                return False
            max_pos = max((st.position for st in wf.steps), default=-1)
            inst = instruction[:500] if instruction else tool_name
            truncated_result = None
            if result:
                truncated_result = (result[:2000] + "...") if len(result) > 2000 else result
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=max_pos + 1,
                name=tool_name.replace("_", " ").title(),
                instruction=inst,
                status=status,
                result=truncated_result,
                tool_used=tool_name,
                routing_path=routing_path,
            )
            db.add(step)
            db.commit()
        return True
    except Exception as e:
        logger.warning("append_audit_step failed: %s", e)
        return False
