"""Signal bridging mixin — connects Qt signals to the agent subprocess command queue."""

import logging
import os
import threading

from PyQt6.QtCore import QTimer

from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.signals import signal_manager

logger = logging.getLogger(__name__)


class SignalBridgeMixin:
    """Bridges Qt signals from the GUI to the agent subprocess via command queue."""

    def _bridge_signals_to_agent(self):
        """Bridge PyQt signals to agent process command queue"""
        # Disconnect existing connections to avoid duplicates
        try:
            signal_manager.push_to_talk_start.disconnect()
            signal_manager.push_to_talk_stop.disconnect()
            signal_manager.voice_set_is_listening.disconnect()
            signal_manager.hands_free_mode_changed.disconnect()
            signal_manager.playback_speed_changed.disconnect()
        except (TypeError, RuntimeError):
            pass  # Signals weren't connected, that's fine
        
        # Push-to-talk signals
        signal_manager.push_to_talk_start.connect(
            lambda: self._send_command_to_agent('push_to_talk_start', {})
        )
        signal_manager.push_to_talk_stop.connect(
            lambda: self._send_command_to_agent('push_to_talk_stop', {})
        )
        
        # Interrupt TTS signal - when agent is dead, hide player locally (command won't reach agent)
        signal_manager.interrupt_tts.connect(self._on_interrupt_tts)
        
        # Listening state signals
        signal_manager.voice_set_is_listening.connect(
            lambda enabled: self._send_command_to_agent('set_listening', {'enabled': enabled})
        )
        
        # Hands-free mode signals
        signal_manager.hands_free_mode_changed.connect(
            lambda enabled: self._send_command_to_agent('set_hands_free', {'enabled': enabled})
        )
        
        # Playback speed signals
        def on_playback_speed_changed(speed):
            logger.info(f"Received playback_speed_changed signal: {speed:.1f}x")
            self.current_playback_speed = speed
            self._send_command_to_agent('set_playback_speed', {'speed': speed})
        self._playback_speed_handler = on_playback_speed_changed
        signal_manager.playback_speed_changed.connect(on_playback_speed_changed)
        logger.info("Connected playback_speed_changed signal to agent command queue")
        
        # Speech volume signals
        def on_speech_volume_changed(volume):
            logger.info(f"Received speech_volume_changed signal: {volume}%")
            self._send_command_to_agent('set_speech_volume', {'volume': volume})
        self._speech_volume_handler = on_speech_volume_changed
        signal_manager.speech_volume_changed.connect(on_speech_volume_changed)
        logger.info("Connected speech_volume_changed signal to agent command queue")
        
        # VAD threshold signals
        def on_vad_threshold_changed(threshold):
            logger.info(f"Received vad_threshold_changed signal: {threshold}")
            self._send_command_to_agent('set_vad_threshold', {'threshold': threshold})
        self._vad_threshold_handler = on_vad_threshold_changed
        signal_manager.vad_threshold_changed.connect(on_vad_threshold_changed)
        logger.info("Connected vad_threshold_changed signal to agent command queue")
        
        # ElevenLabs voice_settings (only when provider is ElevenLabs)
        def on_elevenlabs_voice_settings_changed(stability, similarity_boost, style, use_speaker_boost):
            self._send_command_to_agent('set_elevenlabs_voice_settings', {
                'stability': stability, 'similarity_boost': similarity_boost,
                'style': style, 'use_speaker_boost': use_speaker_boost
            })
        signal_manager.elevenlabs_voice_settings_changed.connect(on_elevenlabs_voice_settings_changed)
        logger.info("Connected elevenlabs_voice_settings_changed signal to agent command queue")
        
        # Chat input signals (4th arg speak: when not None, agent sets speaker before processing - used by web)
        def on_send_text_input(text, is_telegram=False, uploaded_image_path=None, speak=None):
            params = {'text': text, 'is_telegram': is_telegram, 'uploaded_image_path': uploaded_image_path}
            if speak is not None:
                params['speak'] = bool(speak)
            self._send_command_to_agent('process_text_input', params)
        signal_manager.send_text_input.connect(on_send_text_input)
        
        # Speaker enabled state signals
        signal_manager.set_speaker_enabled.connect(
            lambda enabled: self._send_command_to_agent('set_speaker_enabled', {'enabled': enabled})
        )
        
        # Chat change signals - send to agent process so LLM service can update context; persist so web UI can show "in agent"
        def on_current_chat_changed(chat_id):
            # Agent-originated current_chat_changed is UI-notification only; do not echo back.
            if self._suppress_current_chat_relay:
                return
            try:
                settings = load_settings_from_db()
                settings['agent_current_chat_id'] = chat_id
                settings['last_chat_id'] = chat_id
                save_settings_to_db(settings)
            except Exception as e:
                logger.warning(f"Failed to persist agent_current_chat_id: {e}")
            logger.info("current_chat_changed: sending hot-swap command for chat_id=%s", chat_id)
            # Hot-swap LLM/TTS/context in the running agent — no process restart needed.
            # The _cmd_current_chat_changed handler does a single DB read and orchestrates
            # LLM swap, TTS swap, agent identity, and history load in deterministic order.
            self._send_command_to_agent('current_chat_changed', {'chat_id': chat_id})
        signal_manager.current_chat_changed.connect(on_current_chat_changed)
        
        # Model hot-reload signal - send hot_swap_llm command to running agent (no process restart)
        def on_model_hot_reload(provider, model_name, chat_id=None):
            logger.info("App received model_hot_reload: %s / %s / chat_id=%s", provider, model_name, chat_id)
            self._send_command_to_agent('hot_swap_llm', {
                'provider': provider or 'Ollama',
                'model_name': model_name or '',
                'chat_id': chat_id,
            })
        signal_manager.model_hot_reload.connect(on_model_hot_reload)
        logger.info("Connected model_hot_reload signal to hot_swap_llm command")
        
        # Speak text directly signal - send text to agent for TTS
        def on_speak_text_directly(text):
            logger.info(f"App received speak_text_directly signal: '{text}'")
            self._send_command_to_agent('speak_text_directly', {'text': text})
        signal_manager.speak_text_directly.connect(on_speak_text_directly)
        logger.info("Connected speak_text_directly signal to agent command queue")
        
        # Connect play_action_by_name signal to service (fallback if event queue not used)
        def on_play_action_by_name(action_name):
            if hasattr(self, 'action_playback_service') and self.action_playback_service:
                self.action_playback_service.play_action_by_name(action_name)
        signal_manager.play_action_by_name.connect(on_play_action_by_name)
        logger.info("Connected play_action_by_name signal to action playback service")

        # Connect play_recording_file signal to service
        def on_play_recording_file(recording_filename):
            if hasattr(self, 'action_playback_service') and self.action_playback_service:
                self.action_playback_service.play_recording_file(recording_filename)
        signal_manager.play_recording_file.connect(on_play_recording_file)
        logger.info("Connected play_recording_file signal to action playback service")

        # Notify web chat UI (WebSocket) when a chat is updated (e.g. from PTT/voice)
        def on_chat_updated_notify_web(chat_id):
            try:
                import requests
                from distr.gui.web.server import get_unified_server
                from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token
                server = get_unified_server()
                if server and server.is_running:
                    base = server.get_url()
                    requests.post(
                        f"{base}/api/internal/notify-chat-updated",
                        json={"chat_id": int(chat_id)},
                        headers={INTERNAL_AUTH_HEADER: get_internal_api_token()},
                        timeout=2,
                    )
            except Exception as e:
                logger.debug("Notify web chat_updated failed: %s", e)
        signal_manager.chat_updated.connect(on_chat_updated_notify_web)
        logger.info("Connected chat_updated signal to web WebSocket notify")

        # Real-time stream and message events for web chat (PTT/voice flow)
        self._web_stream_chat_id = None
        self._web_stream_token_buffer = []
        self._web_stream_flush_timer = None

        def _post_chat_event(payload):
            """Post chat event to web server; runs in thread to avoid blocking stream token delivery."""
            def _do_post():
                try:
                    import requests
                    from distr.gui.web.server import get_unified_server
                    from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token
                    server = get_unified_server()
                    if server and server.is_running:
                        requests.post(
                            f"{server.get_url()}/api/internal/notify-chat-event",
                            json=payload,
                            headers={INTERNAL_AUTH_HEADER: get_internal_api_token()},
                            timeout=2,
                        )
                except Exception as e:
                    logger.debug("Notify web chat event failed: %s", e)
            threading.Thread(target=_do_post, daemon=True).start()

        def _flush_stream_tokens():
            """Flush buffered stream tokens as one batched POST."""
            self._web_stream_flush_timer = None
            buf = getattr(self, "_web_stream_token_buffer", [])
            if not buf:
                return
            self._web_stream_token_buffer = []
            cid = getattr(self, "_web_stream_chat_id", None)
            if cid is not None:
                combined = "".join(buf)
                if combined:
                    _post_chat_event({"event": "stream_token", "chat_id": cid, "token": combined})

        def on_chat_message_added_web(chat_id, role, content):
            _post_chat_event({"event": "message_added", "chat_id": int(chat_id), "role": role, "content": content or ""})
        signal_manager.chat_message_added.connect(on_chat_message_added_web)

        def on_chat_stream_started_web(chat_id):
            self._web_stream_chat_id = int(chat_id)
            self._web_stream_token_buffer = []
            if self._web_stream_flush_timer is not None:
                self._web_stream_flush_timer.stop()
                self._web_stream_flush_timer = None
            _post_chat_event({"event": "stream_started", "chat_id": self._web_stream_chat_id})
        signal_manager.chat_stream_started.connect(on_chat_stream_started_web)

        def on_chat_stream_token_web(token):
            cid = getattr(self, "_web_stream_chat_id", None)
            if cid is not None and token:
                self._web_stream_token_buffer.append(token)
                if self._web_stream_flush_timer is None:
                    self._web_stream_flush_timer = QTimer()
                    self._web_stream_flush_timer.setSingleShot(True)
                    self._web_stream_flush_timer.timeout.connect(_flush_stream_tokens)
                    self._web_stream_flush_timer.start(10)
        signal_manager.chat_stream_token.connect(on_chat_stream_token_web)

        def on_chat_stream_finished_web(chat_id):
            if self._web_stream_flush_timer is not None:
                self._web_stream_flush_timer.stop()
                self._web_stream_flush_timer = None
            _flush_stream_tokens()
            self._web_stream_chat_id = None
            evt = {"event": "stream_finished", "chat_id": int(chat_id)}
            # Include response_text so the web UI can replace streamed content
            # (e.g. strip <tool_call> blocks that were partially streamed)
            resp_text = getattr(self, '_last_stream_response_text', None)
            if resp_text is not None:
                evt["response_text"] = resp_text
                self._last_stream_response_text = None
            _post_chat_event(evt)
        signal_manager.chat_stream_finished.connect(on_chat_stream_finished_web)

        def on_transcription_progress_web(chat_id, status_text, done):
            try:
                _post_chat_event({
                    "event": "transcription_progress",
                    "chat_id": int(chat_id),
                    "status": status_text or "",
                    "done": bool(done),
                })
            except Exception:
                pass
        signal_manager.transcription_progress.connect(on_transcription_progress_web)
        logger.info("Connected chat stream/message_added signals to web WebSocket notify")

        # Web routes -> main thread: hot-swap LLM in running agent, then send message.
        # No timers, no pending state, no reload locks. Commands go immediately via command_queue.

        def on_web_send_to_agent_requested(chat_id, message, speak, provider=None, model_name=None):
            """Send message to agent. load-in-agent already ran current_chat_changed; just send the text."""
            try:
                speak_bool = speak is True or (isinstance(speak, str) and speak.strip().lower() in ('true', '1'))
                # Pass speak_bool directly into process_text_input so process_chat_input applies it
                # as a per-request override — no separate set_speaker_enabled needed, and no second
                # current_chat_changed that would create a new LLM service after set_speaker_enabled ran.
                self._send_command_to_agent('process_text_input', {'text': message, 'speak': speak_bool})
                logger.info("Web send-to-agent: process_text_input for chat_id=%s (speak=%s)", chat_id, speak)
            except Exception as e:
                logger.error("Web send-to-agent slot failed: %s", e, exc_info=True)
        signal_manager.web_send_to_agent_requested.connect(on_web_send_to_agent_requested)

        def on_web_create_chat_emits_requested(
            chat_id,
            first_message,
            speak,
            provider=None,
            model_name=None,
            voice_provider=None,
            voice_model=None,
        ):
            try:
                speak_bool = speak is True or (isinstance(speak, str) and speak.strip().lower() in ('true', '1'))
                # Interrupt any in-flight TTS, then reload agent for this chat so model/voice/persona are correct.
                # Use ensure_alive=True so if agent is dead it restarts and queues the commands via pending mechanism.
                self._send_command_to_agent('interrupt_tts', {})
                self._send_command_to_agent('current_chat_changed', {'chat_id': chat_id})
                # Queue first message AFTER current_chat_changed via pending mechanism (ensure_alive=True).
                # This guarantees the message is sent once the agent is alive and has processed the chat switch,
                # rather than relying on a fixed timer that races against agent startup.
                self._send_command_to_agent('set_speaker_enabled', {'enabled': speak_bool})
                self._send_command_to_agent('process_text_input', {'text': first_message, 'speak': speak_bool})
                logger.info(
                    "Web create-chat: queued interrupt_tts + current_chat_changed + set_speaker + process_text_input for chat_id=%s (speak=%s)",
                    chat_id,
                    speak,
                )
            except Exception as e:
                logger.error("Web create-chat emits slot failed: %s", e, exc_info=True)
        signal_manager.web_create_chat_emits_requested.connect(on_web_create_chat_emits_requested)
        logger.info("Connected web_send_to_agent_requested and web_create_chat_emits_requested (direct command sends, no timers)")

        def on_web_load_chat_in_agent_requested(chat_id):
            """Hot-swap agent to a different chat without restarting the process."""
            try:
                self._send_command_to_agent('interrupt_tts', {})
                self._send_command_to_agent('current_chat_changed', {'chat_id': chat_id})
                logger.info("Web load-in-agent: queued interrupt_tts + current_chat_changed for chat_id=%s", chat_id)
            except Exception as e:
                logger.error("Web load-in-agent slot failed: %s", e, exc_info=True)
        signal_manager.web_load_chat_in_agent_requested.connect(on_web_load_chat_in_agent_requested)
        logger.info("Connected web_load_chat_in_agent_requested (direct command sends, no process restart)")
