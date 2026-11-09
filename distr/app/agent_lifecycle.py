import sys
"""Agent process lifecycle mixin for the Application class.

Handles starting, stopping, reloading, health-checking, and communicating
with the agent subprocess.
"""

import gc
import logging
import os
import threading
import time

from PyQt6.QtCore import QCoreApplication, QObject, QThread, QTimer, pyqtSignal, pyqtSlot

from distr.core.signals import signal_manager
from distr.core.runtime_lifecycle import append_runtime_event
from distr.core.utils import load_settings_from_db, save_settings_to_db

# Import the headless worker at call time so spawned children never initialize Qt GUI state.

logger = logging.getLogger(__name__)


class _MainThreadInvoker(QObject):
    """Queued hop onto the Qt main thread (same signal pattern the app uses elsewhere)."""

    invoke = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.invoke.connect(self._run)

    @pyqtSlot(object)
    def _run(self, fn):
        try:
            fn()
        except Exception:
            logger.error("Main-thread agent lifecycle callback failed", exc_info=True)


_INVOKER = None
_INVOKER_LOCK = threading.Lock()


def _run_on_main_thread(fn) -> None:
    """Run ``fn`` on the Qt main thread (now if already there; queued otherwise)."""
    global _INVOKER
    app = QCoreApplication.instance()
    if app is None:
        fn()  # headless (no Qt loop): nothing to marshal onto
        return
    if QThread.currentThread() == app.thread():
        fn()
        return
    with _INVOKER_LOCK:
        if _INVOKER is None:
            _INVOKER = _MainThreadInvoker()
            _INVOKER.moveToThread(app.thread())
    _INVOKER.invoke.emit(fn)


