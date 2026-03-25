"""
Workflow routes — /workflows/*
"""
from fastapi import Request, HTTPException, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import json
import re

from ._shared import logger


# ---- Pydantic models (only used in this module) ----

class WorkflowCreateRequest(BaseModel):
    name: str = "Untitled Workflow"
    description: str = ""


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


def register_routes(router, templates):

    @router.get("/workflows")
    async def workflow_list(limit: int = 50, search: Optional[str] = None, status: Optional[str] = None):
        try:
            from distr.core.workflow.service import list_workflows
            return JSONResponse(list_workflows(limit=limit, search=search, status=status))
        except Exception as e:
            logger.error("Workflow list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows")
    async def workflow_create(data: WorkflowCreateRequest):
        try:
            from distr.core.workflow.service import create_workflow, get_workflow
            wf_id = create_workflow(name=data.name, description=data.description)
            return JSONResponse(get_workflow(wf_id))
        except Exception as e:
            logger.error("Workflow create failed: %s", e, exc_info=True)
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
                "Valid action_type values: agent_instruction, run_command, http_request, "
                "execute_code, playwright, play_recording, set_variable.\n\n"
                "The last step's on_pass_goto_position should be null (end workflow).\n"
                "Return ONLY valid JSON, no markdown fences or explanations.\n\n"
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
            from distr.core.workflow.service import update_workflow
            updates = {k: v for k, v in data.dict().items() if v is not None}
            if not update_workflow(workflow_id, **updates):
                return JSONResponse({"detail": "Workflow not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/workflows/{workflow_id}")
    async def workflow_delete(workflow_id: int):
        try:
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
    async def workflow_run(workflow_id: int):
        try:
            from distr.core.workflow.service import start_workflow_run
            result = start_workflow_run(workflow_id)
            if "error" in result:
                return JSONResponse({"detail": result["error"]}, status_code=400)
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow run failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/cancel-run/{run_id}")
    async def workflow_cancel_run(workflow_id: int, run_id: int):
        try:
            from distr.core.workflow.service import cancel_run
            if not cancel_run(run_id):
                return JSONResponse({"detail": "Run not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Workflow cancel run failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/runs/{run_id}/continue")
    async def workflow_continue_run(run_id: int, request: Request):
        """Resume a waiting workflow run. Accepts optional { "input": "..." } body."""
        try:
            from distr.core.workflow.service import continue_waiting_step
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

    # Step execution
    @router.post("/workflows/{workflow_id}/steps/{step_id}/execute")
    async def workflow_execute_step(workflow_id: int, step_id: int):
        try:
            from distr.core.workflow.service import execute_step
            result = execute_step(step_id, isolated=True)
            if "error" in result:
                return JSONResponse({"detail": result["error"]}, status_code=400)
            return JSONResponse(result)
        except Exception as e:
            logger.error("Workflow execute step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/workflows/{workflow_id}/steps/{step_id}/cancel")
    async def workflow_cancel_step(workflow_id: int, step_id: int):
        try:
            from distr.core.workflow.service import cancel_step
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
            from distr.core.workflow.service import complete_step
            body = await request.json()
            res = complete_step(step_id, result=body.get("result", ""), passed=body.get("passed", True))
            if "error" in res:
                return JSONResponse({"detail": res["error"]}, status_code=400)
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
