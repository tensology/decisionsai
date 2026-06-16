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
    from distr.core.automation.store import legacy_public_id, public_id

    try:
        return public_id(int(workflow_id))
    except (TypeError, ValueError):
        return legacy_public_id(workflow_id)


def is_automation_workflow(workflow: AutoWorkflow | None) -> bool:
    from distr.core.automation.store import is_automation_workflow as _is_auto

    return _is_auto(workflow)


def serialize_automation_workflow(workflow: AutoWorkflow) -> dict[str, Any]:
    from distr.core.automation.store import get_automation, serialize_legacy_workflow

    migrated = get_automation(f"wf_{workflow.id}")
    if migrated and migrated.get("record_id"):
        return migrated
    return serialize_legacy_workflow(workflow)


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
                "preset_id": automation.get("preset_id") or "",
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
    execution_mode: str = "agent_chat_orchestrator",
    tool_result: dict[str, Any] | None = None,
) -> int | None:
    from distr.core.automation.store import record_automation_run

    return record_automation_run(
        automation=automation,
        status=status,
        summary=summary,
        event_ids=event_ids,
        manual=manual,
        prompt=prompt,
        chat_id=chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode=execution_mode,
        tool_result=tool_result,
    )


def _bound_tool_name(automation: dict[str, Any]) -> str:
    action_config = automation.get("action_config")
    if not isinstance(action_config, dict):
        return ""
    return str(action_config.get("tool") or "").strip()


def _automation_already_running(automation: dict[str, Any]) -> bool:
    from distr.core.automation_subagent import _workflow_id_from_automation, workflow_run_in_progress

    workflow_id = _workflow_id_from_automation(automation)
    return workflow_id is not None and workflow_run_in_progress(workflow_id)


def _skip_duplicate_running_response(
    automation: dict[str, Any],
    *,
    manual: bool,
    chat_id: int | None,
    schedule_metadata: dict[str, Any] | None,
    execution_mode: str,
    emit_event: Callable[..., int | None],
) -> dict[str, Any]:
    summary = "Automation is already running."
    workflow_run_id = _record_dispatch_run(
        automation=automation,
        status="skipped",
        summary=summary,
        event_ids=[],
        manual=manual,
        prompt="",
        chat_id=chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode=execution_mode,
    )
    emit_event(
        automation=automation,
        event_type="worker_skipped",
        status="skipped",
        summary=summary,
        payload={"skip_reason": "already_running", "workflow_run_id": workflow_run_id, "manual": manual},
    )
    return {
        "status": "skipped",
        "summary": summary,
        "workflow_run_id": workflow_run_id,
        "event_ids": [],
        "chat_id": chat_id,
        "skip_reason": "already_running",
    }


