"""
StepDispatcher — step execution engine.

Validates config → assembles context → executes action → records result → pushes websocket.
Extracted from _dispatch_step() in service.py and _execute_step_directly() in workflow.py.

**Validates: Requirements 1, 2, 5, 8, 9**
"""
import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from distr.core.db import get_session
from distr.core.db.workflow import (
    AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun,
    AutoWorkflowStepResult,
)
from distr.core.workflow.verification import _run_verification
from distr.gui.web.workflow_events import increment_workflow_updated

logger = logging.getLogger(__name__)


# ── Run-context infrastructure ──────────────────────────────────────
# Moved from service.py — tracks per-run WorkflowAgent lifecycle.


@dataclass
class _RunContext:
    """Per-run state for the WorkflowAgent lifecycle."""
    run_id: int
    workflow_agent: "WorkflowAgent"  # noqa: F821
    event_loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    context_prefix: str = ""


_active_runs: Dict[int, _RunContext] = {}
_runs_lock = threading.Lock()


def _cleanup_run(run_id: int) -> None:
    """Clean up a workflow run's WorkflowAgent and event loop."""
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
    """Clean up resources and notify the bridge when a run reaches terminal status."""
    _cleanup_run(run_id)

    steps_summary: List[dict] = []
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
                    "result": (sr.agent_response or "")[:300],
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
        from distr.core.step_runner.agent_bridge import WorkflowAgentBridge
        WorkflowAgentBridge().on_workflow_completed(workflow_id, run_result)
    except Exception:
        logger.error("WorkflowAgentBridge notification failed for run %d", run_id, exc_info=True)


def _clear_workflow_env() -> None:
    """Clear workflow run context environment variables."""
    os.environ.pop("DECISIONS_WORKFLOW_RUN_ID", None)
    os.environ.pop("DECISIONS_WORKFLOW_STEP_ID", None)
    os.environ.pop("DECISIONS_WORKFLOW_ID", None)


# ── Execution-level functions ───────────────────────────────────────
# Thin wrappers that callers (routes, scheduler, kanban agent) import.


def start_workflow_run(
    workflow_id: int,
    context: Optional[str] = None,
    start_step_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Start a full workflow run.

    Creates a run record, spins up a WorkflowAgent, and dispatches the first step.
    """
    from distr.core.workflow_agent import WorkflowAgent

    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}
        if not wf.steps:
            return {"error": "Workflow has no steps"}

        active_run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == workflow_id,
            AutoWorkflowRun.status.in_(["running", "waiting"]),
        ).first()
        if active_run:
            return {"error": "A run is already in progress"}

        sorted_steps = sorted(wf.steps, key=lambda s: s.position)

        first_step = None
        start_idx = 0
        if start_step_id is not None:
            for i, s in enumerate(sorted_steps):
                if s.id == int(start_step_id):
                    first_step = s
                    start_idx = i
                    break
        if first_step is None:
            first_step = sorted_steps[0]
            start_idx = 0

        for i, step in enumerate(sorted_steps):
            if i >= start_idx:
                step.status = "pending"
                step.result = None

        run = AutoWorkflowRun(workflow_id=workflow_id, status="running")
        db.add(run)
        db.flush()
        run_id = run.id

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

        run.current_step_id = first_step.id
        first_step.status = "running"
        first_step_id = first_step.id
        db.commit()

    os.environ["DECISIONS_WORKFLOW_RUN_ID"] = str(run_id)
    os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(first_step_id)
    os.environ["DECISIONS_WORKFLOW_ID"] = str(workflow_id)

    dispatcher = StepDispatcher()
    result = dispatcher.run_in_workflow(first_step_id, run_id)
    if "error" in result:
        _clear_workflow_env()
        complete_run(run_id, "failed")
        return result
    result["run_id"] = run_id
    return result


def execute_step(step_id: int, isolated: bool = False) -> Dict[str, Any]:
    """Execute a single step via StepDispatcher."""
    dispatcher = StepDispatcher()
    return dispatcher.run_isolated(step_id)


def cancel_run(run_id: int) -> bool:
    """Cancel an active workflow run."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        run.status = "cancelled"
        run.completed_at = datetime.utcnow()
        if run.current_step_id:
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == run.current_step_id,
            ).first()
            if step and step.status in ("running", "waiting"):
                step.status = "cancelled"
                step.result = "Cancelled by user."
        _run_id, _wf_id = run.id, run.workflow_id
        db.commit()
    increment_workflow_updated()
    _finalize_terminal_run(_run_id, _wf_id, "cancelled")
    return True


