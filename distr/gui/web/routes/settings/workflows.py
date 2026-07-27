"""
Workflow routes — /workflows/*
"""
from fastapi import Request, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import json
import re
import time
import threading
import asyncio

from ._shared import logger


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


# ---- Pydantic models (only used in this module) ----

class WorkflowCreateRequest(BaseModel):
    name: str = "Untitled Workflow"
    description: str = ""
    workflow_type: Optional[str] = None


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    schedule_preset: Optional[str] = None
    schedule_cron: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    schedule_timezone: Optional[str] = None
    start_step_position: Optional[int] = None
    workflow_type: Optional[str] = None
    context_rules: Optional[str] = None
    workflow_input: Optional[str] = None
    run_settings: Optional[dict] = None
    pre_chain: Optional[List[str]] = None
    post_chain: Optional[List[str]] = None


class WorkflowOrderRequest(BaseModel):
    workflow_ids: List[int]


class CodexBridgeEventRequest(BaseModel):
    event_type: str = "codex_event"
    status: Optional[str] = None
    message: str = ""
    input: str = ""
    output: str = ""
    execution_session_id: Optional[int] = None
    step_id: Optional[int] = None
    ticket_id: Optional[int] = None
    project_id: Optional[int] = None
    mistake_label: Optional[str] = None
    payload: Optional[dict] = None
    evidence: Optional[dict] = None


class UiFeedbackRequest(BaseModel):
    label: str
    reason: str = ""
    step_id: Optional[int] = None
    ticket_id: Optional[int] = None
    board_id: Optional[int] = None
    project_id: Optional[int] = None
    execution_session_id: Optional[int] = None
    screenshot_paths: Optional[List[str]] = None
    save_as_visual_baseline: bool = False
    visual_baseline_name: Optional[str] = None
    baseline_screen_name: Optional[str] = None


class VisualBaselineScreenRequest(BaseModel):
    screen_name: str
    screenshot_path: str
    flow_name: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[dict] = None


class VisualBaselineRequest(BaseModel):
    name: str
    screens: List[VisualBaselineScreenRequest]
    board_id: Optional[int] = None
    project_id: Optional[int] = None
    description: str = ""
    version: str = "v1"
    store_copy: bool = False


class StepCreateRequest(BaseModel):
    name: str = "New Step"
    action_type: str = "agent_instruction"
    position: Optional[int] = None
    instruction: str = ""
    config: Optional[dict] = None
    validation_type: str = "none"
    validation_prompt: str = ""
    wait_for_continue: bool = False


class LoopPresetApplyRequest(BaseModel):
    preset_name: str
    mode: str = "replace"


class LoopPresetSaveRequest(BaseModel):
    name: str


class StepHarnessSuggestRequest(BaseModel):
    instruction: str = ""
    action_type: str = ""
    archetype: str = ""
    loop_contract: Optional[dict] = None
    step_role: str = ""


class StepHarnessLlmSuggestRequest(BaseModel):
    instruction: str = ""
    guardrail: str = ""
    validation_prompt: str = ""
    loop_contract: Optional[dict] = None


class StepReorderRequest(BaseModel):
    step_ids: List[int]


class WorkflowGenerateRequest(BaseModel):
    description: str


class WorkflowPlanRequest(BaseModel):
    instruction: str
    chat_id: Optional[int] = None
    name: Optional[str] = None


class ProjectOpsPlanRequest(BaseModel):
    instruction: str
    board_id: Optional[int] = None


class ProjectOpsExecuteRequest(BaseModel):
    instruction: str
    route: str
    board_id: Optional[int] = None
    ticket_id: Optional[int] = None
    approved: bool = True


class WorkflowGenerateStepsRequest(BaseModel):
    instruction: str


class WorkflowTicketGroupRunRequest(BaseModel):
    ticket_ids: List[int]


class WorkflowGenerateCodeRequest(BaseModel):
    instruction: str
    step_type: str


class WorkflowTestCodeRequest(BaseModel):
    code: str
    step_type: str
    headless: bool = True


class WorkflowValidateStepRequest(BaseModel):
    step_type: str
    config: dict


class WorkflowScheduleUpdate(BaseModel):
    enabled: Optional[bool] = None
    schedule: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    timezone: Optional[str] = None


class WorkflowSeedFixturesRequest(BaseModel):
    force_reset: bool = False
    workflow_names: Optional[List[str]] = None


class WorkflowPurgeAllRequest(BaseModel):
    confirm: bool = False
    include_audit: bool = False


class ScheduledActionRequest(BaseModel):
    title: str = "Scheduled action"
    schedule: dict
    action: dict
    target_context: Optional[dict] = None
    safety: Optional[dict] = None


class ScheduledActionUpdateRequest(BaseModel):
    title: Optional[str] = None
    enabled: Optional[bool] = None
    schedule: Optional[dict] = None
    action: Optional[dict] = None
    target_context: Optional[dict] = None
    safety: Optional[dict] = None


class ContextItemCreateRequest(BaseModel):
    title: str
    content: str = ""
    notes: str = ""


class ContextItemUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    notes: Optional[str] = None


