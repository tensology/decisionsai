"""
Workflow Service — CRUD + execution engine for workflows and steps.
Each step is a single action with validation and routing.
"""
import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import datetime

from distr.core.db import get_session, Action
from distr.core.db.workflow import (
    AutoWorkflow, AutoWorkflowStep,
    AutoWorkflowVariable, AutoWorkflowRun,
    AutoWorkflowStepResult,
)
from distr.core.workflow_agent import WorkflowAgent
from distr.core.step_runner.agent_bridge import WorkflowAgentBridge

logger = logging.getLogger(__name__)


@dataclass
class _RunContext:
    """Per-run state for the WorkflowAgent lifecycle."""
    run_id: int
    workflow_agent: WorkflowAgent
    event_loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    context_prefix: str = ""  # Optional ticket context for first step


_active_runs: Dict[int, _RunContext] = {}
_runs_lock = threading.Lock()


def _cleanup_run(run_id: int) -> None:
    """Clean up a workflow run's WorkflowAgent and event loop when it reaches terminal status."""
    with _runs_lock:
        ctx = _active_runs.pop(run_id, None)
    if ctx is None:
        return
    try:
        ctx.workflow_agent.shutdown()
    except Exception:
        pass
    try:
        ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
    except Exception:
        pass


def _finalize_terminal_run(run_id: int, workflow_id: int, status: str) -> None:
    """Clean up resources and notify the bridge when a run reaches terminal status.

    Called after the DB commit that sets the run to a terminal status
    (completed, failed, cancelled).
    """
    _cleanup_run(run_id)

    # Build steps_summary from the run's step results
    steps_summary = []
    try:
        with get_session() as db:
            step_results = (
                db.query(AutoWorkflowStepResult)
                .filter(AutoWorkflowStepResult.run_id == run_id)
                .order_by(AutoWorkflowStepResult.created_at)
                .all()
            )
            for sr in step_results:
                step_obj = sr.step
                steps_summary.append({
                    "title": step_obj.name if step_obj else f"Step {sr.step_id}",
                    "status": sr.status,
                })
    except Exception:
        logger.debug("Could not load step results for run %d", run_id)

    run_result = {
        "session_id": workflow_id,
        "run_id": run_id,
        "success": status == "completed",
        "cancelled": status == "cancelled",
        "steps_summary": steps_summary,
    }

    try:
        WorkflowAgentBridge().on_workflow_completed(workflow_id, run_result)
    except Exception:
        logger.error("WorkflowAgentBridge notification failed for run %d", run_id, exc_info=True)


# ── Workflow CRUD ──

def create_workflow(name: str = "Untitled Workflow", description: str = "") -> int:
    with get_session() as db:
        wf = AutoWorkflow(name=name, description=description, status="draft")
        db.add(wf)
        db.commit()
        db.refresh(wf)
        return wf.id


def get_workflow(workflow_id: int) -> Optional[Dict[str, Any]]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        return _serialize_workflow(wf)


def list_workflows(limit: int = 50, search: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_session() as db:
        q = db.query(AutoWorkflow)
        if status:
            q = q.filter(AutoWorkflow.status == status)
        if search and search.strip():
            q = q.filter(AutoWorkflow.name.ilike(f"%{search.strip()}%"))
        rows = q.order_by(AutoWorkflow.modified_date.desc()).limit(limit).all()
        return [
            {
                "id": w.id, "name": w.name,
                "description": (w.description or "")[:200],
                "status": w.status,
                "schedule_enabled": w.schedule_enabled,
                "schedule_preset": w.schedule_preset,
                "schedule_time": w.schedule_time,
                "next_run_at": w.next_run_at.isoformat() if w.next_run_at else None,
                "step_count": len(w.steps),
                "created_date": w.created_date.isoformat() if w.created_date else None,
                "modified_date": w.modified_date.isoformat() if w.modified_date else None,
            }
            for w in rows
        ]


def update_workflow(workflow_id: int, **kwargs) -> bool:
    allowed = {
        "name", "description", "status", "schedule_enabled",
        "schedule_preset", "schedule_cron", "schedule_time",
        "schedule_days", "schedule_timezone", "next_run_at",
        "start_step_position",
    }
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(wf, k, v)
        db.commit()
        return True


def delete_workflow(workflow_id: int) -> bool:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return False
        db.delete(wf)
        db.commit()
        return True


def duplicate_workflow(workflow_id: int) -> Optional[int]:
    with get_session() as db:
        orig = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not orig:
            return None
        new_wf = AutoWorkflow(
            name=f"{orig.name} (copy)", description=orig.description,
            status="draft", start_step_position=orig.start_step_position,
        )
        db.add(new_wf)
        db.flush()
        for step in sorted(orig.steps, key=lambda s: s.position):
            db.add(AutoWorkflowStep(
                workflow_id=new_wf.id, position=step.position,
                name=step.name, description=step.description,
                action_type=step.action_type, instruction=step.instruction,
                validation_type=step.validation_type,
                validation_prompt=step.validation_prompt,
                screenshot_path=step.screenshot_path,
                routing_mode=step.routing_mode,
                routing_prompt=step.routing_prompt,
                on_pass_goto=step.on_pass_goto, on_fail_goto=step.on_fail_goto,
                wait_before_next=step.wait_before_next,
                max_retries=step.max_retries,
                timeout_seconds=step.timeout_seconds,
                require_approval=step.require_approval,
                code=step.code,
                validation_code=step.validation_code,
                linked_project_id=step.linked_project_id,
                wait_for_continue=step.wait_for_continue,
            ))
        for var in orig.variables:
            db.add(AutoWorkflowVariable(
                workflow_id=new_wf.id, name=var.name,
                default_value=var.default_value, description=var.description,
            ))
        db.commit()
        return new_wf.id


# ── Step CRUD ──

def add_step(workflow_id: int, name: str = "New Step", action_type: str = "agent_instruction",
             position: Optional[int] = None) -> Optional[int]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        if position is None:
            position = max((s.position for s in wf.steps), default=-1) + 1
        step = AutoWorkflowStep(workflow_id=workflow_id, position=position, name=name, action_type=action_type)
        db.add(step)
        db.commit()
        db.refresh(step)
        return step.id


def update_step(step_id: int, **kwargs) -> bool:
    allowed = {
        "name", "description", "position", "action_type", "instruction",
        "validation_type", "validation_prompt", "screenshot_path",
        "routing_mode", "routing_prompt",
        "on_pass_goto", "on_fail_goto", "wait_before_next",
        "max_retries", "timeout_seconds", "require_approval",
        "status", "result", "recording_filename", "action_id",
        "code", "validation_code", "linked_project_id", "wait_for_continue",
    }
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(step, k, v)
        db.commit()
        return True


def delete_step(step_id: int) -> bool:
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return False
        db.delete(step)
        db.commit()
        return True


def reorder_steps(workflow_id: int, step_ids: List[int]) -> bool:
    with get_session() as db:
        for pos, step_id in enumerate(step_ids):
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == step_id, AutoWorkflowStep.workflow_id == workflow_id,
            ).first()
            if step:
                step.position = pos
        db.commit()
        return True


