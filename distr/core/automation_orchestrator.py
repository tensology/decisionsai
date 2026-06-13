"""Automation handoff into the live Decisions orchestrator chat."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable

from distr.core.agent.services.llm.bulk_instruction import augment_bulk_instruction
from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep

logger = logging.getLogger(__name__)

AUTOMATION_SURFACE = "automation"


def _json_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        loaded = json.loads(str(raw))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def automation_id(workflow_id: int | str) -> str:
    return f"wf_{int(workflow_id)}"


def is_automation_workflow(workflow: AutoWorkflow | None) -> bool:
    if workflow is None:
        return False
    marker = _json_config(workflow.context_rules)
    return (
        workflow.workflow_type == "scheduled"
        and str(marker.get("decisions_surface") or "").strip().lower() == AUTOMATION_SURFACE
    )


def _first_instruction_step(workflow: AutoWorkflow) -> AutoWorkflowStep | None:
    steps = sorted(list(workflow.steps or []), key=lambda s: s.position or 0)
    return steps[0] if steps else None


def serialize_automation_workflow(workflow: AutoWorkflow) -> dict[str, Any]:
    marker = _json_config(workflow.context_rules)
    step = _first_instruction_step(workflow)
    return {
        "id": automation_id(workflow.id),
        "workflow_id": workflow.id,
        "step_id": step.id if step else None,
        "name": workflow.name or "Untitled Automation",
        "automation_type": marker.get("automation_type") or "scheduled_instruction",
        "instruction": (step.instruction if step else "") or "",
    }


def automation_prompt(automation: dict[str, Any]) -> str:
    instruction = str(automation.get("instruction") or "").strip()
    return (
        "This is a DecisionsAI automation run. Treat the automation instruction "
        "as the user's requested outcome, decide the natural next orchestration "
        "step, and respond organically. If sub-agents or tools are needed, route "
        "the work through the orchestrator instead of pretending it is complete. "
        "If the instruction contains multiple requested actions, execute them as "
        "an ordered queue and report each unavailable screen, blocker, or failed "
        "step honestly.\n\n"
        f"Automation: {automation.get('name') or 'Untitled Automation'}\n"
        f"Instruction:\n{instruction}"
    )


def emit_automation_event(
    *,
    automation: dict[str, Any],
    event_type: str,
    status: str,
    summary: str,
    payload: dict[str, Any] | None = None,
) -> int | None:
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        return emit_orchestration_event(
            source="automation",
            event_type=event_type,
            status=status,
            workflow_id=automation.get("workflow_id"),
            summary=summary,
            payload={
                "surface": AUTOMATION_SURFACE,
                "subtype": event_type,
                "automation_id": automation.get("id"),
                "automation_name": automation.get("name"),
                "automation_type": automation.get("automation_type"),
                "is_workflow_attached": True,
                **(payload or {}),
            },
        )
    except Exception:
        logger.debug("Automation orchestration event failed", exc_info=True)
        return None


def resolve_current_agent_chat_id(settings: dict[str, Any] | None = None) -> int | None:
    try:
        if settings is None:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        raw = (
            settings.get("agent_current_chat_id")
            or settings.get("last_chat_id")
            or settings.get("current_chat_id")
        )
        chat_id = int(raw)
        return chat_id if chat_id > 0 else None
    except Exception:
        return None


def emit_to_agent_chat(
    chat_id: int,
    prompt: str,
    speak: bool = True,
    *,
    skip_user_persist: bool = False,
) -> None:
    from distr.core.signals import signal_manager

    options = {"skip_user_persist": True} if skip_user_persist else None
    signal_manager.web_send_to_agent_requested.emit(
        int(chat_id),
        prompt,
        bool(speak),
        None,
        None,
        options,
    )


def _record_dispatch_run(
    *,
    automation: dict[str, Any],
    status: str,
    summary: str,
    event_ids: list[int],
    manual: bool,
    prompt: str,
    chat_id: int | None,
    schedule_metadata: dict[str, Any] | None,
) -> int | None:
    workflow_id = automation.get("workflow_id")
    if not workflow_id:
        return None
    now = datetime.utcnow().replace(microsecond=0)
    run_data = {
        "source_type": "automation",
        "source_label": "Automation",
        "execution_mode": "agent_chat_orchestrator",
        "automation_id": automation.get("id"),
        "automation_name": automation.get("name"),
        "instruction": automation.get("instruction"),
        "manual": bool(manual),
        "message": summary,
        "summary": summary,
        "chat_id": chat_id,
        "is_workflow_attached": True,
        "orchestration_event_ids": event_ids,
        "prompt_preview": prompt[:1500],
        **(schedule_metadata or {}),
    }
    with get_session() as session:
        run = AutoWorkflowRun(
            workflow_id=int(workflow_id),
            status=status,
            started_at=now,
            completed_at=now if status in {"failed", "skipped"} else None,
            current_step_id=automation.get("step_id"),
            run_data=json.dumps(run_data, ensure_ascii=False, default=str),
        )
        session.add(run)
        workflow = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if workflow:
            workflow.last_run_at = now
            workflow.modified_date = now
        session.commit()
        return run.id


def dispatch_automation_to_current_chat(
    automation: dict[str, Any],
    *,
    manual: bool = True,
    schedule_metadata: dict[str, Any] | None = None,
    chat_id: int | None = None,
    speak: bool = True,
    emit_signal: bool = True,
    emit_event: Callable[..., int | None] | None = None,
) -> dict[str, Any]:
    instruction = str(automation.get("instruction") or "").strip()
    if not instruction:
        return {
            "status": "failed",
            "summary": "Automation has no instruction to run.",
            "workflow_run_id": None,
            "event_ids": [],
        }

    event_fn = emit_event or emit_automation_event
    event_ids: list[int] = []
    started_event_id = event_fn(
        automation=automation,
        event_type="run_started",
        status="running",
        summary=f"Automation started: {automation.get('name') or 'Untitled Automation'}",
        payload={
            "instruction": instruction,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if started_event_id is not None:
        event_ids.append(started_event_id)

    prompt = augment_bulk_instruction(automation_prompt(automation), source="automation")
    target_chat_id = chat_id or resolve_current_agent_chat_id()
    status = "dispatched"
    summary = "Automation instruction sent to the orchestrator."

    if not target_chat_id:
        status = "failed"
        summary = "Automation needs an active agent chat before it can run."
    elif emit_signal:
        try:
            emit_to_agent_chat(
                int(target_chat_id),
                prompt,
                bool(speak),
                skip_user_persist=True,
            )
        except Exception as exc:
            logger.debug("Automation chat dispatch failed", exc_info=True)
            status = "failed"
            summary = f"Automation could not reach the orchestrator: {exc}"

    workflow_run_id = _record_dispatch_run(
        automation=automation,
        status=status,
        summary=summary,
        event_ids=event_ids,
        manual=manual,
        prompt=prompt,
        chat_id=target_chat_id,
        schedule_metadata=schedule_metadata,
    )

    dispatched_event_id = event_fn(
        automation=automation,
        event_type="worker_dispatched" if status == "dispatched" else "worker_failed",
        status=status,
        summary=summary,
        payload={
            "instruction": instruction,
            "workflow_run_id": workflow_run_id,
            "chat_id": target_chat_id,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if dispatched_event_id is not None:
        event_ids.append(dispatched_event_id)

    if workflow_run_id:
        try:
            with get_session() as session:
                run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == workflow_run_id).first()
                if run:
                    data = _json_config(run.run_data)
                    data["orchestration_event_ids"] = event_ids
                    run.run_data = json.dumps(data, ensure_ascii=False, default=str)
                    session.commit()
        except Exception:
            logger.debug("Automation run event id update failed", exc_info=True)

    return {
        "status": status,
        "summary": summary,
        "workflow_run_id": workflow_run_id,
        "event_ids": event_ids,
        "chat_id": target_chat_id,
    }
