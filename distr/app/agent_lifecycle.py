"""Agent process lifecycle mixin for the Application class.

Handles starting, stopping, reloading, health-checking, and communicating
with the agent subprocess.
"""

import gc
import logging
import os
import threading
import time

from PyQt6.QtCore import QTimer

from distr.core.signals import signal_manager
from distr.core.utils import load_settings_from_db, save_settings_to_db

# Late import to avoid circular dependency — run_agent_session is defined in app.py
# We import it at function call time instead.

logger = logging.getLogger(__name__)


class AgentLifecycleMixin:
    """Manages the agent subprocess lifecycle and command queue."""

    def check_agent_health(self):
        """Check if agent process is alive and reload if dead."""
        if self.agent_process is None:
            return

        if not self.agent_process.is_alive():
            exitcode = getattr(self.agent_process, 'exitcode', None)
            logger.warning("Agent process found dead (exitcode=%s): reloading session", exitcode)
            try:
                crash_dir = os.path.expanduser("~/.decisionsai/logs")
                crash_file = os.path.join(crash_dir, "agent_death.log")
                os.makedirs(crash_dir, exist_ok=True)
                with open(crash_file, "a") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Agent process died (exitcode={exitcode})\n")
            except Exception as e:
                logger.debug("Could not write agent crash log: %s", e)
            self.reload_agent_session(skip_welcome=True)
        else:
            logger.debug("[HEALTH CHECK] Agent process is healthy")

    def _check_agent_ready_and_send_pending(self):
        """Check if agent is ready and send any pending commands (non-blocking)."""
        if not hasattr(self, '_pending_agent_commands') or not self._pending_agent_commands:
            return

        agent_alive = hasattr(self, 'agent_process') and self.agent_process and self.agent_process.is_alive()

        if agent_alive:
            logger.info(f"Agent process is ready, sending {len(self._pending_agent_commands)} pending command(s)")
            pending = self._pending_agent_commands.copy()
            self._pending_agent_commands.clear()
            if hasattr(self, '_agent_ready_check_attempts'):
                self._agent_ready_check_attempts = 0
            for i, (command, params) in enumerate(pending):
                def send_pending(cmd=command, p=params):
                    self._send_command_to_agent(cmd, p, ensure_alive=False)
                QTimer.singleShot(1000 + (i * 200), send_pending)
        else:
            max_attempts = 20
            if not hasattr(self, '_agent_ready_check_attempts'):
                self._agent_ready_check_attempts = 0
            self._agent_ready_check_attempts += 1
            if self._agent_ready_check_attempts < max_attempts:
                QTimer.singleShot(500, self._check_agent_ready_and_send_pending)
            else:
                logger.error(f"Agent process failed to start after {max_attempts} attempts, dropping {len(self._pending_agent_commands)} pending command(s)")
                self._pending_agent_commands.clear()
                self._agent_ready_check_attempts = 0

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

            if not agent_alive and ensure_alive:
                if self._quitting:
                    logger.debug(f"Skipping agent reload during shutdown for command: {command}")
                    return
                logger.warning(f"Agent process not running for command '{command}', reloading and waiting...")
                self.reload_agent_session()
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

    def start_agent_session(self, skip_welcome=False, chat_id=None):
        """Start the agent session in a separate process."""
        from distr.app.main import run_agent_session

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
            logger.info(f"start_agent_session: effective_chat_id={effective_chat_id}")

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
                logger.info(f"Agent process started with PID: {self.agent_process.pid}")
            except (FileNotFoundError, OSError) as e:
                if "No such file or directory" in str(e) or "semaphore" in str(e).lower():
                    logger.warning(f"Suppressed multiprocessing semaphore error during agent start: {e}")
                    self.agent_process = None
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

        logger.info("🔄 reload_agent_session() called - starting agent reload")

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

            self.agent_process = None
            logger.info("Agent process cleanup completed")
            gc.collect()
