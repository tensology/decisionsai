"""
Workflow routes — /workflows/*
"""
from fastapi import Request, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse
from starlette.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
import json
import re

from ._shared import logger


# ---- Pydantic models (only used in this module) ----

class WorkflowCreateRequest(BaseModel):
    name: str = "Untitled Workflow"
    description: str = ""
    workflow_type: Optional[str] = None


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    schedule_preset: Optional[str] = None
    schedule_cron: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    schedule_timezone: Optional[str] = None
    start_step_position: Optional[int] = None
    workflow_type: Optional[str] = None
    context_rules: Optional[str] = None


class StepCreateRequest(BaseModel):
    name: str = "New Step"
    action_type: str = "agent_instruction"
    position: Optional[int] = None


class VariableCreateRequest(BaseModel):
    name: str
    default_value: str = ""
    description: str = ""


class VariableUpdateRequest(BaseModel):
    name: Optional[str] = None
    default_value: Optional[str] = None
    description: Optional[str] = None


class StepReorderRequest(BaseModel):
    step_ids: List[int]


class WorkflowGenerateRequest(BaseModel):
    description: str


class WorkflowPlanRequest(BaseModel):
    instruction: str
    chat_id: Optional[int] = None


class WorkflowGenerateStepsRequest(BaseModel):
    instruction: str


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