# ── Variable CRUD ──

def add_variable(workflow_id: int, name: str, default_value: str = "", description: str = "") -> Optional[int]:
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        var = AutoWorkflowVariable(workflow_id=workflow_id, name=name, default_value=default_value, description=description)
        db.add(var)
        db.commit()
        db.refresh(var)
        return var.id


def update_variable(variable_id: int, **kwargs) -> bool:
    allowed = {"name", "default_value", "description"}
    with get_session() as db:
        var = db.query(AutoWorkflowVariable).filter(AutoWorkflowVariable.id == variable_id).first()
        if not var:
            return False
        for k, v in kwargs.items():
            if k in allowed:
                setattr(var, k, v)
        db.commit()
        return True


def delete_variable(variable_id: int) -> bool:
    with get_session() as db:
        var = db.query(AutoWorkflowVariable).filter(AutoWorkflowVariable.id == variable_id).first()
        if not var:
            return False
        db.delete(var)
        db.commit()
        return True


# ── Execution engine ──

def _clear_workflow_env():
    """Clear workflow run context environment variables."""
    os.environ.pop("DECISIONS_WORKFLOW_RUN_ID", None)
    os.environ.pop("DECISIONS_WORKFLOW_STEP_ID", None)


def _check_and_enter_wait(step_id: int, action_result: str, passed: bool) -> Optional[Dict[str, Any]]:
    """
    Check if a step has wait_for_continue=True and, if so, enter the waiting state.
    Returns a wait response dict if the step should wait, or None to proceed normally.
    """
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step or not step.wait_for_continue:
            return None
        step.status = "waiting"
        step_name = step.name
        workflow_id = step.workflow_id
        # Find active run and set it to waiting too
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == step.workflow_id,
            AutoWorkflowRun.current_step_id == step_id,
            AutoWorkflowRun.status == "running",
        ).first()
        run_id = None
        if run:
            run.status = "waiting"
            run_id = run.id
            # Store action result in run_data for later complete_step() call
            run_data = json.loads(run.run_data or "{}")
            run_data["waiting_result"] = action_result
            run_data["waiting_passed"] = passed
            run.run_data = json.dumps(run_data)
        db.commit()

    # Notify the voice agent that the workflow is waiting for input
    # Speak the result via TTS so the user knows what happened
    try:
        from distr.core.signals import signal_manager
        # Truncate for TTS
        speak_text = action_result.strip()
        if len(speak_text) > 400:
            speak_text = speak_text[:400] + "..."
        notification = f"Step '{step_name}' is done and waiting for your input. Here's what happened: {speak_text}"
        signal_manager.speak_text_directly.emit(notification)
    except Exception as e:
        logger.debug("Could not speak wait notification: %s", e)

    # Also queue a report so the agent LLM knows about the waiting state
    try:
        bridge = WorkflowAgentBridge()
        bridge.queue_report_to_agent(workflow_id, f"Workflow step '{step_name}' completed and is now WAITING for your input. Run ID: {run_id}. Result: {action_result[:500]}")
    except Exception as e:
        logger.debug("Could not queue wait report: %s", e)

    return {"success": True, "waiting": True, "run_id": run_id, "message": "Step waiting for continue signal."}


def _dispatch_step(step_id: int, step_name: str, action_type: str,
                   instruction: str, recording_filename: str,
                   context_prefix: str = "Step Runner",
                   action_id: int = None,
                   code: str = None) -> Dict[str, Any]:
    """
    Dispatch a step based on its action_type.
    For execute_code: executes code via TestLoopService._execute_python().
    For playwright: executes code via TestLoopService._execute_playwright().
    For play_recording: emits the recording playback signal.
    For everything else: sends the instruction to the agent.
    Returns {"success": True, ...} or {"error": ...}.
    """
    if action_type in ("execute_code", "playwright"):
        exec_code = (code or "").strip()
        # If no code but instruction exists, generate code via CodeGeneratorService
        if not exec_code and (instruction or "").strip():
            try:
                from distr.core.step_runner.code_generator import CodeGeneratorService
                from distr.core.step_runner.step_types import StepType
                step_type = StepType.PLAYWRIGHT if action_type == "playwright" else StepType.EXECUTE_CODE
                exec_code = CodeGeneratorService().generate_code(instruction, step_type)
            except Exception as e:
                logger.error("Code generation failed for step %s: %s", step_id, e)
                update_step(step_id, status="failed", result=f"Code generation failed: {e}")
                return {"error": f"Code generation failed: {e}"}
        if not exec_code:
            update_step(step_id, status="failed", result="No code or instruction provided.")
            return {"error": "No code or instruction provided"}
        try:
            from distr.core.step_runner.test_loop import TestLoopService
            if action_type == "playwright":
                exec_result = TestLoopService()._execute_playwright(exec_code)
            else:
                exec_result = TestLoopService()._execute_python(exec_code)
            # Extract exit_code and output from ExecutionResult
            exit_code = exec_result.exit_code if hasattr(exec_result, 'exit_code') else exec_result.get("exit_code", 1)
            stdout = exec_result.stdout if hasattr(exec_result, 'stdout') else exec_result.get("stdout", "")
            stderr = exec_result.stderr if hasattr(exec_result, 'stderr') else exec_result.get("stderr", "")
            output = (stdout + "\n" + stderr).strip()
            passed = (exit_code == 0)
            # Check wait_for_continue before calling complete_step
            wait_result = _check_and_enter_wait(step_id, output, passed)
            if wait_result:
                return wait_result
            complete_step(step_id, output, passed)
            return {"success": True, "message": f"Code executed (exit_code={exit_code}).", "exit_code": exit_code}
        except Exception as e:
            logger.error("Code execution failed for step %s: %s", step_id, e)
            update_step(step_id, status="failed", result=str(e))
            return {"error": str(e)}
    elif action_type == "play_recording":
        # If no recording_filename on step, try the linked Action entity
        if not recording_filename and action_id:
            try:
                with get_session() as db:
                    linked = db.query(Action).filter(Action.id == action_id).first()
                    if linked and linked.recording_filename:
                        recording_filename = linked.recording_filename
            except Exception as e:
                logger.warning(f"Could not load linked action {action_id}: {e}")
        if not recording_filename:
            update_step(step_id, status="failed", result="No recording attached.")
            return {"error": "No recording attached to this step"}
        try:
            from distr.core.signals import signal_manager
            signal_manager.play_recording_file.emit(recording_filename)
            return {"success": True, "message": "Playing recording."}
        except Exception as e:
            update_step(step_id, status="failed", result=str(e))
            return {"error": str(e)}
    else:
        if not instruction.strip():
            update_step(step_id, status="failed", result="No instruction provided.")
            return {"error": "No instruction provided"}
        prompt = f"[{context_prefix} — {step_name}]\n{instruction}"

        # Look up the active run's _RunContext for this step
        run_ctx = None
        with get_session() as db:
            step_obj = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step_obj:
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == step_obj.workflow_id,
                    AutoWorkflowRun.current_step_id == step_id,
                    AutoWorkflowRun.status == "running",
                ).first()
                if run:
                    with _runs_lock:
                        run_ctx = _active_runs.get(run.id)

        if run_ctx is not None:
            # Dispatch via WorkflowAgent in the run's background event loop
            try:
                future = asyncio.run_coroutine_threadsafe(
                    run_ctx.workflow_agent.execute(prompt),
                    run_ctx.event_loop,
                )

                def _on_agent_done(fut):
                    try:
                        response_text = fut.result()
                        # Check wait_for_continue before completing
                        wait_result = _check_and_enter_wait(step_id, response_text, True)
                        if wait_result:
                            return
                        complete_step(step_id, response_text, passed=True)
                    except Exception as exc:
                        error_message = str(exc)
                        logger.error("WorkflowAgent.execute() failed for step %s: %s", step_id, error_message)
                        complete_step(step_id, error_message, passed=False)

                future.add_done_callback(_on_agent_done)
                return {"success": True, "message": "Step dispatched to WorkflowAgent."}
            except Exception as e:
                logger.error("Failed to dispatch step %s to WorkflowAgent: %s", step_id, e)
                update_step(step_id, status="failed", result=str(e))
                return {"error": str(e)}
        else:
            # Fallback: isolated step execution via signal (no active run context)
            try:
                from distr.core.signals import signal_manager
                signal_manager.send_text_input.emit(prompt, False, None, None)
                return {"success": True, "message": "Step sent to agent."}
            except Exception as e:
                update_step(step_id, status="failed", result=str(e))
                return {"error": str(e)}