def _dispatch_tool_bound_automation(
    automation: dict[str, Any],
    *,
    manual: bool,
    schedule_metadata: dict[str, Any] | None,
    chat_id: int | None,
    speak: bool,
    emit_event: Callable[..., int | None],
) -> dict[str, Any]:
    from distr.core.automation_subagent import start_automation_subagent
    from distr.core.engagement_gates import proactive_delivery_blocked

    blocked, reason = proactive_delivery_blocked(
        delivery_kind="automation_tool",
        body=str(automation.get("name") or ""),
        manual=manual,
        preset_id=str(automation.get("preset_id") or ""),
    )
    if blocked:
        summary = {
            "daily_plan_opt_out": "Skipped — you asked not to receive scheduled daily plans.",
            "user_likely_asleep": "Skipped — you do not look awake yet. I will try again on the next schedule.",
        }.get(reason, "Skipped by engagement policy.")
        workflow_run_id = _record_dispatch_run(
            automation=automation,
            status="skipped",
            summary=summary,
            event_ids=[],
            manual=manual,
            prompt="",
            chat_id=chat_id,
            schedule_metadata=schedule_metadata,
            execution_mode="automation_subagent_tool",
        )
        emit_event(
            automation=automation,
            event_type="worker_skipped",
            status="skipped",
            summary=summary,
            payload={"skip_reason": reason, "workflow_run_id": workflow_run_id, "manual": manual},
        )
        return {
            "status": "skipped",
            "summary": summary,
            "workflow_run_id": workflow_run_id,
            "event_ids": [],
            "chat_id": chat_id,
            "skip_reason": reason,
        }

    if _automation_already_running(automation):
        return _skip_duplicate_running_response(
            automation,
            manual=manual,
            chat_id=chat_id,
            schedule_metadata=schedule_metadata,
            execution_mode="automation_subagent_tool",
            emit_event=emit_event,
        )

    event_ids: list[int] = []
    started_event_id = emit_event(
        automation=automation,
        event_type="run_started",
        status="running",
        summary=f"Automation started: {automation.get('name') or 'Untitled Automation'}",
        payload={
            "tool": _bound_tool_name(automation),
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if started_event_id is not None:
        event_ids.append(started_event_id)

    target_chat_id = chat_id or resolve_current_agent_chat_id()
    workflow_run_id = _record_dispatch_run(
        automation=automation,
        status="running",
        summary="Automation subagent started.",
        event_ids=event_ids,
        manual=manual,
        prompt="",
        chat_id=target_chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode="automation_subagent_tool",
    )

    dispatched_event_id = emit_event(
        automation=automation,
        event_type="worker_dispatched",
        status="running",
        summary="Automation subagent started.",
        payload={
            "workflow_run_id": workflow_run_id,
            "chat_id": target_chat_id,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if dispatched_event_id is not None:
        event_ids.append(dispatched_event_id)

    if workflow_run_id:
        start_automation_subagent(
            automation=automation,
            run_id=int(workflow_run_id),
            manual=manual,
            chat_id=target_chat_id,
            speak=speak,
            schedule_metadata=schedule_metadata,
            emit_event=emit_event,
        )

    return {
        "status": "running",
        "summary": "Automation subagent started.",
        "workflow_run_id": workflow_run_id,
        "event_ids": event_ids,
        "chat_id": target_chat_id,
    }


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
    tool_name = _bound_tool_name(automation)
    if not instruction and not tool_name:
        return {
            "status": "failed",
            "summary": "Automation has no instruction or tool to run.",
            "workflow_run_id": None,
            "event_ids": [],
        }

    event_fn = emit_event or emit_automation_event
    if tool_name:
        return _dispatch_tool_bound_automation(
            automation,
            manual=manual,
            schedule_metadata=schedule_metadata,
            chat_id=chat_id,
            speak=speak,
            emit_event=event_fn,
        )

    if _automation_already_running(automation):
        return _skip_duplicate_running_response(
            automation,
            manual=manual,
            chat_id=chat_id,
            schedule_metadata=schedule_metadata,
            execution_mode="automation_subagent_instruction",
            emit_event=event_fn,
        )

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
    status = "running"
    summary = "Automation subagent started."

    if not target_chat_id:
        status = "failed"
        summary = "Automation needs an active agent chat before it can run."
        workflow_run_id = _record_dispatch_run(
            automation=automation,
            status=status,
            summary=summary,
            event_ids=event_ids,
            manual=manual,
            prompt=prompt,
            chat_id=target_chat_id,
            schedule_metadata=schedule_metadata,
            execution_mode="automation_subagent_instruction",
        )
        return {
            "status": status,
            "summary": summary,
            "workflow_run_id": workflow_run_id,
            "event_ids": event_ids,
            "chat_id": target_chat_id,
        }

    workflow_run_id = _record_dispatch_run(
        automation=automation,
        status="running",
        summary=summary,
        event_ids=event_ids,
        manual=manual,
        prompt=prompt,
        chat_id=target_chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode="automation_subagent_instruction",
    )

    dispatched_event_id = event_fn(
        automation=automation,
        event_type="worker_dispatched",
        status="running",
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

    if workflow_run_id and emit_signal:
        from distr.core.automation_subagent import start_automation_subagent

        start_automation_subagent(
            automation=automation,
            run_id=int(workflow_run_id),
            manual=manual,
            chat_id=target_chat_id,
            speak=speak,
            schedule_metadata=schedule_metadata,
            emit_event=event_fn,
        )

    if workflow_run_id:
        try:
            from distr.core.db.automation import AutomationRun

            with get_session() as session:
                run = session.query(AutomationRun).filter(AutomationRun.id == workflow_run_id).first()
                if run:
                    data = _json_config(run.run_data)
                    data["orchestration_event_ids"] = event_ids
                    run.run_data = json.dumps(data, ensure_ascii=False, default=str)
                    session.commit()
                else:
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
