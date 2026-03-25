"""Step Runner orchestration mixin for the Application class."""

import json
import logging
import threading

from PyQt6.QtCore import QTimer

from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.signals import signal_manager

logger = logging.getLogger(__name__)

# Step types that are executed directly (not via agent)
_DIRECT_EXECUTION_TYPES = {"run_command", "http_request", "execute_code", "playwright", "play_recording"}

# Lock protecting all access to _step_runner_orchestration state.
# Every method that reads or mutates the orchestration dict MUST hold this.
_orch_lock = threading.Lock()


class StepRunnerMixin:
    """Handles step runner scheduling, orchestration, and lifecycle."""

    def _run_step_runner_scheduled(self):
        """Run due Step Runner scheduled sessions (e.g. daily calendar check)."""
        try:
            from distr.core.step_runner.scheduler import get_due_scheduled_sessions, run_scheduled_session
            from distr.core.signals import signal_manager
            due = get_due_scheduled_sessions()
            logger.debug("Step Runner scheduler tick: %d session(s) due", len(due))
            for s in due:
                session_id = s["id"]
                logger.info("Step Runner scheduler: firing session %d (%s)", session_id, s["schedule"])
                run_scheduled_session(
                    session_id,
                    on_start_orchestration=lambda sid, rid, steps, stype: self._start_step_runner_orchestration(
                        sid, rid, steps, stype, signal_manager.send_text_input
                    ),
                )
        except Exception as e:
            logger.error("Step Runner scheduler error: %s", e, exc_info=True)

    def _on_step_runner_run_all_requested(self, session_id: int, steps_data: list, run_id, session_type: str):
        """Handle Run All request from API (one-time or manual run)."""
        from distr.core.signals import signal_manager
        self._start_step_runner_orchestration(session_id, run_id, steps_data, session_type, signal_manager.send_text_input)

    def _resolve_step_runner_chat_id(self, session_id: int, default_chat_id=None):
        """Resolve chat_id for Step Runner execution."""
        chat_id = default_chat_id
        session_instruction = ""
        try:
            from distr.core.db import get_session
            from distr.core.db.step_runner import StepRunnerSession
            with get_session() as db:
                sess = db.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
                if sess:
                    session_instruction = sess.instruction or ""
                    if sess.chat_id:
                        chat_id = sess.chat_id
        except Exception:
            pass
        if chat_id is None:
            try:
                settings = load_settings_from_db()
                chat_id = settings.get("agent_current_chat_id")
            except Exception:
                chat_id = None
        return chat_id, session_instruction

    def _get_last_assistant_message(self, chat_id: int) -> str:
        """Return most recent assistant message for chat."""
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

    def _reset_step_runner_timeout(self, timeout_ms: int = 300000):
        """Reset the per-step timeout (5 min default). If it fires, the step is considered hung."""
        self._cancel_step_runner_timeout()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_step_runner_timeout)
        timer.start(timeout_ms)
        self._step_runner_timeout_timer = timer

    def _cancel_step_runner_timeout(self):
        """Cancel any active step timeout timer."""
        t = getattr(self, "_step_runner_timeout_timer", None)
        if t:
            try:
                t.stop()
            except Exception:
                pass
        self._step_runner_timeout_timer = None

    def _on_step_runner_timeout(self):
        """Called when a step has been running too long with no response."""
        with _orch_lock:
            orch = getattr(self, "_step_runner_orchestration", None)
            if not orch:
                return
            idx = orch.get("current_index", 0)
            steps_data = orch.get("steps_data", [])
            logger.warning("Step Runner: step %d timed out (no agent response in 5 minutes)", idx + 1)
            if 0 <= idx < len(steps_data):
                self._set_step_status(steps_data[idx]["id"], "failed", result="Timed out waiting for agent response.")
        self._handle_step_runner_error("Step timed out - no agent response after 5 minutes.")

    def _send_step_runner_instruction(self, orch: dict, step_index: int, prompt: str = None) -> None:
        """Send step instruction with contextual wrapper.

        Uses ``assemble_step_context()`` to build a ``StepInputContext`` from
        the session, step, and prior results.  The assembled context's
        ``workflow_rules`` feeds into ``build_step_context_prompt`` (replacing
        the old manual ``context_rules`` DB lookup), and the full context
        object is stored on the orchestration dict as ``step_input_context``
        so future type-specific executors can consume it.

        Before execution, validates the step config via ``StepValidator``.
        If validation fails, the step is marked as failed and the orchestration
        skips to the next step.

        For non-agent step types (run_command, http_request, execute_code,
        playwright, play_recording), execution is routed to
        ``_execute_step_directly`` instead of sending to the agent.
        """
        from distr.core.step_runner.service import build_step_context_prompt
        from distr.core.step_runner.context_assembly import assemble_step_context

        instruction = prompt
        if instruction is None:
            step_data = orch["steps_data"][step_index]

            # --- Assemble step input context via the context assembly layer ---
            step_input_ctx = None
            step_type = "agent_instruction"
            try:
                from distr.core.db import get_session as get_db_session
                from distr.core.db.step_runner import StepRunnerSession, StepRunnerStep
                session_id = orch.get("session_id")
                if session_id:
                    with get_db_session() as db:
                        sess = db.query(StepRunnerSession).filter(
                            StepRunnerSession.id == session_id
                        ).first()
                        step_obj = db.query(StepRunnerStep).filter(
                            StepRunnerStep.id == step_data["id"]
                        ).first()
                        if sess and step_obj:
                            step_type = getattr(step_obj, "step_type", "agent_instruction") or "agent_instruction"
                            step_input_ctx = assemble_step_context(
                                session=sess,
                                step=step_obj,
                                prior_results=orch.get("prior_results") or [],
                            )
            except Exception as e:
                logger.debug("Step Runner: failed to assemble step context: %s", e)

            # Store assembled context on orch dict for type-specific executors
            orch["step_input_context"] = step_input_ctx

            # --- Validate step config before execution ---
            if step_type in _DIRECT_EXECUTION_TYPES:
                if not self._validate_step_config(orch, step_index, step_type, step_input_ctx):
                    return  # Validation failed — step skipped

            # --- Route to type-specific executor for non-agent steps ---
            if step_type in _DIRECT_EXECUTION_TYPES:
                self._execute_step_directly(orch, step_index, step_type, step_input_ctx)
                return

            # --- Agent instruction (default): build prompt and send to agent ---
            # Extract workflow_rules from assembled context (falls back to "")
            context_rules = step_input_ctx.workflow_rules if step_input_ctx else ""

            instruction = build_step_context_prompt(
                step_index=step_index,
                total_steps=len(orch["steps_data"]),
                session_instruction=orch.get("session_instruction") or "Complete the requested workflow.",
                step_title=step_data.get("title") or f"Step {step_index + 1}",
                step_instruction=step_data.get("instruction") or "",
                prior_results=orch.get("prior_results") or [],
                context_rules=context_rules,
            )
        orch["signal_send_text_input"].emit(instruction, False, None, None)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_step_config(self, orch: dict, step_index: int, step_type: str, step_input_ctx) -> bool:
        """Validate step config before execution.

        Returns ``True`` if valid, ``False`` if validation failed (step is
        marked failed and orchestration advances to the next step).
        """
        from distr.core.step_runner.validation import StepValidator

        step_data = orch["steps_data"][step_index]
        config_dict = step_input_ctx.step_config if step_input_ctx else {}

        errors = StepValidator().validate(step_type, config_dict)
        if not errors:
            return True

        error_msg = "; ".join(f"{e.field}: {e.message}" for e in errors)
        logger.warning(
            "Step Runner: validation failed for step %d (%s): %s",
            step_data["id"], step_type, error_msg,
        )
        self._set_step_status(
            step_data["id"], "failed",
            result=f"Validation failed: {error_msg}",
        )
        # Skip to next step (don't block orchestration)
        self._skip_to_next_step(orch, step_index)
        return False

    def _skip_to_next_step(self, orch: dict, current_index: int) -> None:
        """Advance orchestration past the current step after a validation failure or skip."""
        steps_data = orch["steps_data"]
        orch["is_retry"] = False
        orch["retry_count"] = 0
        next_idx = current_index + 1
        if next_idx >= len(steps_data):
            self._finish_step_runner_orchestration(success=orch.get("any_step_succeeded", False))
            return
        orch["current_index"] = next_idx
        self._set_step_status(steps_data[next_idx]["id"], "running")
        try:
            self._send_step_runner_instruction(orch, next_idx)
            self._reset_step_runner_timeout()
        except Exception:
            self._finish_step_runner_orchestration(success=False)

    # ------------------------------------------------------------------
    # Type-specific direct execution
    # ------------------------------------------------------------------

    def _execute_step_directly(self, orch: dict, step_index: int, step_type: str, step_input_ctx) -> None:
        """Execute a non-agent step type directly in a background thread.

        For ``play_recording`` the signal is emitted on the main thread
        immediately.  For all other types a background thread is spawned
        so the Qt event loop is not blocked.
        """
        step_data = orch["steps_data"][step_index]
        config = step_input_ctx.step_config if step_input_ctx else {}

        # play_recording is fire-and-forget via signal — no background thread needed
        if step_type == "play_recording":
            self._execute_play_recording(orch, step_index, config)
            return

        def _run():
            result_text = ""
            success = True
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
                result_text = f"Error: {exc}"
                success = False

            # Marshal back to main thread via QTimer
            QTimer.singleShot(
                0,
                lambda: self._on_direct_step_completed(orch, step_index, result_text, success),
            )

        threading.Thread(target=_run, daemon=True).start()

    def _on_direct_step_completed(self, orch: dict, step_index: int, result_text: str, success: bool) -> None:
        """Called on the main thread when a directly-executed step finishes."""
        with _orch_lock:
            # Guard: orchestration may have been cancelled while the thread was running
            if getattr(self, "_step_runner_orchestration", None) is not orch:
                return

            step_data = orch["steps_data"][step_index]
            status = "completed" if success else "failed"
            self._set_step_status(step_data["id"], status, result=(result_text or "")[:2000])

            if success:
                orch["any_step_succeeded"] = True
                orch["prior_results"].append({
                    "title": step_data.get("title") or f"Step {step_index + 1}",
                    "result": (result_text or "")[:200],
                })

        if success:
            # Advance to next step (outside lock — _advance acquires its own)
            self._cancel_step_runner_timeout()
            self._advance_step_runner_orchestration(
                response_text=result_text or "Step completed.",
            )
        else:
            self._handle_step_runner_error(
                result_text or "Step execution failed.",
            )

    # ------------------------------------------------------------------
    # Individual step type executors (run in background thread)
    # ------------------------------------------------------------------

    @staticmethod
    def _exec_run_command(config: dict) -> tuple:
        """Execute a run_command step via subprocess. Returns (result_text, success)."""
        import subprocess as _subprocess

        cmd = config.get("command", "")
        cwd = config.get("working_directory") or None
        timeout = config.get("timeout_seconds", 60)
        try:
            proc = _subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd,
            )
            output = (proc.stdout + proc.stderr).strip()[:2000]
            return output, proc.returncode == 0
        except _subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s", False
        except Exception as exc:
            return f"Error executing command: {exc}", False

    @staticmethod
    def _exec_http_request(config: dict, step_input_ctx) -> tuple:
        """Execute an http_request step. Returns (result_text, success)."""
        from distr.core.step_runner.variable_resolver import resolve_http_variables

        # Resolve variables in the config from previous step outputs
        prior_results = []
        if step_input_ctx and step_input_ctx.previous_results:
            prior_results = step_input_ctx.previous_results
        resolved_config = resolve_http_variables(config, prior_results)

        url = resolved_config.get("url", "")
        method = resolved_config.get("method", "GET")
        headers = resolved_config.get("headers", {})
        body = resolved_config.get("body")
        timeout = resolved_config.get("timeout_seconds", 30)

        # Also apply resolved_variables from step_input_ctx to url/headers/body
        if step_input_ctx and step_input_ctx.resolved_variables:
            for key, val in step_input_ctx.resolved_variables.items():
                placeholder = "{{" + key + "}}"
                url = url.replace(placeholder, str(val))
                if body:
                    body = body.replace(placeholder, str(val))
                headers = {k: v.replace(placeholder, str(val)) for k, v in headers.items()}

        try:
            import requests
            resp = requests.request(
                method, url, headers=headers, data=body, timeout=timeout,
            )
            result_text = f"HTTP {resp.status_code}\n{resp.text[:1500]}"
            success = 200 <= resp.status_code < 400
            return result_text, success
        except Exception as exc:
            return f"HTTP request failed: {exc}", False

    @staticmethod
    def _exec_execute_code(config: dict, step_data: dict) -> tuple:
        """Execute an execute_code step. Returns (result_text, success)."""
        from distr.core.step_runner.test_loop import TestLoopService

        code = config.get("code", "") or step_data.get("code", "") or ""
        if not code.strip():
            return "No code to execute", False
        exec_result = TestLoopService()._execute_python(code)
        output = (exec_result.stdout + exec_result.stderr).strip()[:2000]
        return output, exec_result.exit_code == 0

    @staticmethod
    def _exec_playwright(config: dict, step_data: dict) -> tuple:
        """Execute a playwright step. Returns (result_text, success)."""
        from distr.core.step_runner.test_loop import TestLoopService

        code = config.get("code", "") or step_data.get("code", "") or ""
        if not code.strip():
            return "No Playwright code to execute", False
        headless = config.get("headless", True)
        try:
            exec_result = TestLoopService()._execute_playwright(code, headless=headless)
            output = (exec_result.stdout + exec_result.stderr).strip()[:2000]
            return output, exec_result.exit_code == 0
        except Exception as exc:
            return f"Playwright browser failure: {exc}", False

    def _execute_play_recording(self, orch: dict, step_index: int, config: dict) -> None:
        """Execute a play_recording step by emitting the playback signal."""
        step_data = orch["steps_data"][step_index]
        recording_name = config.get("recording_name", "")
        recording_id = config.get("recording_id")

        if recording_name:
            signal_manager.play_action_by_name.emit(recording_name)
            result_text = f"Playing recording: {recording_name}"
        elif recording_id:
            # Lookup name by ID and emit
            try:
                from distr.core.db import get_session as get_db_session, Action
                with get_db_session() as db:
                    action = db.query(Action).filter(Action.id == recording_id).first()
                    if action:
                        name = action.title or f"action_{recording_id}"
                        signal_manager.play_action_by_name.emit(name)
                        result_text = f"Playing recording: {name}"
                    else:
                        self._set_step_status(step_data["id"], "failed", result=f"Recording ID {recording_id} not found")
                        self._skip_to_next_step(orch, step_index)
                        return
            except Exception as exc:
                self._set_step_status(step_data["id"], "failed", result=f"Error looking up recording: {exc}")
                self._skip_to_next_step(orch, step_index)
                return
        else:
            self._set_step_status(step_data["id"], "failed", result="No recording name or ID specified")
            self._skip_to_next_step(orch, step_index)
            return

        # Mark completed and advance (recording playback is fire-and-forget)
        self._set_step_status(step_data["id"], "completed", result=result_text)
        orch["any_step_succeeded"] = True
        orch["prior_results"].append({
            "title": step_data.get("title") or f"Step {step_index + 1}",
            "result": result_text[:200],
        })
        self._cancel_step_runner_timeout()
        self._advance_step_runner_orchestration(response_text=result_text)

    def _set_step_status(self, step_id: int, status: str, result: str = None):
        """Update step status and trigger UI refresh."""
        try:
            from distr.core.step_runner.service import update_step_status
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            update_step_status(step_id, status, result=result)
            increment_step_runner_updated()
        except Exception as e:
            logger.debug("Step Runner step status update failed: %s", e)

    def _on_step_runner_execute_requested(self, step_id: int, session_id: int, instruction: str, chat_id):
        """Handle single-step execution routed through app event handlers."""
        try:
            resolved_chat_id, _ = self._resolve_step_runner_chat_id(session_id, chat_id)
            self._pending_single_step = {
                "step_id": int(step_id),
                "session_id": int(session_id),
                "chat_id": resolved_chat_id,
            }
            if resolved_chat_id:
                signal_manager.current_chat_changed.emit(int(resolved_chat_id))
            signal_manager.send_text_input.emit(instruction, False, None, None)
        except Exception as e:
            logger.error("Step Runner execute request failed: %s", e, exc_info=True)

    def _on_step_runner_cancel_requested(self, session_id: int):
        """Cancel active step runner orchestration for session."""
        with _orch_lock:
            orch = getattr(self, "_step_runner_orchestration", None)
            if not orch or orch.get("session_id") != session_id:
                return
            idx = orch.get("current_index", 0)
            steps_data = orch.get("steps_data", [])
            if 0 <= idx < len(steps_data):
                self._set_step_status(steps_data[idx]["id"], "cancelled", result="Cancelled by user.")
        self._finish_step_runner_orchestration(success=False, cancelled=True)

    def _on_step_runner_skip_step_requested(self, session_id: int):
        """Skip current step for active orchestration."""
        orch = getattr(self, "_step_runner_orchestration", None)
        if not orch or orch.get("session_id") != session_id:
            return
        idx = orch.get("current_index", 0)
        steps_data = orch.get("steps_data", [])
        if 0 <= idx < len(steps_data):
            self._set_step_status(steps_data[idx]["id"], "skipped", result="Skipped by user.")
        orch["is_verification_step"] = False
        orch["is_retry"] = False
        orch["retry_count"] = 0
        next_idx = idx + 1
        if next_idx >= len(steps_data):
            self._finish_step_runner_orchestration(success=True)
            return
        orch["current_index"] = next_idx
        self._set_step_status(steps_data[next_idx]["id"], "running")
        try:
            self._send_step_runner_instruction(orch, next_idx)
        except Exception:
            self._finish_step_runner_orchestration(success=False)

    def _on_step_runner_continue_requested(self, session_id: int, optional_input: str):
        """Continue a waiting step in an active orchestration.
        
        When a step is in 'waiting' status, this resumes execution.
        If optional_input is provided, it's sent as additional context to the agent.
        If no input, the orchestration simply advances to the next step.
        """
        orch = getattr(self, "_step_runner_orchestration", None)
        if not orch or orch.get("session_id") != session_id:
            logger.warning("Step Runner continue: no active orchestration for session %d", session_id)
            return
        idx = orch.get("current_index", 0)
        steps_data = orch.get("steps_data", [])
        if idx >= len(steps_data):
            return
        current_step = steps_data[idx]

        if optional_input and optional_input.strip():
            # Resume with additional input: set step back to running and send the input as instruction
            self._set_step_status(current_step["id"], "running")
            continue_prompt = (
                f"[CONTINUE] The waiting step has received input. Continue with this additional context:\n\n"
                f"{optional_input.strip()}"
            )
            try:
                self._send_step_runner_instruction(orch, idx, prompt=continue_prompt)
                self._reset_step_runner_timeout()
                logger.info("Step Runner: continued session %d step %d with input", session_id, idx + 1)
            except Exception as e:
                logger.error("Step Runner: failed to send continue prompt: %s", e, exc_info=True)
                self._finish_step_runner_orchestration(success=False)
        else:
            # No input: mark current step completed and advance
            self._set_step_status(current_step["id"], "completed", result="Continued (no additional input).")
            orch["any_step_succeeded"] = True
            orch["prior_results"].append({
                "title": current_step.get("title") or f"Step {idx + 1}",
                "result": "Continued.",
            })
            orch["is_retry"] = False
            orch["retry_count"] = 0
            next_idx = idx + 1
            if next_idx >= len(steps_data):
                self._finish_step_runner_orchestration(success=True)
                return
            orch["current_index"] = next_idx
            self._set_step_status(steps_data[next_idx]["id"], "running")
            try:
                self._send_step_runner_instruction(orch, next_idx)
                self._reset_step_runner_timeout()
                logger.info("Step Runner: continued session %d, advancing to step %d/%d", session_id, next_idx + 1, len(steps_data))
            except Exception as e:
                logger.error("Step Runner: failed to advance after continue: %s", e, exc_info=True)
                self._finish_step_runner_orchestration(success=False)

    def _start_step_runner_orchestration(
        self,
        session_id: int,
        run_id,
        steps_data: list,
        session_type: str,
        signal_send_text_input,
    ):
        """
        Start sequential step execution. Waits for chat_stream_finished before each next step.
        On chat_stream_error, sends retry prompt to resolve the failure.
        """
        if not steps_data:
            logger.warning("Step Runner orchestration: no steps to run")
            return
        with _orch_lock:
            if getattr(self, "_step_runner_orchestration", None):
                logger.warning("Step Runner: orchestration already in progress for session %d, skipping session %d",
                               self._step_runner_orchestration["session_id"], session_id)
                return
            resolved_chat_id, session_instruction = self._resolve_step_runner_chat_id(session_id)
            if resolved_chat_id:
                # Switch chat context without triggering a full agent reload.
                self._suppress_current_chat_relay = True
                try:
                    signal_manager.current_chat_changed.emit(int(resolved_chat_id))
                finally:
                    self._suppress_current_chat_relay = False
                self._send_command_to_agent('current_chat_changed', {'chat_id': int(resolved_chat_id)}, ensure_alive=False)
            self._step_runner_orchestration = {
                "session_id": session_id,
                "run_id": run_id,
                "steps_data": steps_data,
                "current_index": 0,
                "is_retry": False,
                "retry_count": 0,
                "max_retries": 2,
                "on_failure": "skip",
                "is_verification_step": False,
                "prior_results": [],
                "session_instruction": session_instruction,
                "chat_id": resolved_chat_id,
                "any_step_succeeded": False,
                "session_type": session_type,
                "signal_send_text_input": signal_send_text_input,
                "_advancing": False,  # re-entrancy guard
            }
        try:
            self._set_step_status(steps_data[0]["id"], "running")
            self._send_step_runner_instruction(self._step_runner_orchestration, 0)
            self._reset_step_runner_timeout()
            logger.info("Step Runner: started orchestration session %d, step 1/%d", session_id, len(steps_data))
        except Exception as e:
            logger.error("Step Runner: failed to send first step: %s", e, exc_info=True)
            self._finish_step_runner_orchestration(success=False)

    def _advance_step_runner_orchestration(self, chat_id=None, response_text=None):
        """
        Advance to next step or finish. Called after chat_stream_finished.
        Sends next step instruction or completes the run.

        Protected by _orch_lock to prevent concurrent advancement from
        chat_stream_finished and _on_direct_step_completed racing each other.
        """
        with _orch_lock:
            orch = getattr(self, "_step_runner_orchestration", None)
            if not orch:
                return
            # Re-entrancy guard: if we're already inside an advance call
            # (e.g. direct-step completion + chat_stream_finished arriving
            # on the same Qt event-loop tick), bail out.
            if orch.get("_advancing"):
                logger.debug("Step Runner: _advance already in progress, skipping duplicate call")
                return
            orch["_advancing"] = True

        try:
            self._cancel_step_runner_timeout()
            idx = orch["current_index"]
            steps_data = orch["steps_data"]
            session_id = orch["session_id"]
            expected_chat_id = orch.get("chat_id")
            if expected_chat_id and chat_id and expected_chat_id != chat_id:
                return

            if response_text is None:
                response_text = self._get_last_assistant_message(chat_id or expected_chat_id)

            if orch.get("is_verification_step"):
                if "VERIFIED" in (response_text or "").upper():
                    orch["is_verification_step"] = False
                else:
                    self._handle_step_runner_error(
                        "Verification failed. Expected condition not met.",
                        chat_id=chat_id or expected_chat_id,
                    )
                    return
            else:
                current_step = steps_data[idx]
                step_result = (response_text or "Step completed.").strip()

                # Check if the step wants to pause and wait for external input/event
                if "[WAIT]" in (response_text or ""):
                    self._set_step_status(current_step["id"], "waiting", result=step_result[:2000])
                    self._cancel_step_runner_timeout()
                    logger.info("Step Runner: session %d step %d is waiting (use /continue to resume)", session_id, idx + 1)
                    return

                self._set_step_status(current_step["id"], "completed", result=step_result[:2000])
                orch["any_step_succeeded"] = True
                orch["prior_results"].append(
                    {
                        "title": current_step.get("title") or f"Step {idx + 1}",
                        "result": step_result[:200],
                    }
                )
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
                        self._send_step_runner_instruction(orch, idx, prompt=verify_prompt)
                        self._reset_step_runner_timeout()
                    except Exception as e:
                        logger.error("Step Runner: failed to send verification prompt: %s", e, exc_info=True)
                        self._finish_step_runner_orchestration(success=False)
                    return

            orch["is_retry"] = False
            orch["retry_count"] = 0

            next_idx = idx + 1
            if next_idx >= len(steps_data):
                self._finish_step_runner_orchestration(success=orch.get("any_step_succeeded", True))
                return

            orch["current_index"] = next_idx
            try:
                self._set_step_status(steps_data[next_idx]["id"], "running")
                self._send_step_runner_instruction(orch, next_idx)
                self._reset_step_runner_timeout()
                logger.info("Step Runner: session %d, step %d/%d", session_id, next_idx + 1, len(steps_data))
            except Exception as e:
                logger.error("Step Runner: failed to send step %d: %s", next_idx + 1, e, exc_info=True)
                self._finish_step_runner_orchestration(success=False)
        finally:
            with _orch_lock:
                if orch:
                    orch["_advancing"] = False

    def _handle_step_runner_error(self, error: str, chat_id=None):
        """
        On chat_stream_error during orchestration: send retry prompt so agent can resolve.
        """
        with _orch_lock:
            orch = getattr(self, "_step_runner_orchestration", None)
            if not orch:
                return
            expected_chat_id = orch.get("chat_id")
            if expected_chat_id and chat_id and expected_chat_id != chat_id:
                return
            idx = orch.get("current_index", 0)
            steps_data = orch.get("steps_data", [])
            if idx >= len(steps_data):
                self._finish_step_runner_orchestration(success=False)
                return
            current_step = steps_data[idx]
            retry_count = int(orch.get("retry_count", 0))
            max_retries = int(orch.get("max_retries", 2))
        if retry_count >= max_retries:
            self._set_step_status(current_step["id"], "failed", result=(error or "Step failed.")[:2000])
            if orch.get("on_failure") == "stop":
                self._finish_step_runner_orchestration(success=False)
                return
            orch["is_verification_step"] = False
            orch["is_retry"] = False
            orch["retry_count"] = 0
            next_idx = idx + 1
            if next_idx >= len(steps_data):
                self._finish_step_runner_orchestration(success=False)
                return
            orch["current_index"] = next_idx
            self._set_step_status(steps_data[next_idx]["id"], "running")
            try:
                self._send_step_runner_instruction(orch, next_idx)
            except Exception:
                self._finish_step_runner_orchestration(success=False)
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
            self._send_step_runner_instruction(orch, idx, prompt=retry_prompt)
            self._reset_step_runner_timeout()
            logger.info("Step Runner: sent retry prompt (%d/%d)", orch["retry_count"], max_retries)
        except Exception as e:
            logger.error("Step Runner: failed to send retry prompt: %s", e, exc_info=True)
            self._finish_step_runner_orchestration(success=False)

    def _finish_step_runner_orchestration(self, success: bool = True, cancelled: bool = False):
        """Clear orchestration state and update DB (run, session status)."""
        self._cancel_step_runner_timeout()
        with _orch_lock:
            orch = getattr(self, "_step_runner_orchestration", None)
            if not orch:
                return
            self._step_runner_orchestration = None
        session_id = orch["session_id"]
        run_id = orch["run_id"]
        steps_data = orch["steps_data"]
        try:
            if run_id is not None:
                from distr.core.step_runner.scheduler import _finish_run
                _finish_run(session_id, run_id, steps_data, success=success)
                if cancelled:
                    from distr.core.db import get_session
                    from distr.core.db.step_runner import StepRunnerSession
                    with get_session() as db:
                        sess = db.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
                        if sess:
                            sess.status = "cancelled"
                        db.commit()
            else:
                from distr.core.db import get_session
                from distr.core.db.step_runner import StepRunnerSession
                with get_session() as db:
                    sess = db.query(StepRunnerSession).filter(StepRunnerSession.id == session_id).first()
                    if sess:
                        if cancelled:
                            sess.status = "cancelled"
                        else:
                            sess.status = "completed" if success else "failed"
                    db.commit()
            from distr.gui.web.step_runner_events import increment_step_runner_updated
            increment_step_runner_updated()
        except Exception as e:
            logger.error("Step Runner: finish orchestration failed: %s", e, exc_info=True)

        # Notify the Workflow Agent Bridge so the Voice Agent receives a summary.
        try:
            from distr.core.step_runner.agent_bridge import WorkflowAgentBridge
            run_result = {
                "session_id": session_id,
                "run_id": run_id,
                "success": success,
                "cancelled": cancelled,
                "steps_summary": [
                    {"title": s.get("title", ""), "id": s.get("id")}
                    for s in steps_data
                ],
            }
            WorkflowAgentBridge().on_workflow_completed(session_id, run_result)
        except Exception as e:
            logger.debug("WorkflowAgentBridge notification failed: %s", e)

        logger.info("Step Runner: completed orchestration session %d (success=%s)", session_id, success)