class AgentLifecycleMixin:
    """Manages the agent subprocess lifecycle and command queue."""

    _AGENT_HEALTH_PROBE_TIMEOUT_SECONDS = 45.0
    # A starting agent (constructor still running: imports, models, tools) is not
    # probed; it gets this grace before it counts as a failed start.
    _AGENT_STARTUP_GRACE_SECONDS = 300.0
    # Automatic restarts (dead / unresponsive / failed start) share one rolling cap.
    _AGENT_MAX_AUTO_RESTARTS = 3
    _AGENT_RESTART_WINDOW_SECONDS = 600.0
    # Delay before automatic restart attempt 1, 2, 3 (first is immediate = legacy behaviour).
    _AGENT_RESTART_BACKOFF_SECONDS = (0.0, 20.0, 60.0)
    # After the cap is hit, one slow retry (cancelled by any manual reload).
    _AGENT_SLOW_RETRY_SECONDS = 1800.0
    # Commands sent while the agent is (re)starting wait for 'agent_ready' this long.
    _AGENT_PENDING_COMMAND_WAIT_SECONDS = 300.0 + 60.0 + 30.0

    # ------------------------------------------------------------------ restart policy
    @staticmethod
    def _agent_restart_log_path() -> str:
        override = (os.environ.get("DECISIONS_AGENT_RESTART_LOG") or "").strip()
        if override:
            return override
        return os.path.join(os.path.expanduser("~/.decisions/logs"), "agent_death.log")

    def _append_agent_restart_log(self, line: str) -> None:
        try:
            path = self._agent_restart_log_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        except Exception as e:
            logger.debug("Could not write agent restart log: %s", e)

    def _agent_uptime_seconds(self, now: float):
        started = getattr(self, "_agent_started_at", None)
        return round(now - started, 1) if started is not None else None

    def _schedule_agent_restart(self, delay: float, callback) -> None:
        """Run ``callback`` on the Qt main thread after ``delay`` seconds.

        reload_agent_session emits UI signals and arms QTimers, so it must never run
        on a plain worker thread. Immediate (delay 0) on the main thread stays synchronous.
        """
        if delay <= 0:
            _run_on_main_thread(callback)
            return
        if QCoreApplication.instance() is None:
            logger.warning("No Qt application; running delayed agent restart on a timer thread")
            timer = threading.Timer(delay, callback)
            timer.daemon = True
            timer.start()
            return
        _run_on_main_thread(lambda: QTimer.singleShot(int(delay * 1000), callback))

    def _bump_agent_restart_generation(self) -> int:
        self._agent_restart_generation = getattr(self, "_agent_restart_generation", 0) + 1
        return self._agent_restart_generation

    def _reset_agent_restart_budget(self) -> None:
        self._bump_agent_restart_generation()  # invalidates any scheduled restart / slow retry
        self._agent_auto_restart_times = []
        self._agent_restart_halted = False
        self._agent_auto_restart_pending = False
        self._agent_auto_restart_inflight = None
        self._agent_ready_since_restart = True
        self._agent_slow_retry_used = False

    def _on_manual_agent_reload(self) -> None:
        """A user/settings-driven reload: cancel pending auto restarts and reset the budget."""
        if getattr(self, "_agent_restart_halted", False) or getattr(self, "_agent_auto_restart_times", None) \
                or getattr(self, "_agent_auto_restart_pending", False):
            self._append_agent_restart_log("agent_restart reason=manual attempt=0 backoff_s=0 uptime_s="
                                           f"{self._agent_uptime_seconds(time.monotonic())}")
        self._reset_agent_restart_budget()
        self._agent_restart_error = None

    def _on_agent_process_start_result(self, started: bool) -> None:
        """Count an automatic restart attempt once its process start actually happened.

        A failed start is still recorded (as start_failed) so a start that always fails
        cannot loop forever; it is then detected as process_dead on the next tick.
        """
        inflight = getattr(self, "_agent_auto_restart_inflight", None)
        self._agent_start_failed = not started
        if not inflight:
            return
        self._agent_auto_restart_inflight = None
        now = time.monotonic()
        times = list(getattr(self, "_agent_auto_restart_times", None) or [])
        times.append(now)
        self._agent_auto_restart_times = times
        self._agent_ready_since_restart = False
        if not started:
            line = f"agent_restart reason=start_failed attempt={inflight.get('attempt')}/{self._AGENT_MAX_AUTO_RESTARTS}"
            logger.error("[HEALTH CHECK] %s", line)
            self._append_agent_restart_log(line)

    def _request_agent_auto_restart(self, reason: str, now: float = None, exitcode=None) -> bool:
        """Route an automatic restart through the rolling cap + backoff.

        Returns True when a restart was performed or scheduled, False when the cap
        was reached (agent left stopped, error surfaced) or one is already pending.
        """
        if now is None:
            now = time.monotonic()
        if getattr(self, "_agent_restart_halted", False) or getattr(self, "_agent_auto_restart_pending", False):
            return False
        window = self._AGENT_RESTART_WINDOW_SECONDS
        times = list(getattr(self, "_agent_auto_restart_times", None) or [])
        # Old attempts only age out of the rolling window once an agent actually reached
        # 'ready' since the last automatic restart; consecutive failed starts keep counting.
        if getattr(self, "_agent_ready_since_restart", True):
            times = [t for t in times if now - t < window]
        uptime = self._agent_uptime_seconds(now)
        exit_part = f" exitcode={exitcode}" if reason == "process_dead" else ""
        if len(times) >= self._AGENT_MAX_AUTO_RESTARTS:
            self._agent_auto_restart_times = times
            self._agent_restart_halted = True
            line = (f"agent_restart reason=cap_reached last_reason={reason}{exit_part} "
                    f"failures={len(times) + 1} max_restarts={self._AGENT_MAX_AUTO_RESTARTS} backoff_s=none uptime_s={uptime}")
            logger.error("[HEALTH CHECK] %s", line)
            self._append_agent_restart_log(line)
            append_runtime_event("agent_restart_cap_reached", source="agent_health_check",
                                 last_reason=reason, exitcode=exitcode, restarts=len(times))
            message = (
                f"The assistant agent failed {len(times) + 1} times within "
                f"{int(window // 60)} minutes (last: {reason.replace('_', ' ')}) and automatic "
                "restarts were stopped. Restart Decisions or reload the agent (e.g. change the "
                "model in settings) to try again."
            )
            self._agent_restart_error = message
            try:
                signal_manager.chat_stream_error.emit(message)
            except Exception:
                logger.debug("Could not surface agent restart cap error", exc_info=True)
            if not getattr(self, "_agent_slow_retry_used", False):
                self._schedule_agent_slow_retry()
            return False
        attempt = len(times) + 1
        backoff = self._AGENT_RESTART_BACKOFF_SECONDS[min(attempt, len(self._AGENT_RESTART_BACKOFF_SECONDS)) - 1]
        self._agent_auto_restart_times = times
        line = (f"agent_restart reason={reason}{exit_part} attempt={attempt}/{self._AGENT_MAX_AUTO_RESTARTS} "
                f"backoff_s={backoff:g} uptime_s={uptime}")
        logger.warning("[HEALTH CHECK] %s", line)
        self._append_agent_restart_log(line)
        append_runtime_event("agent_restart_scheduled", source="agent_health_check", reason=reason,
                             exitcode=exitcode, attempt=attempt, backoff_seconds=backoff, uptime_seconds=uptime)

        generation = self._bump_agent_restart_generation()

        def _do_restart():
            if getattr(self, "_agent_restart_generation", 0) != generation:
                logger.info("[HEALTH CHECK] Stale scheduled agent restart (%s) skipped", reason)
                return
            self._agent_auto_restart_pending = False
            self._bump_agent_restart_generation()
            self._agent_auto_restart_inflight = {"reason": reason, "attempt": attempt}
            self._agent_auto_restart_active = True
            try:
                self.reload_agent_session(skip_welcome=False)
            finally:
                self._agent_auto_restart_active = False

        self._agent_auto_restart_pending = True
        self._schedule_agent_restart(backoff, _do_restart)
        return True

    def _schedule_agent_slow_retry(self) -> None:
        generation = getattr(self, "_agent_restart_generation", 0)

        def _slow_retry():
            if getattr(self, "_agent_restart_generation", 0) != generation or not getattr(self, "_agent_restart_halted", False):
                return
            self._append_agent_restart_log("agent_restart reason=slow_retry attempt=1/1 backoff_s=0 uptime_s=None")
            self._agent_auto_restart_times = []
            self._agent_restart_halted = False
            self._agent_slow_retry_used = True
            self._request_agent_auto_restart("slow_retry")

        self._schedule_agent_restart(self._AGENT_SLOW_RETRY_SECONDS, _slow_retry)

    def check_agent_health(self):
        """Check if agent process is alive and reload if dead (capped, with backoff)."""
        if getattr(self, "_agent_restart_halted", False) or getattr(self, "_agent_auto_restart_pending", False):
            return
        if self.agent_process is None:
            # Only a failed start counts; a reload in progress also has no process briefly.
            if (getattr(self, "_agent_start_failed", False) and not getattr(self, "_quitting", False)
                    and not getattr(self, "_reloading_agent", False) and not getattr(self, "_agent_starting", False)):
                logger.warning("Agent process missing after a failed start: restarting")
                self._append_agent_restart_log("Agent process missing after failed start (exitcode=None)")
                self._request_agent_auto_restart("process_dead", now=time.monotonic(), exitcode=None)
            return

        now = time.monotonic()
        if not self.agent_process.is_alive():
            exitcode = getattr(self.agent_process, 'exitcode', None)
            logger.warning("Agent process found dead (exitcode=%s): reloading session", exitcode)
            append_runtime_event(
                "agent_process_dead",
                source="agent_health_check",
                exitcode=exitcode,
            )
            self._append_agent_restart_log(f"Agent process died (exitcode={exitcode})")
            # Let welcome/TTS run after recovery. skip_welcome=True left users with no audio after a
            # failed first boot (e.g. missing API key) even once the next process starts cleanly.
            self._request_agent_auto_restart("process_dead", now=now, exitcode=exitcode)
            return

        # Missing state (older objects/tests) is treated as ready: legacy probe behaviour.
        if getattr(self, "_agent_lifecycle_state", "ready") == "starting":
            started = getattr(self, "_agent_started_at", None)
            if started is not None and now - started >= self._AGENT_STARTUP_GRACE_SECONDS:
                logger.error(
                    "[HEALTH CHECK] Agent did not report ready within %.0fs of starting; treating as failed start",
                    self._AGENT_STARTUP_GRACE_SECONDS,
                )
                append_runtime_event("agent_startup_timeout", source="agent_health_check",
                                     age_seconds=round(now - started, 3))
                self._request_agent_auto_restart("startup_timeout", now=now)
            else:
                logger.debug("[HEALTH CHECK] Agent still starting; probe clock not started yet")
            return

        last_ack = getattr(self, '_last_agent_health_ack', None)
        probe_sent_at = getattr(self, '_agent_health_probe_sent_at', None)
        if (
            probe_sent_at is not None
            and now - probe_sent_at >= self._AGENT_HEALTH_PROBE_TIMEOUT_SECONDS
        ):
            logger.error(
                "[HEALTH CHECK] Agent process is alive but did not acknowledge a command probe "
                "within %.1fs; reloading session",
                self._AGENT_HEALTH_PROBE_TIMEOUT_SECONDS,
            )
            append_runtime_event(
                "agent_process_unresponsive",
                source="agent_health_check",
                probe_age_seconds=round(now - probe_sent_at, 3),
                last_ack_age_seconds=(
                    round(now - last_ack, 3) if last_ack is not None else None
                ),
            )
            self._agent_health_probe_sent_at = None
            self._request_agent_auto_restart("probe_timeout", now=now)
            return

        if probe_sent_at is None:
            self._agent_health_probe_sent_at = now
            self._send_command_to_agent(
                'agent_health_probe',
                {'probe_id': f'{time.time_ns()}'},
                ensure_alive=False,
            )
            logger.debug("[HEALTH CHECK] Sent agent command heartbeat probe")
        else:
            logger.debug(
                "[HEALTH CHECK] Agent process is healthy; awaiting heartbeat acknowledgement"
            )

    def _agent_ready_for_commands(self) -> bool:
        alive = bool(getattr(self, 'agent_process', None)) and self.agent_process.is_alive()
        # Missing state = legacy (treated as ready once alive).
        return alive and getattr(self, "_agent_lifecycle_state", "ready") == "ready"

    def _check_agent_ready_and_send_pending(self):
        """Send queued commands once the agent reports ready; give up visibly after a bound."""
        if not hasattr(self, '_pending_agent_commands') or not self._pending_agent_commands:
            self._pending_agent_commands_since = None
            return

        if self._agent_ready_for_commands():
            logger.info(f"Agent process is ready, sending {len(self._pending_agent_commands)} pending command(s)")
            pending = self._pending_agent_commands.copy()
            self._pending_agent_commands.clear()
            self._pending_agent_commands_since = None
            self._agent_ready_check_attempts = 0
            for i, (command, params) in enumerate(pending):
                def send_pending(cmd=command, p=params):
                    self._send_command_to_agent(cmd, p, ensure_alive=False)
                _run_on_main_thread(lambda d=1000 + (i * 200), f=send_pending: QTimer.singleShot(d, f))
            return

        now = time.monotonic()
        since = getattr(self, "_pending_agent_commands_since", None)
        if since is None:
            since = self._pending_agent_commands_since = now
        halted = getattr(self, "_agent_restart_halted", False)
        if not halted and now - since < self._AGENT_PENDING_COMMAND_WAIT_SECONDS:
            self._agent_ready_check_attempts = getattr(self, "_agent_ready_check_attempts", 0) + 1
            # May be called from a timer thread (no Qt loop there): arm the poll on the main thread.
            _run_on_main_thread(lambda: QTimer.singleShot(500, self._check_agent_ready_and_send_pending))
            return

        dropped = len(self._pending_agent_commands)
        names = ", ".join(sorted({str(c) for c, _ in self._pending_agent_commands}))[:200]
        logger.error("Agent not ready after %.0fs%s; dropping %d pending command(s): %s",
                     now - since, " (auto-restart halted)" if halted else "", dropped, names)
        self._pending_agent_commands.clear()
        self._pending_agent_commands_since = None
        self._agent_ready_check_attempts = 0
        try:
            signal_manager.chat_stream_error.emit(
                f"The assistant agent is not running, so {dropped} pending request(s) could not be "
                "delivered. Restart Decisions or reload the agent and try again."
            )
        except Exception:
            logger.debug("Could not surface dropped agent commands", exc_info=True)

    def _send_command_to_agent(self, command: str, params: dict, ensure_alive: bool = True):
        """Send command to agent process via command queue.

        Args:
            command: Command name to send
            params: Command parameters
            ensure_alive: If True, ensure agent is alive before sending (reload and wait if needed)
        """
        if not hasattr(self, 'agent_command_queue') or not self.agent_command_queue:
            logger.warning(f"No agent command queue available, cannot send command: {command}")
            return

        try:
            agent_alive = hasattr(self, 'agent_process') and self.agent_process and self.agent_process.is_alive()

            if (ensure_alive
                    and command in {'speak_text_directly', 'process_text_input'}
                    and getattr(self, '_agent_lifecycle_state', None) == 'starting'):
                if self._quitting:
                    return
                # A live subprocess may still be constructing its LLM/TTS
                # services. Forwarding now silently drops reads and Telegram
                # input; use the existing bounded ready queue instead.
                pending = getattr(self, '_pending_agent_commands', None)
                if pending is None:
                    pending = self._pending_agent_commands = []
                start_poll = not pending
                pending.append((command, params))
                if start_poll:
                    self._check_agent_ready_and_send_pending()
                return

            if not agent_alive and ensure_alive:
                if self._quitting:
                    logger.debug(f"Skipping agent reload during shutdown for command: {command}")
                    return
                if getattr(self, "_agent_restart_halted", False):
                    logger.warning(f"Agent auto-restart halted; not restarting for command '{command}'")
                    try:
                        signal_manager.chat_stream_error.emit(
                            getattr(self, "_agent_restart_error", None)
                            or "The assistant agent is stopped. Restart Decisions or reload the agent to try again."
                        )
                    except Exception:
                        pass
                    return
                logger.warning(f"Agent process not running for command '{command}', reloading and waiting...")
                if not getattr(self, "_agent_auto_restart_pending", False) and not getattr(self, "_reloading_agent", False):
                    self._request_agent_auto_restart(
                        "command_while_dead", exitcode=getattr(self.agent_process, 'exitcode', None))
                if getattr(self, "_agent_restart_halted", False):
                    return  # cap reached: error already surfaced
                if not hasattr(self, '_pending_agent_commands'):
                    self._pending_agent_commands = []
                self._pending_agent_commands.append((command, params))
                self._check_agent_ready_and_send_pending()
                return

            if agent_alive:
                logger.info(f"Sending command to agent: {command} with params: {params}")
                self.agent_command_queue.put((command, params), block=False)
            else:
                logger.warning(f"Agent process not running, cannot send command: {command}")
        except Exception as e:
            logger.error(f"Error sending command to agent: {e}", exc_info=True)

    def _on_interrupt_tts(self):
        """Handle interrupt_tts: hide player immediately for instant feedback, then send to agent."""
        signal_manager.emit_hide_player_window()
        agent_alive = hasattr(self, 'agent_process') and self.agent_process and self.agent_process.is_alive()
        if agent_alive:
            self._send_command_to_agent('interrupt_tts', {})
        else:
            logger.warning("interrupt_tts: agent process dead - stopping sound locally")
            signal_manager.stop_sound_player.emit()

    def _send_initial_states(self):
        """Send the initial states (listening, hands-free) to the agent session after it starts."""
        if hasattr(self, 'oracle_window') and self.oracle_window:
            hands_free_enabled = self.oracle_window.is_hands_free
            logger.info(f"Sending initial hands-free state to agent: {hands_free_enabled}")
            self._send_command_to_agent('set_hands_free', {'enabled': hands_free_enabled})
            listening_enabled = self.oracle_window.is_listening
            logger.info(f"Sending initial listening state to agent: {listening_enabled}")
            self._send_command_to_agent('set_listening', {'enabled': listening_enabled})

    def _replace_agent_queues(self):
        """Create fresh IPC queues so a killed worker cannot poison the next session."""
        old_queues = (
            getattr(self, "agent_command_queue", None),
            getattr(self, "agent_event_queue", None),
        )
        self.agent_command_queue = self.mp_context.Queue()
        self.agent_event_queue = self.mp_context.Queue()

        try:
            from distr.core.signals import set_agent_event_queue

            set_agent_event_queue(self.agent_event_queue)
        except Exception as exc:
            logger.warning("Could not register new agent event queue: %s", exc)
        if hasattr(self, "initiative_service") and self.initiative_service:
            self.initiative_service.event_queue = self.agent_event_queue

        for queue in old_queues:
            if queue is None:
                continue
            try:
                queue.cancel_join_thread()
            except Exception:
                pass
            try:
                queue.close()
            except Exception as exc:
                logger.debug("Could not close old agent IPC queue: %s", exc)

        logger.info("Recreated agent IPC queues for restart")

    def _prepare_agent_ipc_for_start(self):
        """Keep the initial queues, then replace them on every agent restart."""
        generation = getattr(self, "_agent_generation", 0)
        if generation:
            self._replace_agent_queues()
        self._agent_generation = generation + 1

    def start_agent_session(self, skip_welcome=False, chat_id=None):
        """Start the agent session in a separate process."""
        if getattr(sys, "frozen", False):
            from distr.core.packaged_stt import selected_model
            if not selected_model():
                logger.warning("Voice is disabled until you choose and download a speech engine from the Speech engine menu.")
                return
        from distr.app.agent_worker import run_agent_session

        try:
            if self._quitting:
                logger.debug("Skipping agent start during shutdown")
                return

            # Prevent concurrent starts — only one process should be spawning at a time.
            with self._reload_lock:
                if hasattr(self, '_agent_starting') and self._agent_starting:
                    logger.warning("⚠️  Agent start already in progress, skipping duplicate start")
                    return
                self._agent_starting = True

            if hasattr(self, 'agent_process') and self.agent_process and self.agent_process.is_alive():
                logger.info("Terminating existing agent process")
                self.agent_process.terminate()
                self.agent_process.join(timeout=1.0)

            logger.info("Reloading settings from database before starting agent session")
            self.settings = load_settings_from_db()

            effective_chat_id = chat_id
            if not effective_chat_id:
                effective_chat_id = getattr(self, '_pending_chat_id_for_agent', None)
            # Fall back to the last known chat from DB so the agent starts with
            # the correct per-chat voice/model instead of global defaults.
            if not effective_chat_id:
                effective_chat_id = (
                    self.settings.get('agent_current_chat_id')
                    or self.settings.get('last_chat_id')
                )
            if effective_chat_id:
                from distr.core.db import get_session
                from distr.core.workflow.development_threads import resolve_conversational_chat_id

                with get_session() as db:
                    effective_chat_id = resolve_conversational_chat_id(
                        db,
                        effective_chat_id,
                    )
            logger.info(f"start_agent_session: effective_chat_id={effective_chat_id}")

            self._prepare_agent_ipc_for_start()
            self.agent_process = self.mp_context.Process(
                target=run_agent_session,
                args=(self.settings, self.selected_input_device, self.selected_output_device,
                      self.agent_command_queue, self.agent_event_queue,
                      self.confirmation_results_dict, skip_welcome,
                      self.screen_info_cache, effective_chat_id),
                daemon=False,
            )

            try:
                self.agent_process.start()
                # Probe state resets per process; the auto-restart budget deliberately does NOT.
                self._last_agent_health_ack = None
                self._agent_health_probe_sent_at = None
                self._agent_lifecycle_state = 'starting'
                self._agent_started_at = time.monotonic()
                self._on_agent_process_start_result(True)
                logger.info(f"Agent process started with PID: {self.agent_process.pid}")
                # Track this PID so it gets killed on restart/crash
                try:
                    from distr.core.process_tracker import register_child_pid
                    register_child_pid(self.agent_process.pid)
                except Exception:
                    pass
            except (FileNotFoundError, OSError) as e:
                if "No such file or directory" in str(e) or "semaphore" in str(e).lower():
                    logger.warning(f"Suppressed multiprocessing semaphore error during agent start: {e}")
                    self.agent_process = None
                    self._on_agent_process_start_result(False)
                else:
                    raise

            with self._reload_lock:
                self._reloading_agent = False
            if hasattr(self, '_agent_ready_check_attempts'):
                self._agent_ready_check_attempts = 0

            init_state_timer = threading.Timer(1.0, self._send_initial_states)
            init_state_timer.daemon = True
            init_state_timer.start()

            if hasattr(self, '_pending_agent_commands') and self._pending_agent_commands:
                pending_timer = threading.Timer(2.0, self._check_agent_ready_and_send_pending)
                pending_timer.daemon = True
                pending_timer.start()
        except Exception as e:
            logger.error(f"Error starting agent session process: {e}")
            if getattr(self, "_agent_auto_restart_inflight", None) or self.agent_process is None \
                    or not getattr(self.agent_process, "pid", None):
                self.agent_process = None
                self._on_agent_process_start_result(False)
            with self._reload_lock:
                self._reloading_agent = False
        finally:
            with self._reload_lock:
                self._agent_starting = False

    def reload_agent_session(self, skip_welcome=False, new_chat_id=None):
        """Reload the agent session with updated settings."""
        if self._quitting:
            logger.debug("Skipping agent reload during shutdown")
            return

        with self._reload_lock:
            if self._reloading_agent:
                logger.warning("⚠️  Agent reload already in progress, skipping duplicate reload request")
                return
            self._reloading_agent = True

        if not getattr(self, "_agent_auto_restart_active", False):
            # Manual / settings-driven reload: user retry resets the automatic restart budget
            # and cancels any scheduled (backoff / slow-retry) restart via the generation.
            self._on_manual_agent_reload()
        logger.info("🔄 reload_agent_session() called - starting agent reload")
        append_runtime_event(
            "agent_reload_requested",
            source="agent_lifecycle.reload_agent_session",
            skip_welcome=bool(skip_welcome),
            new_chat_id=new_chat_id,
        )

        self._pending_skip_welcome_for_agent = bool(skip_welcome)
        if new_chat_id:
            logger.info(f"🔄 reload_agent_session: will pass new_chat_id={new_chat_id}")
            self._pending_chat_id_for_agent = new_chat_id
        else:
            if hasattr(self, 'chat_manager') and self.chat_manager:
                current_chat_id = self.chat_manager.get_current_chat()
                if current_chat_id:
                    self._pending_chat_id_for_agent = current_chat_id
            if not hasattr(self, '_pending_chat_id_for_agent') or not self._pending_chat_id_for_agent:
                self._pending_chat_id_for_agent = None

        try:
            signal_manager.agent_reload_started.emit()
        except Exception as e:
            logger.warning("Failed to emit agent_reload_started: %s", e)

        try:
            signal_manager.emit_hide_player_window()
        except Exception as e:
            logger.warning(f"Could not hide player window during reload: {e}")

        try:
            logger.info("🔄 Reloading agent session with updated settings")
            self.settings = load_settings_from_db()

            # Update ChatManager with new model from the target chat
            if hasattr(self, 'chat_manager') and self.chat_manager:
                target_chat_id = new_chat_id or self.chat_manager.get_current_chat()
                new_model = None
                new_provider = None
                if target_chat_id:
                    try:
                        from distr.core.db import get_session, Chat
                        with get_session() as session:
                            chat = session.get(Chat, target_chat_id)
                            if chat:
                                new_model = chat.model_name
                                new_provider = chat.provider
                    except Exception as e:
                        logger.warning(f"Could not get model from chat: {e}")

                if not new_model:
                    new_model = self.settings.get('conversational_llm_model', '') or self.settings.get('agent_model', '')
                if not new_provider:
                    new_provider = self.settings.get('conversational_llm_provider', '') or self.settings.get('agent_provider', '')

                if new_model or new_provider:
                    if new_model:
                        self.settings['agent_model'] = new_model
                        self.settings['llm_model'] = new_model
                    if new_provider:
                        self.settings['agent_provider'] = new_provider
                        self.settings['llm_provider'] = new_provider
                    save_settings_to_db(self.settings)

                if new_model:
                    self.chat_manager.update_model(new_model)
                if new_provider:
                    self.chat_manager.update_provider(new_provider)

            # Stop existing agent
            if hasattr(self, 'agent_process') and self.agent_process and self.agent_process.is_alive():
                logger.info("Stopping existing agent process for reload")
                self._cleanup_agent_process()

            def start_after_delay():
                self.settings = load_settings_from_db()
                skip = getattr(self, "_pending_skip_welcome_for_agent", False)
                cid = getattr(self, "_pending_chat_id_for_agent", None)
                self.start_agent_session(skip_welcome=skip, chat_id=cid)
                if hasattr(self, "_pending_skip_welcome_for_agent"):
                    self._pending_skip_welcome_for_agent = False
                # NOTE: _reloading_agent is cleared inside start_agent_session
                # so we do NOT clear it here to avoid a double-clear race.

            restart_timer = threading.Timer(1.0, start_after_delay)
            restart_timer.daemon = True
            restart_timer.start()

            # Reconnect Telegram WebSocket if connected
            try:
                settings = load_settings_from_db()
                connected_accounts = settings.get('connected_accounts', [])
                if connected_accounts:
                    for account in connected_accounts:
                        if isinstance(account, dict) and account.get('provider') == 'telegram':
                            app_user_id = account.get('app_user_id')
                            telegram_user_id = account.get('user_id')
                            if app_user_id or telegram_user_id:
                                self.telegram_manager.connect(
                                    short_code=None,
                                    app_user_id=app_user_id,
                                    telegram_user_id=telegram_user_id,
                                )
                                break
            except Exception as e:
                logger.error(f"Error reconnecting Telegram WebSocket: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"Error during agent reload: {e}", exc_info=True)
            with self._reload_lock:
                self._reloading_agent = False

    def _signal_agent_shutdown_no_wait(self):
        """Ask the agent to stop without blocking the UI thread."""
        if not (
            hasattr(self, "agent_process")
            and self.agent_process
            and self.agent_process.is_alive()
        ):
            return
        try:
            if hasattr(self, "agent_command_queue") and self.agent_command_queue is not None:
                try:
                    self.agent_command_queue.put(("shutdown", {}), block=False)
                except Exception:
                    pass
            import signal as sig

            os.kill(self.agent_process.pid, sig.SIGTERM)
        except Exception as e:
            logger.debug("Non-blocking agent shutdown signal failed: %s", e)

    def _cleanup_agent_process(self):
        """Clean up the agent process with proper signal handling."""
        if hasattr(self, 'agent_process') and self.agent_process and self.agent_process.is_alive():
            logger.info("Stopping agent process")
            try:
                import signal as sig
                if hasattr(self, 'agent_command_queue') and self.agent_command_queue is not None:
                    try:
                        self.agent_command_queue.put(('shutdown', {}), block=False)
                    except Exception:
                        pass
                time.sleep(1.5)
                if self.agent_process.is_alive():
                    os.kill(self.agent_process.pid, sig.SIGTERM)
                    self.agent_process.join(timeout=8.0)

                if self.agent_process.is_alive():
                    logger.warning("Agent process didn't terminate gracefully, forcing termination")
                    self.agent_process.terminate()
                    self.agent_process.join(timeout=2.0)

                    if self.agent_process.is_alive():
                        logger.warning("Agent process still not terminated, using SIGKILL")
                        if hasattr(sig, 'SIGKILL'):
                            os.kill(self.agent_process.pid, sig.SIGKILL)
                        else:
                            self.agent_process.kill()
                        self.agent_process.join(timeout=1.0)
            except Exception as e:
                logger.error(f"Error stopping agent process: {e}")

            if hasattr(self.agent_process, 'close'):
                try:
                    self.agent_process.close()
                except Exception:
                    pass

            # Unregister from process tracker now it's confirmed dead
            try:
                from distr.core.process_tracker import unregister_child_pid
                if hasattr(self, 'agent_process') and self.agent_process:
                    unregister_child_pid(self.agent_process.pid)
            except Exception:
                pass

            self.agent_process = None
            logger.info("Agent process cleanup completed")
            gc.collect()
