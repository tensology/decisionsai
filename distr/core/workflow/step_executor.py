"""StepExecutorMixin — step-type execution methods.

Extracted from StepDispatcher for clarity. All methods remain accessible
via self because StepDispatcher inherits from this mixin.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun, AutoWorkflowStepResult

logger = logging.getLogger(__name__)


class StepExecutorMixin:
    """Provides step execution logic: code, command, HTTP, agent, recording."""

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
            "send_to_project_cli": lambda: self._run_send_to_project_cli(step_data, config, run_id=run_id),
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
            from distr.core.workflow_engine.test_loop import TestLoopService
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

    def _run_send_to_project_cli(
        self,
        step_data: Dict[str, Any],
        config: dict,
        run_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send step instruction to a project's CLI session.

        Project resolution order:
        1) step.linked_project_id
        2) run.run_data.project_id (ticket-board context)
        """
        instruction = (config.get("instruction") or step_data.get("instruction") or "").strip()
        if not instruction:
            return {"output": "No instruction provided for Send to Project CLI", "passed": False}

        project_id = step_data.get("linked_project_id")
        project_folder = None

        try:
            from distr.core.db.projects import Project
            with get_session() as db:
                step_obj = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_data["id"]).first()
                if step_obj and step_obj.linked_project_id:
                    project_id = step_obj.linked_project_id

                if not project_id and run_id:
                    run_obj = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                    if run_obj and run_obj.run_data:
                        try:
                            run_data = json.loads(run_obj.run_data or "{}")
                            run_project_id = run_data.get("project_id")
                            if run_project_id is not None and str(run_project_id).strip():
                                project_id = int(run_project_id)
                        except (ValueError, TypeError, json.JSONDecodeError):
                            pass

                if project_id:
                    project = db.query(Project).filter(Project.id == int(project_id)).first()
                    if project and project.folder_location:
                        project_folder = project.folder_location
        except Exception as e:
            return {"output": f"Failed resolving project context: {e}", "passed": False}

        if not project_id or not project_folder:
            return {
                "output": "Bypassed: no linked project context available for Send to Project CLI.",
                "passed": True,
                "skip_wait": True,
            }

        try:
            from distr.core import terminal as terminal_runtime
            import concurrent.futures

            async def _get_session():
                return await terminal_runtime.get_or_create_session(
                    project_id=int(project_id),
                    cwd=project_folder,
                    command="pi",
                )

            # asyncio.run() raises RuntimeError if called from within a running
            # event loop (e.g. inside an async workflow dispatched from FastAPI).
            # Fall back to a thread-bound loop when that happens.
            try:
                session = asyncio.run(_get_session())
            except RuntimeError:
                def _thread_runner():
                    return asyncio.run(_get_session())
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    session = pool.submit(_thread_runner).result(timeout=30)

            session.write(instruction + "\n")
            return {
                "output": f"Sent to project CLI (project_id={project_id}): {instruction[:600]}",
                "passed": True,
                "skip_wait": True,
            }
        except Exception as e:
            return {"output": f"Failed sending to project CLI: {e}", "passed": False}

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
                # "stopped" usually indicates an interruption/cancel path rather
                # than a successful recording completion.
                _on_playback_done(
                    f"Recording stopped: {reason}" if reason else "Recording stopped.",
                    False,
                )

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
                        result_text = self._augment_agent_result_with_tool_evidence(
                            result_text, run_ctx.workflow_agent,
                        )
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

        # Fallback: no per-run event loop / shared WorkflowAgent — execute synchronously
        # in a worker thread. WorkflowAgent still loads the standard tool set (via
        # load_tools + optional cache warmup); prefer start_workflow_run() for async
        # dispatch and a persistent agent loop when available.
        logger.info(
            "_run_agent: no RunContext for step %s (run_id=%s) — using threaded WorkflowAgent fallback.",
            step_id,
            run_id,
        )
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
        from distr.core.workflow.context_limits import (
            extract_artifact_paths_from_result,
            truncate_step_result,
        )
        from distr.core.workflow.planning import build_step_context_prompt

        step_id = step_data["id"]
        workflow_id = step_data.get("workflow_id")

        # ── Load workflow-level context ──
        workflow_description = ""
        context_rules = ""
        workflow_input_context = ""
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

                if wf:
                    workflow_description = wf.description or ""
                    context_rules = getattr(wf, 'context_rules', None) or ""
                    try:
                        from distr.core.workflow.service import build_combined_context_rules
                        context_rules = build_combined_context_rules(workflow_id, context_rules)
                    except Exception as ce:
                        logger.debug("_build_agent_prompt: failed to combine context items: %s", ce)
                    all_steps = sorted(wf.steps, key=lambda s: s.position)
                    total_steps = len(all_steps)
                    for i, s in enumerate(all_steps):
                        if s.id == step_id:
                            step_index = i
                            break

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
                            truncated = truncate_step_result(result)
                            paths = extract_artifact_paths_from_result(result)
                            entry: Dict[str, Any] = {
                                "title": title,
                                "result": truncated,
                                "step_type": s.action_type if s else "agent_instruction",
                            }
                            if paths:
                                entry["artifact_paths"] = paths
                            prior_results.append(entry)
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
            # ── Load structured ticket context (if run metadata has ticket_id) ──
            try:
                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(
                        AutoWorkflowRun.id == run_id).first()
                    if run and run.run_data:
                        run_data = json.loads(run.run_data or "{}")
                        ticket_id = run_data.get("ticket_id")
                        if ticket_id:
                            try:
                                from distr.core.kanban.ticket_cli_context import (
                                    build_kanban_ticket_cli_instruction,
                                )
                                ticket_ctx = build_kanban_ticket_cli_instruction(
                                    db,
                                    int(ticket_id),
                                    project_name=(run_data.get("project_name") or ""),
                                    project_folder=(run_data.get("project_folder") or ""),
                                    project_id=run_data.get("project_id"),
                                    max_total_chars=4500,
                                )
                                if ticket_ctx and ticket_ctx.strip():
                                    if workflow_input_context:
                                        workflow_input_context = (
                                            workflow_input_context
                                            + "\n\n"
                                            + ticket_ctx.strip()
                                        )
                                    else:
                                        workflow_input_context = ticket_ctx.strip()
                            except Exception as ce:
                                logger.debug("_build_agent_prompt: ticket context assembly failed: %s", ce)
            except Exception as e:
                logger.debug("_build_agent_prompt: failed loading ticket metadata context: %s", e)

        # ── Inject run context (ticket/board/project context) from start_workflow_run(context=...) ──
        if run_id is not None:
            try:
                from distr.core.workflow.dispatcher import _runs_lock, _active_runs
                with _runs_lock:
                    run_ctx_obj = _active_runs.get(run_id)
                if run_ctx_obj:
                    # Legacy free-form context string
                    if (run_ctx_obj.context_prefix or "").strip():
                        workflow_input_context = run_ctx_obj.context_prefix.strip()
                    # Structured WorkflowRunContext — inject parent session data
                    if run_ctx_obj.run_ctx is not None:
                        structured = run_ctx_obj.run_ctx.as_context_string()
                        if structured:
                            if workflow_input_context:
                                workflow_input_context = (
                                    workflow_input_context + "\n\n" + structured
                                )
                            else:
                                workflow_input_context = structured
            except Exception as e:
                logger.debug("_build_agent_prompt: failed to load run context prefix: %s", e)

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
            workflow_description=(
                f"{workflow_description}\n\nWorkflow input:\n{workflow_input_context}".strip()
                if workflow_input_context
                else (workflow_description or "Complete the requested workflow.")
            ),
            step_title=step_title,
            step_instruction=step_instruction,
            prior_results=prior_results,
            context_rules=context_rules,
            continuation_input=continuation_input,
        )

        logger.info(
            "_build_agent_prompt: step_id=%s run_id=%s prior_results=%d context_rules_len=%d workflow_input_len=%d feedback_len=%d",
            step_id,
            run_id,
            len(prior_results),
            len(context_rules or ""),
            len(workflow_input_context or ""),
            len(continuation_input or ""),
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
        from distr.core.workflow.dispatcher import _runs_lock, _active_runs
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
            from distr.core.workflow_engine.code_generator import CodeGeneratorService
            from distr.core.workflow_engine.step_types import StepType
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
                                context_parts.append(f"{title}: {result[:500]}")

                        run = db.query(AutoWorkflowRun).filter(
                            AutoWorkflowRun.id == run_id).first()
                        if run and run.run_data:
                            run_data = json.loads(run.run_data or "{}")
                            feedback = run_data.get("feedback", "")
                            if feedback and feedback.strip():
                                context_parts.append(f"User feedback: {feedback.strip()[:500]}")
                except Exception as e:
                    logger.debug("_generate_code: failed to load context: %s", e)

                if context_parts:
                    context = "\n".join(context_parts)

            return CodeGeneratorService().generate_code(instruction, step_type, context=context)
        except Exception as e:
            logger.error("Code generation failed: %s", e)
            return None

    @staticmethod
    def _extract_attachment_paths(text: str) -> List[str]:
        if not text:
            return []
        # Capture absolute file paths that end in common media extensions.
        pattern = r"(/[^ \n\t\"']+\.(?:png|jpg|jpeg|webp|gif|mp4|mov|m4a|wav|mp3))"
        hits = re.findall(pattern, text, flags=re.IGNORECASE)
        deduped: List[str] = []
        for p in hits:
            if p not in deduped:
                deduped.append(p)
        return deduped

    def _augment_agent_result_with_tool_evidence(self, result_text: str, workflow_agent) -> str:
        """Append tool evidence + attachments so workflow history/audit is actionable."""
        base = (result_text or "").strip() or "Step completed."
        try:
            msgs = list(workflow_agent.messages or [])
        except Exception:
            return base

        # Collect tool outputs from this step only (messages since last user prompt).
        recent_tool_outputs: List[str] = []
        for msg in reversed(msgs):
            role = (msg or {}).get("role")
            if role == "user":
                break
            if role == "tool":
                content = str((msg or {}).get("content") or "").strip()
                if content:
                    recent_tool_outputs.append(content[:600])
        recent_tool_outputs.reverse()

        attachment_paths = self._extract_attachment_paths(base)
        for chunk in recent_tool_outputs:
            for path in self._extract_attachment_paths(chunk):
                if path not in attachment_paths:
                    attachment_paths.append(path)

        out = base
        if recent_tool_outputs:
            preview = "\n".join(f"- {c}" for c in recent_tool_outputs[:4])
            out = f"{out}\n\nTool evidence:\n{preview}"
        if attachment_paths:
            out = f"{out}\n\nAttachments:\n" + "\n".join(f"- {p}" for p in attachment_paths)
        return out[:8000]
