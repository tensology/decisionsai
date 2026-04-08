"""Workflow orchestration mixin for the Application class.

Replaces the legacy StepRunnerMixin. Operates on AutoWorkflow / AutoWorkflowStep
models and unified ``workflow_*`` signals.
"""
import asyncio
import json
import logging
import threading
from typing import Dict, Optional

from PyQt6.QtCore import QTimer

from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.signals import signal_manager
from distr.core.workflow_agent import WorkflowAgent

logger = logging.getLogger(__name__)

_DIRECT_EXECUTION_TYPES = {
    "run_command", "http_request", "execute_code", "playwright", "play_recording",
}

_orch_lock = threading.Lock()


def _build_steps_summary(steps_data: list) -> list:
    """Pull step status and result from DB to build a rich summary for the agent report."""
    try:
        from distr.core.db import get_session as get_db_session
        from distr.core.db.workflow import AutoWorkflowStep

        ids = [s.get("id") for s in steps_data if s.get("id")]
        if not ids:
            return [{"title": s.get("title", ""), "id": s.get("id")} for s in steps_data]
        with get_db_session() as db:
            rows = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id.in_(ids)).all()
            by_id = {r.id: r for r in rows}
        summary = []
        for s in steps_data:
            row = by_id.get(s.get("id"))
            summary.append({
                "title": s.get("title", ""),
                "id": s.get("id"),
                "status": row.status if row else "",
                "result": (row.result or "")[:300] if row else "",
            })
        return summary
    except Exception:
        return [{"title": s.get("title", ""), "id": s.get("id")} for s in steps_data]