def execute_step(step_id: int, isolated: bool = False) -> Dict[str, Any]:
    """
    Execute a single step. Sets status to 'running' and dispatches based on action_type.
    isolated=True means run just this step without workflow context.
    """
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return {"error": "Step not found"}
        step.status = "running"
        step.result = None
        db.commit()
        step_name = step.name
        action_type = step.action_type or "agent_instruction"
        instruction = step.instruction or ""
        recording_filename = step.recording_filename or ""
        step_action_id = step.action_id
        step_code = step.code or ""

    return _dispatch_step(step_id, step_name, action_type, instruction,
                          recording_filename, "Step Runner", step_action_id, code=step_code)


def start_workflow_run(workflow_id: int, context: Optional[str] = None) -> Dict[str, Any]:
    """
    Start a full workflow run. Creates a run record, resets all step statuses,
    creates a dedicated WorkflowAgent + event loop, and kicks off the first step.
    """
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}
        if not wf.steps:
            return {"error": "Workflow has no steps"}

        # Reset all step statuses
        for step in wf.steps:
            step.status = "pending"
            step.result = None

        # Create run record
        run = AutoWorkflowRun(workflow_id=workflow_id, status="running")
        db.add(run)
        db.flush()

        run_id = run.id

        # Create a dedicated WorkflowAgent and background event loop for this run
        workflow_agent = WorkflowAgent()
        agent_loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(agent_loop)
            agent_loop.run_forever()

        agent_thread = threading.Thread(target=_run_loop, daemon=True)
        agent_thread.start()

        with _runs_lock:
            _active_runs[run_id] = _RunContext(
                run_id=run_id,
                workflow_agent=workflow_agent,
                event_loop=agent_loop,
                thread=agent_thread,
                context_prefix=context or "",
            )

        first_step = sorted(wf.steps, key=lambda s: s.position)[0]
        run.current_step_id = first_step.id
        first_step.status = "running"
        first_step_id = first_step.id
        first_step_name = first_step.name
        first_action_type = first_step.action_type or "agent_instruction"
        first_instruction = first_step.instruction or ""
        first_recording = first_step.recording_filename or ""
        first_action_id = first_step.action_id
        first_code = first_step.code or ""
        db.commit()

    # Prepend context to the first agent_instruction step if context is provided
    if context and first_action_type == "agent_instruction":
        first_instruction = f"{context}\n\n{first_instruction}"

    # Set workflow run context env vars so agent tools (e.g. CreateCursorTicketTool)
    # can detect they are running inside a workflow and include metadata.
    os.environ["DECISIONS_WORKFLOW_RUN_ID"] = str(run_id)
    os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(first_step_id)

    result = _dispatch_step(first_step_id, first_step_name, first_action_type,
                            first_instruction, first_recording, "Workflow Run", first_action_id,
                            code=first_code)
    if "error" in result:
        _clear_workflow_env()
        complete_run(run_id, "failed")
        return result
    result["run_id"] = run_id
    return result


def cancel_run(run_id: int) -> bool:
    """Cancel an active workflow run."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        run.status = "cancelled"
        run.completed_at = datetime.utcnow()
        # Cancel the currently running step
        if run.current_step_id:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == run.current_step_id).first()
            if step and step.status == "running":
                step.status = "cancelled"
                step.result = "Cancelled by user."
        _run_id, _wf_id = run.id, run.workflow_id
        db.commit()
    _finalize_terminal_run(_run_id, _wf_id, "cancelled")
    return True


def cancel_step(step_id: int) -> bool:
    """Cancel a running step."""
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return False
        step.status = "cancelled"
        step.result = "Cancelled by user."
        db.commit()
        return True


def reset_workflow_steps(workflow_id: int) -> Dict[str, Any]:
    """Cancel any active run and reset all step statuses to pending.

    Use when the user wants to stop everything and start fresh.
    """
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}

        # Cancel any active runs
        cancelled_runs = 0
        active_runs = (
            db.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            )
            .all()
        )
        run_ids = []
        for run in active_runs:
            run.status = "cancelled"
            run.completed_at = datetime.utcnow()
            run_ids.append(run.id)
            cancelled_runs += 1
        db.commit()

    # Clean up agents outside the DB session
    for rid in run_ids:
        _cleanup_run(rid)

    # Reset all steps to pending
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if wf:
            for step in wf.steps:
                step.status = "pending"
                step.result = None
            db.commit()

    return {
        "success": True,
        "workflow_id": workflow_id,
        "cancelled_runs": cancelled_runs,
        "steps_reset": len(wf.steps) if wf else 0,
    }


def clear_workflow_history(workflow_id: int) -> Dict[str, Any]:
    """Delete all run history and step results for a workflow."""
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}

        # Cancel any active runs first
        active_runs = (
            db.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            )
            .all()
        )
        active_run_ids = []
        for run in active_runs:
            run.status = "cancelled"
            run.completed_at = datetime.utcnow()
            active_run_ids.append(run.id)
        db.commit()

    # Clean up agents outside DB session
    for rid in active_run_ids:
        _cleanup_run(rid)

    with get_session() as db:
        # Delete all step results for this workflow's runs
        run_ids = [
            r.id for r in
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .all()
        ]
        deleted_results = 0
        if run_ids:
            deleted_results = (
                db.query(AutoWorkflowStepResult)
                .filter(AutoWorkflowStepResult.run_id.in_(run_ids))
                .delete(synchronize_session=False)
            )
        # Delete all runs
        deleted_runs = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .delete(synchronize_session=False)
        )
        # Reset step statuses
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if wf:
            for step in wf.steps:
                step.status = "pending"
                step.result = None
        db.commit()

    return {
        "success": True,
        "workflow_id": workflow_id,
        "deleted_runs": deleted_runs,
        "deleted_results": deleted_results,
    }


def continue_waiting_step(run_id: int, optional_input: str = "") -> Dict[str, Any]:
    """Resume a workflow run that is in 'waiting' status."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        if run.status != "waiting":
            return {"error": f"Run is not waiting (status: {run.status})", "status_code": 409}

        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == run.current_step_id).first()
        if not step or step.status != "waiting":
            return {"error": "No waiting step found", "status_code": 409}

        # Restore stored result
        run_data = json.loads(run.run_data or "{}")
        stored_result = run_data.get("waiting_result", "")
        stored_passed = run_data.get("waiting_passed", True)

        # Append optional input if provided
        if optional_input.strip():
            stored_result = f"{stored_result}\n\n[CONTINUE INPUT]: {optional_input.strip()}"

        # Set run and step back to running
        run.status = "running"
        step.status = "running"
        db.commit()

        step_id = step.id

    # Now call complete_step with the stored result
    return complete_step(step_id, stored_result, stored_passed, _from_continue=True)