def register_routes(router, templates):

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

    @router.get("/workflows")
    async def workflow_list(limit: int = 50, search: Optional[str] = None, type: Optional[str] = None):
        try:
            from distr.core.workflow.service import list_workflows
            rows = await asyncio.to_thread(list_workflows, limit=limit, search=search, workflow_type=type)
            return JSONResponse(rows)
        except Exception as e:
            logger.error("Workflow list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/skills")
    async def workflow_skills_catalog(source: Optional[str] = None, limit: int = 200):
        """Bundled skills registry for workflow skill chains and orchestrator transfer."""
        try:
            from distr.core.skills.catalog import load_registry

            rows = load_registry()
            if source:
                src = source.strip().lower()
                rows = tuple(r for r in rows if str(r.get("source") or "").lower() == src)
            out = []
            for row in rows[: max(1, min(int(limit or 200), 500))]:
                out.append(
                    {
                        "id": row.get("id"),
                        "name": row.get("name") or row.get("id"),
                        "description": row.get("description") or "",
                        "source": row.get("source") or "bundled",
                        "tags": row.get("tags") or [],
                    }
                )
            return JSONResponse({"skills": out, "count": len(out)})
        except Exception as e:
            logger.error("Workflow skills catalog failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows")
    async def workflow_create(data: WorkflowCreateRequest):
        try:
            from distr.core.workflow.service import create_workflow, get_workflow
            kwargs = {"name": data.name, "description": data.description}
            if data.workflow_type is not None:
                kwargs["workflow_type"] = data.workflow_type
            wf_id = create_workflow(**kwargs)
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
            return JSONResponse(get_workflow(wf_id))
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Workflow create failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/order")
    async def workflow_order_update(data: WorkflowOrderRequest):
        try:
            from distr.core.workflow.service import update_workflow_order

            ok = update_workflow_order(data.workflow_ids)
            if not ok:
                return JSONResponse({"detail": "Workflow order update failed."}, status_code=400)
            return JSONResponse({"success": True, "workflow_ids": data.workflow_ids})
        except Exception as e:
            logger.error("Workflow order update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/visual-baselines")
    async def workflow_visual_baseline_create(data: VisualBaselineRequest):
        """Create a named visual baseline set for UI quality validation."""
        try:
            from distr.core.orchestrator import create_visual_baseline_set, get_visual_baseline_set

            baseline_id = create_visual_baseline_set(
                name=data.name,
                board_id=data.board_id,
                project_id=data.project_id,
                description=data.description,
                version=data.version,
                screens=[screen.model_dump() for screen in data.screens],
                copy_screenshots=data.store_copy,
            )
            baseline = get_visual_baseline_set(baseline_set_id=baseline_id)
            return JSONResponse({"success": True, "visual_baseline": baseline})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Workflow visual baseline create failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/visual-baselines")
    async def workflow_visual_baseline_list(
        board_id: Optional[int] = None,
        project_id: Optional[int] = None,
        include_global: bool = False,
        limit: int = 50,
    ):
        """List named visual baselines for board/project UI validation."""
        try:
            from distr.core.orchestrator import list_visual_baseline_sets

            baselines = list_visual_baseline_sets(
                board_id=board_id,
                project_id=project_id,
                include_global=include_global,
                limit=limit,
            )
            return JSONResponse({"success": True, "visual_baselines": baselines})
        except Exception as e:
            logger.error("Workflow visual baseline list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/visual-baselines/readiness")
    async def workflow_visual_baseline_readiness(
        board_id: Optional[int] = None,
        project_id: Optional[int] = None,
        baseline_set_id: Optional[int] = None,
        name: Optional[str] = None,
        include_global: bool = False,
        limit: int = 50,
    ):
        """Check whether visual baseline screenshot files are present on disk."""
        try:
            from distr.core.orchestrator import inspect_visual_baseline_readiness

            readiness = inspect_visual_baseline_readiness(
                board_id=board_id,
                project_id=project_id,
                baseline_set_id=baseline_set_id,
                name=name,
                include_global=include_global,
                limit=limit,
            )
            return JSONResponse({"success": True, "visual_baseline_readiness": readiness})
        except Exception as e:
            logger.error("Workflow visual baseline readiness failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/visual-baselines/{baseline_set_id}")
    async def workflow_visual_baseline_get(baseline_set_id: int):
        """Return one named visual baseline set and its reference screens."""
        try:
            from distr.core.orchestrator import get_visual_baseline_set

            baseline = get_visual_baseline_set(baseline_set_id=baseline_set_id)
            if not baseline:
                return JSONResponse({"detail": "Visual baseline not found"}, status_code=404)
            return JSONResponse({"success": True, "visual_baseline": baseline})
        except Exception as e:
            logger.error("Workflow visual baseline get failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/scheduled-actions/preview")
    async def workflow_scheduled_action_preview(data: ScheduledActionRequest):
        """Preview the workflow payload for a simple scheduled desktop action."""
        try:
            from distr.core.harness.scheduled_actions import compile_scheduled_action_workflow

            compiled = compile_scheduled_action_workflow(data.model_dump())
            return JSONResponse({"success": True, **compiled})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Scheduled action preview failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/scheduled-actions")
    async def workflow_scheduled_action_create(data: ScheduledActionRequest):
        """Create a scheduled workflow from a simple desktop action spec."""
        try:
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
            from distr.core.harness.scheduled_actions import compile_scheduled_action_workflow
            from distr.core.workflow.service import get_session

            compiled = compile_scheduled_action_workflow(data.model_dump())
            workflow_data = compiled["workflow"]
            step_data = compiled["steps"][0]
            next_run_at = _next_run_for_schedule(workflow_data)
            with get_session() as db:
                wf = AutoWorkflow(
                    name=workflow_data["name"],
                    description=workflow_data.get("description", ""),
                    status=workflow_data.get("status", "active"),
                    workflow_type=workflow_data.get("workflow_type", "scheduled"),
                    schedule_enabled=bool(workflow_data.get("schedule_enabled", True)),
                    schedule_preset=workflow_data.get("schedule_preset"),
                    schedule_time=workflow_data.get("schedule_time"),
                    schedule_days=workflow_data.get("schedule_days"),
                    schedule_timezone=workflow_data.get("schedule_timezone"),
                    next_run_at=next_run_at,
                )
                db.add(wf)
                db.flush()
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=int(step_data.get("position") or 0),
                    name=step_data.get("name") or workflow_data["name"],
                    action_type=step_data.get("action_type") or "computer_use",
                    step_type=step_data.get("step_type") or step_data.get("action_type") or "computer_use",
                    instruction=step_data.get("instruction") or "",
                    config=json.dumps(step_data.get("config") or {}),
                    validation_type=step_data.get("validation_type") or "none",
                    recording_filename=step_data.get("recording_filename"),
                )
                db.add(step)
                db.commit()
                workflow_id = int(wf.id)
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                **compiled,
            })
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Scheduled action create failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/scheduled-actions")
    async def workflow_scheduled_action_list(limit: int = 50):
        """List scheduled workflows through the simple scheduled-action API."""
        try:
            from distr.core.db.workflow import AutoWorkflow
            from distr.core.workflow.service import get_session

            with get_session() as db:
                rows = (
                    db.query(AutoWorkflow)
                    .filter(AutoWorkflow.workflow_type == "scheduled")
                    .order_by(AutoWorkflow.modified_date.desc())
                    .limit(max(1, min(int(limit or 50), 200)))
                    .all()
                )
                payload = [_scheduled_action_payload(wf) for wf in rows]
            return JSONResponse({"success": True, "scheduled_actions": payload})
        except Exception as e:
            logger.error("Scheduled action list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/scheduled-actions/by-title")
    async def workflow_scheduled_action_update_by_title(title: str, data: ScheduledActionUpdateRequest):
        """Update a scheduled action by title substring for voice-style management."""
        try:
            from distr.core.db.workflow import AutoWorkflow
            from distr.core.harness.scheduled_actions import compile_scheduled_action_workflow
            from distr.core.workflow.service import get_session

            title_query = (title or "").strip()
            if not title_query:
                return JSONResponse({"detail": "title is required"}, status_code=422)
            with get_session() as db:
                wf = (
                    db.query(AutoWorkflow)
                    .filter(AutoWorkflow.workflow_type == "scheduled")
                    .filter(AutoWorkflow.name.ilike(f"%{title_query}%"))
                    .order_by(AutoWorkflow.modified_date.desc(), AutoWorkflow.id.desc())
                    .first()
                )
                if not wf:
                    return JSONResponse({"detail": "Scheduled action not found"}, status_code=404)
                step = sorted(list(wf.steps or []), key=lambda s: s.position or 0)[0] if wf.steps else None
                existing = {
                    "title": wf.name or "Scheduled action",
                    "schedule": _schedule_from_workflow(wf),
                    "action": _action_from_step(step),
                    "target_context": data.target_context or {},
                    "safety": data.safety or {},
                }
                if data.title is not None:
                    existing["title"] = data.title
                if data.schedule is not None:
                    existing["schedule"] = data.schedule
                if data.action is not None:
                    existing["action"] = data.action
                if data.target_context is not None:
                    existing["target_context"] = data.target_context
                if data.safety is not None:
                    existing["safety"] = data.safety

                compiled = compile_scheduled_action_workflow(existing)
                workflow_data = compiled["workflow"]
                step_data = compiled["steps"][0]
                wf.name = workflow_data["name"]
                wf.description = workflow_data.get("description", "")
                wf.status = workflow_data.get("status", wf.status or "active")
                wf.schedule_enabled = bool(data.enabled) if data.enabled is not None else bool(workflow_data.get("schedule_enabled", True))
                wf.schedule_preset = workflow_data.get("schedule_preset")
                wf.schedule_time = workflow_data.get("schedule_time")
                wf.schedule_days = workflow_data.get("schedule_days")
                wf.schedule_timezone = workflow_data.get("schedule_timezone")
                wf.next_run_at = _next_run_for_schedule(workflow_data) if wf.schedule_enabled else None
                if step:
                    step.name = step_data.get("name") or wf.name
                    step.action_type = step_data.get("action_type") or "computer_use"
                    step.step_type = step_data.get("step_type") or step.action_type
                    step.instruction = step_data.get("instruction") or ""
                    step.config = json.dumps(step_data.get("config") or {})
                    step.validation_type = step_data.get("validation_type") or "none"
                    step.recording_filename = step_data.get("recording_filename")
                db.commit()
                db.refresh(wf)
                payload = _scheduled_action_payload(wf)
            return JSONResponse({"success": True, **compiled, "scheduled_action": payload})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Scheduled action update by title failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/scheduled-actions/by-title")
    async def workflow_scheduled_action_delete_by_title(title: str):
        """Cancel a scheduled action by title substring."""
        try:
            from distr.core.db.workflow import AutoWorkflow
            from distr.core.workflow.service import delete_workflow, get_session

            title_query = (title or "").strip()
            if not title_query:
                return JSONResponse({"detail": "title is required"}, status_code=422)
            with get_session() as db:
                wf = (
                    db.query(AutoWorkflow)
                    .filter(AutoWorkflow.workflow_type == "scheduled")
                    .filter(AutoWorkflow.name.ilike(f"%{title_query}%"))
                    .order_by(AutoWorkflow.modified_date.desc(), AutoWorkflow.id.desc())
                    .first()
                )
                if not wf:
                    return JSONResponse({"detail": "Scheduled action not found"}, status_code=404)
                workflow_id = int(wf.id)
                action_title = wf.name or "Scheduled action"
            if not delete_workflow(workflow_id):
                return JSONResponse({"detail": "Scheduled action not found"}, status_code=404)
            return JSONResponse({
                "success": True,
                "message": f"Scheduled action {action_title} cancelled.",
                "next_action": "Create a new scheduled action if this should run again.",
            })
        except Exception as e:
            logger.error("Scheduled action delete by title failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/scheduled-actions/{workflow_id}")
    async def workflow_scheduled_action_update(workflow_id: int, data: ScheduledActionUpdateRequest):
        """Update enablement, schedule, or action details for a scheduled workflow."""
        try:
            from distr.core.db.workflow import AutoWorkflow
            from distr.core.harness.scheduled_actions import compile_scheduled_action_workflow
            from distr.core.workflow.service import get_session

            with get_session() as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
                if not wf or wf.workflow_type != "scheduled":
                    return JSONResponse({"detail": "Scheduled action not found"}, status_code=404)
                step = sorted(list(wf.steps or []), key=lambda s: s.position or 0)[0] if wf.steps else None
                existing = {
                    "title": wf.name or "Scheduled action",
                    "schedule": _schedule_from_workflow(wf),
                    "action": _action_from_step(step),
                    "target_context": data.target_context or {},
                    "safety": data.safety or {},
                }
                if data.title is not None:
                    existing["title"] = data.title
                if data.schedule is not None:
                    existing["schedule"] = data.schedule
                if data.action is not None:
                    existing["action"] = data.action
                if data.target_context is not None:
                    existing["target_context"] = data.target_context
                if data.safety is not None:
                    existing["safety"] = data.safety

                compiled = compile_scheduled_action_workflow(existing)
                workflow_data = compiled["workflow"]
                step_data = compiled["steps"][0]
                wf.name = workflow_data["name"]
                wf.description = workflow_data.get("description", "")
                wf.status = workflow_data.get("status", wf.status or "active")
                if data.enabled is not None:
                    wf.schedule_enabled = bool(data.enabled)
                else:
                    wf.schedule_enabled = bool(workflow_data.get("schedule_enabled", True))
                wf.schedule_preset = workflow_data.get("schedule_preset")
                wf.schedule_time = workflow_data.get("schedule_time")
                wf.schedule_days = workflow_data.get("schedule_days")
                wf.schedule_timezone = workflow_data.get("schedule_timezone")
                wf.next_run_at = _next_run_for_schedule(workflow_data) if wf.schedule_enabled else None

                if step:
                    step.name = step_data.get("name") or wf.name
                    step.action_type = step_data.get("action_type") or "computer_use"
                    step.step_type = step_data.get("step_type") or step.action_type
                    step.instruction = step_data.get("instruction") or ""
                    step.config = json.dumps(step_data.get("config") or {})
                    step.validation_type = step_data.get("validation_type") or "none"
                    step.recording_filename = step_data.get("recording_filename")
                db.commit()
                db.refresh(wf)
                payload = _scheduled_action_payload(wf)
            return JSONResponse({"success": True, **compiled, "scheduled_action": payload})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Scheduled action update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/scheduled-actions/{workflow_id}")
    async def workflow_scheduled_action_delete(workflow_id: int):
        """Cancel a scheduled action by deleting its backing scheduled workflow."""
        try:
            from distr.core.db.workflow import AutoWorkflow
            from distr.core.workflow.service import delete_workflow, get_session

            with get_session() as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
                if not wf or wf.workflow_type != "scheduled":
                    return JSONResponse({"detail": "Scheduled action not found"}, status_code=404)
            if not delete_workflow(workflow_id):
                return JSONResponse({"detail": "Scheduled action not found"}, status_code=404)
            return JSONResponse({
                "success": True,
                "message": "Scheduled action cancelled.",
                "next_action": "Create a new scheduled action if this should run again.",
            })
        except Exception as e:
            logger.error("Scheduled action delete failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/purge-all")
    async def workflow_purge_all(data: WorkflowPurgeAllRequest):
        """Remove every workflow (except audit workflows unless include_audit is True)."""
        if not data.confirm:
            return JSONResponse(
                {"detail": "Set confirm=true in the JSON body to delete all workflows."},
                status_code=400,
            )
        try:
            from distr.core.workflow.service import purge_all_workflows
            from distr.gui.web.workflow_events import increment_workflow_updated

            removed = purge_all_workflows(include_audit=data.include_audit)
            increment_workflow_updated()
            return JSONResponse({"success": True, "removed": removed})
        except Exception as e:
            logger.error("Workflow purge-all failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # ── Plan / Version / Step-level endpoints (must be before {workflow_id} routes) ──

    @router.post("/workflows/plan")
    async def workflow_plan(data: WorkflowPlanRequest):
        """Plan a workflow from a natural-language instruction."""
        try:
            from distr.core.workflow.service import plan_workflow, get_workflow
            from distr.gui.web.workflow_events import increment_workflow_updated
            wf_id = plan_workflow(data.instruction, chat_id=data.chat_id, name=data.name)
            if not wf_id:
                return JSONResponse({"detail": "Failed to plan workflow"}, status_code=500)
            increment_workflow_updated()
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Workflow plan failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/project-context")
    async def workflow_project_context(workflow_id: int, board_id: Optional[int] = None):
        """Return active project, board, queue, and execution status for the ops harness."""
        try:
            from distr.core.workflow.project_ops import gather_project_ops_context

            return JSONResponse(gather_project_ops_context(workflow_id=workflow_id, board_id=board_id))
        except Exception as e:
            logger.error("Workflow project context failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/ops/plan")
    async def workflow_project_ops_plan(workflow_id: int, data: ProjectOpsPlanRequest):
        """Classify a project outcome instruction and return a short execution plan."""
        try:
            from distr.core.workflow.project_ops import (
                build_execution_plan,
                classify_project_instruction,
                gather_project_ops_context,
                suggest_skills_for_route,
            )

            context = gather_project_ops_context(workflow_id=workflow_id, board_id=data.board_id)
            classification = classify_project_instruction(data.instruction)
            route = classification.get("route") or "clarification"
            context["skills_hint"] = suggest_skills_for_route(route, data.instruction)
            plan = build_execution_plan(
                data.instruction,
                route=route,
                classification=classification,
                context=context,
            )
            return JSONResponse(plan)
        except Exception as e:
            logger.error("Workflow project ops plan failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/ops/execute")
    async def workflow_project_ops_execute(workflow_id: int, data: ProjectOpsExecuteRequest):
        """Execute an approved project operations plan."""
        if not data.approved:
            return JSONResponse({"detail": "Approval is required before execution."}, status_code=400)
        try:
            from distr.core.workflow.project_ops import execute_project_ops_plan

            result = execute_project_ops_plan(
                workflow_id=workflow_id,
                instruction=data.instruction,
                route=data.route,
                board_id=data.board_id,
                ticket_id=data.ticket_id,
            )
            status_code = 200
            if result.get("status") == "failed":
                status_code = 400
            elif result.get("status") == "needs_input":
                status_code = 422
            return JSONResponse(result, status_code=status_code)
        except Exception as e:
            logger.error("Workflow project ops execute failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/version")
    async def workflow_version():
        """Return a version counter that increments when workflow data changes. UI polls to refresh."""
        try:
            from distr.gui.web.workflow_events import get_workflow_update_counter
            return JSONResponse({"version": get_workflow_update_counter()})
        except Exception:
            return JSONResponse({"version": 0})

    @router.get("/workflows/events")
    async def get_workflow_events_since(since: int = 0):
        """Return workflow events logged after *since* version.

        Clients reconnecting after a WebSocket gap call this to check whether
        they missed updates, then refresh their state if the returned list
        is non-empty.
        """
        from distr.gui.web.workflow_events import get_events_since, get_workflow_update_counter
        events = get_events_since(since)
        return JSONResponse({
            "current_version": get_workflow_update_counter(),
            "missed": len(events),
            "events": events,
        })

    @router.get("/workflows/llm-settings")
    async def get_workflow_llm_settings():
        """Return the workflow engine's dedicated LLM provider and model."""
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        return JSONResponse({
            "provider": settings.get("workflow_llm_provider") or "",
            "model": settings.get("workflow_llm_model") or "",
        })

    @router.post("/workflows/llm-settings")
    async def save_workflow_llm_settings(request: Request):
        """Save the workflow engine's dedicated LLM provider and model."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db
        data = await request.json()
        settings = load_settings_from_db()
        provider = (data.get("provider") or "").strip()
        model = (data.get("model") or "").strip()
        settings["workflow_llm_provider"] = provider
        settings["workflow_llm_model"] = model
        save_settings_to_db(settings)
        return JSONResponse({"success": True})

    @router.get("/workflows/orchestrator-setup")
    async def get_orchestrator_setup():
        """Return Orchestrator readiness and ticket complexity routing for workflow onboarding."""
        from distr.core.settings import load_settings_from_db
        from distr.core.orchestrator import (
            ensure_orchestrator_tables,
            list_correction_attempts,
            list_events,
            list_project_runtime_sessions,
            list_validation_records,
        )
        from distr.core.project_cli_backends import get_backend_statuses

        settings = load_settings_from_db()
        ledger_ready = True
        ledger_error = ""
        try:
            ensure_orchestrator_tables()
        except Exception as exc:
            ledger_ready = False
            ledger_error = str(exc)

        routing = {}
        from distr.core.kanban.codex_prefs import normalize_codex_intelligence, normalize_codex_speed

        for level, default_backend, default_model in [
            ("low", "cursor", "auto"),
            ("medium", "codex", "auto"),
            ("high", "codex", "gpt-5.3-codex"),
        ]:
            backend = (settings.get(f"project_cli_{level}_backend") or default_backend).strip().lower()
            routing[level] = {
                "backend": backend,
                "model": (settings.get(f"project_cli_{level}_model") or default_model).strip(),
                "model_provider": (settings.get(f"project_cli_{level}_model_provider") or "").strip().lower(),
                "codex_intelligence": normalize_codex_intelligence(
                    settings.get(f"project_cli_{level}_codex_intelligence")
                ),
                "codex_speed": normalize_codex_speed(settings.get(f"project_cli_{level}_codex_speed")),
                "fallback_backend": (settings.get(f"project_cli_{level}_fallback_backend") or "").strip().lower(),
                "fallback_model": (settings.get(f"project_cli_{level}_fallback_model") or "").strip(),
            }

        try:
            accounts = json.loads(settings.get("connected_accounts") or "[]")
        except Exception:
            accounts = []
        connected_sources = sorted({
            (account.get("provider") or account.get("type") or account.get("service") or "").strip().lower()
            for account in accounts
            if isinstance(account, dict) and (account.get("provider") or account.get("type") or account.get("service"))
        })

        backends = get_backend_statuses(routing["medium"]["backend"])
        backend_rows = backends.get("backends") or []
        ready_backends = [
            row for row in backend_rows
            if row.get("id") in {routing[level]["backend"] for level in routing}
            and row.get("available", row.get("ready", False))
        ]

        readiness = [
            {
                "id": "ledger",
                "label": "Run ledger",
                "status": "ready" if ledger_ready else "blocked",
                "detail": "Event, runtime, validation, and correction tables are available." if ledger_ready else ledger_error,
            },
            {
                "id": "executor_routing",
                "label": "Complexity routing",
                "status": "ready" if all(routing[level]["backend"] and routing[level]["model"] for level in routing) else "needs_setup",
                "detail": "Low, medium, and high tickets have executor/model routes.",
            },
            {
                "id": "executors",
                "label": "Executor backends",
                "status": "ready" if ready_backends else "needs_setup",
                "detail": "Codex, Cursor, IDE, or other executors need to be installed and available.",
            },
        ]

        counts = {"events": 0, "runtime_sessions": 0, "validations": 0, "corrections": 0}
        if ledger_ready:
            try:
                counts = {
                    "events": len(list_events(limit=500)),
                    "runtime_sessions": len(list_project_runtime_sessions(limit=200)),
                    "validations": len(list_validation_records(limit=500)),
                    "corrections": len(list_correction_attempts(limit=500)),
                }
            except Exception:
                pass

        hermes_agent = next(
            (item for item in (backends.get("backends") or []) if item.get("id") == "hermes_agent"),
            None,
        )

        return JSONResponse({
            "enabled": bool(settings.get("orchestrator_enabled", True)),
            "memory_export_enabled": bool(settings.get("orchestrator_memory_export_enabled", False)),
            "routing": routing,
            "readiness": readiness,
            "counts": counts,
            "backends": backends,
            "connected_sources": connected_sources,
            "optional_backends": {
                "hermes_agent": {
                    "installed": bool(hermes_agent and hermes_agent.get("installed")),
                    "ready": bool(hermes_agent and hermes_agent.get("ready")),
                    "message": (hermes_agent or {}).get("message") or "",
                    "setup_command": "NONINTERACTIVE=1 bash scripts/setup_project_clis.sh hermes-agent",
                    "docs": "docs/nous-hermes-agent.md",
                    "required_for_orchestrator": False,
                }
            },
        })

    @router.post("/workflows/orchestrator-setup")
    async def save_orchestrator_setup(request: Request):
        """Save Orchestrator workflow onboarding settings."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db
        from distr.core.project_cli_backends import normalize_backend_id
        from distr.core.kanban.codex_prefs import normalize_codex_intelligence, normalize_codex_speed

        data = await request.json()
        settings = load_settings_from_db()
        settings["orchestrator_enabled"] = bool(data.get("enabled", True))
        if "memory_export_enabled" in data:
            settings["orchestrator_memory_export_enabled"] = bool(data.get("memory_export_enabled", False))

        models = data.get("models") or {}
        if "models" in data:
            from distr.core.orchestrator import ORCHESTRATOR_ROLE_SETTINGS_KEYS

            for role in ["orchestrator", "validator", "correction"]:
                row = models.get(role) or {}
                provider_key, model_key = ORCHESTRATOR_ROLE_SETTINGS_KEYS[role]
                settings[provider_key] = (row.get("provider") or "").strip()
                settings[model_key] = (row.get("model") or "").strip()

        routing = data.get("routing") or {}
        if "routing" in data:
            from distr.core.project_cli_backends.ide_handoff import is_ide_backend

            for level, default_backend, default_model in [
                ("low", "cursor", "auto"),
                ("medium", "codex", "auto"),
                ("high", "codex", "gpt-5.3-codex"),
            ]:
                row = routing.get(level) or {}
                settings[f"project_cli_{level}_backend"] = normalize_backend_id(row.get("backend") or default_backend)
                backend_id = settings[f"project_cli_{level}_backend"]
                if "model_provider" in row or "provider" in row:
                    settings[f"project_cli_{level}_model_provider"] = (
                        row.get("model_provider") or row.get("provider") or ""
                    ).strip().lower()
                fallback_backend = normalize_backend_id(row.get("fallback_backend") or "")
                fallback_model = (row.get("fallback_model") or "").strip()
                if is_ide_backend(backend_id):
                    settings[f"project_cli_{level}_model"] = ""
                    settings[f"project_cli_{level}_model_provider"] = ""
                    settings[f"project_cli_{level}_fallback_backend"] = (
                        fallback_backend if fallback_backend and not is_ide_backend(fallback_backend) else ""
                    )
                    settings[f"project_cli_{level}_fallback_model"] = (
                        fallback_model if settings[f"project_cli_{level}_fallback_backend"] else ""
                    )
                    settings.pop(f"project_cli_{level}_codex_intelligence", None)
                    settings.pop(f"project_cli_{level}_codex_speed", None)
                    continue
                settings[f"project_cli_{level}_model"] = (row.get("model") or default_model).strip()
                if backend_id == "codex":
                    settings[f"project_cli_{level}_model_provider"] = "openai"
                elif backend_id == "claude_code":
                    settings[f"project_cli_{level}_model_provider"] = "anthropic"
                settings[f"project_cli_{level}_fallback_backend"] = ""
                settings[f"project_cli_{level}_fallback_model"] = ""
                if backend_id == "codex":
                    settings[f"project_cli_{level}_codex_intelligence"] = normalize_codex_intelligence(
                        row.get("codex_intelligence") or row.get("codex_reasoning_effort")
                    )
                    settings[f"project_cli_{level}_codex_speed"] = normalize_codex_speed(
                        row.get("codex_speed") or row.get("codex_service_tier")
                    )
                else:
                    settings.pop(f"project_cli_{level}_codex_intelligence", None)
                    settings.pop(f"project_cli_{level}_codex_speed", None)

        # Keep the existing workflow LLM fallback aligned with orchestrator routing
        # so older code paths still resolve to the same brain.
        orchestrator = models.get("orchestrator") or {}
        if "models" in data and (orchestrator.get("provider") or orchestrator.get("model")):
            settings["workflow_llm_provider"] = (orchestrator.get("provider") or "").strip()
            settings["workflow_llm_model"] = (orchestrator.get("model") or "").strip()

        save_settings_to_db(settings)
        return JSONResponse({"success": True})

    @router.get("/workflows/actions/catalog")
    async def get_workflow_actions_catalog():
        """Return saved Decisions Actions usable by workflow/orchestrator steps."""
        from distr.core.db import get_session, Action
        from sqlalchemy import desc, nulls_last
        with get_session() as session:
            actions = session.query(Action).order_by(
                nulls_last(desc(Action.last_run_date)),
                desc(Action.modified_date),
            ).all()
            rows = []
            for action in actions:
                is_instruction = bool(action.is_instruction) if action.is_instruction is not None else False
                recording_filename = action.recording_filename or ""
                instruction_text = action.instruction_text or ""
                mode = "instruction" if is_instruction else "recording"
                usable = bool(instruction_text.strip()) if is_instruction else bool(recording_filename.strip())
                rows.append({
                    "id": action.id,
                    "title": action.title or f"Action #{action.id}",
                    "description": action.description or "",
                    "mode": mode,
                    "is_instruction": is_instruction,
                    "recording_filename": recording_filename,
                    "has_recording": bool(recording_filename.strip()),
                    "has_instruction": bool(instruction_text.strip()),
                    "usable": usable,
                    "last_run_date": action.last_run_date.isoformat() if action.last_run_date else None,
                })
            return JSONResponse(rows)

    @router.post("/workflows/seed-fixtures")
    async def seed_workflow_fixtures(data: WorkflowSeedFixturesRequest):
        """Seed workflow fixtures. Optional force reset for dynamic template updates."""
        try:
            from distr.core.db.seed_workflows import seed_workflows
            result = seed_workflows(force_reset=data.force_reset, workflow_names=data.workflow_names)
            return JSONResponse({"success": True, **result})
        except Exception as e:
            logger.error("Workflow fixture seed failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/steps/{step_id}/generate-code")
    async def workflow_generate_step_code(step_id: int, data: WorkflowGenerateCodeRequest):
        """Generate code for a step from a natural-language instruction."""
        try:
            from distr.core.workflow.service import generate_step_code
            code = generate_step_code(step_id, data.instruction, data.step_type)
            return JSONResponse({"code": code})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        except RuntimeError as e:
            logger.error("Workflow generate-code LLM error: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)
        except Exception as e:
            logger.error("Workflow generate-code failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/steps/{step_id}/test-code")
    async def workflow_test_step_code(step_id: int, data: WorkflowTestCodeRequest):
        """Execute code in an isolated subprocess with auto-fix loop."""
        try:
            from distr.core.workflow.service import test_step_code
            result = test_step_code(step_id, data.code, data.step_type, headless=data.headless)
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        except Exception as e:
            logger.error("Workflow test-code failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/steps/{step_id}/validate")
    async def workflow_validate_step(step_id: int, data: WorkflowValidateStepRequest):
        """Validate step configuration against type-specific rules."""
        try:
            from distr.core.workflow.service import validate_step_config
            errors = validate_step_config(data.step_type, data.config)
            if errors:
                return JSONResponse({"errors": errors}, status_code=422)
            return JSONResponse({"valid": True})
        except Exception as e:
            logger.error("Workflow validate step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # ── Presets / Export / Import (must be before {workflow_id} routes) ──

    @router.get("/workflows/presets")
    async def workflow_list_presets():
        try:
            from distr.core.workflow.service import list_presets
            return JSONResponse(list_presets())
        except Exception as e:
            logger.error("List presets failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/loop-presets")
    async def workflow_loop_presets():
        from distr.core.workflow.loop_presets import list_loop_presets

        return JSONResponse({"presets": list_loop_presets()})

    @router.post("/workflows/presets/{filename}/load")
    async def workflow_load_preset(filename: str):
        try:
            from distr.core.workflow.service import load_preset
            wf_id = load_preset(filename)
            if not wf_id:
                return JSONResponse({"detail": "Preset not found"}, status_code=404)
            from distr.core.workflow.service import get_workflow
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Load preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/import")
    async def workflow_import(file: UploadFile = File(...)):
        """Import a .dwf bundle or .json file."""
        try:
            raw = await file.read()
            fname = file.filename or ""
            if fname.endswith(".dwf"):
                from distr.core.workflow.service import import_workflow_bundle, get_workflow
                wf_id = import_workflow_bundle(raw)
            else:
                from distr.core.workflow.service import import_workflow, get_workflow
                data = json.loads(raw)
                wf_id = import_workflow(data)
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Workflow import failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/generate")
    async def workflow_generate(data: WorkflowGenerateRequest):
        """Generate a workflow from a natural-language description using the coding LLM."""
        try:
            from distr.core.workflow_engine.code_generator import CodeGeneratorService
            from distr.core.workflow.service import import_workflow

            prompt = (
                "You are a workflow generator. Given the user's description, produce a JSON object "
                "representing a workflow compatible with the following schema:\n"
                "{\n"
                '  "name": "Workflow Name",\n'
                '  "description": "...",\n'
                '  "steps": [\n'
                "    {\n"
                '      "position": 0,\n'
                '      "name": "Step 1",\n'
                '      "action_type": "agent_instruction",\n'
                '      "instruction": "...",\n'
                '      "validation_type": "none",\n'
                '      "validation_prompt": "",\n'
                '      "routing_mode": "static",\n'
                '      "on_pass_goto_position": 1,\n'
                '      "on_fail_goto_position": null,\n'
                '      "wait_for_continue": false\n'
                "    }\n"
                "  ],\n"
                '  "context_rules": ""\n'
                "}\n\n"
                "Valid action_type values and when to use them:\n"
                '- "agent_instruction" — general-purpose desktop/UI automation (default for most tasks)\n'
                '- "playwright" — browser automation: navigate, login, fill forms, click, scrape, screenshot\n'
                '- "computer_use" — local vision-action loop for mechanical GUI tasks when browser automation is unavailable\n'
                '- "execute_code" — run a Python script (data processing, file I/O, computation)\n'
                '- "run_command" — execute a shell command (mkdir, cp, ls, app launch)\n'
                '- "http_request" — make an HTTP request (GET, POST, PUT, DELETE)\n'
                '- "play_recording" — replay a previously recorded macro\n\n'
                "Rules:\n"
                "- Use \"playwright\" for all web browser tasks.\n"
                "- Use \"computer_use\" for repetitive local GUI/screen-control tasks that need screenshots and sidecar actions.\n"
                "- Use \"agent_instruction\" for desktop app tasks and general automation.\n"
                "- Use \"execute_code\" for data/file processing.\n"
                "- The last step's on_pass_goto_position should be null (end workflow).\n"
                "- Return ONLY valid JSON, no markdown fences or explanations.\n\n"
                f"User description:\n{data.description}"
            )

            svc = CodeGeneratorService()
            raw_response = svc._call_coding_llm(prompt)

            # Strip markdown fences if present
            cleaned = raw_response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[\w]*\s*\n?", "", cleaned)
                cleaned = re.sub(r"\n?```\s*$", "", cleaned)
                cleaned = cleaned.strip()

            try:
                workflow_data = json.loads(cleaned)
            except json.JSONDecodeError as je:
                return JSONResponse(
                    {"detail": f"Failed to parse generated workflow JSON: {je}"},
                    status_code=422,
                )

            wf_id = import_workflow(workflow_data)
            return JSONResponse({"id": wf_id})
        except Exception as e:
            logger.error("Workflow generation failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/active-runs")
    async def workflow_active_runs(limit: int = 50, workflow_id: Optional[int] = None):
        try:
            from distr.core.workflow.service import get_active_runs
            rows = await asyncio.to_thread(get_active_runs, limit=limit, workflow_id=workflow_id)
            return JSONResponse(rows)
        except Exception as e:
            logger.error("Workflow active runs failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/intake/inbox")
    async def workflow_intake_inbox(limit: int = 40):
        """Mission Control inbox for channel WorkIntake decisions."""
        try:
            from distr.core.work_intake import get_work_intake_service

            items = await asyncio.to_thread(get_work_intake_service().list_inbox, limit=limit)
            return JSONResponse({"items": items})
        except Exception as e:
            logger.error("Workflow intake inbox failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/blueprint/checklist")
    async def workflow_blueprint_checklist():
        """Return the durable agent-system blueprint adherence checklist."""
        try:
            from distr.core.workflow.blueprint_adherence import checklist_snapshot

            return JSONResponse({"success": True, **checklist_snapshot()})
        except Exception as e:
            logger.error("Workflow blueprint checklist failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/blueprint/evals")
    async def workflow_blueprint_evals():
        """Run the standing outcome eval pack for Development."""
        try:
            from distr.core.workflow.blueprint_eval_pack import run_blueprint_eval_pack

            return JSONResponse({"success": True, **await asyncio.to_thread(run_blueprint_eval_pack)})
        except Exception as e:
            logger.error("Workflow blueprint evals failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/intake/inbox/{event_id}/action")
    async def workflow_intake_inbox_action(event_id: int, request: Request):
        """Continue / stop / steer / push / dismiss an inbox item."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = str((body or {}).get("action") or "").strip()
        message = str((body or {}).get("message") or "").strip()
        try:
            from distr.core.work_intake import get_work_intake_service

            result = await asyncio.to_thread(
                get_work_intake_service().resolve_inbox_item,
                int(event_id),
                action=action,
                message=message,
            )
            status = 200 if result.get("success") else 400
            return JSONResponse(result, status_code=status)
        except Exception as e:
            logger.error("Workflow intake inbox action failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.post("/workflows/intake/ingest")
    async def workflow_intake_ingest(request: Request):
        """Web Mission Control entry into the shared WorkIntake pipeline."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            from distr.core.work_intake import WorkIntake, get_work_intake_service

            payload = dict(body or {})
            payload.setdefault("source", "web")
            decision = await asyncio.to_thread(
                get_work_intake_service().ingest,
                WorkIntake.from_payload(payload),
            )
            return JSONResponse({"success": True, "decision": decision.to_dict()})
        except Exception as e:
            logger.error("Workflow intake ingest failed: %s", e, exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}")
    async def workflow_get(workflow_id: int):
        try:
            from distr.core.workflow.service import get_workflow
            data = await asyncio.to_thread(get_workflow, workflow_id)
            if not data:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse(data)
        except Exception as e:
            logger.error("Workflow get failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}")
    async def workflow_update(workflow_id: int, data: WorkflowUpdateRequest):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import update_workflow
            updates = {k: v for k, v in data.dict().items() if v is not None}
            if "run_settings" in updates:
                updates["run_settings"] = json.dumps(updates["run_settings"])
            if not update_workflow(workflow_id, **updates):
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"success": True})
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Workflow update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}")
    async def workflow_delete(workflow_id: int):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import delete_workflow
            if not delete_workflow(workflow_id):
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow delete failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/duplicate")
    async def workflow_duplicate(workflow_id: int):
        try:
            from distr.core.workflow.service import duplicate_workflow, get_workflow
            new_id = duplicate_workflow(workflow_id)
            if not new_id:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse(get_workflow(new_id))
        except Exception as e:
            logger.error("Workflow duplicate failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/generate-steps")
    async def workflow_generate_steps(workflow_id: int, data: WorkflowGenerateStepsRequest):
        """Generate steps for an existing workflow using the LLM planner."""
        try:
            from distr.core.workflow.service import generate_steps
            from distr.gui.web.workflow_events import increment_workflow_updated
            steps = generate_steps(workflow_id, data.instruction)
            increment_workflow_updated()
            return JSONResponse({"steps": steps})
        except Exception as e:
            logger.error("Workflow generate-steps failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/schedule")
    async def workflow_update_schedule(workflow_id: int, data: WorkflowScheduleUpdate):
        """Update a workflow's schedule configuration."""
        try:
            from distr.core.workflow.service import update_workflow
            updates = {}
            if data.enabled is not None:
                updates["schedule_enabled"] = data.enabled
            if data.schedule is not None:
                updates["schedule_preset"] = data.schedule
            if data.schedule_time is not None:
                updates["schedule_time"] = data.schedule_time
            if data.schedule_days is not None:
                updates["schedule_days"] = data.schedule_days
            if data.timezone is not None:
                updates["schedule_timezone"] = data.timezone
            if not update_workflow(workflow_id, **updates):
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow update schedule failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/runs")
    async def workflow_runs(workflow_id: int, limit: int = 10):
        try:
            from distr.core.workflow.service import get_run_history
            return JSONResponse(get_run_history(workflow_id, limit=limit))
        except Exception as e:
            logger.error("Workflow runs failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/orchestrator-events")
    async def workflow_orchestrator_events(
        workflow_id: int,
        limit: int = 100,
        ticket_id: Optional[int] = None,
        run_id: Optional[int] = None,
        board_id: Optional[int] = None,
    ):
        try:
            from distr.core.orchestrator import list_events

            return JSONResponse(list_events(
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                run_id=run_id,
                board_id=board_id,
                limit=limit,
            ))
        except Exception as e:
            logger.error("Workflow orchestrator events failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/validations")
    async def workflow_validations(workflow_id: int, limit: int = 100, ticket_id: Optional[int] = None, run_id: Optional[int] = None, verdict: Optional[str] = None):
        try:
            from distr.core.orchestrator import list_validation_records

            return JSONResponse(list_validation_records(
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                run_id=run_id,
                verdict=verdict,
                limit=limit,
            ))
        except Exception as e:
            logger.error("Workflow validation records failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/corrections")
    async def workflow_corrections(workflow_id: int, limit: int = 100, ticket_id: Optional[int] = None, run_id: Optional[int] = None, validation_record_id: Optional[int] = None, status: Optional[str] = None):
        try:
            from distr.core.orchestrator import list_correction_attempts

            return JSONResponse(list_correction_attempts(
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                run_id=run_id,
                validation_record_id=validation_record_id,
                status=status,
                limit=limit,
            ))
        except Exception as e:
            logger.error("Workflow correction attempts failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/runs")
    async def workflow_clear_runs(workflow_id: int):
        """Clear this workflow's completed run history without touching other logs."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import clear_workflow_history
            result = clear_workflow_history(workflow_id)
            if "error" in result:
                return JSONResponse(_workflow_error_payload(result["error"], "clear_audit"), status_code=404)
            result.update(_workflow_feedback_message("clear_audit", result))
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow clear runs failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "clear_audit"), status_code=500)

    @router.delete("/workflows/{workflow_id}/runs/{run_id}")
    async def workflow_delete_run(workflow_id: int, run_id: int):
        """Delete one inactive workflow run and the logs scoped to that run."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.db import get_session
            from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
            from distr.core.db.orchestrator import (
                OrchestratorCorrectionAttempt,
                OrchestratorEvent,
                OrchestratorValidationRecord,
            )
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStepResult
            from distr.gui.web.workflow_events import increment_workflow_updated

            with get_session() as db:
                run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                    .filter(AutoWorkflowRun.id == int(run_id))
                    .first()
                )
                if not run:
                    return JSONResponse(_workflow_error_payload("Run not found", "delete_run"), status_code=404)
                status = str(run.status or "").strip().lower()
                if status in {"queued", "running", "waiting"}:
                    return JSONResponse(
                        {
                            "detail": "Active workflow runs cannot be deleted.",
                            "action": "delete_run",
                            "next_action": "Cancel or complete the run before deleting its history.",
                            "workflow_id": workflow_id,
                            "run_id": run_id,
                            "status": status,
                        },
                        status_code=409,
                    )

                session_rows = (
                    db.query(ProjectExecutionSession.id)
                    .filter(ProjectExecutionSession.workflow_id == int(workflow_id))
                    .filter(ProjectExecutionSession.run_id == int(run_id))
                    .all()
                )
                session_ids = [int(row[0]) for row in session_rows]
                deleted_executor_events = 0
                deleted_executor_sessions = 0
                if session_ids:
                    deleted_executor_events = (
                        db.query(ProjectExecutionEvent)
                        .filter(ProjectExecutionEvent.session_id.in_(session_ids))
                        .delete(synchronize_session=False)
                    )
                    deleted_executor_sessions = (
                        db.query(ProjectExecutionSession)
                        .filter(ProjectExecutionSession.id.in_(session_ids))
                        .delete(synchronize_session=False)
                    )
                deleted_corrections = (
                    db.query(OrchestratorCorrectionAttempt)
                    .filter(OrchestratorCorrectionAttempt.workflow_id == int(workflow_id))
                    .filter(OrchestratorCorrectionAttempt.run_id == int(run_id))
                    .delete(synchronize_session=False)
                )
                deleted_validations = (
                    db.query(OrchestratorValidationRecord)
                    .filter(OrchestratorValidationRecord.workflow_id == int(workflow_id))
                    .filter(OrchestratorValidationRecord.run_id == int(run_id))
                    .delete(synchronize_session=False)
                )
                deleted_orchestrator_events = (
                    db.query(OrchestratorEvent)
                    .filter(OrchestratorEvent.workflow_id == int(workflow_id))
                    .filter(OrchestratorEvent.run_id == int(run_id))
                    .delete(synchronize_session=False)
                )
                deleted_step_results = (
                    db.query(AutoWorkflowStepResult)
                    .filter(AutoWorkflowStepResult.run_id == int(run_id))
                    .delete(synchronize_session=False)
                )
                db.delete(run)
                db.commit()

            increment_workflow_updated()
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "deleted_run": run_id,
                "deleted_executor_sessions": deleted_executor_sessions,
                "deleted_executor_events": deleted_executor_events,
                "deleted_orchestrator_events": deleted_orchestrator_events,
                "deleted_validations": deleted_validations,
                "deleted_corrections": deleted_corrections,
                "deleted_step_results": deleted_step_results,
                "message": "Workflow run history deleted.",
                "next_action": "Open Active Runs or run history to continue with remaining runs.",
            })
        except Exception as e:
            logger.error("Workflow delete run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "delete_run"), status_code=500)

    @router.delete("/workflows/{workflow_id}/events")
    async def workflow_clear_events(workflow_id: int):
        """Clear orchestration events for this workflow only."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.db import get_session
            from distr.core.db.orchestrator import OrchestratorEvent
            from distr.core.db.workflow import AutoWorkflow
            from distr.gui.web.workflow_events import increment_workflow_updated

            with get_session() as db:
                wf = db.query(AutoWorkflow.id).filter(AutoWorkflow.id == workflow_id).first()
                if not wf:
                    return JSONResponse(_workflow_error_payload("Workflow not found", "clear_events"), status_code=404)
                deleted_events = (
                    db.query(OrchestratorEvent)
                    .filter(OrchestratorEvent.workflow_id == workflow_id)
                    .delete(synchronize_session=False)
                )
                db.commit()
            increment_workflow_updated()
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "deleted_events": deleted_events,
                **_workflow_feedback_message("clear_events", {"deleted_events": deleted_events}),
            })
        except Exception as e:
            logger.error("Workflow clear events failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "clear_events"), status_code=500)

    @router.delete("/workflows/{workflow_id}/executor-sessions")
    async def workflow_clear_executor_sessions(workflow_id: int):
        """Clear CLI/IDE execution sessions for this workflow only."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.db import get_session
            from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
            from distr.core.db.workflow import AutoWorkflow
            from distr.gui.web.workflow_events import increment_workflow_updated

            with get_session() as db:
                wf = db.query(AutoWorkflow.id).filter(AutoWorkflow.id == workflow_id).first()
                if not wf:
                    return JSONResponse(_workflow_error_payload("Workflow not found", "clear_executor"), status_code=404)
                session_ids = [
                    row[0]
                    for row in (
                        db.query(ProjectExecutionSession.id)
                        .filter(ProjectExecutionSession.workflow_id == workflow_id)
                        .all()
                    )
                ]
                deleted_events = 0
                deleted_sessions = 0
                if session_ids:
                    deleted_events = (
                        db.query(ProjectExecutionEvent)
                        .filter(ProjectExecutionEvent.session_id.in_(session_ids))
                        .delete(synchronize_session=False)
                    )
                    deleted_sessions = (
                        db.query(ProjectExecutionSession)
                        .filter(ProjectExecutionSession.id.in_(session_ids))
                        .delete(synchronize_session=False)
                    )
                db.commit()
            increment_workflow_updated()
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "deleted_sessions": deleted_sessions,
                "deleted_events": deleted_events,
                **_workflow_feedback_message("clear_executor", {"deleted_sessions": deleted_sessions}),
            })
        except Exception as e:
            logger.error("Workflow clear executor sessions failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "clear_executor"), status_code=500)

    # Execution
    @router.post("/workflows/{workflow_id}/run-ticket-group")
    async def workflow_run_ticket_group(workflow_id: int, data: WorkflowTicketGroupRunRequest):
        """Start the explicit queued-ticket selection using sequential/parallel policy."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            ticket_ids = list(dict.fromkeys(int(ticket_id) for ticket_id in data.ticket_ids))
            if not ticket_ids:
                return JSONResponse({"detail": "Select at least one ticket"}, status_code=422)
            if len(ticket_ids) > 100:
                return JSONResponse({"detail": "A ticket group may contain at most 100 tickets"}, status_code=422)

            from distr.core.workflow.dispatcher import start_workflow_ticket_group
            from distr.core.workflow.ticket_dispatch import compact_ticket_run_ref

            ticket_items = [compact_ticket_run_ref({"ticket_id": ticket_id}) for ticket_id in ticket_ids]
            # WorkflowAgent construction may lazily import and warm many optional
            # tools. Keep that cold-start work off uvicorn's event loop so status,
            # heartbeat, and cancellation requests remain responsive.
            result = await asyncio.to_thread(
                start_workflow_ticket_group,
                workflow_id,
                ticket_items,
                dispatch_async=True,
            )
            if result.get("error"):
                return JSONResponse(_workflow_error_payload(result["error"], "run_group"), status_code=400)
            result.update({
                "message": (
                    f"Started {len(result.get('started') or [])} ticket run(s); "
                    f"{int(result.get('queued_count') or 0)} queued in the selected group."
                ),
                "next_action": "Watch the existing Runs or Loop view for step progress.",
            })
            return JSONResponse(result)
        except ValueError as exc:
            return JSONResponse(_workflow_error_payload(str(exc), "run_group"), status_code=422)
        except Exception as exc:
            logger.error("Workflow ticket-group run failed: %s", exc, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(exc), "run_group"), status_code=500)

    @router.post("/workflows/{workflow_id}/run")
    async def workflow_run(workflow_id: int, request: Request):
        """Start a workflow run. Accepts optional { "start_step_id": int }."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.dispatcher import start_workflow_run
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            start_step_id = body.get("start_step_id") if isinstance(body, dict) else None
            result = await asyncio.to_thread(
                start_workflow_run,
                workflow_id,
                start_step_id=start_step_id,
            )
            if "error" in result:
                return JSONResponse(_workflow_error_payload(result["error"], "run"), status_code=400)
            result.update(_workflow_feedback_message("run_started", result))
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "run"), status_code=500)

    @router.post("/workflows/{workflow_id}/cancel-run/{run_id}")
    async def workflow_cancel_run(workflow_id: int, run_id: int):
        try:
            try:
                from distr.core.signals import signal_manager

                signal_manager.interrupt_tts.emit()
                signal_manager.player_stop.emit()
            except Exception:
                pass
            from distr.core.workflow.dispatcher import cancel_run
            if not cancel_run(run_id):
                return JSONResponse(_workflow_error_payload("Run not found", "cancel"), status_code=404)
            return JSONResponse({"success": True, **_workflow_feedback_message("cancelled")})
        except Exception as e:
            logger.error("Workflow cancel run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "cancel"), status_code=500)

    @router.post("/workflows/{workflow_id}/stop-reset")
    async def workflow_stop_reset(workflow_id: int):
        """Cancel active run (if any) and reset all step statuses/results."""
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import reset_workflow_steps
            result = reset_workflow_steps(workflow_id)
            if "error" in result:
                return JSONResponse(_workflow_error_payload(result["error"], "reset"), status_code=404)
            result.update(_workflow_feedback_message("reset", result))
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow stop-reset failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "reset"), status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/route-approval")
    async def workflow_route_approval(workflow_id: int, run_id: int, request: Request):
        """Approve or reject a pending orchestrator route override for a waiting run."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        approved = bool(body.get("approved"))
        try:
            from distr.core.workflow.service import apply_run_route_approval

            result = apply_run_route_approval(run_id, approved=approved)
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"]},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow route approval failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/provider-model-selection")
    async def workflow_provider_model_selection(workflow_id: int, run_id: int, request: Request):
        """Readiness-check a selected free model before retrying a waiting step."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            candidate_index = int(body.get("candidate_index", 0))
        except (TypeError, ValueError):
            return JSONResponse({"detail": "candidate_index must be an integer"}, status_code=400)
        try:
            from distr.core.workflow.service import apply_run_provider_model_selection

            result = apply_run_provider_model_selection(run_id, candidate_index)
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"]},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow provider model selection failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/steer")
    async def workflow_harness_steer(workflow_id: int, run_id: int, request: Request):
        """Steer the active harness mid-flight without restarting the workflow step."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = ""
        if isinstance(body, dict):
            message = str(body.get("message") or body.get("input") or body.get("instruction") or "")
        try:
            from distr.core.workflow.service import apply_run_harness_steer

            result = apply_run_harness_steer(run_id, message)
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"]},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow harness steer failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/ui-feedback")
    async def workflow_ui_feedback(workflow_id: int, run_id: int, payload: UiFeedbackRequest):
        """Record the user's UI approval/rejection label for a run outcome."""
        try:
            from distr.core.orchestrator import (
                get_visual_baseline_set,
                inspect_visual_baseline_readiness,
                record_ui_feedback_label,
                upsert_visual_baseline_screens,
            )

            event_id = record_ui_feedback_label(
                label=payload.label,
                reason=payload.reason,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=payload.step_id,
                ticket_id=payload.ticket_id,
                board_id=payload.board_id,
                project_id=payload.project_id,
                execution_session_id=payload.execution_session_id,
                screenshot_paths=payload.screenshot_paths or [],
            )
            response = {
                "success": True,
                "event_id": event_id,
                "message": "UI feedback recorded.",
                "next_action": "Refresh the workflow timeline or learned rules to see the new signal.",
            }
            if payload.save_as_visual_baseline:
                if str(payload.label or "").strip().lower() != "approved":
                    return JSONResponse(
                        {"detail": "Only approved UI feedback can be accepted as a visual baseline."},
                        status_code=422,
                    )
                screenshot_path = next((str(path or "").strip() for path in (payload.screenshot_paths or []) if str(path or "").strip()), "")
                if not screenshot_path:
                    return JSONResponse({"detail": "At least one screenshot path is required to save a visual baseline."}, status_code=422)
                baseline_name = (payload.visual_baseline_name or "").strip() or f"Approved workflow {workflow_id}"
                screen_name = (payload.baseline_screen_name or "").strip() or f"Run {run_id}"
                baseline_id = upsert_visual_baseline_screens(
                    name=baseline_name,
                    board_id=payload.board_id,
                    project_id=payload.project_id,
                    description=f"Accepted from workflow {workflow_id} run {run_id} approval.",
                    screens=[{
                        "screen_name": screen_name,
                        "screenshot_path": screenshot_path,
                        "notes": payload.reason or "Approved UI screenshot.",
                        "metadata": {
                            "workflow_id": workflow_id,
                            "run_id": run_id,
                            "feedback_event_id": event_id,
                        },
                    }],
                    copy_screenshots=True,
                )
                response["visual_baseline"] = get_visual_baseline_set(baseline_set_id=baseline_id)
                response["message"] = "UI feedback recorded and visual baseline saved."
                readiness = inspect_visual_baseline_readiness(baseline_set_id=baseline_id)
                response["visual_baseline_readiness"] = readiness
                response["next_action"] = (
                    "Visual baseline is ready for UI validation."
                    if readiness.get("ready")
                    else "Visual baseline saved, but reference screenshot readiness failed."
                )
            return JSONResponse(response)
        except Exception as e:
            logger.error("Workflow UI feedback failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/continue")
    async def workflow_continue_run(workflow_id: int, run_id: int, request: Request):
        """Resume a waiting workflow run.

        Accepts optional JSON body. Preferred field is ``input``, but callback-style
        payloads using ``response``, ``message``, ``result``, or ``output`` are
        also accepted for compatibility.
        """
        try:
            from distr.core.workflow.dispatcher import continue_waiting_step
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            optional_input = ""
            if isinstance(body, dict):
                optional_input = (
                    body.get("input")
                    or body.get("response")
                    or body.get("message")
                    or body.get("result")
                    or body.get("output")
                    or ""
                )
            optional_input = str(optional_input or "")
            result = continue_waiting_step(run_id, optional_input)
            if "error" in result:
                status_code = result.get("status_code", 400)
                return JSONResponse(_workflow_error_payload(result["error"], "continue"), status_code=status_code)
            result.update(_workflow_feedback_message("continued", result))
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow continue run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "continue"), status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/reissue-interaction")
    async def workflow_reissue_interaction(workflow_id: int, run_id: int):
        """Re-send the same durable Telegram question and controls for a waiting run."""
        try:
            from distr.core.workflow.interactions import reissue_workflow_interaction

            result = reissue_workflow_interaction(run_id, workflow_id=workflow_id)
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"]},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow interaction reissue failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/codex-events")
    async def workflow_codex_bridge_event(workflow_id: int, run_id: int, event: CodexBridgeEventRequest):
        """Record Codex IDE/plugin steering and execution events into Decisions/orchestrator.

        This endpoint is intentionally not limited to waiting runs. Codex may report
        mid-run steering, interruption, progress, or completion while the workflow is
        still running, waiting, or already terminal.
        """
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
            from distr.core.kanban.project_execution import append_execution_event
            from distr.core.orchestrator import record_human_intervention_memory
            from distr.core.orchestration_events import (
                emit_orchestration_event,
                normalize_orchestration_event_type,
            )
            from distr.gui.web.workflow_events import increment_workflow_updated

            event_type = (event.event_type or "codex_event").strip() or "codex_event"
            status = (event.status or "").strip() or None
            message = (event.message or event.input or event.output or "").strip()
            payload = dict(event.payload or {})
            if event.input:
                payload["input"] = event.input
            if event.output:
                payload["output"] = event.output
            if event.mistake_label:
                payload["mistake_label"] = event.mistake_label
            payload.setdefault("bridge", "codex")

            with get_session() as db:
                run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.id == int(run_id))
                    .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                    .first()
                )
                if not run:
                    return JSONResponse(_workflow_error_payload("Run not found", "codex_event"), status_code=404)
                run_data = {}
                try:
                    run_data = json.loads(run.run_data or "{}") or {}
                except Exception:
                    run_data = {}
                step_id = event.step_id or run.current_step_id
                ticket_id = event.ticket_id or run.ticket_id
                board_id = run.board_id
                project_id = event.project_id or run_data.get("project_id")
                execution_session_id = event.execution_session_id or payload.get("execution_session_id")
                latest_handoff = (
                    run_data.get("latest_backend_handoff")
                    if isinstance(run_data.get("latest_backend_handoff"), dict)
                    else {}
                )
                lower_event_type = event_type.lower().replace("-", "_").replace(" ", "_")
                bridge_suffix = lower_event_type
                for prefix in ("cursor_", "codex_", "worker_"):
                    if bridge_suffix.startswith(prefix):
                        bridge_suffix = bridge_suffix[len(prefix):]
                        break
                run_status_before = run.status
                waiting_kind_before = str(run_data.get("waiting_kind") or "").strip()
                needs_human = bridge_suffix in {
                    "needs_input",
                    "waiting",
                    "interrupted",
                } or lower_event_type in {
                    "needs_input",
                    "worker_needs_input",
                    "codex_needs_input",
                    "cursor_needs_input",
                    "codex_waiting",
                    "cursor_waiting",
                    "human_takeover",
                    "manual_fix",
                    "changes_requested",
                }
                worker_terminal = bridge_suffix in {
                    "completed",
                    "failed",
                } or lower_event_type in {
                    "completed",
                    "worker_completed",
                    "codex_completed",
                    "cursor_completed",
                    "failed",
                    "worker_failed",
                    "codex_failed",
                    "cursor_failed",
                }
                needs_input_context = {}
                worker_question_spoken = ""
                if needs_human:
                    needs_input_context, worker_question_spoken = _needs_input_context_and_spoken(
                        db,
                        workflow_id=workflow_id,
                        run=run,
                        step_id=int(step_id) if step_id else None,
                        project_id=int(project_id) if str(project_id or "").isdigit() else None,
                        message=message,
                        payload=payload,
                    )

                history = run_data.get("codex_bridge_events") or []
                history.append({
                    "event_type": event_type,
                    "status": status,
                    "message": message,
                    "input": event.input or "",
                    "output": event.output or "",
                    "step_id": step_id,
                    "ticket_id": ticket_id,
                    "project_id": project_id,
                    "execution_session_id": execution_session_id,
                    "human_intervention_state": "needs_human_input" if needs_human else run_data.get("human_intervention_state", "none"),
                    "ts": time.time(),
                })
                run_data["codex_bridge_events"] = history[-50:]
                live_context = run_data.get("live_agent_context") if isinstance(run_data.get("live_agent_context"), dict) else {}
                live_context.update({
                    "last_event_type": event_type,
                    "last_status": status,
                    "last_message": message,
                    "last_input": event.input or live_context.get("last_input", ""),
                    "last_output": event.output or live_context.get("last_output", ""),
                    "step_id": step_id,
                    "ticket_id": ticket_id,
                    "project_id": project_id,
                    "execution_session_id": execution_session_id,
                    "updated_at": history[-1]["ts"],
                    "recent_events": history[-10:],
                })
                if event_type == "user_steer" and message:
                    live_context["latest_user_steer"] = message
                if event_type in {"codex_completed", "codex_failed", "cursor_completed", "cursor_failed"} and message:
                    live_context["latest_terminal_summary"] = message
                run_data["live_agent_context"] = live_context
                if event_type in {"user_steer", "codex_interrupted", "codex_waiting", "codex_needs_input", "cursor_interrupted", "cursor_waiting", "cursor_needs_input"}:
                    run_data["last_codex_bridge_state"] = {
                        "event_type": event_type,
                        "status": status,
                        "message": message,
                        "step_id": step_id,
                        "execution_session_id": execution_session_id,
                    }
                if needs_human:
                    run.status = "waiting"
                    if step_id:
                        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
                        if step:
                            step.status = "waiting"
                    run_data["waiting_kind"] = "needs_human_input"
                    run_data["human_intervention_state"] = "needs_human_input"
                    run_data["next_action"] = "needs_human_input"
                    run_data["worker_question"] = message
                    run_data["worker_question_spoken"] = worker_question_spoken or message
                    run_data["needs_input_context"] = needs_input_context
                    if latest_handoff:
                        latest_handoff["human_intervention"] = {
                            **(
                                latest_handoff.get("human_intervention")
                                if isinstance(latest_handoff.get("human_intervention"), dict)
                                else {}
                            ),
                            "state": "needs_human_input",
                            "latest_message": message,
                            "latest_label": event.mistake_label or "",
                        }
                        run_data["latest_backend_handoff"] = latest_handoff
                elif worker_terminal:
                    if run_data.get("ticket_dispatch"):
                        run.status = "failed" if "failed" in lower_event_type else "completed"
                        if step_id:
                            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
                            if step:
                                step.status = "failed" if "failed" in lower_event_type else "completed"
                        run_data.pop("waiting_kind", None)
                        run_data.pop("ide_handoff_pending", None)
                        run_data["human_intervention_state"] = "resolved"
                        run_data["worker_status"] = "failed" if "failed" in lower_event_type else "completed"
                    else:
                        if run.status == "waiting":
                            run.status = "running"
                        if step_id:
                            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
                            if step and step.status == "waiting":
                                step.status = "running"
                        if run_data.get("waiting_kind") in {"needs_human_input", "worker_needs_input"}:
                            run_data.pop("waiting_kind", None)
                        if run_data.get("next_action") == "needs_human_input":
                            run_data.pop("next_action", None)
                        run_data["human_intervention_state"] = "resolved"
                        run_data["worker_status"] = "failed" if "failed" in lower_event_type else "completed"
                    run_data["last_codex_bridge_state"] = {
                        "event_type": event_type,
                        "status": status,
                        "message": message,
                        "step_id": step_id,
                        "execution_session_id": execution_session_id,
                    }
                    if latest_handoff:
                        latest_handoff["state"] = "failed" if "failed" in lower_event_type else "completed"
                        latest_handoff["human_intervention"] = {
                            **(
                                latest_handoff.get("human_intervention")
                                if isinstance(latest_handoff.get("human_intervention"), dict)
                                else {}
                            ),
                            "state": "resolved",
                            "latest_message": message,
                        }
                        run_data["latest_backend_handoff"] = latest_handoff
                run.run_data = json.dumps(run_data)
                db.commit()

            append_execution_event(
                int(execution_session_id) if execution_session_id else None,
                event_type,
                status=status,
                message=message,
                payload=payload,
            )

            standard_event_type = normalize_orchestration_event_type(
                event_type,
                source="codex",
                status=status,
            )
            captured_standard = False
            if event_type in {
                "user_steer",
                "manual_fix",
                "changes_requested",
                "codex_interrupted",
                "cursor_interrupted",
            } and message:
                try:
                    from distr.core.workflow.control_policy import classify_learning_signal
                    from distr.core.workflow.steering_memory import record_run_steering_feedback

                    learning = classify_learning_signal(message, event_type=event_type)
                    record_run_steering_feedback(
                        run_id=run_id,
                        source="cursor" if "cursor" in lower_event_type else "codex",
                        event_type=event_type,
                        message=message,
                        step_id=int(step_id) if step_id else None,
                        workflow_id=workflow_id,
                        board_id=int(board_id) if board_id else None,
                        ticket_id=int(ticket_id) if ticket_id else None,
                        project_id=int(project_id) if str(project_id or "").isdigit() else None,
                    )
                    captured_standard = bool(learning.enabled)
                except Exception:
                    logger.debug("Could not persist bridge steering", exc_info=True)

            mistake_event_id = None
            if message and (
                event.mistake_label
                or event_type in {"manual_fix", "changes_requested"}
            ):
                mistake_event_id = record_human_intervention_memory(
                    label=event.mistake_label or ("manual_fix_applied" if event_type == "manual_fix" else "missed_requirement"),
                    message=message,
                    workflow_id=workflow_id,
                    run_id=run_id,
                    step_id=int(step_id) if step_id else None,
                    ticket_id=int(ticket_id) if ticket_id else None,
                    board_id=int(board_id) if board_id else None,
                    project_id=int(project_id) if str(project_id or "").isdigit() else None,
                    execution_session_id=int(execution_session_id) if execution_session_id else None,
                    handoff_event_id=(
                        latest_handoff.get("handoff_event_id")
                        if isinstance(latest_handoff, dict)
                        else None
                    ),
                )
            orchestrator_event_id = emit_orchestration_event(
                source="codex",
                event_type=event_type,
                status=status,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=int(step_id) if step_id else None,
                ticket_id=int(ticket_id) if ticket_id else None,
                board_id=int(board_id) if board_id else None,
                project_id=int(project_id) if str(project_id or "").isdigit() else None,
                execution_session_id=int(execution_session_id) if execution_session_id else None,
                summary=message or f"Codex bridge event: {event_type}",
                payload=payload,
                evidence=event.evidence or {},
            )
            try:
                from distr.core.agent_activity import emit_agent_activity_step

                activity_result = emit_agent_activity_step(
                    source="codex",
                    surface="workflow",
                    status="waiting" if needs_human else (status or ("completed" if worker_terminal else "running")),
                    title=(
                        "Needs input"
                        if needs_human
                        else ("Worker completed" if worker_terminal else "Worker progress")
                    ),
                    summary=worker_question_spoken if needs_human and worker_question_spoken else (message or f"Codex bridge event: {event_type}"),
                    workflow_id=workflow_id,
                    run_id=run_id,
                    step_id=int(step_id) if step_id else None,
                    ticket_id=int(ticket_id) if ticket_id else None,
                    board_id=int(board_id) if board_id else None,
                    project_id=int(project_id) if str(project_id or "").isdigit() else None,
                    execution_session_id=int(execution_session_id) if execution_session_id else None,
                    parent_event_id=orchestrator_event_id,
                    thread_key="codex",
                    step_key=event_type,
                    step_type="needs_input" if needs_human else "cli_bridge",
                    context=needs_input_context if needs_human else {},
                    question=message if needs_human else "",
                    spoken_text=worker_question_spoken if needs_human else "",
                    payload={"bridge_event_type": event_type},
                    evidence=event.evidence or {},
                )
                orchestrator_event_id = activity_result.get("event_id") or orchestrator_event_id
            except Exception:
                logger.debug("Could not emit codex bridge agent activity", exc_info=True)

            increment_workflow_updated()

            if worker_terminal and (event.output or event.message):
                try:
                    from distr.core.workflow.step_iteration import record_harness_step_report

                    record_harness_step_report(
                        run_id=int(run_id),
                        step_id=int(step_id) if step_id else None,
                        report_text=(event.output or event.message or "").strip(),
                        source="cursor" if "cursor" in lower_event_type else "codex",
                        event_type=event_type,
                    )
                except Exception:
                    logger.debug("Could not record harness step report", exc_info=True)

            try:
                from distr.core.workspace_memory.feedback_sync import persist_worker_feedback

                persist_worker_feedback(
                    message=message,
                    output=event.output or "",
                    input_text=event.input or "",
                    event_type=event_type,
                    source="cursor" if "cursor" in lower_event_type else "codex",
                    ticket_id=int(ticket_id) if ticket_id else None,
                    project_id=int(project_id) if str(project_id or "").isdigit() else None,
                    board_id=int(board_id) if board_id else None,
                    workflow_id=int(workflow_id),
                    run_id=int(run_id),
                    step_id=int(step_id) if step_id else None,
                    execution_session_id=int(execution_session_id) if execution_session_id else None,
                    mistake_label=event.mistake_label or "",
                    skip_steering_log=True,
                    skip_human_intervention=True,
                )
            except Exception:
                logger.debug("Could not persist bridge feedback to workspace memory", exc_info=True)

            auto_continue_result = None
            if (
                bridge_suffix == "completed"
                and run_status_before == "waiting"
                and waiting_kind_before in {"ide_handoff", "needs_human_input"}
                and not bool(run_data.get("ticket_dispatch"))
            ):
                try:
                    from distr.core.workflow.dispatcher import continue_waiting_step

                    resume_text = (event.output or event.message or "IDE work completed.").strip()
                    auto_continue_result = continue_waiting_step(int(run_id), resume_text)
                except Exception:
                    logger.debug("IDE bridge auto-continue failed", exc_info=True)

            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "event_type": standard_event_type,
                "legacy_event_type": event_type if event_type != standard_event_type else "",
                "event_id": orchestrator_event_id,
                "orchestrator_event_id": orchestrator_event_id,
                "human_intervention_event_id": mistake_event_id,
                "captured_standard": captured_standard,
                "auto_continue": auto_continue_result,
            })
        except Exception as e:
            logger.error("Workflow Codex bridge event failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "codex_event"), status_code=500)

    @router.get("/workflows/{workflow_id}/runs/{run_id}/steering-memory")
    async def workflow_run_steering_memory(workflow_id: int, run_id: int):
        """Return run steering log and board learned rules for the Runs memory panel."""
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflowRun
            from distr.core.workflow.steering_memory import get_run_steering_snapshot

            with get_session() as db:
                run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.id == int(run_id))
                    .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                    .first()
                )
                if not run:
                    return JSONResponse(_workflow_error_payload("Run not found", "steering_memory"), status_code=404)
            snapshot = get_run_steering_snapshot(int(run_id))
            if not snapshot:
                return JSONResponse(_workflow_error_payload("Run not found", "steering_memory"), status_code=404)
            return JSONResponse({"success": True, **snapshot})
        except Exception as e:
            logger.error("Workflow steering memory failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "steering_memory"), status_code=500)

    @router.get("/workflows/{workflow_id}/runs/{run_id}/timeline")
    async def workflow_run_timeline(
        workflow_id: int,
        run_id: int,
        limit: int = 100,
        mission_control: bool = False,
        detail: bool = False,
    ):
        """Return the normalized orchestration conversation timeline for a run."""
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflowRun
            from distr.core.orchestration_events import list_orchestration_timeline

            with get_session() as db:
                run = (
                    db.query(AutoWorkflowRun)
                    .filter(AutoWorkflowRun.id == int(run_id))
                    .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                    .first()
                )
                if not run:
                    return JSONResponse(_workflow_error_payload("Run not found", "timeline"), status_code=404)
                # Copy scalar state before the session closes. Workflow polling
                # can race with runner commits, which expires ORM attributes;
                # reading the detached row below used to turn that race into a
                # stream of 500s in the Mission Control side panel.
                current_step_id = int(run.current_step_id) if run.current_step_id else None
                try:
                    run_data = json.loads(run.run_data or "{}") or {}
                except Exception:
                    run_data = {}
            blueprint = {}
            try:
                from distr.core.workflow.blueprint_adherence import build_run_blueprint_snapshot

                blueprint = build_run_blueprint_snapshot(run_data if isinstance(run_data, dict) else {})
            except Exception:
                blueprint = {}
            if detail:
                from distr.core.workflow.runtime_contract import detailed_execution_timeline

                events = detailed_execution_timeline(list_orchestration_timeline(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    limit=min(max(int(limit or 500), 1), 500),
                ))
            elif mission_control:
                from distr.core.orchestration_events import list_mission_control_timeline

                events = list_mission_control_timeline(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    current_step_id=current_step_id,
                )
            else:
                events = list_orchestration_timeline(
                    workflow_id=workflow_id,
                    run_id=run_id,
                    limit=limit,
                )
            return JSONResponse({
                "success": True,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "events": events,
                "blueprint": blueprint,
                "mission_control": bool(mission_control),
                "detail": bool(detail),
            })
        except Exception as e:
            logger.error("Workflow timeline failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "timeline"), status_code=500)

    @router.get("/workflows/{workflow_id}/runs/{run_id}/current-step/activity")
    async def workflow_current_step_activity(workflow_id: int, run_id: int, limit: int = 60):
        """Return compact activity for the run's current active step only."""
        try:
            from distr.core.workflow.runtime_contract import current_step_activity

            result = current_step_activity(
                workflow_id=workflow_id,
                run_id=run_id,
                limit=limit,
            )
            if not result.get("success"):
                return JSONResponse(
                    _workflow_error_payload(result.get("error") or "Run not found", "current_step_activity"),
                    status_code=404,
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow current step activity failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "current_step_activity"), status_code=500)

    @router.get("/workflows/{workflow_id}/active-run")
    async def workflow_active_run(workflow_id: int):
        try:
            from distr.core.workflow.service import get_active_run
            run = get_active_run(workflow_id)
            return JSONResponse(run or {
                "active": False,
                "message": "No active run for this workflow.",
                "next_action": "Start the workflow or open run history for previous results.",
            })
        except Exception as e:
            logger.error("Workflow active run failed: %s", e, exc_info=True)
            return JSONResponse(_workflow_error_payload(str(e), "status"), status_code=500)

    # Context items (structured agent context snippets)
    @router.get("/workflows/{workflow_id}/context-items")
    async def workflow_context_items(workflow_id: int):
        try:
            from distr.core.workflow.service import get_context_items
            return JSONResponse(get_context_items(workflow_id))
        except Exception as e:
            logger.error("Workflow context items failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/context-items")
    async def workflow_add_context_item(workflow_id: int, data: ContextItemCreateRequest):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import add_context_item
            item_id = add_context_item(workflow_id, title=data.title, content=data.content, notes=data.notes)
            if not item_id:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"id": item_id, "success": True})
        except Exception as e:
            logger.error("Workflow add context item failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/context-items/{context_item_id}")
    async def workflow_update_context_item(workflow_id: int, context_item_id: int, data: ContextItemUpdateRequest):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import update_context_item
            updates = {k: v for k, v in data.dict().items() if v is not None}
            if not update_context_item(context_item_id, workflow_id=workflow_id, **updates):
                return JSONResponse({"detail": "Context item not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow update context item failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/context-items/{context_item_id}")
    async def workflow_delete_context_item(workflow_id: int, context_item_id: int):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import delete_context_item
            if not delete_context_item(context_item_id, workflow_id=workflow_id):
                return JSONResponse({"detail": "Context item not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow delete context item failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/apply-loop-preset")
    async def workflow_apply_loop_preset(workflow_id: int, data: LoopPresetApplyRequest):
        try:
            from distr.core.workflow.loop_presets import apply_loop_preset

            result = apply_loop_preset(workflow_id, data.preset_name, mode=data.mode)
            if not result.get("success"):
                return JSONResponse(
                    {"detail": result.get("error") or "Failed"},
                    status_code=int(result.get("status_code") or 400),
                )
            from distr.core.workflow.service import get_workflow

            return JSONResponse({"success": True, **result, "workflow": get_workflow(workflow_id)})
        except Exception as e:
            logger.error("Workflow apply loop preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/export-loop-preset")
    async def workflow_export_loop_preset(workflow_id: int):
        """Download current loop steps as a loop preset JSON bundle."""
        try:
            import re

            from distr.core.workflow.loop_presets import export_loop_preset_json
            from starlette.responses import Response

            bundle = export_loop_preset_json(workflow_id)
            if not bundle:
                return JSONResponse(
                    {"detail": "Workflow not found or has no steps to export"},
                    status_code=404,
                )
            safe_name = re.sub(
                r"[^a-z0-9_-]+",
                "-",
                str(bundle.get("slug") or bundle.get("name") or "loop").lower(),
            ).strip("-") or "loop"
            payload = json.dumps(bundle, indent=2, ensure_ascii=False)
            return Response(
                content=payload.encode("utf-8"),
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="{safe_name}.loop-preset.json"'
                },
            )
        except Exception as e:
            logger.error("Workflow export loop preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/import-loop-preset")
    async def workflow_import_loop_preset(
        workflow_id: int,
        file: UploadFile = File(...),
        mode: str = "replace",
    ):
        """Import a loop preset JSON file into the current workflow."""
        try:
            from distr.core.workflow.loop_presets import import_loop_preset_json
            from distr.core.workflow.service import get_workflow

            raw = await file.read()
            try:
                bundle_data = json.loads(raw.decode("utf-8"))
            except Exception:
                return JSONResponse({"detail": "Invalid JSON file"}, status_code=400)

            result = import_loop_preset_json(workflow_id, bundle_data, mode=mode)
            if not result.get("success"):
                return JSONResponse(
                    {"detail": result.get("error") or "Import failed"},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse({"success": True, **result, "workflow": get_workflow(workflow_id)})
        except Exception as e:
            logger.error("Workflow import loop preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/save-loop-preset")
    async def workflow_save_loop_preset(workflow_id: int, data: LoopPresetSaveRequest):
        """Save current workflow steps as a reusable user loop preset."""
        try:
            from distr.core.workflow.loop_presets import save_loop_preset_from_workflow

            result = save_loop_preset_from_workflow(workflow_id, data.name)
            if not result.get("success"):
                return JSONResponse(
                    {"detail": result.get("error") or "Save failed"},
                    status_code=int(result.get("status_code") or 400),
                )
            return JSONResponse({"success": True, **result})
        except Exception as e:
            logger.error("Workflow save loop preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/steps/suggest-harness")
    async def workflow_suggest_step_harness(data: StepHarnessSuggestRequest):
        from distr.core.workflow.step_harness import suggest_step_harness

        return JSONResponse(
            suggest_step_harness(
                instruction=data.instruction,
                action_type=data.action_type,
                archetype=data.archetype,
                loop_contract=data.loop_contract or {},
                step_role=data.step_role,
            )
        )

    @router.post("/workflows/steps/suggest-harness-llm")
    async def workflow_suggest_step_harness_llm(data: StepHarnessLlmSuggestRequest):
        from distr.core.workflow.step_harness import suggest_step_harness_llm

        return JSONResponse(
            suggest_step_harness_llm(
                instruction=data.instruction,
                guardrail=data.guardrail,
                validation_prompt=data.validation_prompt,
                loop_contract=data.loop_contract or {},
            )
        )

    # Steps
    @router.post("/workflows/{workflow_id}/steps")
    async def workflow_add_step(workflow_id: int, data: StepCreateRequest):
        try:
            from distr.core.workflow.service import add_step, get_workflow
            step_id = add_step(
                workflow_id,
                name=data.name,
                action_type=data.action_type,
                position=data.position,
                instruction=data.instruction,
                config=data.config,
                validation_type=data.validation_type,
                validation_prompt=data.validation_prompt,
                wait_for_continue=data.wait_for_continue,
            )
            if not step_id:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse(get_workflow(workflow_id))
        except Exception as e:
            logger.error("Workflow add step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/steps/reorder")
    async def workflow_reorder_steps(workflow_id: int, data: StepReorderRequest):
        try:
            from distr.core.workflow.service import reorder_steps
            reorder_steps(workflow_id, data.step_ids)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow reorder steps failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/steps/{step_id}")
    async def workflow_update_step(workflow_id: int, step_id: int, request: Request):
        try:
            from distr.core.workflow.service import update_step
            body = await request.json()
            if not update_step(step_id, **body):
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow update step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/steps/{step_id}")
    async def workflow_delete_step(workflow_id: int, step_id: int):
        try:
            from distr.core.workflow.service import delete_step
            if not delete_step(step_id, workflow_id=workflow_id):
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow delete step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Step execution (path-param route)
    @router.post("/workflows/{workflow_id}/steps/{step_id}/execute")
    async def workflow_execute_step(workflow_id: int, step_id: int):
        """Execute a single step in isolation.

        Runs the step in a background thread so the LLM call doesn't block
        the uvicorn event loop (which would hang the entire server). Returns
        immediately; the UI polls / soft-refreshes to see the result.
        """
        import asyncio

        # Server-side debounce/idempotency guard for double-clicks or duplicate listeners.
        now = time.time()
        with _isolated_step_exec_lock:
            last_started = _isolated_step_exec_started_at.get(step_id, 0.0)
            if now - last_started < 1.5:
                logger.info(
                    "Workflow step execute deduped: workflow_id=%s step_id=%s delta=%.3fs",
                    workflow_id,
                    step_id,
                    now - last_started,
                )
                return JSONResponse({"success": True, "message": "Step execution already in progress."})
            _isolated_step_exec_started_at[step_id] = now

        def _run():
            try:
                from distr.core.workflow.dispatcher import StepDispatcher
                dispatcher = StepDispatcher()
                logger.info(
                    "Workflow step execute started: workflow_id=%s step_id=%s",
                    workflow_id,
                    step_id,
                )
                dispatcher.run_isolated(step_id)
                logger.info(
                    "Workflow step execute finished: workflow_id=%s step_id=%s",
                    workflow_id,
                    step_id,
                )
            except Exception as exc:
                logger.error("Background step execution failed for step %s: %s", step_id, exc, exc_info=True)
            finally:
                # Keep timestamp for a short debounce window only.
                try:
                    with _isolated_step_exec_lock:
                        started = _isolated_step_exec_started_at.get(step_id, 0.0)
                        if started and (time.time() - started) > 10.0:
                            _isolated_step_exec_started_at.pop(step_id, None)
                except Exception:
                    pass

        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _run)
        return JSONResponse({"success": True, "message": "Step execution started."})

    @router.post("/workflows/{workflow_id}/steps/{step_id}/stop")
    async def workflow_stop_step(workflow_id: int, step_id: int):
        """Stop a running/waiting step without cancelling the full run."""
        try:
            from distr.core.workflow.dispatcher import cancel_step

            # Stop any recording playback via the action playback service
            try:
                from distr.core.signals import signal_manager
                svc = getattr(signal_manager, 'action_playback_service', None)
                if svc is not None:
                    svc.stop_action()
            except Exception:
                pass

            # Stop TTS and player if a step is being stopped
            try:
                from distr.core.signals import signal_manager
                signal_manager.interrupt_tts.emit()
                signal_manager.player_stop.emit()
            except Exception:
                pass

            # Cancel the step itself
            cancel_step(step_id)

            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow stop step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/cancel")
    async def workflow_cancel_step(workflow_id: int, step_id: int):
        try:
            from distr.core.workflow.dispatcher import cancel_step
            if not cancel_step(step_id):
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow cancel step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/complete")
    async def workflow_complete_step(workflow_id: int, step_id: int, request: Request):
        """Mark step complete with result. Body: {result: str, passed: bool}"""
        try:
            from distr.core.workflow.router import StepRouter
            from distr.core.db import get_session as _get_session
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep as _Step
            body = await request.json()
            result_text = body.get("result", "")
            passed = body.get("passed", True)
            # Find the active run for this step (if any)
            run_id = None
            with _get_session() as db:
                step = db.query(_Step).filter(_Step.id == step_id).first()
                if not step:
                    return JSONResponse({"detail": "Step not found"}, status_code=404)
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == step.workflow_id,
                    AutoWorkflowRun.current_step_id == step_id,
                    AutoWorkflowRun.status == "running",
                ).first()
                if run:
                    run_id = run.id
            if run_id is not None:
                router = StepRouter()
                res = router.route(step_id, result_text, passed, run_id)
            else:
                # Isolated step — just record the result
                from distr.core.workflow.service import update_step
                update_step(step_id, status="passed" if passed else "failed", result=result_text)
                res = {"done": True, "status": "passed" if passed else "failed"}
            return JSONResponse(res)
        except Exception as e:
            logger.error("Workflow complete step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Screenshot upload for validation
    @router.post("/workflows/{workflow_id}/steps/{step_id}/screenshot")
    async def workflow_upload_screenshot(workflow_id: int, step_id: int, file: UploadFile = File(...)):
        try:
            from distr.core.workflow.service import save_screenshot
            data = await file.read()
            path = save_screenshot(step_id, data, file.filename or "screenshot.png")
            if not path:
                return JSONResponse({"detail": "Failed to save screenshot"}, status_code=500)
            return JSONResponse({"success": True, "path": path})
        except Exception as e:
            logger.error("Workflow screenshot upload failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Step result history
    @router.get("/workflows/{workflow_id}/steps/{step_id}/results")
    async def workflow_step_results(workflow_id: int, step_id: int, limit: int = 20):
        try:
            from distr.core.workflow.service import get_step_results
            return JSONResponse(get_step_results(step_id, limit=limit))
        except Exception as e:
            logger.error("Workflow step results failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/steps/{step_id}/results")
    async def workflow_clear_step_results(workflow_id: int, step_id: int):
        try:
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.workflow.service import clear_step_results
            result = clear_step_results(step_id)
            if "error" in result:
                return JSONResponse({"detail": result["error"]}, status_code=404)
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow clear step results failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Step recording
    @router.post("/workflows/{workflow_id}/steps/{step_id}/start-recording")
    async def workflow_start_step_recording(workflow_id: int, step_id: int):
        try:
            from distr.core.signals import signal_manager
            signal_manager.start_step_recording.emit(step_id)
            return JSONResponse({"success": True, "message": "Recording countdown started"})
        except Exception as e:
            logger.error("Start step recording failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/stop-recording")
    async def workflow_stop_step_recording(workflow_id: int, step_id: int):
        try:
            from distr.core.signals import signal_manager
            signal_manager.stop_step_recording.emit()

            # Poll briefly for the recording to be saved to the database.
            # The signal handler saves asynchronously, so we wait up to ~2s.
            import asyncio
            from distr.core.workflow.service import get_workflow
            for _ in range(10):
                await asyncio.sleep(0.2)
                wf = get_workflow(workflow_id)
                if wf:
                    s = next((s for s in wf.get("steps", []) if s["id"] == step_id), None)
                    if s and s.get("recording_filename"):
                        return JSONResponse({"success": True, "message": "Recording stopped", "recording_filename": s["recording_filename"]})

            # Timed out waiting, but the signal was sent — return success anyway
            return JSONResponse({"success": True, "message": "Recording stopped"})
        except Exception as e:
            logger.error("Stop step recording failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/play-recording")
    async def workflow_play_step_recording(workflow_id: int, step_id: int):
        """Play the recorded action for a step."""
        try:
            from distr.core.workflow.service import get_workflow
            from distr.core.signals import signal_manager
            wf = get_workflow(workflow_id)
            if not wf:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            step = next((s for s in wf.get("steps", []) if s["id"] == step_id), None)
            if not step:
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            rec = step.get("recording_filename", "")
            if not rec:
                return JSONResponse({"detail": "No recording for this step"}, status_code=400)
            signal_manager.play_recording_file.emit(rec)
            return JSONResponse({"success": True, "message": "Playing recording"})
        except Exception as e:
            logger.error("Play step recording failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # ── Export ──

    @router.get("/workflows/{workflow_id}/export")
    async def workflow_export(workflow_id: int):
        """Download a .dwf bundle (ZIP with recordings + screenshots)."""
        try:
            from distr.core.workflow.service import export_workflow_bundle, export_workflow
            bundle = export_workflow_bundle(workflow_id)
            if not bundle:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            # Get workflow name for the filename
            data = export_workflow(workflow_id)
            import re
            safe_name = re.sub(r'[^a-z0-9_]', '', (data.get("name", "workflow") or "workflow").lower().replace(" ", "_"))
            from starlette.responses import Response
            return Response(
                content=bundle,
                media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}.dwf"'}
            )
        except Exception as e:
            logger.error("Workflow export failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/export-preset")
    async def workflow_export_preset(workflow_id: int):
        try:
            from distr.core.workflow.service import save_preset
            filename = save_preset(workflow_id)
            if not filename:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"success": True, "filename": filename})
        except Exception as e:
            logger.error("Workflow export preset failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

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

    @router.websocket("/workflows/ws")
    async def workflows_websocket(websocket: WebSocket):
        await _workflows_websocket_handler(websocket)

    @router.websocket("/ws/workflows")
    async def workflows_websocket_legacy(websocket: WebSocket):
        await _workflows_websocket_handler(websocket)