class WorkflowOrchestrationMixin:
    """Handles workflow scheduling, orchestration, and lifecycle.

    Replaces the legacy ``StepRunnerMixin``.  All methods operate on
    ``AutoWorkflow`` / ``AutoWorkflowStep`` models and use the unified
    ``workflow_*`` signals.
    """

    # ── Scheduler tick ──────────────────────────────────────────────

    def _run_workflow_scheduled(self):
        """Periodic scheduler tick — fires due scheduled workflows."""
        try:
            from distr.core.workflow.scheduler import (
                get_due_scheduled_workflows,
                run_scheduled_workflow,
            )

            due = get_due_scheduled_workflows()
            logger.debug("Workflow scheduler tick: %d workflow(s) due", len(due))
            for wf in due:
                workflow_id = wf["id"]
                logger.info(
                    "Workflow scheduler: firing workflow %d (%s)",
                    workflow_id,
                    wf.get("schedule_preset", ""),
                )
                run_scheduled_workflow(
                    workflow_id,
                    on_start_orchestration=lambda wid, rid, steps, wtype: (
                        self._start_workflow_orchestration(wid, rid, steps, wtype)
                    ),
                )
        except Exception as e:
            logger.error("Workflow scheduler error: %s", e, exc_info=True)

        try:
            from distr.core.kanban.scheduler import check_kanban_schedules
            check_kanban_schedules()
        except Exception as e:
            logger.error("Kanban scheduler error: %s", e, exc_info=True)

    # ── Signal handlers ─────────────────────────────────────────────

    def _on_workflow_run_all_requested(
        self,
        workflow_id: int,
        steps_data: list,
        run_id,
        workflow_type: str,
    ):
        self._start_workflow_orchestration(workflow_id, run_id, steps_data, workflow_type)

    def _on_workflow_cancel_requested(self, workflow_id: int):
        with _orch_lock:
            orchestrations: Dict[int, dict] = getattr(self, "_workflow_orchestrations", {})
            orch = orchestrations.get(workflow_id)
            if not orch:
                return
            idx = orch.get("current_index", 0)
            steps_data = orch.get("steps_data", [])
            if 0 <= idx < len(steps_data):
                self._set_workflow_step_status(
                    steps_data[idx]["id"], "cancelled", result="Cancelled by user.",
                )
        self._finish_workflow_orchestration(
            workflow_id=workflow_id, success=False, cancelled=True,
        )

    def _on_workflow_skip_step_requested(self, workflow_id: int):
        with _orch_lock:
            orchestrations: Dict[int, dict] = getattr(self, "_workflow_orchestrations", {})
            orch = orchestrations.get(workflow_id)
        if not orch:
            return
        idx = orch.get("current_index", 0)
        steps_data = orch.get("steps_data", [])
        if 0 <= idx < len(steps_data):
            self._set_workflow_step_status(
                steps_data[idx]["id"], "skipped", result="Skipped by user.",
            )
        orch["is_verification_step"] = False
        orch["is_retry"] = False
        orch["retry_count"] = 0
        next_idx = idx + 1
        if next_idx >= len(steps_data):
            self._finish_workflow_orchestration(workflow_id=workflow_id, success=True)
            return
        orch["current_index"] = next_idx
        self._set_workflow_step_status(steps_data[next_idx]["id"], "running")
        try:
            self._send_workflow_instruction(orch, next_idx)
        except Exception:
            self._finish_workflow_orchestration(workflow_id=workflow_id, success=False)

    def _on_workflow_continue_requested(self, workflow_id: int, optional_input: str):
        with _orch_lock:
            orchestrations: Dict[int, dict] = getattr(self, "_workflow_orchestrations", {})
            orch = orchestrations.get(workflow_id)
        if not orch:
            logger.warning(
                "Workflow continue: no active orchestration for workflow %d",
                workflow_id,
            )
            return
        idx = orch.get("current_index", 0)
        steps_data = orch.get("steps_data", [])
        if idx >= len(steps_data):
            return
        current_step = steps_data[idx]

        if optional_input and optional_input.strip():
            self._set_workflow_step_status(current_step["id"], "running")
            continue_prompt = (
                "[CONTINUE] The waiting step has received input. "
                "Continue with this additional context:\n\n"
                + optional_input.strip()
            )
            try:
                self._send_workflow_instruction(orch, idx, prompt=continue_prompt)
                self._reset_workflow_timeout(workflow_id)
                logger.info(
                    "Workflow: continued workflow %d step %d with input",
                    workflow_id, idx + 1,
                )
            except Exception as e:
                logger.error(
                    "Workflow: failed to send continue prompt: %s", e, exc_info=True,
                )
                self._finish_workflow_orchestration(workflow_id=workflow_id, success=False)
        else:
            self._set_workflow_step_status(
                current_step["id"], "completed",
                result="Continued (no additional input).",
            )
            orch["any_step_succeeded"] = True
            orch["prior_results"].append({
                "title": current_step.get("title") or f"Step {idx + 1}",
                "result": "Continued.",
            })
            orch["is_retry"] = False
            orch["retry_count"] = 0
            next_idx = idx + 1
            if next_idx >= len(steps_data):
                self._finish_workflow_orchestration(workflow_id=workflow_id, success=True)
                return
            orch["current_index"] = next_idx
            self._set_workflow_step_status(steps_data[next_idx]["id"], "running")
            try:
                self._send_workflow_instruction(orch, next_idx)
                self._reset_workflow_timeout(workflow_id)
                logger.info(
                    "Workflow: continued workflow %d, advancing to step %d/%d",
                    workflow_id, next_idx + 1, len(steps_data),
                )
            except Exception as e:
                logger.error(
                    "Workflow: failed to advance after continue: %s", e, exc_info=True,
                )
                self._finish_workflow_orchestration(workflow_id=workflow_id, success=False)


    # ── Chat / context helpers ──────────────────────────────────────

    def _resolve_workflow_chat_id(self, workflow_id: int, default_chat_id=None):
        """Resolve the chat_id for a workflow, falling back to settings."""
        chat_id = default_chat_id
        workflow_description = ""
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflow

            with get_session() as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
                if wf:
                    workflow_description = wf.description or ""
                    if wf.chat_id:
                        chat_id = wf.chat_id
        except Exception:
            pass
        if chat_id is None:
            try:
                settings = load_settings_from_db()
                chat_id = settings.get("agent_current_chat_id")
            except Exception:
                chat_id = None
        return chat_id, workflow_description

    def _get_last_assistant_message(self, chat_id: int) -> str:
        if not chat_id or not self.chat_manager:
            return ""
        try:
            history = self.chat_manager.get_chat_history(int(chat_id)) or []
            for msg in reversed(history):
                if (msg or {}).get("role") == "assistant":
                    return str((msg or {}).get("content") or "").strip()
        except Exception:
            return ""
        return ""

    # ── Timeout management ──────────────────────────────────────────

    def _reset_workflow_timeout(self, workflow_id: int, timeout_ms: int = 300000):
        self._cancel_workflow_timeout(workflow_id)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda wid=workflow_id: self._on_workflow_timeout(wid))
        timer.start(timeout_ms)
        if not hasattr(self, "_workflow_timeout_timers"):
            self._workflow_timeout_timers: Dict[int, QTimer] = {}
        self._workflow_timeout_timers[workflow_id] = timer

    def _cancel_workflow_timeout(self, workflow_id: int = None):
        timers: Dict[int, QTimer] = getattr(self, "_workflow_timeout_timers", {})
        if workflow_id is not None:
            t = timers.pop(workflow_id, None)
            if t:
                try:
                    t.stop()
                except Exception:
                    pass
        else:
            for t in timers.values():
                try:
                    t.stop()
                except Exception:
                    pass
            timers.clear()

    def _on_workflow_timeout(self, workflow_id: int):
        with _orch_lock:
            orchestrations: Dict[int, dict] = getattr(self, "_workflow_orchestrations", {})
            orch = orchestrations.get(workflow_id)
            if not orch:
                return
            idx = orch.get("current_index", 0)
            steps_data = orch.get("steps_data", [])
            logger.warning(
                "Workflow: step %d timed out for workflow %d", idx + 1, workflow_id,
            )
            if 0 <= idx < len(steps_data):
                self._set_workflow_step_status(
                    steps_data[idx]["id"], "failed",
                    result="Timed out waiting for agent response.",
                )
        self._handle_workflow_error(
            "Step timed out - no agent response after 5 minutes.",
            workflow_id=workflow_id,
        )


    # ── Step status helper ──────────────────────────────────────────

    def _set_workflow_step_status(self, step_id: int, status: str, result: str = None):
        """Update an AutoWorkflowStep's status and optionally its result."""
        try:
            from distr.core.workflow.service import update_step
            from distr.gui.web.workflow_events import increment_workflow_updated

            update_step(step_id, status=status, result=result)
            increment_workflow_updated()
        except Exception as e:
            logger.debug("Workflow step status update failed: %s", e)

    # ── Single-step execution (execute-in-place) ────────────────────

    def _on_workflow_execute_step_requested(
        self, step_id: int, workflow_id: int, instruction: str, chat_id,
    ):
        try:
            resolved_chat_id, _ = self._resolve_workflow_chat_id(workflow_id, chat_id)
            self._pending_single_step = {
                "step_id": int(step_id),
                "workflow_id": int(workflow_id),
                "chat_id": resolved_chat_id,
            }
            if resolved_chat_id:
                signal_manager.current_chat_changed.emit(int(resolved_chat_id))
            signal_manager.send_text_input.emit(instruction, False, None, None)
        except Exception as e:
            logger.error("Workflow execute request failed: %s", e, exc_info=True)

    # ── Orchestration lifecycle ─────────────────────────────────────

    def _start_workflow_orchestration(
        self,
        workflow_id: int,
        run_id,
        steps_data: list,
        workflow_type: str,
    ):
        """Start sequential step execution using an independent WorkflowAgent.

        Workflow agents run in isolation — no signal_send_text_input, no
        current_chat_changed emit, and no _suppress_current_chat_relay needed.
        """
        if not steps_data:
            logger.warning("Workflow orchestration: no steps to run")
            return

        with _orch_lock:
            if not hasattr(self, "_workflow_orchestrations"):
                self._workflow_orchestrations: Dict[int, dict] = {}
            if workflow_id in self._workflow_orchestrations:
                logger.warning(
                    "Workflow: orchestration already in progress for workflow %d, skipping",
                    workflow_id,
                )
                return

            resolved_chat_id, workflow_description = self._resolve_workflow_chat_id(
                workflow_id,
            )

            workflow_agent = WorkflowAgent()
            agent_loop = asyncio.new_event_loop()

            def _run_loop():
                asyncio.set_event_loop(agent_loop)
                agent_loop.run_forever()

            agent_thread = threading.Thread(target=_run_loop, daemon=True)
            agent_thread.start()

            orch = {
                "workflow_id": workflow_id,
                "run_id": run_id,
                "steps_data": steps_data,
                "current_index": 0,
                "is_retry": False,
                "retry_count": 0,
                "max_retries": 2,
                "on_failure": "skip",
                "is_verification_step": False,
                "prior_results": [],
                "workflow_description": workflow_description,
                "chat_id": resolved_chat_id,
                "any_step_succeeded": False,
                "workflow_type": workflow_type,
                "workflow_agent": workflow_agent,
                "agent_loop": agent_loop,
                "agent_thread": agent_thread,
                "_advancing": False,
            }
            self._workflow_orchestrations[workflow_id] = orch

        try:
            self._set_workflow_step_status(steps_data[0]["id"], "running")
            self._send_workflow_instruction(orch, 0)
            self._reset_workflow_timeout(workflow_id)
            logger.info(
                "Workflow: started orchestration workflow %d, step 1/%d",
                workflow_id, len(steps_data),
            )
        except Exception as e:
            logger.error(
                "Workflow: failed to send first step: %s", e, exc_info=True,
            )
            self._finish_workflow_orchestration(workflow_id=workflow_id, success=False)


    # ── Instruction dispatch ────────────────────────────────────────

    def _send_workflow_instruction(
        self, orch: dict, step_index: int, prompt: str = None,
    ) -> None:
        """Dispatch a step to the WorkflowAgent or execute directly for typed steps."""
        from distr.core.workflow.service import build_step_context_prompt
        from distr.core.step_runner.context_assembly import assemble_step_context

        workflow_id = orch.get("workflow_id")
        instruction = prompt

        if instruction is None:
            step_data = orch["steps_data"][step_index]
            step_input_ctx = None
            step_type = "agent_instruction"

            try:
                from distr.core.db import get_session as get_db_session
                from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep

                if workflow_id:
                    with get_db_session() as db:
                        wf = (
                            db.query(AutoWorkflow)
                            .filter(AutoWorkflow.id == workflow_id)
                            .first()
                        )
                        step_obj = (
                            db.query(AutoWorkflowStep)
                            .filter(AutoWorkflowStep.id == step_data["id"])
                            .first()
                        )
                        if wf and step_obj:
                            step_type = (
                                getattr(step_obj, "step_type", "agent_instruction")
                                or "agent_instruction"
                            )
                            step_input_ctx = assemble_step_context(
                                session=wf,
                                step=step_obj,
                                prior_results=orch.get("prior_results") or [],
                            )
            except Exception as e:
                logger.debug("Workflow: failed to assemble step context: %s", e)

            orch["step_input_context"] = step_input_ctx

            # Direct execution path for non-agent step types
            if step_type in _DIRECT_EXECUTION_TYPES:
                if not self._validate_workflow_step_config(
                    orch, step_index, step_type, step_input_ctx,
                ):
                    return
                self._execute_step_directly(orch, step_index, step_type, step_input_ctx)
                return

            # Agent instruction path — build context prompt
            context_rules = step_input_ctx.workflow_rules if step_input_ctx else ""
            instruction = build_step_context_prompt(
                step_index=step_index,
                total_steps=len(orch["steps_data"]),
                workflow_description=(
                    orch.get("workflow_description")
                    or "Complete the requested workflow."
                ),
                step_title=step_data.get("title") or f"Step {step_index + 1}",
                step_instruction=step_data.get("instruction") or "",
                prior_results=orch.get("prior_results") or [],
                context_rules=context_rules,
            )

        workflow_agent: Optional[WorkflowAgent] = orch.get("workflow_agent")
        if workflow_agent is None:
            logger.error(
                "Workflow: no WorkflowAgent on orchestration for workflow %s",
                workflow_id,
            )
            self._finish_workflow_orchestration(workflow_id=workflow_id, success=False)
            return

        agent_loop: Optional[asyncio.AbstractEventLoop] = orch.get("agent_loop")
        if agent_loop is None:
            logger.error(
                "Workflow: no event loop on orchestration for workflow %s",
                workflow_id,
            )
            self._finish_workflow_orchestration(workflow_id=workflow_id, success=False)
            return

        future = asyncio.run_coroutine_threadsafe(
            workflow_agent.execute(instruction), agent_loop,
        )

        def _on_agent_done(fut):
            try:
                response_text = fut.result()
            except Exception as exc:
                logger.error(
                    "Workflow: WorkflowAgent.execute failed: %s", exc, exc_info=True,
                )
                QTimer.singleShot(
                    0,
                    lambda: self._handle_workflow_error(
                        str(exc), workflow_id=workflow_id,
                    ),
                )
                return
            QTimer.singleShot(
                0,
                lambda: self._advance_workflow_orchestration(
                    workflow_id=workflow_id, response_text=response_text,
                ),
            )

        future.add_done_callback(_on_agent_done)


    # ── Step validation ─────────────────────────────────────────────

    def _validate_workflow_step_config(
        self, orch: dict, step_index: int, step_type: str, step_input_ctx,
    ) -> bool:
        from distr.core.step_runner.validation import StepValidator

        step_data = orch["steps_data"][step_index]
        config_dict = step_input_ctx.step_config if step_input_ctx else {}
        errors = StepValidator().validate(step_type, config_dict)
        if not errors:
            return True
        error_msg = "; ".join(f"{e.field}: {e.message}" for e in errors)
        logger.warning(
            "Workflow: validation failed for step %d (%s): %s",
            step_data["id"], step_type, error_msg,
        )
        self._set_workflow_step_status(
            step_data["id"], "failed", result=f"Validation failed: {error_msg}",
        )
        self._skip_to_next_workflow_step(orch, step_index)
        return False

    def _skip_to_next_workflow_step(self, orch: dict, current_index: int) -> None:
        steps_data = orch["steps_data"]
        workflow_id = orch.get("workflow_id")
        orch["is_retry"] = False
        orch["retry_count"] = 0
        next_idx = current_index + 1
        if next_idx >= len(steps_data):
            self._finish_workflow_orchestration(
                workflow_id=workflow_id,
                success=orch.get("any_step_succeeded", False),
            )
            return
        orch["current_index"] = next_idx
        self._set_workflow_step_status(steps_data[next_idx]["id"], "running")
        try:
            self._send_workflow_instruction(orch, next_idx)
            self._reset_workflow_timeout(workflow_id)
        except Exception:
            self._finish_workflow_orchestration(workflow_id=workflow_id, success=False)

    # ── Direct execution for typed steps ────────────────────────────

    def _execute_step_directly(
        self, orch: dict, step_index: int, step_type: str, step_input_ctx,
    ) -> None:
        """Execute a non-agent step type directly (run_command, http_request, etc.).

        Resolves ``{{variable}}`` placeholders via ``variable_resolver.resolve_variables()``
        before execution.
        """
        from distr.core.step_runner.variable_resolver import resolve_variables

        step_data = orch["steps_data"][step_index]
        config = step_input_ctx.step_config if step_input_ctx else {}
        workflow_id = orch.get("workflow_id")

        # Resolve {{variable}} placeholders in config string values
        prior_results = orch.get("prior_results") or []
        resolved_config: Dict = {}
        for key, val in config.items():
            if isinstance(val, str):
                resolved_config[key] = resolve_variables(val, prior_results)
            else:
                resolved_config[key] = val
        config = resolved_config

        if step_type == "play_recording":
            self._execute_play_recording(orch, step_index, config)
            return

        def _run():
            result_text, success = "", True
            try:
                if step_type == "run_command":
                    result_text, success = self._exec_run_command(config)
                elif step_type == "http_request":
                    result_text, success = self._exec_http_request(config, step_input_ctx)
                elif step_type == "execute_code":
                    result_text, success = self._exec_execute_code(config, step_data)
                elif step_type == "playwright":
                    result_text, success = self._exec_playwright(config, step_data)
            except Exception as exc:
                result_text, success = f"Error: {exc}", False
            QTimer.singleShot(
                0,
                lambda: self._on_direct_step_completed(
                    orch, step_index, result_text, success,
                ),
            )

        threading.Thread(target=_run, daemon=True).start()

    def _on_direct_step_completed(
        self, orch: dict, step_index: int, result_text: str, success: bool,
    ) -> None:
        workflow_id = orch.get("workflow_id")
        with _orch_lock:
            orchestrations: Dict[int, dict] = getattr(
                self, "_workflow_orchestrations", {},
            )
            if (
                workflow_id not in orchestrations
                or orchestrations[workflow_id] is not orch
            ):
                return
            step_data = orch["steps_data"][step_index]
            self._set_workflow_step_status(
                step_data["id"],
                "completed" if success else "failed",
                result=(result_text or "")[:2000],
            )
            if success:
                orch["any_step_succeeded"] = True
                orch["prior_results"].append({
                    "title": step_data.get("title") or f"Step {step_index + 1}",
                    "result": (result_text or "")[:200],
                })
        if success:
            self._cancel_workflow_timeout(workflow_id)
            self._advance_workflow_orchestration(
                workflow_id=workflow_id,
                response_text=result_text or "Step completed.",
            )
        else:
            self._handle_workflow_error(
                result_text or "Step execution failed.", workflow_id=workflow_id,
            )


    # ── Static execution helpers (shared with StepRunnerMixin) ──────

    @staticmethod
    def _exec_run_command(config: dict) -> tuple:
        import subprocess as _subprocess

        cmd = config.get("command", "")
        cwd = config.get("working_directory") or None
        timeout = config.get("timeout_seconds", 60)
        try:
            proc = _subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd,
            )
            return (proc.stdout + proc.stderr).strip()[:2000], proc.returncode == 0
        except _subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s", False
        except Exception as exc:
            return f"Error executing command: {exc}", False

    @staticmethod
    def _exec_http_request(config: dict, step_input_ctx) -> tuple:
        from distr.core.step_runner.variable_resolver import resolve_http_variables

        prior_results = (
            step_input_ctx.previous_results
            if step_input_ctx and step_input_ctx.previous_results
            else []
        )
        resolved_config = resolve_http_variables(config, prior_results)
        url = resolved_config.get("url", "")
        method = resolved_config.get("method", "GET")
        headers = resolved_config.get("headers", {})
        body = resolved_config.get("body")
        timeout = resolved_config.get("timeout_seconds", 30)

        if step_input_ctx and step_input_ctx.resolved_variables:
            for key, val in step_input_ctx.resolved_variables.items():
                placeholder = "{{" + key + "}}"
                url = url.replace(placeholder, str(val))
                if body:
                    body = body.replace(placeholder, str(val))
                headers = {
                    k: v.replace(placeholder, str(val)) for k, v in headers.items()
                }
        try:
            import requests

            resp = requests.request(
                method, url, headers=headers, data=body, timeout=timeout,
            )
            return (
                f"HTTP {resp.status_code}\n{resp.text[:1500]}",
                200 <= resp.status_code < 400,
            )
        except Exception as exc:
            return f"HTTP request failed: {exc}", False

    @staticmethod
    def _exec_execute_code(config: dict, step_data: dict) -> tuple:
        from distr.core.step_runner.test_loop import TestLoopService

        code = config.get("code", "") or step_data.get("code", "") or ""
        if not code.strip():
            return "No code to execute", False
        exec_result = TestLoopService()._execute_python(code)
        return (
            (exec_result.stdout + exec_result.stderr).strip()[:2000],
            exec_result.exit_code == 0,
        )

    @staticmethod
    def _exec_playwright(config: dict, step_data: dict) -> tuple:
        from distr.core.step_runner.test_loop import TestLoopService

        code = config.get("code", "") or step_data.get("code", "") or ""
        if not code.strip():
            return "No Playwright code to execute", False
        headless = config.get("headless", True)
        try:
            exec_result = TestLoopService()._execute_playwright(code, headless=headless)
            return (
                (exec_result.stdout + exec_result.stderr).strip()[:2000],
                exec_result.exit_code == 0,
            )
        except Exception as exc:
            return f"Playwright browser failure: {exc}", False

    def _execute_play_recording(
        self, orch: dict, step_index: int, config: dict,
    ) -> None:
        step_data = orch["steps_data"][step_index]
        workflow_id = orch.get("workflow_id")
        recording_name = config.get("recording_name", "")
        recording_id = config.get("recording_id")

        if recording_name:
            play_name = recording_name
        elif recording_id:
            try:
                from distr.core.db import get_session as get_db_session, Action

                with get_db_session() as db:
                    action = db.query(Action).filter(Action.id == recording_id).first()
                    if action:
                        play_name = action.title or f"action_{recording_id}"
                    else:
                        self._set_workflow_step_status(
                            step_data["id"], "failed",
                            result=f"Recording ID {recording_id} not found",
                        )
                        self._skip_to_next_workflow_step(orch, step_index)
                        return
            except Exception as exc:
                self._set_workflow_step_status(
                    step_data["id"], "failed",
                    result=f"Error looking up recording: {exc}",
                )
                self._skip_to_next_workflow_step(orch, step_index)
                return
        else:
            self._set_workflow_step_status(
                step_data["id"], "failed",
                result="No recording name or ID specified",
            )
            self._skip_to_next_workflow_step(orch, step_index)
            return

        _advanced = [False]

        def _advance(result_text: str):
            if _advanced[0]:
                return
            _advanced[0] = True
            try:
                signal_manager.action_playback_finished.disconnect(_on_finished)
            except Exception:
                pass
            try:
                signal_manager.action_playback_stopped.disconnect(_on_stopped)
            except Exception:
                pass
            self._set_workflow_step_status(
                step_data["id"], "completed", result=result_text,
            )
            orch["any_step_succeeded"] = True
            orch["prior_results"].append({
                "title": step_data.get("title") or f"Step {step_index + 1}",
                "result": result_text[:200],
            })
            self._cancel_workflow_timeout(workflow_id)
            QTimer.singleShot(
                0,
                lambda: self._advance_workflow_orchestration(
                    workflow_id=workflow_id, response_text=result_text,
                ),
            )

        def _on_finished():
            _advance(f"Completed recording: {play_name}")

        def _on_stopped(reason: str):
            _advance(
                f"Recording stopped: {play_name}"
                + (f" ({reason})" if reason else ""),
            )

        signal_manager.action_playback_finished.connect(_on_finished)
        signal_manager.action_playback_stopped.connect(_on_stopped)

        self._set_workflow_step_status(
            step_data["id"], "running", result=f"Playing: {play_name}",
        )
        signal_manager.play_action_by_name.emit(play_name)
        logger.info(
            "Workflow: workflow %d waiting for recording '%s' to finish",
            workflow_id, play_name,
        )


    # ── Advance orchestration ───────────────────────────────────────

    def _advance_workflow_orchestration(
        self,
        workflow_id: int,
        chat_id=None,
        response_text=None,
    ):
        with _orch_lock:
            orchestrations: Dict[int, dict] = getattr(
                self, "_workflow_orchestrations", {},
            )
            orch = orchestrations.get(workflow_id)
            if not orch:
                return
            if orch.get("_advancing"):
                logger.debug(
                    "Workflow: _advance already in progress for workflow %d",
                    workflow_id,
                )
                return
            orch["_advancing"] = True

        try:
            self._cancel_workflow_timeout(workflow_id)
            idx = orch["current_index"]
            steps_data = orch["steps_data"]
            expected_chat_id = orch.get("chat_id")

            if expected_chat_id and chat_id and expected_chat_id != chat_id:
                return

            if response_text is None:
                response_text = self._get_last_assistant_message(
                    chat_id or expected_chat_id,
                )

            if orch.get("is_verification_step"):
                if "VERIFIED" in (response_text or "").upper():
                    orch["is_verification_step"] = False
                else:
                    self._handle_workflow_error(
                        "Verification failed. Expected condition not met.",
                        workflow_id=workflow_id,
                        chat_id=chat_id or expected_chat_id,
                    )
                    return
            else:
                current_step = steps_data[idx]
                step_result = (response_text or "Step completed.").strip()

                if "[WAIT]" in (response_text or ""):
                    self._set_workflow_step_status(
                        current_step["id"], "waiting", result=step_result[:2000],
                    )
                    self._cancel_workflow_timeout(workflow_id)
                    logger.info(
                        "Workflow: workflow %d step %d is waiting",
                        workflow_id, idx + 1,
                    )
                    return

                self._set_workflow_step_status(
                    current_step["id"], "completed", result=step_result[:2000],
                )
                orch["any_step_succeeded"] = True
                orch["prior_results"].append({
                    "title": current_step.get("title") or f"Step {idx + 1}",
                    "result": step_result[:200],
                })

                verification = (current_step.get("verification") or "").strip()
                if verification:
                    orch["is_verification_step"] = True
                    orch["retry_count"] = 0
                    verify_prompt = (
                        "[VERIFICATION] Take a screenshot and verify this condition:\n"
                        f"{verification}\n\n"
                        "Use the playwright_browser tool if this is a web/browser task — it will "
                        "capture both a screenshot AND browser console logs for cross-checking.\n"
                        "If confirmed, respond with VERIFIED. If not, describe what you see "
                        "and any console errors that contradict the expected state."
                    )
                    try:
                        self._send_workflow_instruction(orch, idx, prompt=verify_prompt)
                        self._reset_workflow_timeout(workflow_id)
                    except Exception as e:
                        logger.error(
                            "Workflow: failed to send verification prompt: %s",
                            e, exc_info=True,
                        )
                        self._finish_workflow_orchestration(
                            workflow_id=workflow_id, success=False,
                        )
                    return

            orch["is_retry"] = False
            orch["retry_count"] = 0
            next_idx = idx + 1
            if next_idx >= len(steps_data):
                self._finish_workflow_orchestration(
                    workflow_id=workflow_id,
                    success=orch.get("any_step_succeeded", True),
                )
                return

            orch["current_index"] = next_idx
            try:
                self._set_workflow_step_status(steps_data[next_idx]["id"], "running")
                self._send_workflow_instruction(orch, next_idx)
                self._reset_workflow_timeout(workflow_id)
                logger.info(
                    "Workflow: workflow %d, step %d/%d",
                    workflow_id, next_idx + 1, len(steps_data),
                )
            except Exception as e:
                logger.error(
                    "Workflow: failed to send step %d: %s",
                    next_idx + 1, e, exc_info=True,
                )
                self._finish_workflow_orchestration(
                    workflow_id=workflow_id, success=False,
                )
        finally:
            with _orch_lock:
                if orch:
                    orch["_advancing"] = False


    # ── Error handling / retry ──────────────────────────────────────

    def _handle_workflow_error(
        self, error: str, workflow_id: int = None, chat_id=None,
    ):
        with _orch_lock:
            orchestrations: Dict[int, dict] = getattr(
                self, "_workflow_orchestrations", {},
            )
            orch = orchestrations.get(workflow_id) if workflow_id is not None else None
            if not orch:
                return
            workflow_id = orch["workflow_id"]
            expected_chat_id = orch.get("chat_id")
            if expected_chat_id and chat_id and expected_chat_id != chat_id:
                return
            idx = orch.get("current_index", 0)
            steps_data = orch.get("steps_data", [])
            if idx >= len(steps_data):
                self._finish_workflow_orchestration(
                    workflow_id=workflow_id, success=False,
                )
                return
            current_step = steps_data[idx]
            retry_count = int(orch.get("retry_count", 0))
            max_retries = int(orch.get("max_retries", 2))

        if retry_count >= max_retries:
            self._set_workflow_step_status(
                current_step["id"], "failed", result=(error or "Step failed.")[:2000],
            )
            if orch.get("on_failure") == "stop":
                self._finish_workflow_orchestration(
                    workflow_id=workflow_id, success=False,
                )
                return
            orch["is_verification_step"] = False
            orch["is_retry"] = False
            orch["retry_count"] = 0
            next_idx = idx + 1
            if next_idx >= len(steps_data):
                self._finish_workflow_orchestration(
                    workflow_id=workflow_id, success=False,
                )
                return
            orch["current_index"] = next_idx
            self._set_workflow_step_status(steps_data[next_idx]["id"], "running")
            try:
                self._send_workflow_instruction(orch, next_idx)
            except Exception:
                self._finish_workflow_orchestration(
                    workflow_id=workflow_id, success=False,
                )
            return

        orch["is_retry"] = True
        orch["retry_count"] = retry_count + 1
        if retry_count == 0:
            retry_prompt = (
                f"Step failed: {error}\n\n"
                "Take a screenshot to assess the current state, then retry the step."
            )
        else:
            retry_prompt = (
                f"Step failed again: {error}\n\n"
                "Try an alternative approach to accomplish this step."
            )
        try:
            self._send_workflow_instruction(orch, idx, prompt=retry_prompt)
            self._reset_workflow_timeout(workflow_id)
            logger.info(
                "Workflow: sent retry prompt (%d/%d)",
                orch["retry_count"], max_retries,
            )
        except Exception as e:
            logger.error(
                "Workflow: failed to send retry prompt: %s", e, exc_info=True,
            )
            self._finish_workflow_orchestration(
                workflow_id=workflow_id, success=False,
            )

    # ── Finish orchestration ────────────────────────────────────────

    def _finish_workflow_orchestration(
        self,
        workflow_id: int,
        success: bool = True,
        cancelled: bool = False,
    ):
        self._cancel_workflow_timeout(workflow_id)

        with _orch_lock:
            orchestrations: Dict[int, dict] = getattr(
                self, "_workflow_orchestrations", {},
            )
            orch = orchestrations.pop(workflow_id, None)
            if not orch:
                return

        # Shut down the workflow agent and its event loop
        workflow_agent: Optional[WorkflowAgent] = orch.get("workflow_agent")
        agent_loop: Optional[asyncio.AbstractEventLoop] = orch.get("agent_loop")
        if workflow_agent:
            try:
                workflow_agent.shutdown()
            except Exception:
                pass
        if agent_loop:
            try:
                agent_loop.call_soon_threadsafe(agent_loop.stop)
            except Exception:
                pass

        run_id = orch["run_id"]
        steps_data = orch["steps_data"]

        try:
            if run_id is not None:
                self._finish_workflow_run(
                    workflow_id, run_id, steps_data, success=success,
                )
                if cancelled:
                    from distr.core.db import get_session
                    from distr.core.db.workflow import AutoWorkflow

                    with get_session() as db:
                        wf = (
                            db.query(AutoWorkflow)
                            .filter(AutoWorkflow.id == workflow_id)
                            .first()
                        )
                        if wf:
                            wf.status = "cancelled"
                        db.commit()
            else:
                from distr.core.db import get_session
                from distr.core.db.workflow import AutoWorkflow

                with get_session() as db:
                    wf = (
                        db.query(AutoWorkflow)
                        .filter(AutoWorkflow.id == workflow_id)
                        .first()
                    )
                    if wf:
                        if cancelled:
                            wf.status = "cancelled"
                        else:
                            wf.status = "completed" if success else "failed"
                    db.commit()

            from distr.gui.web.workflow_events import increment_workflow_updated
            increment_workflow_updated()
        except Exception as e:
            logger.error(
                "Workflow: finish orchestration failed: %s", e, exc_info=True,
            )

        # Notify the Workflow Agent Bridge so the Voice Agent receives a summary.
        try:
            from distr.core.step_runner.agent_bridge import WorkflowAgentBridge

            run_result = {
                "session_id": workflow_id,
                "run_id": run_id,
                "success": success,
                "cancelled": cancelled,
                "steps_summary": _build_steps_summary(steps_data),
            }
            WorkflowAgentBridge().on_workflow_completed(workflow_id, run_result)
        except Exception as e:
            logger.debug("WorkflowAgentBridge notification failed: %s", e)

        logger.info(
            "Workflow: completed orchestration workflow %d (success=%s)",
            workflow_id, success,
        )

    @staticmethod
    def _finish_workflow_run(
        workflow_id: int,
        run_id: int,
        steps_data: list,
        success: bool = True,
    ):
        """Update run and workflow after orchestration completes."""
        from distr.core.db import get_session
        from distr.core.db.workflow import (
            AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun,
        )
        from datetime import datetime

        with get_session() as db:
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            run_status = "completed" if success else "failed"

            if wf:
                wf.last_run_at = datetime.utcnow()
                wf.status = run_status

            if run:
                run.completed_at = datetime.utcnow()
                run.status = run_status
                steps = (
                    db.query(AutoWorkflowStep)
                    .filter(AutoWorkflowStep.workflow_id == workflow_id)
                    .order_by(AutoWorkflowStep.position.asc())
                    .all()
                )
                run.step_results = json.dumps([
                    {"step_id": s.id, "status": s.status, "result": s.result}
                    for s in steps
                ])

            db.commit()

        logger.info(
            "Workflow: finished workflow %d (success=%s)", workflow_id, success,
        )