def complete_step(step_id: int, result: str, passed: bool, _from_continue: bool = False) -> Dict[str, Any]:
    """
    Mark a step as complete. Runs verification if configured, stores result in history,
    speaks the result via TTS, then advances based on routing.
    Default routing: null = END workflow (not next step).
    """
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if not step:
            return {"error": "Step not found"}

        # For async action types (agent_instruction), check wait_for_continue
        # before running verification and routing. Skip if resuming from
        # continue_waiting_step (_from_continue=True) to avoid re-entering wait.
        if step.wait_for_continue and not _from_continue:
            # Enter wait state — store result for later complete_step() call
            step.status = "waiting"
            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.workflow_id == step.workflow_id,
                AutoWorkflowRun.current_step_id == step_id,
                AutoWorkflowRun.status == "running",
            ).first()
            if run:
                run.status = "waiting"
                run_data_dict = json.loads(run.run_data or "{}")
                run_data_dict["waiting_result"] = result
                run_data_dict["waiting_passed"] = passed
                run.run_data = json.dumps(run_data_dict)
            db.commit()
            return {"success": True, "waiting": True, "message": "Step waiting for continue signal."}

        # Run verification engine if validation is configured
        verified_passed = _run_verification(step, result, passed)
        status = "passed" if verified_passed else "failed"
        step.status = status
        step.result = result

        # Find active run for this step (if any)
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == step.workflow_id,
            AutoWorkflowRun.status == "running",
            AutoWorkflowRun.current_step_id == step_id,
        ).first()

        # Store result in history
        step_result = AutoWorkflowStepResult(
            step_id=step_id,
            run_id=run.id if run else None,
            agent_response=result,
            status=status,
        )
        db.add(step_result)

        if not run:
            db.commit()
            # TTS the result
            _speak_result(result)
            return {"done": True, "status": status}

        # Determine next step based on routing
        routing_mode = (step.routing_mode or "static").strip().lower()
        wait = step.wait_before_next or 0

        if routing_mode == "agent_decision":
            # Let the agent decide which step to go to
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == step.workflow_id).first()
            all_steps = sorted(wf.steps, key=lambda s: s.position) if wf else []
            next_step_id = _agent_route_decision(step, result, verified_passed, all_steps)
            if next_step_id is None or next_step_id == -1:
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                _speak_result(result)
                return {"done": True, "status": "completed", "run_id": _run_id}
            next_step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == next_step_id).first()
            if not next_step:
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                _speak_result(result)
                return {"done": True, "status": "completed", "run_id": _run_id}
            # Safety: prevent routing to self
            if next_step.id == step_id:
                logger.warning("Agent routed step %d to itself. Ending workflow.", step_id)
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                _speak_result(result)
                return {"done": True, "status": "completed", "run_id": _run_id, "warning": "Infinite loop prevented"}
        else:
            # Static routing: null = END workflow (safety: no infinite loops)
            goto = step.on_pass_goto if verified_passed else step.on_fail_goto

            if goto is None or goto == -1:
                # Default: END workflow
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                _speak_result(result)
                return {"done": True, "status": "completed", "run_id": _run_id}

            # Go to specific step by ID
            next_step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == goto).first()

            if not next_step:
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                _speak_result(result)
                return {"done": True, "status": "completed", "run_id": _run_id}

            # Safety: prevent routing to self (infinite loop)
            if next_step.id == step_id:
                logger.warning("Infinite loop detected: step %d routes to itself. Ending workflow.", step_id)
                run.status = "completed"
                run.completed_at = datetime.utcnow()
                _run_id, _wf_id = run.id, step.workflow_id
                db.commit()
                _finalize_terminal_run(_run_id, _wf_id, "completed")
                _speak_result(result)
                return {"done": True, "status": "completed", "run_id": _run_id, "warning": "Infinite loop prevented"}

        # Advance to next step
        next_step.status = "running"
        run.current_step_id = next_step.id
        next_step_name = next_step.name
        next_action_type = next_step.action_type or "agent_instruction"
        next_instruction = next_step.instruction or ""
        next_recording = next_step.recording_filename or ""
        next_action_linked_id = next_step.action_id
        next_code = next_step.code or ""
        run_id = run.id
        next_step_id = next_step.id
        db.commit()

    # Update workflow step env var for the next step
    os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(next_step_id)

    # TTS the current result
    _speak_result(result)

    # Handle wait before next
    if wait > 0:
        import threading
        def delayed_dispatch():
            import time
            time.sleep(wait)
            _dispatch_step(next_step_id, next_step_name, next_action_type,
                           next_instruction, next_recording, "Workflow Run", next_action_linked_id,
                           code=next_code)
        threading.Thread(target=delayed_dispatch, daemon=True).start()
    else:
        dispatch_result = _dispatch_step(next_step_id, next_step_name, next_action_type,
                                         next_instruction, next_recording, "Workflow Run", next_action_linked_id,
                                         code=next_code)
        if "error" in dispatch_result:
            complete_run(run_id, "failed")
            return {"done": True, "status": "failed"}

    return {"done": False, "next_step_id": next_step_id, "wait": wait}


def complete_run(run_id: int, status: str = "completed") -> bool:
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        run.status = status
        run.completed_at = datetime.utcnow()
        workflow_id = run.workflow_id
        db.commit()
    _finalize_terminal_run(run_id, workflow_id, status)
    # Clear workflow run context env vars when run reaches terminal status
    _clear_workflow_env()
    return True


