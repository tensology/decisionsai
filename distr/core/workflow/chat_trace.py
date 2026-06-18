"""Persist workflow progress into the owning chat timeline."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _preview(text: Optional[str], limit: int = 420) -> str:
    if not text:
        return ""
    one_line = " ".join(str(text).split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."


def _load_params(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_chat_id(db, run) -> Optional[int]:
    workflow = getattr(run, "workflow", None)
    chat_id = getattr(workflow, "chat_id", None)
    if chat_id:
        return int(chat_id)
    ticket_id = getattr(run, "ticket_id", None)
    if not ticket_id:
        return None
    try:
        from distr.core.db.kanban import KanbanTicket

        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == ticket_id).first()
        if ticket and ticket.source_chat_id:
            return int(ticket.source_chat_id)
    except Exception:
        logger.debug("Could not resolve workflow chat from ticket", exc_info=True)
    return None


def record_chat_workflow_event(
    chat_id: int,
    event_type: str,
    *,
    status: str = "running",
    workflow_id: Optional[int] = None,
    workflow_name: Optional[str] = None,
    run_id: Optional[int] = None,
    step_id: Optional[int] = None,
    step_name: Optional[str] = None,
    summary: Optional[str] = None,
    phase: Optional[str] = None,
    agent_activity: Optional[Dict[str, Any]] = None,
    limit: int = 300,
) -> Optional[Dict[str, Any]]:
    """Append a workflow-style action card to Chat.params and emit a live UI signal."""
    try:
        from distr.core.db import Chat, get_session

        now = datetime.now(timezone.utc).isoformat()
        resolved_chat_id = int(chat_id)
        with get_session() as db:
            chat = db.query(Chat).filter(Chat.id == resolved_chat_id).first()
            if not chat:
                return None
            payload = {
                "id": f"workflow-{run_id or 'chat'}-{event_type}-{step_id or 'run'}-{now}",
                "event": "workflow_event",
                "chat_id": resolved_chat_id,
                "type": event_type,
                "status": (status or "running").lower(),
                "timestamp": now,
                "summary": _preview(summary),
            }
            if workflow_id is not None:
                payload["workflow_id"] = int(workflow_id)
            if workflow_name:
                payload["workflow_name"] = workflow_name
            if run_id is not None:
                payload["run_id"] = int(run_id)
            if phase:
                payload["phase"] = phase
            if step_id is not None:
                payload["step_id"] = int(step_id)
            if step_name:
                payload["step_name"] = _preview(step_name, 160)
            if isinstance(agent_activity, dict) and agent_activity:
                payload["agent_activity"] = agent_activity

            params = _load_params(chat.params)
            events = params.get("workflow_events")
            if not isinstance(events, list):
                events = []
            events.append(payload)
            params["workflow_events"] = events[-limit:]
            chat.params = json.dumps(params)
            chat.modified_date = datetime.utcnow()
            db.commit()

        try:
            from distr.core.signals import signal_manager

            signal_manager.workflow_event.emit(payload)
        except Exception:
            logger.debug("workflow_event signal emit failed", exc_info=True)
        return payload
    except Exception:
        logger.debug("record_chat_workflow_event failed", exc_info=True)
        return None


def record_workflow_chat_event(
    run_id: int,
    event_type: str,
    *,
    status: str = "running",
    step_id: Optional[int] = None,
    step_name: Optional[str] = None,
    summary: Optional[str] = None,
    phase: Optional[str] = None,
    agent_activity: Optional[Dict[str, Any]] = None,
    limit: int = 300,
) -> Optional[Dict[str, Any]]:
    """Append a workflow event to the owning chat timeline."""
    try:
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflowRun

        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run:
                return None
            workflow = run.workflow
            chat_id = _resolve_chat_id(db, run)
            if not chat_id:
                return None
            workflow_name = getattr(workflow, "name", None) or f"Workflow {run.workflow_id}"
        return record_chat_workflow_event(
            int(chat_id),
            event_type,
            status=status,
            workflow_id=int(run.workflow_id),
            workflow_name=workflow_name,
            run_id=int(run_id),
            step_id=step_id,
            step_name=step_name,
            summary=summary,
            phase=phase,
            agent_activity=agent_activity,
            limit=limit,
        )
    except Exception:
        logger.debug("record_workflow_chat_event failed", exc_info=True)
        return None
