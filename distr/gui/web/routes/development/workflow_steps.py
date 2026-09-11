"""Workflow steps HTTP boundary. Existing URLs remain compatible."""
from fastapi.responses import JSONResponse
import asyncio
import logging

logger = logging.getLogger(__name__)
from .models import StepHarnessLlmSuggestRequest, StepHarnessSuggestRequest, WorkflowGenerateCodeRequest, WorkflowTestCodeRequest
from ._workflow_support import _is_audit_workflow, _workflow_error_payload


def register_routes(router, templates):
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


    @router.post("/workflows/{workflow_id}/steps/{step_id}/start-recording")
    async def workflow_start_step_recording(workflow_id: int, step_id: int):
        try:
            from distr.gui.web.routes.development._workflow_support import _is_audit_workflow
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
            from distr.core.signals import signal_manager
            signal_manager.start_step_recording.emit(step_id)
            return JSONResponse({"success": True, "message": "Recording countdown started"})
        except Exception as e:
            logger.error("Start step recording failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/{workflow_id}/steps/{step_id}/stop-recording")
    async def workflow_stop_step_recording(workflow_id: int, step_id: int):
        try:
            from distr.gui.web.routes.development._workflow_support import _is_audit_workflow
            if _is_audit_workflow(workflow_id):
                return JSONResponse({"detail": "Audit workflows are read-only"}, status_code=403)
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

