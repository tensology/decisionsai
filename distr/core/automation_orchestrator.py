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
    action_config = marker.get("action_config") if isinstance(marker.get("action_config"), dict) else {}
    return {
        "id": automation_id(workflow.id),
        "workflow_id": workflow.id,
        "step_id": step.id if step else None,
        "name": workflow.name or "Untitled Automation",
        "automation_type": marker.get("automation_type") or "scheduled_instruction",
        "preset_id": str(marker.get("preset_id") or "").strip(),
        "action_config": dict(action_config),
        "instruction": (step.instruction if step else "") or "",
        "status": workflow.status or "active",
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
    workflow_id = automation.get("workflow_id")
    if not workflow_id:
        return None
    now = datetime.utcnow().replace(microsecond=0)
    run_data = {
        "source_type": "automation",
        "source_label": "Automation",
        "execution_mode": execution_mode,
        "automation_id": automation.get("id"),
        "automation_name": automation.get("name"),
        "preset_id": automation.get("preset_id") or "",
        "instruction": automation.get("instruction"),
        "action_config": automation.get("action_config") or {},
        "manual": bool(manual),
        "message": summary,
        "summary": summary,
        "chat_id": chat_id,
        "is_workflow_attached": True,
        "orchestration_event_ids": event_ids,
        "prompt_preview": prompt[:1500],
        **(schedule_metadata or {}),
    }
    if tool_result:
        run_data["tool_result"] = tool_result
    with get_session() as session:
        run = AutoWorkflowRun(
            workflow_id=int(workflow_id),
            status=status,
            started_at=now,
            completed_at=now if status in {"failed", "skipped", "completed"} else None,
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


def _bound_tool_name(automation: dict[str, Any]) -> str:
    action_config = automation.get("action_config")
    if not isinstance(action_config, dict):
        return ""
    return str(action_config.get("tool") or "").strip()


def _dispatch_tool_bound_automation(
    automation: dict[str, Any],
    *,
    manual: bool,
    schedule_metadata: dict[str, Any] | None,
    chat_id: int | None,
    speak: bool,
    emit_event: Callable[..., int | None],
) -> dict[str, Any]:
    from distr.core.automation_tool_runner import run_automation_tool
    from distr.core.engagement_gates import proactive_delivery_blocked

    action_config = automation.get("action_config") if isinstance(automation.get("action_config"), dict) else {}
    tool_name = str(action_config.get("tool") or "").strip()
    tool_args = action_config.get("args") if isinstance(action_config.get("args"), dict) else {}

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
            execution_mode="tool_direct",
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

    event_ids: list[int] = []
    started_event_id = emit_event(
        automation=automation,
        event_type="run_started",
        status="running",
        summary=f"Automation started: {automation.get('name') or 'Untitled Automation'}",
        payload={
            "tool": tool_name,
            "tool_args": tool_args,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if started_event_id is not None:
        event_ids.append(started_event_id)

    tool_result = run_automation_tool(tool_name, tool_args)
    output = str(tool_result.get("output") or "").strip()
    spoken = str(tool_result.get("spoken_summary") or output).strip()
    success = bool(tool_result.get("success"))
    status = "completed" if success else "failed"
    summary = spoken or output or ("Tool run finished." if success else "Tool run failed.")

    target_chat_id = chat_id or resolve_current_agent_chat_id()
    chat_body = f"[Automation — {automation.get('name') or 'Untitled Automation'}]\n\n{output or summary}"
    if target_chat_id:
        try:
            emit_to_agent_chat(int(target_chat_id), chat_body, bool(speak), skip_user_persist=True)
        except Exception as exc:
            logger.debug("Automation tool chat delivery failed", exc_info=True)
            if success:
                status = "completed"
                summary = f"{summary} (chat delivery failed: {exc})"

    workflow_run_id = _record_dispatch_run(
        automation=automation,
        status=status,
        summary=summary,
        event_ids=event_ids,
        manual=manual,
        prompt=chat_body,
        chat_id=target_chat_id,
        schedule_metadata=schedule_metadata,
        execution_mode="tool_direct",
        tool_result=tool_result,
    )

    finished_event_id = emit_event(
        automation=automation,
        event_type="worker_completed" if success else "worker_failed",
        status=status,
        summary=summary,
        payload={
            "tool": tool_name,
            "workflow_run_id": workflow_run_id,
            "chat_id": target_chat_id,
            "manual": bool(manual),
            **(schedule_metadata or {}),
        },
    )
    if finished_event_id is not None:
        event_ids.append(finished_event_id)

    return {
        "status": status,
        "summary": summary,
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
