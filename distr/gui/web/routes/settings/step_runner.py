"""
Step Runner routes — /step-runner/*
"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List

from ._shared import logger


# ---- Pydantic models (only used in this module) ----

class StepRunnerPlanRequest(BaseModel):
    instruction: str
    chat_id: Optional[int] = None


class StepRunnerScheduledRequest(BaseModel):
    instruction: str
    schedule: str  # "daily", "hourly", "weekly", or cron
    chat_id: Optional[int] = None
    schedule_time: Optional[str] = None  # "08:00" or "09:30" for time
    schedule_days: Optional[str] = None  # for weekly: "1,3,5" = Mon, Wed, Fri
    timezone: Optional[str] = None


class StepRunnerScheduleUpdate(BaseModel):
    enabled: Optional[bool] = None
    schedule: Optional[str] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[str] = None
    timezone: Optional[str] = None


class StepRunnerStepUpdate(BaseModel):
    status: Optional[str] = None
    result: Optional[str] = None
    tool_used: Optional[str] = None
    title: Optional[str] = None
    instruction: Optional[str] = None
    step_type: Optional[str] = None
    config: Optional[str] = None
    code: Optional[str] = None


class StepRunnerExecuteRequest(BaseModel):
    step_id: int


class StepRunnerReorderRequest(BaseModel):
    step_ids: List[int]


class StepRunnerContinueRequest(BaseModel):
    input: Optional[str] = None


class StepRunnerValidateRequest(BaseModel):
    step_type: str
    config: dict


class StepRunnerGenerateCodeRequest(BaseModel):
    step_id: int
    instruction: str
    step_type: str


class StepRunnerTestCodeRequest(BaseModel):
    step_id: int
    code: str
    step_type: str
    headless: bool = True


class StepRunnerSessionUpdate(BaseModel):
    context_rules: Optional[str] = None


def register_routes(router, templates):

    @router.post("/step-runner/scheduled")
    async def step_runner_create_scheduled(data: StepRunnerScheduledRequest):
        """Create a scheduled session (e.g. 'every day check my calendar')."""
        try:
            from distr.core.step_runner.service import create_scheduled_session
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            session = create_scheduled_session(
                data.instruction, data.schedule, data.chat_id,
                schedule_time=data.schedule_time, schedule_days=data.schedule_days,
                timezone=data.timezone,
            )
            if not session:
                return JSONResponse({"detail": "Failed to create scheduled session"}, status_code=500)
            increment_step_runner_updated()
            from distr.core.step_runner.service import get_session_with_steps
            return JSONResponse(get_session_with_steps(int(session)))
        except Exception as e:
            logger.error("Step Runner create scheduled failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/step-runner/sessions/{session_id}/schedule")
    async def step_runner_update_schedule(session_id: int, data: StepRunnerScheduleUpdate):
        """Update a scheduled session's enabled state or schedule."""
        try:
            from distr.core.step_runner.service import update_scheduled_session
            ok = update_scheduled_session(
                session_id,
                enabled=data.enabled,
                schedule=data.schedule,
                schedule_time=data.schedule_time,
                schedule_days=data.schedule_days,
                timezone=data.timezone,
            )
            if not ok:
                return JSONResponse({"detail": "Session not found or not scheduled"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Step Runner update schedule failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/plan")
    async def step_runner_plan(data: StepRunnerPlanRequest):
        """Break down an instruction into steps and create a session."""
        try:
            from distr.core.step_runner.service import plan_session
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            session = plan_session(data.instruction, data.chat_id)
            if not session:
                return JSONResponse({"detail": "Failed to plan steps"}, status_code=500)
            increment_step_runner_updated()
            from distr.core.step_runner.service import get_session_with_steps
            return JSONResponse(get_session_with_steps(int(session)))
        except Exception as e:
            logger.error("Step Runner plan failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/step-runner/version")
    async def step_runner_version():
        """Return a version counter that increments when Step Runner data changes (audit steps, etc.). UI polls to refresh."""
        try:
            from distr.gui.web.step_runner_events import get_step_runner_update_counter
            return JSONResponse({"version": get_step_runner_update_counter()})
        except Exception:
            return JSONResponse({"version": 0})

    @router.get("/step-runner/sessions")
    async def step_runner_list_sessions(limit: int = 50, session_type: Optional[str] = None, search: Optional[str] = None):
        """List recent Step Runner sessions. Filter by session_type (instruction/scheduled) or search."""
        try:
            from distr.core.step_runner.service import list_sessions
            return JSONResponse(list_sessions(limit=limit, session_type=session_type, search=search))
        except Exception as e:
            logger.error("Step Runner list sessions failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/step-runner/sessions/{session_id}")
    async def step_runner_get_session(session_id: int):
        """Get a session with its steps."""
        try:
            from distr.core.step_runner.service import get_session_with_steps
            data = get_session_with_steps(session_id)
            if not data:
                return JSONResponse({"detail": "Session not found"}, status_code=404)
            return JSONResponse(data)
        except Exception as e:
            logger.error("Step Runner get session failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/step-runner/steps/{step_id}")
    async def step_runner_update_step(step_id: int, data: StepRunnerStepUpdate):
        """Update a step's status, result, tool_used, title, instruction, step_type, config, or code."""
        try:
            from distr.core.step_runner.service import update_step_status
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            ok = update_step_status(
                step_id,
                status=data.status,
                result=data.result,
                tool_used=data.tool_used,
                title=data.title,
                instruction=data.instruction,
                config=data.config,
                code=data.code,
            )
            if not ok:
                return JSONResponse({"detail": "Step not found"}, status_code=404)
            # Update step_type separately if provided (not in update_step_status signature as positional)
            if data.step_type is not None:
                from distr.core.db import get_session as get_db_session
                from distr.core.db.step_runner import StepRunnerStep as StepModel
                with get_db_session() as db:
                    step = db.query(StepModel).filter(StepModel.id == step_id).first()
                    if step:
                        step.step_type = data.step_type
                        db.commit()
            increment_step_runner_updated()
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Step Runner update step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/execute")
    async def step_runner_execute(data: StepRunnerExecuteRequest):
        """Execute a single step via the agent. Returns execution result."""
        try:
            from distr.core.step_runner.service import update_step_status, update_session_status
            from distr.core.db import get_session
            from distr.core.db.step_runner import StepRunnerStep, StepRunnerSession
            from distr.core.utils import load_settings_from_db

            with get_session() as db:
                step = db.query(StepRunnerStep).filter(StepRunnerStep.id == data.step_id).first()
                if not step:
                    return JSONResponse({"detail": "Step not found"}, status_code=404)
                session = db.query(StepRunnerSession).filter(StepRunnerSession.id == step.session_id).first()
                if not session:
                    return JSONResponse({"detail": "Session not found"}, status_code=404)
                resolved_chat_id = session.chat_id
                step_type = getattr(step, "step_type", "run_command") or "run_command"
                step_config_raw = getattr(step, "config", None)

            # Validate step config before execution
            try:
                import json as _json
                from distr.core.step_runner.validation import StepValidator
                config_dict = _json.loads(step_config_raw) if step_config_raw else {}
                errors = StepValidator().validate(step_type, config_dict)
                if errors:
                    return JSONResponse(
                        {"errors": [{"field": e.field, "message": e.message} for e in errors]},
                        status_code=422,
                    )
            except (ValueError, TypeError):
                pass  # If config is not valid JSON, skip validation (will fail at execution)

            update_step_status(step.id, "running")
            update_session_status(session.id, "in_progress")

            # Emit app-owned single-step execution signal so completion updates are tied to stream events.
            from distr.core.signals import signal_manager
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            try:
                if resolved_chat_id is None:
                    settings = load_settings_from_db()
                    resolved_chat_id = settings.get("agent_current_chat_id")
                signal_manager.step_runner_execute_requested.emit(
                    int(step.id),
                    int(session.id),
                    str(step.instruction or ""),
                    resolved_chat_id,
                )
                increment_step_runner_updated()
                return JSONResponse({"success": True, "result": "Step sent to agent."})
            except Exception as e:
                update_step_status(step.id, "failed", result=str(e))
                return JSONResponse({"detail": str(e)}, status_code=500)
        except Exception as e:
            logger.error("Step Runner execute failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/step-runner/sessions/{session_id}/reorder")
    async def step_runner_reorder(session_id: int, data: StepRunnerReorderRequest):
        """Reorder steps by new position order."""
        try:
            from distr.core.step_runner.service import reorder_steps
            ok = reorder_steps(session_id, data.step_ids)
            if not ok:
                return JSONResponse({"detail": "Session not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Step Runner reorder failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.delete("/step-runner/sessions/{session_id}")
    async def step_runner_delete_session(session_id: int):
        """Delete a session and its steps."""
        try:
            from distr.core.step_runner.service import delete_session
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            ok = delete_session(session_id)
            if not ok:
                return JSONResponse({"detail": "Session not found"}, status_code=404)
            increment_step_runner_updated()
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Step Runner delete failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/sessions/{session_id}/duplicate")
    async def step_runner_duplicate_session(session_id: int):
        """Duplicate a session and its steps (as one-time)."""
        try:
            from distr.core.step_runner.service import duplicate_session, get_session_with_steps
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            session = duplicate_session(session_id)
            if not session:
                return JSONResponse({"detail": "Session not found"}, status_code=404)
            increment_step_runner_updated()
            return JSONResponse(get_session_with_steps(int(session)))
        except Exception as e:
            logger.error("Step Runner duplicate failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.get("/step-runner/sessions/{session_id}/runs")
    async def step_runner_run_history(session_id: int, limit: int = 10):
        """Get run history for a scheduled session."""
        try:
            from distr.core.step_runner.service import get_run_history
            return JSONResponse(get_run_history(session_id, limit=limit))
        except Exception as e:
            logger.error("Step Runner run history failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/sessions/{session_id}/run-all")
    async def step_runner_run_all(session_id: int):
        """
        Run all steps in sequence. Agent executes each step, waits for completion,
        then proceeds. On failure, agent tries to resolve before continuing.
        """
        try:
            from distr.core.step_runner.service import update_session_status
            from distr.core.db import get_session
            from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep
            from distr.core.signals import signal_manager
            from distr.gui.web.step_runner_events import increment_step_runner_updated

            with get_session() as db:
                sess = db.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
                if not sess:
                    return JSONResponse({"detail": "Session not found"}, status_code=404)
                if sess.status == "in_progress":
                    return JSONResponse({"detail": "Session is already running"}, status_code=409)
                session_type = sess.session_type or "instruction"
                steps = sorted(sess.steps, key=lambda s: s.position)
                # Reset all steps to pending so this run starts fresh
                for step in steps:
                    step.status = "pending"
                    step.result = None
                db.commit()
                steps_data = [
                    {
                        "id": s.id,
                        "title": s.title,
                        "instruction": s.instruction,
                        "verification": getattr(s, "verification", None),
                    }
                    for s in steps
                ]
                run_chat_id = sess.chat_id

            if not steps_data:
                return JSONResponse({"detail": "No steps to run"}, status_code=400)

            update_session_status(session_id, "in_progress")
            increment_step_runner_updated()
            run_id = None
            if session_type == "scheduled":
                from distr.core.db.step_runner import StepRunnerRun
                from datetime import datetime
                with get_session() as db:
                    run = StepRunnerRun(session_id=session_id, status="running")
                    db.add(run)
                    db.commit()
                    run_id = run.id

            if run_chat_id:
                signal_manager.current_chat_changed.emit(int(run_chat_id))
            signal_manager.step_runner_run_all_requested.emit(
                session_id, steps_data, run_id, session_type
            )
            return JSONResponse({"success": True, "message": "Running all steps in sequence"})
        except Exception as e:
            logger.error("Step Runner run-all failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/sessions/{session_id}/cancel")
    async def step_runner_cancel(session_id: int):
        """Cancel an in-progress Step Runner orchestration."""
        try:
            from distr.core.signals import signal_manager
            signal_manager.step_runner_cancel_requested.emit(session_id)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Step Runner cancel failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/sessions/{session_id}/skip-step")
    async def step_runner_skip_step(session_id: int):
        """Skip the current step in an in-progress Step Runner orchestration."""
        try:
            from distr.core.signals import signal_manager
            signal_manager.step_runner_skip_step_requested.emit(session_id)
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Step Runner skip-step failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/sessions/{session_id}/continue")
    async def step_runner_continue(session_id: int, data: StepRunnerContinueRequest = None):
        """
        Continue a paused/waiting step in an in-progress Step Runner orchestration.
        When a step is in 'waiting' status (waiting for external input or an event to finish),
        call this endpoint to resume execution. Optionally pass 'input' to provide additional
        context or data to the step before it resumes.
        """
        try:
            from distr.core.signals import signal_manager
            optional_input = ""
            if data and data.input:
                optional_input = data.input
            signal_manager.step_runner_continue_requested.emit(session_id, optional_input)
            return JSONResponse({"success": True, "message": "Continue signal sent"})
        except Exception as e:
            logger.error("Step Runner continue failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    # ---- Step Runner: Validation, Code Generation, Test Loop, Session Update ----

    @router.post("/step-runner/validate")
    async def step_runner_validate(data: StepRunnerValidateRequest):
        """Validate step configuration against type-specific rules."""
        try:
            from distr.core.step_runner.validation import StepValidator
            errors = StepValidator().validate(data.step_type, data.config)
            if errors:
                return JSONResponse(
                    {"errors": [{"field": e.field, "message": e.message} for e in errors]},
                    status_code=422,
                )
            return JSONResponse({"valid": True})
        except Exception as e:
            logger.error("Step Runner validate failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/generate-code")
    async def step_runner_generate_code(data: StepRunnerGenerateCodeRequest):
        """Generate code from a natural language instruction using the coding LLM."""
        try:
            from distr.core.step_runner.code_generator import CodeGeneratorService
            from distr.core.step_runner.step_types import StepType
            try:
                stype = StepType(data.step_type)
            except ValueError:
                return JSONResponse(
                    {"detail": f"Invalid step type: {data.step_type}"},
                    status_code=400,
                )
            code = CodeGeneratorService().generate_code(
                instruction=data.instruction,
                step_type=stype,
            )
            return JSONResponse({"code": code})
        except RuntimeError as e:
            logger.error("Step Runner generate-code LLM error: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)
        except Exception as e:
            logger.error("Step Runner generate-code failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.post("/step-runner/test-code")
    async def step_runner_test_code(data: StepRunnerTestCodeRequest):
        """Execute code in an isolated subprocess with auto-fix loop."""
        try:
            from distr.core.step_runner.test_loop import TestLoopService
            from distr.core.step_runner.step_types import StepType
            try:
                stype = StepType(data.step_type)
            except ValueError:
                return JSONResponse(
                    {"detail": f"Invalid step type: {data.step_type}"},
                    status_code=400,
                )
            config = {"headless": data.headless}
            result = TestLoopService().run_test(
                code=data.code,
                step_type=stype,
                config=config,
            )
            return JSONResponse({
                "success": result.success,
                "code": result.code,
                "attempts": result.attempts,
                "output": result.output,
            })
        except Exception as e:
            logger.error("Step Runner test-code failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)

    @router.patch("/step-runner/sessions/{session_id}")
    async def step_runner_update_session(session_id: int, data: StepRunnerSessionUpdate):
        """Update a session's context_rules."""
        try:
            from distr.core.db import get_session
            from distr.core.db.step_runner import StepRunnerSession
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            with get_session() as db:
                s = db.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
                if not s:
                    return JSONResponse({"detail": "Session not found"}, status_code=404)
                if data.context_rules is not None:
                    s.context_rules = data.context_rules
                db.commit()
            increment_step_runner_updated()
            return JSONResponse({"success": True})
        except Exception as e:
            logger.error("Step Runner update session failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)