def cancel_step(step_id: int) -> bool:
    """Cancel a running step."""
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == step_id,
        ).first()
        if not step:
            return False
        step.status = "cancelled"
        step.result = "Cancelled by user."
        db.commit()
    increment_workflow_updated()
    return True


def continue_waiting_step(run_id: int, optional_input: str = "") -> Dict[str, Any]:
    """Resume a workflow run that is in 'waiting' status."""
    from distr.core.workflow.router import StepRouter

    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        if run.status != "waiting":
            return {"error": f"Run is not waiting (status: {run.status})", "status_code": 409}
        step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == run.current_step_id,
        ).first()
        if not step or step.status != "waiting":
            return {"error": "No waiting step found", "status_code": 409}
        step_id = step.id

    router = StepRouter()
    return router.resume_from_feedback(step_id, run_id, optional_input)


def complete_run(run_id: int, status: str = "completed") -> bool:
    """Mark a run as terminal and clean up resources."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        run.status = status
        run.completed_at = datetime.utcnow()
        workflow_id = run.workflow_id
        db.commit()
    increment_workflow_updated()
    _finalize_terminal_run(run_id, workflow_id, status)
    _clear_workflow_env()
    return True


class StepDispatcher:
    """Execute a single workflow step.

    Two public entry points:
    - ``run_isolated(step_id)`` — run one step, record result, done.
    - ``run_in_workflow(step_id, run_id)`` — run step, then hand off to StepRouter.
    """

    # ── Public API ──────────────────────────────────────────────────

    def run_isolated(self, step_id: int) -> Dict[str, Any]:
        """Execute one step in isolation. No routing afterwards."""
        step_data = self._load_step(step_id)
        if "error" in step_data:
            return step_data
        errors = self._validate_before_dispatch(step_data)
        if errors:
            self._fail_step(step_id, f"Validation failed: {errors}")
            return {"error": errors}
        self._set_status(step_id, "running")
        result = self._execute(step_data, run_id=None)
        if result.get("async"):
            return {"success": True, "message": result.get("message", "Step dispatched.")}
        self._record_result(step_id, run_id=None,
                            result_text=result.get("output", ""),
                            passed=result.get("passed", False))
        return {"success": True, "status": "passed" if result.get("passed") else "failed"}

    def run_in_workflow(self, step_id: int, run_id: int) -> Dict[str, Any]:
        """Execute step within a workflow run, then hand off to StepRouter."""
        step_data = self._load_step(step_id)
        if "error" in step_data:
            return step_data
        errors = self._validate_before_dispatch(step_data)
        if errors:
            self._fail_step(step_id, f"Validation failed: {errors}")
            return {"error": errors}
        self._set_status(step_id, "running")
        result = self._execute(step_data, run_id=run_id)
        if result.get("async"):
            return {"success": True, "message": result.get("message", "Step dispatched.")}
        self._record_result(step_id, run_id=run_id,
                            result_text=result.get("output", ""),
                            passed=result.get("passed", False))
        return {
            "success": True, "status": "passed" if result.get("passed") else "failed",
            "output": result.get("output", ""), "passed": result.get("passed", False),
        }

    # ── Step loading ────────────────────────────────────────────────

    def _load_step(self, step_id: int) -> Dict[str, Any]:
        """Load step data from the database."""
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if not step:
                return {"error": "Step not found"}
            return {
                "id": step.id, "name": step.name,
                "action_type": step.action_type or "agent_instruction",
                "instruction": step.instruction or "",
                "code": step.code or "",
                "recording_filename": step.recording_filename or "",
                "action_id": step.action_id,
                "workflow_id": step.workflow_id,
                "config": json.loads(step.config) if step.config else {},
                "wait_for_continue": step.wait_for_continue or False,
                "timeout_seconds": step.timeout_seconds or 300,
            }

    # ── Validation ──────────────────────────────────────────────────

    def _validate_before_dispatch(self, step_data: Dict[str, Any]) -> Optional[str]:
        """Validate step config before execution. Returns error string or None."""
        action_type = step_data["action_type"]
        if action_type == "agent_instruction":
            return "No instruction provided" if not step_data["instruction"].strip() else None
        try:
            from distr.core.step_runner.validation import StepValidator
            config = self._build_config(step_data)
            errors = StepValidator().validate(action_type, config)
            if errors:
                return "; ".join(f"{e.field}: {e.message}" for e in errors)
        except Exception as e:
            logger.warning("Validation import failed: %s", e)
        return None

    def _build_config(self, step_data: Dict[str, Any]) -> dict:
        """Build a config dict suitable for StepValidator from step data."""
        action_type = step_data["action_type"]
        config = dict(step_data.get("config") or {})
        if action_type in ("execute_code", "playwright"):
            config.setdefault("code", step_data.get("code", ""))
            config.setdefault("instruction", step_data.get("instruction", ""))
        elif action_type == "play_recording":
            if not config.get("recording_name") and step_data.get("recording_filename"):
                config["recording_name"] = step_data["recording_filename"]
            if not config.get("recording_id") and step_data.get("action_id"):
                config["recording_id"] = step_data["action_id"]
        return config

    # ── Execution dispatch ──────────────────────────────────────────

    def _execute(self, step_data: Dict[str, Any], run_id: Optional[int]) -> Dict[str, Any]:
        """Route to the correct step-type handler."""
        action_type = step_data["action_type"]
        config = self._build_config(step_data)
        handlers = {
            "execute_code": lambda: self._run_code(step_data, config),
            "playwright": lambda: self._run_playwright(step_data, config),
            "run_command": lambda: self._run_command(config),
            "http_request": lambda: self._run_http(config),
            "play_recording": lambda: self._run_recording(step_data, config),
            "agent_instruction": lambda: self._run_agent(step_data, run_id),
        }
        handler = handlers.get(action_type)
        if handler is None:
            return {"output": f"Unknown action type: {action_type}", "passed": False}
        return handler()

    # ── Step type handlers ──────────────────────────────────────────

    def _run_code(self, step_data: Dict[str, Any], config: dict) -> Dict[str, Any]:
        """Execute Python code. Generate from instruction if no code provided."""
        return self._run_code_type(step_data, config, "execute_code")

    def _run_playwright(self, step_data: Dict[str, Any], config: dict) -> Dict[str, Any]:
        """Execute Playwright browser automation code."""
        return self._run_code_type(step_data, config, "playwright")

    def _run_code_type(self, step_data: Dict[str, Any], config: dict,
                       action_type: str) -> Dict[str, Any]:
        """Shared logic for execute_code and playwright steps."""
        exec_code = (config.get("code") or step_data.get("code") or "").strip()
        if not exec_code and step_data["instruction"].strip():
            exec_code = self._generate_code(step_data["instruction"], action_type)
            if exec_code is None:
                return {"output": f"Code generation failed for {action_type}", "passed": False}
        if not exec_code:
            return {"output": "No code or instruction provided", "passed": False}
        try:
            from distr.core.step_runner.test_loop import TestLoopService
            svc = TestLoopService()
            if action_type == "playwright":
                exec_result = svc._execute_playwright(exec_code, headless=config.get("headless", True))
            else:
                exec_result = svc._execute_python(exec_code)
            stdout = getattr(exec_result, "stdout", "") or (exec_result.get("stdout", "") if isinstance(exec_result, dict) else "")
            stderr = getattr(exec_result, "stderr", "") or (exec_result.get("stderr", "") if isinstance(exec_result, dict) else "")
            exit_code = getattr(exec_result, "exit_code", None)
            if exit_code is None:
                exit_code = exec_result.get("exit_code", 1) if isinstance(exec_result, dict) else 1
            return {"output": (stdout + "\n" + stderr).strip()[:2000], "passed": exit_code == 0}
        except Exception as e:
            return {"output": f"{action_type} execution error: {e}", "passed": False}

    def _run_command(self, config: dict) -> Dict[str, Any]:
        """Execute a shell command."""
        import subprocess
        cmd = config.get("command", "")
        cwd = config.get("working_directory") or None
        timeout = config.get("timeout_seconds", 60)
        try:
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                  timeout=timeout, cwd=cwd)
            return {"output": (proc.stdout + proc.stderr).strip()[:2000],
                    "passed": proc.returncode == 0}
        except subprocess.TimeoutExpired:
            return {"output": f"Command timed out after {timeout}s", "passed": False}
        except Exception as e:
            return {"output": f"Command execution error: {e}", "passed": False}

    def _run_http(self, config: dict) -> Dict[str, Any]:
        """Make an HTTP request."""
        url = config.get("url", "")
        method = config.get("method", "GET")
        headers = config.get("headers", {})
        body = config.get("body")
        timeout = config.get("timeout_seconds", 30)
        try:
            import requests
            resp = requests.request(method, url, headers=headers, data=body, timeout=timeout)
            return {"output": f"HTTP {resp.status_code}\n{resp.text[:1500]}",
                    "passed": 200 <= resp.status_code < 400}
        except Exception as e:
            return {"output": f"HTTP request failed: {e}", "passed": False}

    def _run_recording(self, step_data: Dict[str, Any], config: dict) -> Dict[str, Any]:
        """Play a recorded action. Async — completes via signal callback."""
        recording_name = self._resolve_recording_name(step_data, config)
        if not recording_name:
            return {"output": "No recording attached to this step", "passed": False}
        try:
            from distr.core.signals import signal_manager
            step_id = step_data["id"]
            # Look up run_id from the current workflow run
            _run_id = None
            with get_session() as db:
                step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
                if step:
                    run = db.query(AutoWorkflowRun).filter(
                        AutoWorkflowRun.workflow_id == step.workflow_id,
                        AutoWorkflowRun.current_step_id == step_id,
                        AutoWorkflowRun.status == "running",
                    ).first()
                    if run:
                        _run_id = run.id
            _done = [False]

            def _on_playback_done(result_text: str, passed: bool):
                if _done[0]:
                    return
                _done[0] = True
                try:
                    signal_manager.action_playback_finished.disconnect(_on_finished)
                except Exception:
                    pass
                try:
                    signal_manager.action_playback_stopped.disconnect(_on_stopped)
                except Exception:
                    pass
                self._record_result_and_route(step_id, run_id=_run_id,
                                               result_text=result_text, passed=passed)

            def _on_finished():
                _on_playback_done("Recording completed.", True)

            def _on_stopped(reason: str):
                _on_playback_done(f"Recording stopped: {reason}" if reason else "Recording stopped.", True)

            signal_manager.action_playback_finished.connect(_on_finished)
            signal_manager.action_playback_stopped.connect(_on_stopped)
            signal_manager.play_recording_file.emit(recording_name)
            return {"async": True, "message": "Playing recording."}
        except Exception as e:
            return {"output": f"Recording playback error: {e}", "passed": False}

    def _run_agent(self, step_data: Dict[str, Any], run_id: Optional[int]) -> Dict[str, Any]:
        """Send instruction to the workflow agent (or main agent as fallback)."""
        step_id = step_data["id"]
        timeout_seconds = step_data.get("timeout_seconds", 300)
        prompt = f"[Workflow — {step_data['name']}]\n{step_data['instruction'].strip()}"
        run_ctx = self._get_run_context(step_id, run_id)

        if run_ctx is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    run_ctx.workflow_agent.execute(prompt), run_ctx.event_loop)

                def _on_agent_done(fut):
                    try:
                        result_text = fut.result(timeout=0)
                        self._record_result_and_route(step_id, run_id=run_id,
                                                      result_text=result_text, passed=True)
                    except Exception as exc:
                        logger.error("WorkflowAgent failed for step %s: %s", step_id, exc)
                        self._record_result_and_route(step_id, run_id=run_id,
                                                      result_text=str(exc), passed=False)

                future.add_done_callback(_on_agent_done)

                # Timeout watchdog — if the agent doesn't finish in time, fail the step
                def _timeout_watchdog():
                    import time
                    time.sleep(timeout_seconds)
                    if not future.done():
                        logger.warning("WorkflowAgent timed out for step %s after %ds", step_id, timeout_seconds)
                        future.cancel()
                        self._record_result_and_route(
                            step_id, run_id=run_id,
                            result_text=f"Step timed out after {timeout_seconds}s",
                            passed=False)

                watchdog = threading.Thread(target=_timeout_watchdog, daemon=True)
                watchdog.start()

                return {"async": True, "message": "Step dispatched to WorkflowAgent."}
            except Exception as e:
                logger.error("Failed to dispatch step %s to WorkflowAgent: %s", step_id, e)
                return {"output": str(e), "passed": False}

        # Fallback: send to main agent via signal
        try:
            from distr.core.signals import signal_manager
            signal_manager.send_text_input.emit(prompt, False, None, None)
            return {"async": True, "message": "Step sent to agent."}
        except Exception as e:
            return {"output": f"Agent dispatch error: {e}", "passed": False}

    # ── Helpers ──────────────────────────────────────────────────────

    def _resolve_recording_name(self, step_data: Dict[str, Any], config: dict) -> Optional[str]:
        """Resolve the recording filename from config, step data, or linked action."""
        name = config.get("recording_name", "") or step_data.get("recording_filename", "")
        if name:
            return name
        action_id = config.get("recording_id") or step_data.get("action_id")
        if not action_id:
            return None
        try:
            from distr.core.db import Action
            with get_session() as db:
                action = db.query(Action).filter(Action.id == action_id).first()
                if action and action.recording_filename:
                    return action.recording_filename
        except Exception as e:
            logger.warning("Could not load linked action %s: %s", action_id, e)
        return None

    def _get_run_context(self, step_id: int, run_id: Optional[int]):
        """Look up the active _RunContext for a workflow run, if any."""
        if run_id is not None:
            with _runs_lock:
                ctx = _active_runs.get(run_id)
            if ctx:
                return ctx

        # Fallback: look up run from step's workflow
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step:
                run = db.query(AutoWorkflowRun).filter(
                    AutoWorkflowRun.workflow_id == step.workflow_id,
                    AutoWorkflowRun.current_step_id == step_id,
                    AutoWorkflowRun.status == "running",
                ).first()
                if run:
                    with _runs_lock:
                        return _active_runs.get(run.id)
        return None

    def _generate_code(self, instruction: str, action_type: str) -> Optional[str]:
        """Generate code from instruction using the coding LLM."""
        try:
            from distr.core.step_runner.code_generator import CodeGeneratorService
            from distr.core.step_runner.step_types import StepType
            step_type = StepType.PLAYWRIGHT if action_type == "playwright" else StepType.EXECUTE_CODE
            return CodeGeneratorService().generate_code(instruction, step_type)
        except Exception as e:
            logger.error("Code generation failed: %s", e)
            return None

    def _set_status(self, step_id: int, status: str, result: Optional[str] = None) -> None:
        """Update step status in DB and push websocket notification."""
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step:
                step.status = status
                if result is not None:
                    step.result = result
                db.commit()
        increment_workflow_updated()

    def _fail_step(self, step_id: int, error_msg: str) -> None:
        """Mark step as failed with error message."""
        self._set_status(step_id, "failed", result=error_msg)

    def _record_result(self, step_id: int, run_id: Optional[int],
                       result_text: str, passed: bool) -> None:
        """Run verification, store result, update step status, push websocket."""
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if not step:
                return
            # Check wait_for_continue before finalizing
            if step.wait_for_continue:
                if self._enter_wait_state(step_id, result_text, passed):
                    return
            verified_passed = _run_verification(step, result_text, passed)
            status = "passed" if verified_passed else "failed"
            step.status = status
            step.result = result_text
            db.add(AutoWorkflowStepResult(
                step_id=step_id, run_id=run_id,
                agent_response=result_text, status=status,
            ))
            db.commit()
        increment_workflow_updated()

    def _record_result_and_route(self, step_id: int, run_id: Optional[int],
                                  result_text: str, passed: bool) -> None:
        """Record result AND route to the next step (for async step completion).

        This is the critical fix: when an async step (agent_instruction, recording)
        completes via callback, we need to not only store the result but also
        advance the workflow to the next step. Without this, the workflow stalls
        after every async step.
        """
        # First, record the result (verification, DB update, etc.)
        self._record_result(step_id, run_id, result_text, passed)

        # Then route to the next step if we're in a workflow run
        if run_id is not None:
            try:
                from distr.core.workflow.router import StepRouter
                router = StepRouter()
                decision = router.route(step_id, result_text, passed, run_id)

                if decision.get("action") == "next_step":
                    next_step_id = decision["step_id"]
                    wait_before = decision.get("wait_before_next", 0)
                    if wait_before > 0:
                        import time
                        time.sleep(wait_before)

                    # Update env vars for the next step
                    os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(next_step_id)

                    # Set next step to running
                    with get_session() as db:
                        next_step = db.query(AutoWorkflowStep).filter(
                            AutoWorkflowStep.id == next_step_id).first()
                        if next_step:
                            next_step.status = "running"
                            db.commit()
                    increment_workflow_updated()

                    # Dispatch the next step
                    dispatcher = StepDispatcher()
                    dispatcher.run_in_workflow(next_step_id, run_id)

                elif decision.get("action") == "end_run":
                    status = decision.get("status", "completed")
                    complete_run(run_id, status)

                elif decision.get("action") == "waiting":
                    pass  # Step entered wait state — nothing more to do

            except Exception as e:
                logger.error("Routing failed after step %s: %s", step_id, e, exc_info=True)
                try:
                    complete_run(run_id, "failed")
                except Exception:
                    pass

    def _enter_wait_state(self, step_id: int, result_text: str, passed: bool) -> bool:
        """Put step into waiting state if wait_for_continue is set. Returns True if entered."""
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if not step or not step.wait_for_continue:
                return False
            step.status = "waiting"
            step_name, workflow_id = step.name, step.workflow_id
            run = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.current_step_id == step_id,
                AutoWorkflowRun.status == "running",
            ).first()
            run_id = None
            if run:
                run.status = "waiting"
                run_id = run.id
                run_data = json.loads(run.run_data or "{}")
                run_data["waiting_result"] = result_text
                run_data["waiting_passed"] = passed
                run.run_data = json.dumps(run_data)
            db.commit()
        increment_workflow_updated()
        # Notify main agent via TTS
        try:
            from distr.core.signals import signal_manager
            speak = result_text.strip()[:400]
            if len(result_text.strip()) > 400:
                speak += "..."
            signal_manager.speak_text_directly.emit(
                f"Step '{step_name}' is done and waiting for your input. Here's what happened: {speak}")
        except Exception as e:
            logger.debug("Could not speak wait notification: %s", e)
        # Queue report for the main agent
        try:
            from distr.core.step_runner.agent_bridge import WorkflowAgentBridge
            WorkflowAgentBridge().queue_report_to_agent(
                workflow_id,
                f"Workflow step '{step_name}' completed and is now WAITING for your input. "
                f"Run ID: {run_id}. Result: {result_text[:500]}")
        except Exception as e:
            logger.debug("Could not queue wait report: %s", e)
        return True