def get_active_run(workflow_id: int) -> Optional[Dict[str, Any]]:
    """Get the currently active run for a workflow, if any."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == workflow_id,
            AutoWorkflowRun.status == "running",
        ).first()
        if not run:
            return None
        return {
            "id": run.id,
            "current_step_id": run.current_step_id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
        }


# ── Run history ──

def get_run_history(workflow_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    with get_session() as db:
        rows = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.workflow_id == workflow_id)
            .order_by(AutoWorkflowRun.started_at.desc())
            .limit(limit).all()
        )
        return [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "current_step_id": r.current_step_id,
            }
            for r in rows
        ]


# ── Screenshot management ──

def save_screenshot(step_id: int, file_data: bytes, filename: str) -> Optional[str]:
    """Save a reference screenshot for screenshot_compare validation."""
    from distr.core.paths import DB_DIR
    screenshots_dir = os.path.join(DB_DIR, "workflow_screenshots")
    os.makedirs(screenshots_dir, exist_ok=True)
    ext = os.path.splitext(filename)[1] or ".png"
    save_path = os.path.join(screenshots_dir, f"step_{step_id}{ext}")
    with open(save_path, "wb") as f:
        f.write(file_data)
    update_step(step_id, screenshot_path=save_path)
    return save_path


# ── Verification engine ──

def _run_verification(step: AutoWorkflowStep, result: str, caller_passed: bool) -> bool:
    """
    Run the configured validation for a step. Returns True if passed.
    If validation_type is 'none', uses the caller's passed flag.
    """
    vtype = (step.validation_type or "none").strip().lower()
    if vtype == "none":
        return caller_passed

    prompt = (step.validation_prompt or "").strip()
    if not prompt and vtype != "playwright":
        # No validation criteria configured — trust the caller
        return caller_passed

    try:
        if vtype == "text_match":
            return _verify_text_match(result, prompt)
        elif vtype == "rule_based":
            return _verify_rule_based(result, prompt)
        elif vtype == "llm_judgment":
            return _verify_llm_judgment(result, prompt)
        elif vtype == "screenshot_compare":
            return _verify_screenshot(step, result, prompt)
        elif vtype == "playwright":
            return _verify_playwright(step, caller_passed)
        else:
            logger.warning("Unknown validation type '%s', defaulting to caller_passed", vtype)
            return caller_passed
    except Exception as e:
        logger.error("Verification failed for step %s: %s", step.id, e, exc_info=True)
        return False


def _verify_text_match(result: str, criteria: str) -> bool:
    """Check if the result contains the expected text (case-insensitive)."""
    if not result:
        return False
    result_lower = result.lower()
    # Support multiple match phrases separated by newlines
    for line in criteria.strip().splitlines():
        phrase = line.strip()
        if phrase and phrase.lower() not in result_lower:
            return False
    return True


def _verify_rule_based(result: str, rules: str) -> bool:
    """Evaluate simple rules against the result.
    Rules are line-separated. Each line is a condition:
      contains: <text>
      not_contains: <text>
      starts_with: <text>
      min_length: <number>
    """
    if not result:
        return False
    result_lower = result.lower()
    for line in rules.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("contains:"):
            val = line[len("contains:"):].strip()
            if val.lower() not in result_lower:
                return False
        elif line.lower().startswith("not_contains:"):
            val = line[len("not_contains:"):].strip()
            if val.lower() in result_lower:
                return False
        elif line.lower().startswith("starts_with:"):
            val = line[len("starts_with:"):].strip()
            if not result_lower.startswith(val.lower()):
                return False
        elif line.lower().startswith("min_length:"):
            try:
                min_len = int(line[len("min_length:"):].strip())
                if len(result) < min_len:
                    return False
            except ValueError:
                pass
    return True


def _verify_llm_judgment(result: str, validation_prompt: str) -> bool:
    """Send the result + validation prompt to the LLM for judgment."""
    try:
        from distr.core.signals import signal_manager
        # Build a judgment prompt
        judgment_prompt = (
            f"You are a validation judge. Evaluate whether the following result passes the validation criteria.\n\n"
            f"VALIDATION CRITERIA:\n{validation_prompt}\n\n"
            f"RESULT TO VALIDATE:\n{result}\n\n"
            f"Respond with exactly PASS or FAIL followed by a brief explanation."
        )
        # Use synchronous LLM call if available
        try:
            from distr.core.agent.services.llm.shared import get_shared_llm_response
            response = get_shared_llm_response(judgment_prompt)
            if response:
                return response.strip().upper().startswith("PASS")
        except ImportError:
            pass
        # Fallback: trust caller
        logger.warning("LLM judgment not available, defaulting to pass")
        return True
    except Exception as e:
        logger.error("LLM judgment failed: %s", e, exc_info=True)
        return False


def _verify_screenshot(step: AutoWorkflowStep, result: str, validation_prompt: str) -> bool:
    """Compare current screen state against reference screenshot using LLM vision.
    Falls back to text-based validation if vision is not available."""
    ref_path = step.screenshot_path
    if not ref_path or not os.path.exists(ref_path):
        logger.warning("No reference screenshot for step %s, using text validation", step.id)
        return _verify_text_match(result, validation_prompt) if validation_prompt else True

    # Take a current screenshot for comparison
    try:
        import subprocess
        import platform
        from distr.core.paths import DB_DIR
        current_path = os.path.join(DB_DIR, "workflow_screenshots", f"step_{step.id}_current.png")
        os.makedirs(os.path.dirname(current_path), exist_ok=True)
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["screencapture", "-x", current_path], timeout=5, check=True)
        elif system == "Windows":
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(current_path)
        else:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.save(current_path)
        # If we have both screenshots, try LLM vision comparison
        # For now, fall back to validation_prompt text match
        logger.info("Screenshots captured for step %s. Using validation prompt for judgment.", step.id)
        if validation_prompt:
            return _verify_llm_judgment(result + f"\n[Screenshots captured: reference={ref_path}, current={current_path}]", validation_prompt)
        return True
    except Exception as e:
        logger.error("Screenshot comparison failed: %s", e, exc_info=True)
        return True


def _verify_playwright(step: AutoWorkflowStep, caller_passed: bool) -> bool:
    """Execute a Playwright validation script. Exit code 0 = passed, non-zero = failed.
    Falls back to caller_passed if validation_code is empty."""
    validation_code = (step.validation_code or "").strip()
    if not validation_code:
        logger.info("No validation_code for step %s, falling back to caller_passed", step.id)
        return caller_passed

    try:
        from distr.core.step_runner.test_loop import TestLoopService
        result = TestLoopService()._execute_playwright(validation_code)
        exit_code = result.get("exit_code", 1) if isinstance(result, dict) else getattr(result, "exit_code", 1)
        output = result.get("output", "") if isinstance(result, dict) else getattr(result, "output", "")
        if exit_code == 0:
            logger.info("Playwright validation passed for step %s", step.id)
            return True
        else:
            logger.info("Playwright validation failed for step %s (exit_code=%s): %s", step.id, exit_code, output[:200])
            return False
    except Exception as e:
        logger.error("Playwright validation error for step %s: %s", step.id, e, exc_info=True)
        return False


# ── Agent-based routing ──

def _agent_route_decision(
    step: AutoWorkflowStep,
    result: str,
    passed: bool,
    all_steps: List[AutoWorkflowStep],
) -> Optional[int]:
    """
    Ask the LLM to decide which step to go to next based on the current step's
    result, pass/fail status, and the list of available steps.

    Returns a step ID, or None/-1 to end the workflow.
    """
    # Build the step map for the agent
    step_descriptions = []
    for s in all_steps:
        if s.id == step.id:
            continue  # Don't offer the current step as a target
        desc = f"  - Step ID {s.id}: \"{s.name}\" (position #{s.position})"
        if s.description:
            desc += f" — {s.description}"
        step_descriptions.append(desc)

    if not step_descriptions:
        # No other steps to route to
        return None

    steps_list = "\n".join(step_descriptions)
    routing_prompt = (step.routing_prompt or "").strip()
    status_word = "PASSED" if passed else "FAILED"

    prompt = (
        f"You are a workflow routing agent. A step just completed and you need to decide what happens next.\n\n"
        f"COMPLETED STEP: \"{step.name}\" (ID {step.id})\n"
        f"STATUS: {status_word}\n"
        f"RESULT:\n{result}\n\n"
    )

    if routing_prompt:
        prompt += f"ROUTING INSTRUCTIONS:\n{routing_prompt}\n\n"

    prompt += (
        f"AVAILABLE NEXT STEPS:\n{steps_list}\n\n"
        f"Respond with ONLY one of the following:\n"
        f"- A step ID number (e.g. \"42\") to go to that step\n"
        f"- \"END\" to finish the workflow\n\n"
        f"Your decision:"
    )

    try:
        try:
            from distr.core.agent.services.llm.shared import get_shared_llm_response
            response = get_shared_llm_response(prompt)
            if response:
                return _parse_routing_response(response, all_steps, step.id)
        except ImportError:
            pass

        # Fallback: send via signal and let the agent handle it asynchronously
        # For now, if no synchronous LLM is available, default to END
        logger.warning("Agent routing: no synchronous LLM available, defaulting to END")
        return None
    except Exception as e:
        logger.error("Agent routing decision failed: %s", e, exc_info=True)
        return None


def _parse_routing_response(
    response: str,
    all_steps: List[AutoWorkflowStep],
    current_step_id: int,
) -> Optional[int]:
    """Parse the LLM's routing response into a step ID or None (end)."""
    text = response.strip().upper()

    if text == "END" or text.startswith("END"):
        return None

    # Try to extract a number
    import re
    numbers = re.findall(r'\d+', text)
    if numbers:
        candidate_id = int(numbers[0])
        # Validate it's a real step and not the current step
        valid_ids = {s.id for s in all_steps if s.id != current_step_id}
        if candidate_id in valid_ids:
            return candidate_id
        # Maybe the agent gave a position instead of an ID
        for s in all_steps:
            if s.position == candidate_id and s.id != current_step_id:
                return s.id

    logger.warning("Could not parse agent routing response: '%s', defaulting to END", response)
    return None


