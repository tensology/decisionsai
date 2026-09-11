"""Automations HTTP boundary. Existing URLs remain compatible."""
from fastapi.responses import JSONResponse
import json
import logging

logger = logging.getLogger(__name__)
from .models import ScheduledActionRequest, ScheduledActionUpdateRequest
from ._workflow_support import _action_from_step, _next_run_for_schedule, _schedule_from_workflow, _scheduled_action_payload


def register_routes(router, templates):
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


