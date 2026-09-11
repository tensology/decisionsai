"""Shared workflow transport helpers and isolated-step coordination."""
from fastapi import WebSocket, WebSocketDisconnect
from typing import List
import json
import threading
import asyncio
_isolated_step_exec_lock = threading.Lock()


_isolated_step_exec_started_at = {}


def _workflow_feedback_message(action: str, result: dict | None = None) -> dict:
    """Return human-readable workflow feedback for API/UI callers."""
    result = result or {}
    if action == "run_started":
        return {
            "message": "Workflow run started.",
            "next_action": "Watch Active Runs for the current step and final result.",
        }
    if action == "cancelled":
        return {
            "message": "Workflow run cancelled.",
            "next_action": "Review the run history before starting it again.",
        }
    if action == "reset":
        return {
            "message": "Workflow stopped and reset.",
            "next_action": "Run it again when the steps look right.",
        }
    if action == "clear_audit":
        return {
            "message": "Workflow run history cleared.",
            "next_action": "Executor sessions and orchestration events were left intact.",
        }
    if action == "clear_events":
        return {
            "message": "Workflow events cleared.",
            "next_action": "New orchestration events will appear when the workflow runs again.",
        }
    if action == "clear_executor":
        return {
            "message": "Executor log cleared.",
            "next_action": "New CLI or IDE sessions will appear when a run reaches an executor step.",
        }
    if action == "continued":
        decision = result.get("action") or ""
        if decision == "next_step":
            return {
                "message": f"Workflow continued to step #{result.get('step_id')}.",
                "next_action": "Watch Active Runs for the next step outcome.",
            }
        if decision == "end_run":
            return {
                "message": f"Workflow finished with status: {result.get('status', 'completed')}.",
                "next_action": "Open run history for the final evidence packet.",
            }
        if decision == "waiting":
            return {
                "message": "Workflow is still waiting for input.",
                "next_action": "Provide the missing decision or continue instruction.",
            }
        return {
            "message": "Workflow continued.",
            "next_action": "Refresh Active Runs to see the latest state.",
        }
    return {"message": "Workflow updated.", "next_action": "Refresh the workflow status."}


def _workflow_error_payload(error: str, action: str = "workflow") -> dict:
    """Normalize workflow errors into useful, non-noisy API payloads."""
    raw = str(error or "Workflow request failed.").strip()
    lower = raw.lower()
    detail = raw
    next_action = "Refresh the workflow and check the current run state."

    if "already in progress" in lower:
        detail = "A run is already active for this workflow scope."
        next_action = "Open Active Runs, then continue, cancel, or wait for that run."
    elif "not waiting" in lower:
        detail = "This workflow is not currently waiting for input."
        next_action = "Refresh Active Runs; continue only applies to waiting runs."
    elif "no waiting step" in lower:
        detail = "There is no waiting step to continue."
        next_action = "Refresh the workflow status and inspect the current step."
    elif "has no steps" in lower:
        detail = "This workflow has no steps to run."
        next_action = "Add at least one workflow step, then run it again."
    elif "no instruction" in lower or "no command configured" in lower or "no url configured" in lower:
        detail = "A workflow step is missing required configuration."
        next_action = "Open the highlighted step and fill in the missing action details."
    elif "audit workflows are read-only" in lower:
        detail = "Audit workflows are read-only."
        next_action = "Duplicate or create a non-audit workflow before editing or running it."
    elif "run not found" in lower:
        detail = "That workflow run no longer exists."
        next_action = "Refresh Active Runs and use the latest run id."
    elif "workflow not found" in lower:
        detail = "That workflow no longer exists."
        next_action = "Refresh the workflow list."
    elif "step not found" in lower:
        detail = "That workflow step no longer exists."
        next_action = "Refresh the workflow details."

    return {
        "detail": detail,
        "raw_detail": raw,
        "action": action,
        "next_action": next_action,
    }


def _step_tool_list(step) -> List[str]:
    from distr.core.workflow.tools import normalize_tool_list, tools_for_action

    try:
        config = json.loads(getattr(step, "config", None) or "{}") or {}
    except Exception:
        config = {}
    tools = config.get("tools") if isinstance(config, dict) else []
    clean = normalize_tool_list(tools or [])
    if clean:
        return clean
    action = str(getattr(step, "action_type", None) or getattr(step, "step_type", None) or "").strip()
    return tools_for_action(action)