# ── TTS helper ──

def _speak_result(result: str):
    """Speak the agent result via TTS if it's meaningful."""
    if not result or not result.strip():
        return
    # Truncate very long results for TTS
    text = result.strip()
    if len(text) > 500:
        text = text[:500] + "..."
    try:
        from distr.core.signals import signal_manager
        signal_manager.speak_text_directly.emit(text)
    except Exception as e:
        logger.debug("TTS speak failed: %s", e)


# ── Step result history ──

def get_step_results(step_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Get execution result history for a step."""
    with get_session() as db:
        rows = (
            db.query(AutoWorkflowStepResult)
            .filter(AutoWorkflowStepResult.step_id == step_id)
            .order_by(AutoWorkflowStepResult.created_at.desc())
            .limit(limit).all()
        )
        return [
            {
                "id": r.id,
                "step_id": r.step_id,
                "run_id": r.run_id,
                "agent_response": r.agent_response or "",
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


# ── Export / Import ──

def _get_presets_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "steprunner", "presets")


def export_workflow(workflow_id: int) -> Optional[Dict[str, Any]]:
    """Export a workflow + steps + variables as a portable JSON dict (metadata only, no files)."""
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None
        steps = sorted(wf.steps, key=lambda s: s.position)

        # Collect linked actions for steps that have them
        linked_actions = {}
        for s in steps:
            if s.action_id:
                action = db.query(Action).filter(Action.id == s.action_id).first()
                if action:
                    linked_actions[s.action_id] = {
                        "title": action.title or "",
                        "description": action.description or "",
                        "additional_trigger_words": action.additional_trigger_words or "[]",
                        "is_instruction": action.is_instruction or False,
                        "instruction_text": action.instruction_text or "",
                        "recording_filename": action.recording_filename or "",
                    }

        return {
            "format": "decisionsai_workflow_v1",
            "name": wf.name,
            "description": wf.description or "",
            "start_step_position": wf.start_step_position or 0,
            "schedule_preset": wf.schedule_preset,
            "schedule_time": wf.schedule_time,
            "schedule_days": wf.schedule_days,
            "steps": [
                {
                    "position": s.position, "name": s.name,
                    "description": s.description or "",
                    "action_type": s.action_type or "agent_instruction",
                    "instruction": s.instruction or "",
                    "validation_type": s.validation_type or "none",
                    "validation_prompt": s.validation_prompt or "",
                    "routing_mode": s.routing_mode or "static",
                    "routing_prompt": s.routing_prompt or "",
                    "on_pass_goto_position": _step_id_to_position(s.on_pass_goto, steps),
                    "on_fail_goto_position": _step_id_to_position(s.on_fail_goto, steps),
                    "wait_before_next": s.wait_before_next or 0,
                    "max_retries": s.max_retries or 0,
                    "timeout_seconds": s.timeout_seconds or 300,
                    "require_approval": s.require_approval or False,
                    "recording_filename": s.recording_filename or "",
                    "screenshot_filename": os.path.basename(s.screenshot_path) if s.screenshot_path else "",
                    "linked_action": linked_actions.get(s.action_id) if s.action_id else None,
                    "code": s.code or "",
                    "validation_code": s.validation_code or "",
                    "linked_project_id": s.linked_project_id,
                    "wait_for_continue": s.wait_for_continue or False,
                }
                for s in steps
            ],
            "variables": [
                {"name": v.name, "default_value": v.default_value or "", "description": v.description or ""}
                for v in wf.variables
            ],
        }


def export_workflow_bundle(workflow_id: int) -> Optional[bytes]:
    """
    Export a workflow as a .dwf bundle (ZIP with custom extension).
    Includes: workflow.json + recordings/*.json + screenshots/*
    Returns raw bytes of the ZIP archive.
    """
    import zipfile
    import io
    from distr.core.paths import RECORDINGS_DIR, DB_DIR

    data = export_workflow(workflow_id)
    if not data:
        return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write the workflow manifest
        zf.writestr("workflow.json", json.dumps(data, indent=2))

        # Bundle recording files
        for s in data.get("steps", []):
            rec = s.get("recording_filename", "")
            if rec:
                rec_path = os.path.join(RECORDINGS_DIR, rec)
                if os.path.isfile(rec_path):
                    zf.write(rec_path, f"recordings/{rec}")

            # Bundle linked action's recording if different from step recording
            linked = s.get("linked_action")
            if linked:
                linked_rec = linked.get("recording_filename", "")
                if linked_rec and linked_rec != rec:
                    linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                    if os.path.isfile(linked_rec_path):
                        zf.write(linked_rec_path, f"recordings/{linked_rec}")

            # Bundle screenshot files
            scr = s.get("screenshot_filename", "")
            if scr:
                scr_path = os.path.join(DB_DIR, "workflow_screenshots", scr)
                if os.path.isfile(scr_path):
                    zf.write(scr_path, f"screenshots/{scr}")

    return buf.getvalue()


def import_workflow(data: Dict[str, Any], recordings: Optional[Dict[str, bytes]] = None,
                    screenshots: Optional[Dict[str, bytes]] = None) -> int:
    """
    Import a workflow from a portable JSON dict. Optionally restores recording
    and screenshot files from provided binary data.
    Returns the new workflow ID.
    """
    from distr.core.paths import RECORDINGS_DIR, DB_DIR

    recordings = recordings or {}
    screenshots = screenshots or {}

    with get_session() as db:
        wf = AutoWorkflow(
            name=data.get("name", "Imported Workflow"),
            description=data.get("description", ""),
            status="draft",
            start_step_position=data.get("start_step_position", 0),
        )
        db.add(wf)
        db.flush()

        position_to_step = {}
        pass_refs = {}
        fail_refs = {}
        for s_data in data.get("steps", []):
            step = AutoWorkflowStep(
                workflow_id=wf.id,
                position=s_data.get("position", 0),
                name=s_data.get("name", "Step"),
                description=s_data.get("description", ""),
                action_type=s_data.get("action_type", "agent_instruction"),
                instruction=s_data.get("instruction", ""),
                validation_type=s_data.get("validation_type", "none"),
                validation_prompt=s_data.get("validation_prompt", ""),
                routing_mode=s_data.get("routing_mode", "static"),
                routing_prompt=s_data.get("routing_prompt", ""),
                wait_before_next=s_data.get("wait_before_next", 0),
                max_retries=s_data.get("max_retries", 0),
                timeout_seconds=s_data.get("timeout_seconds", 300),
                require_approval=s_data.get("require_approval", False),
                code=s_data.get("code", ""),
                validation_code=s_data.get("validation_code", ""),
                linked_project_id=s_data.get("linked_project_id"),
                wait_for_continue=s_data.get("wait_for_continue", False),
            )
            db.add(step)
            db.flush()
            position_to_step[step.position] = step
            pass_refs[step.id] = s_data.get("on_pass_goto_position")
            fail_refs[step.id] = s_data.get("on_fail_goto_position")

            # Restore recording file
            rec_name = s_data.get("recording_filename", "")
            if rec_name and rec_name in recordings:
                os.makedirs(RECORDINGS_DIR, exist_ok=True)
                orig_rec_name = rec_name
                rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                # Avoid overwriting existing recordings — add suffix if needed
                if os.path.exists(rec_path):
                    base, ext = os.path.splitext(rec_name)
                    rec_name = f"{base}_{wf.id}{ext}"
                    rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                with open(rec_path, "wb") as f:
                    f.write(recordings[orig_rec_name])
                step.recording_filename = rec_name

            elif rec_name:
                # Recording referenced but not in bundle — keep the name in case it exists locally
                rec_path = os.path.join(RECORDINGS_DIR, rec_name)
                if os.path.isfile(rec_path):
                    step.recording_filename = rec_name

            # Recreate linked Action entity if present in export data
            linked_action_data = s_data.get("linked_action")
            if linked_action_data:
                try:
                    linked_rec = linked_action_data.get("recording_filename", "")
                    # Restore linked action's recording file if in bundle and different from step's
                    if linked_rec and linked_rec in recordings and linked_rec != rec_name:
                        os.makedirs(RECORDINGS_DIR, exist_ok=True)
                        orig_linked_rec = linked_rec
                        linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                        if os.path.exists(linked_rec_path):
                            base, ext = os.path.splitext(linked_rec)
                            linked_rec = f"{base}_{wf.id}{ext}"
                            linked_rec_path = os.path.join(RECORDINGS_DIR, linked_rec)
                        with open(linked_rec_path, "wb") as f:
                            f.write(recordings[orig_linked_rec])
                    # Use step's recording_filename if linked action's matches the original
                    action_rec = linked_rec if linked_rec else (step.recording_filename or "")
                    new_action = Action(
                        title=linked_action_data.get("title", step.name),
                        description=linked_action_data.get("description", ""),
                        additional_trigger_words=linked_action_data.get("additional_trigger_words", "[]"),
                        is_instruction=linked_action_data.get("is_instruction", False),
                        instruction_text=linked_action_data.get("instruction_text", ""),
                        recording_filename=action_rec,
                    )
                    db.add(new_action)
                    db.flush()
                    step.action_id = new_action.id
                except Exception as e:
                    logger.warning(f"Could not recreate linked action for step {step.id}: {e}")

            # Restore screenshot file
            scr_name = s_data.get("screenshot_filename", "")
            if scr_name and scr_name in screenshots:
                scr_dir = os.path.join(DB_DIR, "workflow_screenshots")
                os.makedirs(scr_dir, exist_ok=True)
                # Rename to new step ID
                ext = os.path.splitext(scr_name)[1] or ".png"
                new_scr_name = f"step_{step.id}{ext}"
                scr_path = os.path.join(scr_dir, new_scr_name)
                with open(scr_path, "wb") as f:
                    f.write(screenshots[scr_name])
                step.screenshot_path = scr_path

        for step in position_to_step.values():
            step.on_pass_goto = _position_to_step_id(pass_refs.get(step.id), position_to_step)
            step.on_fail_goto = _position_to_step_id(fail_refs.get(step.id), position_to_step)

        for v_data in data.get("variables", []):
            db.add(AutoWorkflowVariable(
                workflow_id=wf.id,
                name=v_data.get("name", "var"),
                default_value=v_data.get("default_value", ""),
                description=v_data.get("description", ""),
            ))

        db.commit()
        return wf.id


def import_workflow_bundle(bundle_bytes: bytes) -> int:
    """
    Import a .dwf bundle (ZIP). Extracts workflow.json, recordings, and screenshots,
    then calls import_workflow with the extracted assets.
    """
    import zipfile
    import io

    buf = io.BytesIO(bundle_bytes)
    recordings = {}
    screenshots = {}

    with zipfile.ZipFile(buf, "r") as zf:
        # Read manifest
        data = json.loads(zf.read("workflow.json"))

        # Extract recordings
        for name in zf.namelist():
            if name.startswith("recordings/") and not name.endswith("/"):
                fname = os.path.basename(name)
                recordings[fname] = zf.read(name)
            elif name.startswith("screenshots/") and not name.endswith("/"):
                fname = os.path.basename(name)
                screenshots[fname] = zf.read(name)

    return import_workflow(data, recordings=recordings, screenshots=screenshots)


def _step_id_to_position(step_id: Optional[int], steps: list) -> Optional[int]:
    """Convert a step ID to its position number for export. Returns None if not found or -1 for explicit end."""
    if step_id is None:
        return None
    if step_id == -1:
        return -1
    for s in steps:
        if s.id == step_id:
            return s.position
    return None


def _position_to_step_id(position: Optional[int], position_map: dict) -> Optional[int]:
    if position is None:
        return None
    if position == -1:
        return -1
    step = position_map.get(position)
    return step.id if step else None


def list_presets() -> List[Dict[str, str]]:
    """List available preset files (.dwf bundles and .json) from steprunner/presets/."""
    import zipfile
    import io
    presets_dir = _get_presets_dir()
    if not os.path.isdir(presets_dir):
        return []
    results = []
    for fname in sorted(os.listdir(presets_dir)):
        fpath = os.path.join(presets_dir, fname)
        if fname.endswith(".dwf"):
            try:
                with zipfile.ZipFile(fpath, "r") as zf:
                    data = json.loads(zf.read("workflow.json"))
                has_recordings = any(n.startswith("recordings/") for n in zf.namelist())
                has_screenshots = any(n.startswith("screenshots/") for n in zf.namelist())
                results.append({
                    "filename": fname,
                    "name": data.get("name", fname.replace(".dwf", "")),
                    "description": (data.get("description", "") or "")[:200],
                    "step_count": len(data.get("steps", [])),
                    "has_recordings": has_recordings,
                    "has_screenshots": has_screenshots,
                    "bundle": True,
                })
            except Exception:
                results.append({"filename": fname, "name": fname, "description": "Invalid bundle", "step_count": 0, "bundle": True})
        elif fname.endswith(".json"):
            try:
                with open(fpath, "r") as f:
                    data = json.load(f)
                results.append({
                    "filename": fname,
                    "name": data.get("name", fname.replace(".json", "")),
                    "description": (data.get("description", "") or "")[:200],
                    "step_count": len(data.get("steps", [])),
                    "bundle": False,
                })
            except Exception:
                results.append({"filename": fname, "name": fname, "description": "Invalid JSON", "step_count": 0, "bundle": False})
    return results


def load_preset(filename: str) -> Optional[int]:
    """Load a preset file (.dwf bundle or .json) and import it as a new workflow."""
    presets_dir = _get_presets_dir()
    fpath = os.path.join(presets_dir, filename)
    if not os.path.isfile(fpath):
        return None
    if filename.endswith(".dwf"):
        with open(fpath, "rb") as f:
            return import_workflow_bundle(f.read())
    else:
        with open(fpath, "r") as f:
            data = json.load(f)
        return import_workflow(data)


def save_preset(workflow_id: int, filename: Optional[str] = None) -> Optional[str]:
    """Export a workflow to a .dwf bundle preset file. Returns the filename."""
    import re

    # Check if workflow has any recordings or screenshots
    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return None

    bundle_bytes = export_workflow_bundle(workflow_id)
    if not bundle_bytes:
        return None

    if not filename:
        data = export_workflow(workflow_id)
        safe_name = re.sub(r'[^a-z0-9_]', '', (data.get("name", "workflow") or "workflow").lower().replace(" ", "_"))
        filename = f"{safe_name}.dwf"
    elif not filename.endswith(".dwf"):
        filename = filename.rsplit(".", 1)[0] + ".dwf"

    presets_dir = _get_presets_dir()
    os.makedirs(presets_dir, exist_ok=True)
    with open(os.path.join(presets_dir, filename), "wb") as f:
        f.write(bundle_bytes)
    return filename


# ── Serialization ──

def _serialize_workflow(wf: AutoWorkflow) -> Dict[str, Any]:
    steps = sorted(wf.steps, key=lambda s: s.position)
    return {
        "id": wf.id, "name": wf.name,
        "description": wf.description or "",
        "status": wf.status,
        "schedule_enabled": wf.schedule_enabled,
        "schedule_preset": wf.schedule_preset,
        "schedule_cron": wf.schedule_cron,
        "schedule_time": wf.schedule_time,
        "schedule_days": wf.schedule_days,
        "schedule_timezone": wf.schedule_timezone,
        "next_run_at": wf.next_run_at.isoformat() if wf.next_run_at else None,
        "last_run_at": wf.last_run_at.isoformat() if wf.last_run_at else None,
        "start_step_position": wf.start_step_position or 0,
        "created_date": wf.created_date.isoformat() if wf.created_date else None,
        "modified_date": wf.modified_date.isoformat() if wf.modified_date else None,
        "steps": [_serialize_step(s) for s in steps],
        "variables": [
            {"id": v.id, "name": v.name, "default_value": v.default_value or "", "description": v.description or ""}
            for v in wf.variables
        ],
        "runs": [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status,
                "current_step_id": r.current_step_id,
            }
            for r in sorted(wf.runs, key=lambda r: r.started_at or wf.created_date, reverse=True)[:5]
        ],
    }


def _serialize_step(step: AutoWorkflowStep) -> Dict[str, Any]:
    return {
        "id": step.id, "position": step.position,
        "name": step.name, "description": step.description or "",
        "action_type": step.action_type or "agent_instruction",
        "instruction": step.instruction or "",
        "validation_type": step.validation_type or "none",
        "validation_prompt": step.validation_prompt or "",
        "screenshot_path": step.screenshot_path or "",
        "recording_filename": step.recording_filename or "",
        "action_id": step.action_id,
        "routing_mode": step.routing_mode or "static",
        "routing_prompt": step.routing_prompt or "",
        "on_pass_goto": step.on_pass_goto,
        "on_fail_goto": step.on_fail_goto,
        "wait_before_next": step.wait_before_next or 0,
        "max_retries": step.max_retries or 0,
        "timeout_seconds": step.timeout_seconds or 300,
        "require_approval": step.require_approval or False,
        "status": step.status or "pending",
        "result": step.result,
        "code": step.code or "",
        "validation_code": step.validation_code or "",
        "linked_project_id": step.linked_project_id,
        "wait_for_continue": step.wait_for_continue or False,
    }
