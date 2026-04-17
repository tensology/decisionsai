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
# Thin wrappers that callers (routes, scheduler, ticket board agent) import.


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

        # Validate all steps before starting
        sorted_steps = sorted(wf.steps, key=lambda s: s.position)
        for step in sorted_steps:
            if step.action_type == "agent_instruction" and not (step.instruction or "").strip():
                return {"error": f"Step '{step.name}' (#{step.position}) has no instruction"}
            if step.action_type == "run_command" and not (step.instruction or "").strip() and not (step.code or "").strip() and not (json.loads(step.config) if step.config else {}).get("command", ""):
                return {"error": f"Step '{step.name}' (#{step.position}) has no command configured"}
            if step.action_type == "http_request" and not (json.loads(step.config) if step.config else {}).get("url", ""):
                return {"error": f"Step '{step.name}' (#{step.position}) has no URL configured"}

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
    # TODO: Replace os.environ with contextvars for concurrent workflow support
    # Currently only read by create_cursor_ticket._build_decisions_meta

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
        # If step entered waiting state, return early — routing handled by wait state
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step and step.wait_for_continue and step.status == "waiting":
                return {"success": True, "status": "waiting",
                        "output": result.get("output", ""), "passed": result.get("passed", False)}
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
                "description": step.description or "",
                "position": step.position,
                "action_type": step.action_type or "agent_instruction",
                "step_type": step.step_type or step.action_type or "agent_instruction",
                "instruction": step.instruction or "",
                "code": step.code or "",
                "recording_filename": step.recording_filename or "",
                "action_id": step.action_id,
                "workflow_id": step.workflow_id,
                "config": json.loads(step.config) if step.config else {},
                "wait_for_continue": step.wait_for_continue or False,
                "timeout_seconds": step.timeout_seconds or 300,
                "max_retries": step.max_retries or 0,
                "require_approval": step.require_approval or False,
                "validation_type": step.validation_type or "none",
                "validation_prompt": step.validation_prompt or "",
                "verification": step.verification or "",
                "routing_mode": step.routing_mode or "static",
                "routing_prompt": step.routing_prompt or "",
                "on_pass_goto": step.on_pass_goto,
                "on_fail_goto": step.on_fail_goto,
                "wait_before_next": step.wait_before_next or 0,
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
            "execute_code": lambda: self._run_code(step_data, config, run_id=run_id),
            "playwright": lambda: self._run_playwright(step_data, config, run_id=run_id),
            "run_command": lambda: self._run_command(config, run_id=run_id),
            "http_request": lambda: self._run_http(config),
            "play_recording": lambda: self._run_recording(step_data, config, run_id=run_id),
            "agent_instruction": lambda: self._run_agent(step_data, run_id),
        }
        handler = handlers.get(action_type)
        if handler is None:
            return {"output": f"Unknown action type: {action_type}", "passed": False}
        return handler()

    # ── Step type handlers ──────────────────────────────────────────

    def _run_code(self, step_data: Dict[str, Any], config: dict,
                   run_id: Optional[int] = None) -> Dict[str, Any]:
        """Execute Python code. Generate from instruction if no code provided."""
        return self._run_code_type(step_data, config, "execute_code", run_id=run_id)

    def _run_playwright(self, step_data: Dict[str, Any], config: dict,
                        run_id: Optional[int] = None) -> Dict[str, Any]:
        """Execute Playwright browser automation code."""
        return self._run_code_type(step_data, config, "playwright", run_id=run_id)

    def _run_code_type(self, step_data: Dict[str, Any], config: dict,
                       action_type: str, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Shared logic for execute_code and playwright steps."""
        exec_code = (config.get("code") or step_data.get("code") or "").strip()
        if not exec_code and step_data["instruction"].strip():
            exec_code = self._generate_code(step_data["instruction"], action_type, run_id=run_id)
            if exec_code is None:
                return {"output": f"Code generation failed for {action_type}", "passed": False}
        if not exec_code:
            return {"output": "No code or instruction provided", "passed": False}
        try:
            from distr.core.step_runner.test_loop import TestLoopService
            svc = TestLoopService()
            # Resolve project working directory if step has a linked project
            cwd = None
            linked_project_id = step_data.get("linked_project_id")
            if not linked_project_id and step_data.get("workflow_id"):
                with get_session() as db:
                    step_obj = db.query(AutoWorkflowStep).filter(
                        AutoWorkflowStep.id == step_data["id"]).first()
                    if step_obj and step_obj.linked_project_id:
                        linked_project_id = step_obj.linked_project_id
            if linked_project_id:
                try:
                    from distr.core.db.projects import Project
                    with get_session() as db:
                        proj = db.query(Project).filter(Project.id == linked_project_id).first()
                        if proj and proj.folder_location:
                            cwd = proj.folder_location
                except Exception:
                    pass
            if action_type == "playwright":
                exec_result = svc._execute_playwright(exec_code, headless=config.get("headless", True))
            else:
                exec_result = svc._execute_python(exec_code, cwd=cwd)
            stdout = getattr(exec_result, "stdout", "") or (exec_result.get("stdout", "") if isinstance(exec_result, dict) else "")
            stderr = getattr(exec_result, "stderr", "") or (exec_result.get("stderr", "") if isinstance(exec_result, dict) else "")
            exit_code = getattr(exec_result, "exit_code", None)
            if exit_code is None:
                exit_code = exec_result.get("exit_code", 1) if isinstance(exec_result, dict) else 1
            return {"output": (stdout + "\n" + stderr).strip()[:2000], "passed": exit_code == 0}
        except Exception as e:
            return {"output": f"{action_type} execution error: {e}", "passed": False}

    def _run_command(self, config: dict, run_id: Optional[int] = None) -> Dict[str, Any]:
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

    def _run_http(self, config: dict, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Make an HTTP request."""
        url = config.get("url", "")
        method = config.get("method", "GET")
        headers = config.get("headers", {})
        body = config.get("body")
        timeout = config.get("timeout_seconds", 30)
        # Auto-serialize dict bodies as JSON
        json_body = None
        if isinstance(body, dict):
            json_body = body
            body = None
            headers = dict(headers) if headers else {}
            headers.setdefault("Content-Type", "application/json")
        try:
            import requests
            resp = requests.request(method, url, headers=headers, data=body, json=json_body, timeout=timeout)
            return {"output": f"HTTP {resp.status_code}\n{resp.text[:1500]}",
                    "passed": 200 <= resp.status_code < 400}
        except Exception as e:
            return {"output": f"HTTP request failed: {e}", "passed": False}

    def _run_recording(self, step_data: Dict[str, Any], config: dict, run_id: Optional[int] = None) -> Dict[str, Any]:
        """Play a recorded action. Async — completes via signal callback.

        Connects to action_playback_finished, action_playback_stopped, AND
        playback_failed (for file-not-found / startup errors) to ensure the
        workflow always advances. Includes a timeout watchdog as a safety net.
        """
        recording_name = self._resolve_recording_name(step_data, config)
        if not recording_name:
            return {"output": "No recording attached to this step", "passed": False}
        try:
            from distr.core.signals import signal_manager
            step_id = step_data["id"]
            # Use the run_id passed from the dispatcher rather than a fragile DB lookup.
            # If run_id is None (isolated step), we still record the result but skip routing.
            _run_id = run_id
            # Fallback: try to look up run_id from DB if not provided (backward compat)
            if _run_id is None:
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
            if _run_id is None:
                logger.warning("_run_recording: run_id is None for step %s — workflow routing will be skipped", step_id)
            _done = [False]
            _timeout_seconds = config.get("timeout_seconds", 300)

            def _cleanup():
                """Disconnect all signal handlers."""
                for sig, handler in _handlers:
                    try:
                        sig.disconnect(handler)
                    except Exception:
                        pass

            def _on_playback_done(result_text: str, passed: bool):
                if _done[0]:
                    return
                _done[0] = True
                _cleanup()
                self._record_result_and_route(step_id, run_id=_run_id,
                                               result_text=result_text, passed=passed)

            def _on_finished():
                _on_playback_done("Recording completed.", True)

            def _on_stopped(reason: str):
                _on_playback_done(f"Recording stopped: {reason}" if reason else "Recording stopped.", True)

            def _on_failed(error_msg: str):
                _on_playback_done(f"Recording failed: {error_msg}", False)

            # Register handlers on both signal_manager (global) and the
            # ActionPlaybackService instance (for file-not-found errors)
            signal_manager.action_playback_finished.connect(_on_finished)
            signal_manager.action_playback_stopped.connect(_on_stopped)
            _handlers = [
                (signal_manager.action_playback_finished, _on_finished),
                (signal_manager.action_playback_stopped, _on_stopped),
            ]

            # Connect to the playback service's playback_failed signal for
            # early errors (file not found, startup failure) that don't
            # emit action_playback_stopped.
            try:
                svc = getattr(signal_manager, 'action_playback_service', None)
                if svc is not None:
                    svc.playback_failed.connect(_on_failed)
                    _handlers.append((svc.playback_failed, _on_failed))
            except Exception:
                pass  # Playback service not available — skip

            # Timeout watchdog — ensure the workflow can't hang forever
            def _timeout_watchdog():
                import time
                time.sleep(_timeout_seconds)
                if not _done[0]:
                    logger.warning("Recording playback timed out for step %s after %ds",
                                   step_id, _timeout_seconds)
                    _cleanup()
                    self._record_result_and_route(
                        step_id, run_id=_run_id,
                        result_text=f"Recording playback timed out after {_timeout_seconds}s",
                        passed=False)

            watchdog = threading.Thread(target=_timeout_watchdog, daemon=True)
            watchdog.start()

            signal_manager.play_recording_file.emit(recording_name)
            return {"async": True, "message": "Playing recording."}
        except Exception as e:
            return {"output": f"Recording playback error: {e}", "passed": False}

    def _run_agent(self, step_data: Dict[str, Any], run_id: Optional[int]) -> Dict[str, Any]:
        """Send instruction to the workflow agent (or main agent as fallback)."""
        step_id = step_data["id"]
        timeout_seconds = step_data.get("timeout_seconds", 300)
        prompt = self._build_agent_prompt(step_data, run_id)
        run_ctx = self._get_run_context(step_id, run_id)

        if run_ctx is not None:
            logger.info("_run_agent: using WorkflowAgent for step %s (run_id=%s)", step_id, run_id)
            try:
                future = asyncio.run_coroutine_threadsafe(
                    run_ctx.workflow_agent.execute(prompt), run_ctx.event_loop)

                def _on_agent_done(fut):
                    try:
                        result_text = fut.result(timeout=0)
                        self._record_result_and_route(step_id, run_id=run_id,
                                                      result_text=result_text, passed=True)
                    except asyncio.CancelledError:
                        # Agent was cancelled by timeout watchdog — already handled
                        pass
                    except Exception as exc:
                        logger.error("WorkflowAgent failed for step %s: %s", step_id, exc)
                        self._record_result_and_route(step_id, run_id=run_id,
                                                      result_text=str(exc), passed=False)

                future.add_done_callback(_on_agent_done)

                # Timeout watchdog — if the agent doesn't finish in time, fail the step
                _timed_out = [False]

                def _timeout_watchdog():
                    import time
                    time.sleep(timeout_seconds)
                    if not future.done():
                        _timed_out[0] = True
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

        # Fallback: create a WorkflowAgent on-demand and execute.
        # WARNING: This path means no RunContext exists (no event loop, no tools).
        # The agent can only produce text responses — it cannot use file_operations,
        # execute_code, pi_agent, etc. This usually means the workflow wasn't
        # started via start_workflow_run().
        logger.warning("_run_agent: no RunContext for step %s (run_id=%s) — fallback agent has no tools. Workflow should be started via start_workflow_run().", step_id, run_id)
        # Must run in a background thread since we may be inside an existing
        # event loop (e.g. FastAPI uvicorn loop). We can't call
        # loop.run_until_complete() when another loop is running.
        try:
            from distr.core.workflow_agent import WorkflowAgent
            import concurrent.futures
            agent = WorkflowAgent()

            def _run_agent_sync():
                """Run agent.execute() in an isolated thread with its own loop."""
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(agent.execute(prompt))
                finally:
                    loop.close()
                    agent.shutdown()

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_agent_sync)
                result_text = future.result(timeout=timeout_seconds)

            if not result_text or not result_text.strip():
                result_text = "Step completed with no output."
            self._record_result_and_route(step_id, run_id=run_id,
                                          result_text=result_text, passed=True)
            return {"output": result_text, "passed": True}
        except Exception as e:
            logger.error("WorkflowAgent fallback failed for step %s: %s", step_id, e)
            # Last resort: record failure so the workflow doesn't stall forever
            self._record_result_and_route(step_id, run_id=run_id,
                                          result_text=f"Agent dispatch failed: {e}", passed=False)
            return {"output": f"Agent dispatch error: {e}", "passed": False}

    # ── Helpers ──────────────────────────────────────────────────────

    def _build_agent_prompt(self, step_data: Dict[str, Any], run_id: Optional[int]) -> str:
        """Build the prompt for the WorkflowAgent, enriched with full context.

        Uses the shared ``assemble_step_context`` and ``build_step_context_prompt``
        infrastructure so the dispatcher path (web UI) gets the same context
        awareness as the orchestration path (Qt app).

        Includes: workflow description, context rules, workflow input, prior step
        results, user feedback from wait_for_continue steps, step position,
        variable resolution, and step description.
        """
        from distr.core.step_runner.context_assembly import assemble_step_context
        from distr.core.workflow.planning import build_step_context_prompt

        step_id = step_data["id"]
        workflow_id = step_data.get("workflow_id")

        # ── Load workflow-level context ──
        workflow_description = ""
        context_rules = ""
        prior_results: List[Dict[str, str]] = []
        total_steps = 1
        step_index = 0
        continuation_input = ""
        wf = None
        step_obj = None

        try:
            with get_session() as db:
                wf = db.query(AutoWorkflow).filter(
                    AutoWorkflow.id == workflow_id).first()
                step_obj = db.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.id == step_id).first()
        except Exception as e:
            logger.debug("_build_agent_prompt: failed to load workflow/step objects: %s", e)

        # ── Build prior results from step result history ──
        if run_id is not None:
            try:
                with get_session() as db:
                    results = (
                        db.query(AutoWorkflowStepResult)
                        .filter(AutoWorkflowStepResult.run_id == run_id)
                        .order_by(AutoWorkflowStepResult.created_at)
                        .all()
                    )
                    for sr in results:
                        if sr.step_id == step_id:
                            continue  # Skip the current step
                        s = sr.step
                        title = s.name if s else f"Step {sr.step_id}"
                        result = (sr.agent_response or "").strip()
                        if result:
                            prior_results.append({
                                "title": title,
                                "result": result[:300],
                                "step_type": s.action_type if s else "agent_instruction",
                            })
            except Exception as e:
                logger.debug("_build_agent_prompt: failed to load prior results: %s", e)

            # ── Load user feedback from a preceding wait step ──
            try:
                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(
                        AutoWorkflowRun.id == run_id).first()
                    if run and run.run_data:
                        run_data = json.loads(run.run_data or "{}")
                        feedback = run_data.get("feedback", "")
                        if feedback and feedback.strip():
                            continuation_input = feedback.strip()
            except Exception as e:
                logger.debug("_build_agent_prompt: failed to load feedback: %s", e)

        # ── Determine step position and total steps ──
        if wf:
            workflow_description = wf.description or ""
            context_rules = getattr(wf, 'context_rules', None) or ""
            all_steps = sorted(wf.steps, key=lambda s: s.position)
            total_steps = len(all_steps)
            for i, s in enumerate(all_steps):
                if s.id == step_id:
                    step_index = i
                    break

        # ── Use shared context assembly if we have real model objects ──
        step_input_ctx = None
        if wf and step_obj and prior_results is not None:
            try:
                step_input_ctx = assemble_step_context(
                    session=wf,
                    step=step_obj,
                    prior_results=prior_results,
                )
                # Override context_rules from the assembled context
                context_rules = step_input_ctx.workflow_rules or context_rules
            except Exception as e:
                logger.debug("_build_agent_prompt: assemble_step_context failed: %s", e)

        # ── Build the prompt using the shared function ──
        step_instruction = step_data["instruction"].strip()
        step_title = step_data.get("name", f"Step {step_index + 1}")
        step_description = step_data.get("description", "")

        # Prepend step description if it exists (adds "why" context)
        if step_description:
            step_instruction = f"{step_description}\n\n{step_instruction}"

        prompt = build_step_context_prompt(
            step_index=step_index,
            total_steps=total_steps,
            workflow_description=workflow_description or "Complete the requested workflow.",
            step_title=step_title,
            step_instruction=step_instruction,
            prior_results=prior_results,
            context_rules=context_rules,
            continuation_input=continuation_input,
        )

        return prompt

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
                logger.debug("_get_run_context: found run_id=%s in _active_runs", run_id)
                return ctx
            else:
                logger.warning("_get_run_context: run_id=%s not found in _active_runs (keys=%s)",
                               run_id, list(_active_runs.keys()))

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

    def _generate_code(self, instruction: str, action_type: str,
                        run_id: Optional[int] = None) -> Optional[str]:
        """Generate code from instruction using the coding LLM.

        If run_id is provided, includes workflow context (prior results,
        feedback, description) so the generated code is contextually aware.
        """
        try:
            from distr.core.step_runner.code_generator import CodeGeneratorService
            from distr.core.step_runner.step_types import StepType
            step_type = StepType.PLAYWRIGHT if action_type == "playwright" else StepType.EXECUTE_CODE

            # Build context string from workflow run history
            context = None
            if run_id is not None:
                context_parts = []
                try:
                    with get_session() as db:
                        results = (
                            db.query(AutoWorkflowStepResult)
                            .filter(AutoWorkflowStepResult.run_id == run_id)
                            .order_by(AutoWorkflowStepResult.created_at)
                            .all()
                        )
                        for sr in results:
                            s = sr.step
                            title = s.name if s else f"Step {sr.step_id}"
                            result = (sr.agent_response or "").strip()
                            if result:
                                context_parts.append(f"{title}: {result[:200]}")

                        run = db.query(AutoWorkflowRun).filter(
                            AutoWorkflowRun.id == run_id).first()
                        if run and run.run_data:
                            run_data = json.loads(run.run_data or "{}")
                            feedback = run_data.get("feedback", "")
                            if feedback and feedback.strip():
                                context_parts.append(f"User feedback: {feedback.strip()[:300]}")
                except Exception as e:
                    logger.debug("_generate_code: failed to load context: %s", e)

                if context_parts:
                    context = "\n".join(context_parts)

            return CodeGeneratorService().generate_code(instruction, step_type, context=context)
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

    def __init__(self):
        # Track which steps have been routed to prevent double-dispatch from
        # concurrent callbacks (e.g., timeout watchdog + successful completion).
        self._routed_steps: set = set()
        # Thread lock for _routed_steps access
        self._routed_lock = threading.Lock()

    def _record_result_and_route(self, step_id: int, run_id: Optional[int],
                                  result_text: str, passed: bool) -> None:
        """Record result AND route to the next step (for async step completion).

        This is the critical fix: when an async step (agent_instruction, recording)
        completes via callback, we need to not only store the result but also
        advance the workflow to the next step. Without this, the workflow stalls
        after every async step.
        """
        # Guard against double-routing the same step
        with self._routed_lock:
            if step_id in self._routed_steps:
                logger.warning("_record_result_and_route: step %s already routed, skipping", step_id)
                return
            self._routed_steps.add(step_id)

        # First, record the result (verification, DB update, etc.)
        self._record_result(step_id, run_id, result_text, passed)

        # Check if the step entered waiting state during _record_result.
        # If so, skip routing — the StepRouter will handle routing when the
        # user continues from the wait state, and we avoid a duplicate TTS speak.
        step_in_waiting = False
        if step_id:
            with get_session() as db:
                _step = db.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.id == step_id).first()
                if _step and _step.status == "waiting":
                    step_in_waiting = True

        if step_in_waiting:
            logger.info("Step %s entered waiting state — skipping router to avoid duplicate TTS", step_id)
            return

        # Route to the next step if we're in a workflow run
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
            from distr.core.signals import speak_text_directly_event_queue
            speak = result_text.strip()[:400]
            if len(result_text.strip()) > 400:
                speak += "..."
            speak_text_directly_event_queue(
                f"{speak}. Step '{step_name}' is now waiting for your input.")
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