def _needs_input_context_and_spoken(
    db,
    *,
    workflow_id: int,
    run,
    step_id: int | None,
    project_id: int | None,
    message: str,
    payload: dict,
) -> tuple[dict, str]:
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

    workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
    step = None
    if step_id:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
    try:
        run_data = json.loads(getattr(run, "run_data", None) or "{}") or {}
    except Exception:
        run_data = {}
    project = (
        str(payload.get("project") or payload.get("project_name") or run_data.get("project_name") or "").strip()
        or (f"project #{project_id}" if project_id else "this project")
    )
    workflow_name = str(getattr(workflow, "name", None) or f"workflow #{workflow_id}").strip()
    step_name = str(getattr(step, "name", None) or (f"step #{step_id}" if step_id else "the current step")).strip()
    situation = str(payload.get("situation") or payload.get("summary") or message or "").strip()
    tools = _step_tool_list(step) if step else []
    context = {
        "project": project,
        "workflow": workflow_name,
        "run": f"Run #{getattr(run, 'id', '')}".strip(),
        "step": step_name,
        "situation": situation,
        "tools": tools,
    }
    spoken = (
        f"I'm working on {project} in {workflow_name}, at {step_name}. "
        f"{situation + ' ' if situation else ''}"
        f"{message}"
    ).strip()
    return context, spoken


def _is_audit_workflow(workflow_id: int) -> bool:
    """Return True if the workflow exists and has workflow_type='audit'."""
    from distr.core.workflow.service import get_workflow_type
    return get_workflow_type(workflow_id) == "audit"


def _json_config(text: str | None) -> dict:
    try:
        data = json.loads(text or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _schedule_from_workflow(wf) -> dict:
    preset = str(wf.schedule_preset or "").strip().lower()
    days = str(wf.schedule_days or "").strip()
    if preset == "once":
        return {
            "kind": "once",
            "run_at": wf.schedule_time or "",
            "timezone": wf.schedule_timezone or "",
        }
    if preset == "daily":
        return {
            "kind": "daily",
            "time": wf.schedule_time or "",
            "timezone": wf.schedule_timezone or "",
        }
    if preset == "weekly" and days == "1,2,3,4,5":
        return {
            "kind": "weekdays",
            "time": wf.schedule_time or "",
            "timezone": wf.schedule_timezone or "",
        }
    return {
        "kind": "weekly",
        "time": wf.schedule_time or "",
        "weekday": days or "1",
        "timezone": wf.schedule_timezone or "",
    }


def _action_from_step(step) -> dict:
    if not step:
        return {"type": "keypress", "key": "enter"}
    if step.action_type == "play_recording":
        return {
            "type": "play_recording",
            "recording_name": step.recording_filename or "",
        }
    config = _json_config(step.config)
    action = config.get("scheduled_action")
    if isinstance(action, dict) and action.get("type"):
        return action
    instruction = step.instruction or ""
    return {"type": "type_text", "text": instruction}


def _scheduled_action_payload(wf) -> dict:
    step = sorted(list(wf.steps or []), key=lambda s: s.position or 0)[0] if wf.steps else None
    run_log = []
    for run in sorted(list(wf.runs or []), key=lambda r: r.started_at or r.id, reverse=True)[:5]:
        run_data = _json_config(run.run_data)
        packet = run_data.get("result_packet") if isinstance(run_data.get("result_packet"), dict) else {}
        result_note = packet.get("summary") or run_data.get("message") or run_data.get("phase") or ""
        run_log.append({
            "run_id": run.id,
            "status": run.status or "",
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "result": str(result_note or "")[:500],
        })
    return {
        "id": wf.id,
        "workflow_id": wf.id,
        "workflow_type": wf.workflow_type or "scheduled",
        "title": wf.name or "Scheduled action",
        "description": wf.description or "",
        "enabled": bool(wf.schedule_enabled),
        "status": wf.status or "",
        "schedule": _schedule_from_workflow(wf),
        "action": _action_from_step(step),
        "next_run_at": wf.next_run_at.isoformat() if wf.next_run_at else None,
        "last_run_at": wf.last_run_at.isoformat() if wf.last_run_at else None,
        "step_id": step.id if step else None,
        "step_action_type": step.action_type if step else None,
        "run_log": run_log,
    }


def _next_run_for_schedule(workflow_data: dict) -> object | None:
    from distr.core.workflow.scheduler import _next_run_from_cron, schedule_to_cron

    cron = schedule_to_cron(
        workflow_data.get("schedule_preset"),
        workflow_data.get("schedule_time"),
        workflow_data.get("schedule_timezone"),
        workflow_data.get("schedule_days"),
    )
    return _next_run_from_cron(
        cron,
        timezone=workflow_data.get("schedule_timezone"),
        allow_current_minute=True,
    ) if cron else None


async def _workflows_websocket_handler(websocket: WebSocket):
    """WebSocket stream for realtime workflow UI refresh."""
    import asyncio
    from distr.gui.web.security import is_allowed_local_origin
    from distr.gui.web.workflow_events import register_wf_websocket, unregister_wf_websocket

    origin = websocket.headers.get("origin")
    if origin and not is_allowed_local_origin(origin):
        await websocket.close(code=1008, reason="Origin not allowed")
        return
    await websocket.accept()
    loop = asyncio.get_event_loop()
    register_wf_websocket(websocket, loop)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                await websocket.send_text('{"type":"ping"}')
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        unregister_wf_websocket(websocket)