def register_routes(router, templates):

    def _is_audit_workflow(workflow_id: int) -> bool:
        """Return True if the workflow exists and has workflow_type='audit'."""
        from distr.core.workflow.service import get_workflow_type
        return get_workflow_type(workflow_id) == "audit"

    @router.get("/workflows")
    async def workflow_list(limit: int = 50, search: Optional[str] = None, status: Optional[str] = None, type: Optional[str] = None):
        try:
            from distr.core.workflow.service import list_workflows
            return JSONResponse(list_workflows(limit=limit, search=search, status=status, workflow_type=type))
        except Exception as e:
            logger.error("Workflow list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows")
    async def workflow_create(data: WorkflowCreateRequest):
        try:
            from distr.core.workflow.service import create_workflow, get_workflow
            kwargs = {"name": data.name, "description": data.description}
            if data.workflow_type is not None:
                kwargs["workflow_type"] = data.workflow_type
            wf_id = create_workflow(**kwargs)
            return JSONResponse(get_workflow(wf_id))
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Workflow create failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # ── Plan / Version / Step-level endpoints (must be before {workflow_id} routes) ──

    @router.post("/workflows/plan")
    async def workflow_plan(data: WorkflowPlanRequest):
        """Plan a workflow from a natural-language instruction."""
        try:
            from distr.core.workflow.service import plan_workflow, get_workflow
            from distr.gui.web.workflow_events import increment_workflow_updated
            wf_id = plan_workflow(data.instruction, chat_id=data.chat_id)
            if not wf_id:
                return JSONResponse({"detail": "Failed to plan workflow"}, status_code=500)
            increment_workflow_updated()
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Workflow plan failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/version")
    async def workflow_version():
        """Return a version counter that increments when workflow data changes. UI polls to refresh."""
        try:
            from distr.gui.web.workflow_events import get_workflow_update_counter
            return JSONResponse({"version": get_workflow_update_counter()})
        except Exception:
            return JSONResponse({"version": 0})

    @router.get("/workflows/llm-settings")
    async def get_workflow_llm_settings():
        """Return the workflow engine's dedicated LLM provider and model."""
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        return JSONResponse({
            "provider": settings.get("step_runner_llm_provider") or "",
            "model": settings.get("step_runner_llm_model") or "",
        })

    @router.post("/workflows/llm-settings")
    async def save_workflow_llm_settings(request: Request):
        """Save the workflow engine's dedicated LLM provider and model."""
        from distr.core.settings import load_settings_from_db, save_settings_to_db
        data = await request.json()
        settings = load_settings_from_db()
        settings["step_runner_llm_provider"] = (data.get("provider") or "").strip()
        settings["step_runner_llm_model"] = (data.get("model") or "").strip()
        save_settings_to_db(settings)
        return JSONResponse({"success": True})

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
            from distr.core.step_runner.code_generator import CodeGeneratorService
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
                '  "variables": []\n'
                "}\n\n"
                "Valid action_type values and when to use them:\n"
                '- "agent_instruction" — general-purpose desktop/UI automation (default for most tasks)\n'
                '- "playwright" — browser automation: navigate, login, fill forms, click, scrape, screenshot\n'
                '- "execute_code" — run a Python script (data processing, file I/O, computation)\n'
                '- "run_command" — execute a shell command (mkdir, cp, ls, app launch)\n'
                '- "http_request" — make an HTTP request (GET, POST, PUT, DELETE)\n'
                '- "play_recording" — replay a previously recorded macro\n\n'
                "Rules:\n"
                "- Use \"playwright\" for all web browser tasks.\n"
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

    @router.get("/workflows/{workflow_id}")
    async def workflow_get(workflow_id: int):
        try:
            from distr.core.workflow.service import get_workflow
            data = get_workflow(workflow_id)
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

    # Execution
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
            result = start_workflow_run(workflow_id, start_step_id=start_step_id)
            if "error" in result:
                return JSONResponse({"detail": result["error"]}, status_code=400)
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow run failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/cancel-run/{run_id}")
    async def workflow_cancel_run(workflow_id: int, run_id: int):
        try:
            from distr.core.workflow.dispatcher import cancel_run
            if not cancel_run(run_id):
                return JSONResponse({"detail": "Run not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow cancel run failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/runs/{run_id}/continue")
    async def workflow_continue_run(workflow_id: int, run_id: int, request: Request):
        """Resume a waiting workflow run. Accepts optional { "input": "..." } body."""
        try:
            from distr.core.workflow.dispatcher import continue_waiting_step
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            optional_input = body.get("input", "") if isinstance(body, dict) else ""
            result = continue_waiting_step(run_id, optional_input)
            if "error" in result:
                status_code = result.get("status_code", 400)
                return JSONResponse({"detail": result["error"]}, status_code=status_code)
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow continue run failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/workflows/{workflow_id}/active-run")
    async def workflow_active_run(workflow_id: int):
        try:
            from distr.core.workflow.service import get_active_run
            run = get_active_run(workflow_id)
            return JSONResponse(run or {"active": False})
        except Exception as e:
            logger.error("Workflow active run failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # Steps
    @router.post("/workflows/{workflow_id}/steps")
    async def workflow_add_step(workflow_id: int, data: StepCreateRequest):
        try:
            from distr.core.workflow.service import add_step, get_workflow
            step_id = add_step(workflow_id, name=data.name, action_type=data.action_type, position=data.position)
            if not step_id:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse(get_workflow(workflow_id))
        except Exception as e:
            logger.error("Workflow add step failed: %s", e, exc_info=True)
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
            if not delete_step(step_id):
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow delete step failed: %s", e, exc_info=True)
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

    # Step execution (path-param route)
    @router.post("/workflows/{workflow_id}/steps/{step_id}/execute")
    async def workflow_execute_step(workflow_id: int, step_id: int):
        """Execute a single step in isolation.

        Runs the step in a background thread so the LLM call doesn't block
        the uvicorn event loop (which would hang the entire server). Returns
        immediately; the UI polls / soft-refreshes to see the result.
        """
        import asyncio

        def _run():
            try:
                from distr.core.workflow.dispatcher import StepDispatcher
                dispatcher = StepDispatcher()
                dispatcher.run_isolated(step_id)
            except Exception as exc:
                logger.error("Background step execution failed for step %s: %s", step_id, exc, exc_info=True)

        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _run)
        return JSONResponse({"success": True, "message": "Step execution started."})

    @router.post("/workflows/{workflow_id}/steps/{step_id}/stop")
    async def workflow_stop_step(workflow_id: int, step_id: int):
        """Stop a running/waiting step: cancel playback, stop TTS/player, cancel the active run, reset the step."""
        try:
            from distr.core.workflow.dispatcher import cancel_step, cancel_run
            from distr.core.db import get_session as _get_session
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep as _Step

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

            # Also cancel the active run for this workflow
            with _get_session() as db:
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == workflow_id,
                    AutoWorkflowRun.status.in_(["running", "waiting"])
                ).first()
                if run:
                    cancel_run(run.id)

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

    # Variables
    @router.post("/workflows/{workflow_id}/variables")
    async def workflow_add_variable(workflow_id: int, data: VariableCreateRequest):
        try:
            from distr.core.workflow.service import add_variable
            var_id = add_variable(workflow_id, name=data.name, default_value=data.default_value, description=data.description)
            if not var_id:
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"id": var_id, "success": True})
        except Exception as e:
            logger.error("Workflow add variable failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/workflows/{workflow_id}/variables/{variable_id}")
    async def workflow_update_variable(workflow_id: int, variable_id: int, data: VariableUpdateRequest):
        try:
            from distr.core.workflow.service import update_variable
            updates = {k: v for k, v in data.dict().items() if v is not None}
            if not update_variable(variable_id, **updates):
                return JSONResponse({"detail": "Variable not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow update variable failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}/variables/{variable_id}")
    async def workflow_delete_variable(workflow_id: int, variable_id: int):
        try:
            from distr.core.workflow.service import delete_variable
            if not delete_variable(variable_id):
                return JSONResponse({"detail": "Variable not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow delete variable failed: %s", e, exc_info=True)
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

    # ── Legacy redirect: /step-runner/* → /workflows/* (HTTP 301) ──

    @router.api_route("/step-runner/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def legacy_step_runner_redirect(request: Request, path: str):
        """Redirect legacy /api/step-runner/* requests to /api/workflows/* with HTTP 301."""
        new_path = f"/api/workflows/{path}" if path else "/api/workflows"
        return RedirectResponse(url=new_path, status_code=301)

    @router.api_route("/step-runner", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def legacy_step_runner_redirect_root(request: Request):
        """Redirect legacy /api/step-runner root to /api/workflows with HTTP 301."""
        return RedirectResponse(url="/api/workflows", status_code=301)
