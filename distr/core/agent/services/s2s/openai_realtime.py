"""OpenAI Realtime speech-to-speech bridge for agent conversation.

When the chat model is an OpenAI Realtime S2S model, agent PTT/hands-free mic
audio is sent to the conversation Realtime API and response audio is played
through the existing transport output. Dictation still passes through to STT.

Always sits in the pipeline; when disabled it is a pure passthrough.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Optional

import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    FrameProcessor,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
    EndFrame,
    CancelFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    SpeakingStartedFrames,
    SpeakingStoppedFrames,
)
from distr.core.openai_s2s import coerce_realtime_voice, is_openai_s2s_model

logger = logging.getLogger(__name__)

_EXPLICIT_WEB_LOOKUP_RE = re.compile(
    r"(?:^|[.!?]\s*)(?:please\s+)?(?:can|could|would|will)\s+you\s+"
    r"(?:please\s+)?(?:search|look\s+up|research|find\s+online)\b|"
    r"^\s*(?:please\s+)?(?:search(?:\s+(?:the\s+)?web|\s+online|\s+for)?|"
    r"look\s+up|research|find\s+online)\b|"
    r"\bwhat(?:'s|\s+is)\s+the\s+(?:latest|current)\b",
    re.IGNORECASE,
)

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None
    WEBSOCKETS_AVAILABLE = False


def _resample_pcm16_16k_to_24k(audio_bytes: bytes) -> bytes:
    samples = np.frombuffer(audio_bytes, dtype=np.int16)
    if samples.size < 2:
        return audio_bytes
    target_size = (samples.size * 3) // 2
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.arange(target_size, dtype=np.float64) * (2.0 / 3.0)
    resampled = np.interp(target_positions, source_positions, samples)
    return np.clip(np.rint(resampled), -32768, 32767).astype(np.int16).tobytes()


class OpenAIRealtimeS2SBridge(FrameProcessor):
    """Intercepts agent-talk audio for Realtime S2S; passes dictation through."""

    _TRANSCRIPTION_WAIT_SECONDS = 2.5

    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-realtime-2.1",
        voice: str = "marin",
        instructions: str = "",
        transcription_model: str = "gpt-4o-mini-transcribe",
        enabled: bool = False,
        event_queue=None,
        chat_manager=None,
        llm_service=None,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for OpenAIRealtimeS2SBridge")
        if not hasattr(self, "_FrameProcessor__process_queue"):
            self._FrameProcessor__process_queue = None
        super().__init__(**kwargs)

        self.api_key = (api_key or "").strip()
        self.model = model if is_openai_s2s_model(model) else "gpt-realtime-2.1"
        self.voice = coerce_realtime_voice(voice)
        self.instructions = (instructions or "").strip() or (
            "You are a helpful voice assistant. Speak clearly and briefly."
        )
        supported_transcription_models = {
            "gpt-4o-mini-transcribe",
            "gpt-4o-transcribe",
            "whisper-1",
        }
        requested_transcription_model = (transcription_model or "").strip()
        self.transcription_model = (
            requested_transcription_model
            if requested_transcription_model in supported_transcription_models
            else "gpt-4o-mini-transcribe"
        )
        self.event_queue = event_queue
        self.chat_manager = chat_manager
        self.llm_service = llm_service
        self._enabled = bool(enabled)

        self._ws = None
        self._connected = False
        self._listener_task = None
        self._lock = asyncio.Lock()
        self._ptt_active = False
        self._is_hands_free = False
        self._is_dictating = False
        self._user_speaking = False
        self._tts_started = False
        self._response_active = False
        self._response_waiting_for_transcript = False
        self._response_timeout_task = None
        self._needs_input_reset = False
        self._assistant_transcript = ""
        self._assistant_stream_started = False
        self._assistant_transcript_saved = False
        self._seen_input_transcript_items: set[str] = set()
        self._completed_tool_call_ids: set[str] = set()
        self._buffered_audio_bytes = 0
        self._audio_out = None  # transport.output() — play Realtime audio here
        self._pending_out: asyncio.Queue = asyncio.Queue()
        self._event_loop = None
        self._realtime_url = "wss://api.openai.com/v1/realtime"

        logger.info(
            "OpenAIRealtimeS2SBridge ready enabled=%s model=%s voice=%s",
            self._enabled,
            self.model,
            self.voice,
        )

    def set_audio_out(self, audio_out):
        """Wire transport.output() so S2S audio bypasses chained TTS."""
        self._audio_out = audio_out

    def set_s2s_enabled(
        self,
        enabled: bool,
        *,
        model: Optional[str] = None,
        voice: Optional[str] = None,
        api_key: Optional[str] = None,
        instructions: Optional[str] = None,
    ):
        was = self._enabled
        previous = (self.model, self.voice, self.api_key, self.instructions)
        self._enabled = bool(enabled)
        if model and is_openai_s2s_model(model):
            self.model = model
        if voice is not None:
            self.voice = coerce_realtime_voice(voice)
        if api_key is not None:
            self.api_key = (api_key or "").strip()
        if instructions is not None:
            self.instructions = instructions.strip() or (
                "You are a helpful voice assistant. Speak clearly and briefly."
            )
        changed = previous != (self.model, self.voice, self.api_key, self.instructions)
        if was and not self._enabled:
            self._schedule_coro(self.disconnect())
        elif self._enabled and not was:
            self._schedule_coro(self.connect())
        elif self._enabled and was and changed and self._connected:
            # Model and credentials are bound to the websocket URL/headers.
            # Reconnecting also makes voice and instruction changes atomic, so
            # the UI can never advertise a voice different from the live one.
            self._schedule_coro(self._reconnect())

    # --- mode flags (mirrored from STT/TTS command wiring) ---

    def set_ptt_active(self, active: bool, queue_interruption: bool = True):
        was = self._ptt_active
        self._ptt_active = bool(active)
        if not self._enabled:
            return
        if self._ptt_active and not was:
            self._buffered_audio_bytes = 0
            self._queue_transcription_progress("", done=False)
            if self._response_active or self._tts_started:
                # Do not clear the remote input buffer from a detached task.
                # Microphone frames can arrive before that task runs, which
                # would erase the beginning of the new turn or mix the prior
                # assistant playback into its transcript. The first append
                # performs cancel -> clear -> append in strict order instead.
                self._needs_input_reset = True
                self._schedule_coro(self._stop_output())
        if was and not self._ptt_active and not self._is_dictating:
            self._schedule_coro(self._commit_and_respond())

    def _schedule_coro(self, coro):
        """Run coroutine on the pipeline loop (PTT commands arrive from another thread)."""
        loop = self._event_loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            running.create_task(coro)
            return
        if (
            loop is not None
            and isinstance(loop, asyncio.AbstractEventLoop)
            and loop.is_running()
        ):
            try:
                asyncio.run_coroutine_threadsafe(coro, loop)
                return
            except Exception as exc:
                logger.debug("S2S: schedule on event_loop failed: %s", exc)
        try:
            coro.close()
        except Exception:
            pass
        logger.debug("S2S: no event loop to schedule commit/respond")

    def set_hands_free(self, enabled: bool):
        enabled = bool(enabled)
        changed = enabled != self._is_hands_free
        self._is_hands_free = enabled
        if changed and self._connected:
            self._schedule_coro(self._send_session_update())

    def set_dictating(self, enabled: bool):
        self._is_dictating = bool(enabled)

    def _agent_capture_active(self) -> bool:
        if not self._enabled or self._is_dictating:
            return False
        if self._ptt_active:
            return True
        # In hands-free S2S mode, Realtime semantic VAD must receive the
        # complete filtered microphone stream. Using local Silero's
        # SpeakingStartedFrame as an admission gate makes a local false
        # positive the authority that interrupts the assistant, while also
        # hiding the audio Realtime needs to make its own semantic decision.
        if self._is_hands_free:
            return True
        return False

    # --- websocket ---

    def _session_update_event(self) -> dict:
        turn = (
            {
                "type": "semantic_vad",
                "create_response": False,
                "interrupt_response": True,
            }
            if self._is_hands_free
            else None
        )
        session = {
            "type": "realtime",
            "model": self.model,
            "output_modalities": ["audio"],
            "instructions": self.instructions,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {"model": self.transcription_model},
                    "turn_detection": turn,
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": self.voice,
                },
            },
        }
        # Tools are selected after the finalized input transcript. Exposing the
        # entire catalog here makes harmless conversation eligible for unrelated
        # actions and gives semantic retrieval no utterance to work with.
        session["tools"] = []
        session["tool_choice"] = "none"
        return {
            "type": "session.update",
            "session": session,
        }

    @staticmethod
    def _realtime_tool_names_for_message(user_message: str) -> set[str]:
        """Resolve only explicit spoken tool intent.

        Typed chat deliberately gives ambiguous prompts access to tools. That
        default is too permissive for speech because transcription noise and
        casual conversation can otherwise start unrelated actions. Realtime
        therefore requires a deterministic action match before exposing any
        function schema to the model.
        """
        text = (user_message or "").strip()
        if not text:
            return set()
        forced_names: set[str] = set()
        try:
            from distr.core.agent.tool_intents import forced_tool_names_for_text

            forced_names.update(forced_tool_names_for_text(text))
        except Exception:
            logger.debug("S2S: deterministic tool-intent lookup failed", exc_info=True)
        fast_name = ""
        try:
            from distr.core.agent.services.llm.fast_action_detector import (
                ActionType,
                get_detector,
            )

            action = get_detector().detect(text)
            if action.action_type not in {ActionType.CONVERSATIONAL, ActionType.UNKNOWN}:
                if action.tool_name:
                    fast_name = str(action.tool_name)
        except Exception:
            logger.debug("S2S: fast action lookup failed", exc_info=True)

        # One deterministic hint is more trustworthy than a conflicting broad
        # fast-action match. When several overlapping hints exist, a concrete
        # fast action identifies the single executor the spoken command needs.
        if len(forced_names) == 1:
            names = set(forced_names)
        elif fast_name:
            names = {fast_name}
        else:
            names = set(forced_names)
        if _EXPLICIT_WEB_LOOKUP_RE.search(text):
            names.add("web_search")
        return names

    def _realtime_tool_schemas(self, user_message: str) -> list[dict]:
        """Flatten explicitly requested agent tools for Realtime session.update."""
        service = self.llm_service
        if service is None:
            return []
        allowed_names = self._realtime_tool_names_for_message(user_message)
        if not allowed_names:
            return []
        try:
            if hasattr(service, "_tools_list_for_message"):
                normal = service._tools_list_for_message(user_message) or []
            else:
                from distr.core.agent.services.llm.tool_format import (
                    convert_tools_to_openai_format,
                )

                normal = convert_tools_to_openai_format(
                    list(getattr(service, "_tools", []) or [])
                )
        except Exception:
            logger.warning("S2S: could not build Realtime tool schemas", exc_info=True)
            return []
        realtime = []
        for item in normal:
            fn = item.get("function") if isinstance(item, dict) else None
            if (
                not isinstance(fn, dict)
                or not fn.get("name")
                or fn["name"] not in allowed_names
            ):
                continue
            realtime.append(
                {
                    "type": "function",
                    "name": fn["name"],
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return realtime

    async def _send_session_update(self):
        ws = self._ws
        if not self._connected or ws is None:
            return
        try:
            await ws.send(json.dumps(self._session_update_event()))
        except Exception as exc:
            logger.warning("S2S session update failed: %s", exc)
            self._connected = False

    async def _reconnect(self):
        await self.disconnect()
        if self._enabled:
            await self.connect()

    async def connect(self) -> bool:
        if not self._enabled:
            return False
        if not WEBSOCKETS_AVAILABLE:
            logger.warning("websockets not installed — S2S Realtime unavailable")
            return False
        if not self.api_key:
            logger.error("OpenAI API key missing — cannot start S2S Realtime")
            return False
        async with self._lock:
            if self._connected:
                return True
            try:
                url = f"{self._realtime_url}?model={self.model}"
                headers = {"Authorization": f"Bearer {self.api_key}"}
                logger.info("Connecting OpenAI Realtime S2S: %s", url)
                self._ws = await websockets.connect(
                    url, additional_headers=headers, max_size=None
                )
                first = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10.0))
                if first.get("type") == "error":
                    raise RuntimeError(first.get("error", {}))
                if first.get("type") != "session.created":
                    raise RuntimeError(f"Unexpected first event: {first.get('type')}")
                await self._ws.send(json.dumps(self._session_update_event()))
                self._connected = True
                self._listener_task = asyncio.create_task(self._listen())
                logger.info("OpenAI Realtime S2S session ready")
                return True
            except Exception as exc:
                logger.error("Failed to connect Realtime S2S: %s", exc)
                self._connected = False
                self._ws = None
                return False

    async def disconnect(self):
        async with self._lock:
            if self._listener_task:
                self._listener_task.cancel()
                try:
                    await self._listener_task
                except asyncio.CancelledError:
                    pass
                self._listener_task = None
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
            self._connected = False
            self._response_active = False
            self._response_waiting_for_transcript = False
            self._cancel_response_timeout()
            self._needs_input_reset = False
            self._buffered_audio_bytes = 0
            await self._stop_output()

    async def _stop_output(self):
        """Close the player immediately when a response terminates or is cut off."""
        if not self._tts_started:
            return
        self._tts_started = False
        if TTSStoppedFrame is not None:
            await self._emit_to_output(TTSStoppedFrame())
        if LLMFullResponseEndFrame is not None:
            await self._emit_to_output(LLMFullResponseEndFrame())

    def _current_chat_id(self) -> Optional[int]:
        manager = self.chat_manager
        if manager is None:
            return None
        try:
            chat_id = manager.get_current_chat()
            return int(chat_id) if chat_id else None
        except Exception:
            logger.debug("S2S: could not resolve current chat", exc_info=True)
            return None

    def _queue_chat_event(self, event: str, data: dict) -> None:
        if self.event_queue is None:
            return
        try:
            self.event_queue.put((event, data), block=False)
        except Exception:
            logger.debug("S2S: could not emit %s", event, exc_info=True)

    def _queue_transcription_progress(
        self,
        text: str,
        *,
        done: bool,
        clear_live_preview: bool = False,
        discard_live_preview: bool = False,
    ) -> None:
        chat_id = self._current_chat_id()
        if not chat_id:
            return
        self._queue_chat_event(
            "transcription_progress",
            {
                "chat_id": chat_id,
                "status_text": text or "",
                "done": bool(done),
                "clear_live_preview": bool(clear_live_preview),
                "discard_live_preview": bool(discard_live_preview),
            },
        )

    def _persist_user_transcript(self, transcript: str, item_id: str = "") -> None:
        text = (transcript or "").strip()
        chat_id = self._current_chat_id()
        if not text or not chat_id:
            return
        dedup_id = item_id or f"text:{text}"
        if dedup_id in self._seen_input_transcript_items:
            return
        self._seen_input_transcript_items.add(dedup_id)
        if len(self._seen_input_transcript_items) > 100:
            self._seen_input_transcript_items = set(
                list(self._seen_input_transcript_items)[-50:]
            )
        try:
            self.chat_manager.add_user_message(chat_id, text)
        except Exception:
            logger.warning("S2S: could not persist user transcript", exc_info=True)
            return
        service_messages = getattr(self.llm_service, "_messages", None)
        if isinstance(service_messages, list):
            if not service_messages or not (
                service_messages[-1].get("role") == "user"
                and str(service_messages[-1].get("content") or "").strip() == text
            ):
                service_messages.append({"role": "user", "content": text})
        # Finalize the live PTT preview before broadcasting the durable row.
        # The web client promotes the preview and then reconciles message_added
        # by identity/text, so the spoken turn is visible exactly once.
        self._queue_transcription_progress(text, done=True)
        self._queue_chat_event(
            "chat_message_added",
            {"chat_id": chat_id, "role": "user", "content": text},
        )

    def _start_assistant_stream(self) -> Optional[int]:
        chat_id = self._current_chat_id()
        if chat_id and not self._assistant_stream_started:
            self._assistant_stream_started = True
            self._queue_chat_event("chat_stream_started", {"chat_id": chat_id})
        return chat_id

    def _append_assistant_transcript(self, delta: str) -> None:
        if not delta:
            return
        chat_id = self._start_assistant_stream()
        if not chat_id:
            return
        self._assistant_transcript += delta
        self._queue_chat_event("chat_stream_token", {"chat_id": chat_id, "token": delta})

    def _response_transcript(self, event: dict) -> str:
        response = event.get("response") or {}
        parts = []
        for output in response.get("output") or []:
            for content in output.get("content") or []:
                value = content.get("transcript") or content.get("text") or ""
                if value:
                    parts.append(str(value))
        return "".join(parts).strip()

    def _finish_assistant_transcript(self, transcript: str = "") -> None:
        if self._assistant_transcript_saved:
            return
        text = (transcript or self._assistant_transcript or "").strip()
        chat_id = self._current_chat_id()
        if not text or not chat_id:
            return
        if not self._assistant_stream_started:
            self._start_assistant_stream()
        try:
            self.chat_manager.add_assistant_message(chat_id, text)
        except Exception:
            logger.warning("S2S: could not persist assistant transcript", exc_info=True)
            return
        self._assistant_transcript_saved = True
        service_messages = getattr(self.llm_service, "_messages", None)
        if isinstance(service_messages, list):
            service_messages.append({"role": "assistant", "content": text})
        self._queue_chat_event(
            "chat_stream_finished",
            {"chat_id": chat_id, "response_text": text},
        )

    async def _handle_realtime_event(self, event: dict):
        """Apply one Realtime event directly to response and player state."""
        et = event.get("type") or ""
        if et == "response.created":
            self._response_active = True
            self._response_waiting_for_transcript = False
            self._cancel_response_timeout()
            self._assistant_transcript = ""
            self._assistant_stream_started = False
            self._assistant_transcript_saved = False
            return
        if et in ("input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped"):
            # These are the server's semantic turn markers. Keep the local
            # speaking hint coherent for UI/diagnostics, but do not use it to
            # gate microphone frames in hands-free mode.
            self._user_speaking = et.endswith("speech_started")
            return
        if et == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript") or ""
            self._persist_user_transcript(
                transcript, str(event.get("item_id") or "")
            )
            await self._begin_response_for_transcript(transcript)
            return
        if et == "conversation.item.input_audio_transcription.failed":
            logger.warning("Realtime S2S input transcription failed: %s", event.get("error"))
            self._queue_transcription_progress("", done=True, discard_live_preview=True)
            await self._begin_response_for_transcript("")
            return
        if et in (
            "response.output_audio_transcript.delta",
            "response.audio_transcript.delta",
            "response.output_text.delta",
        ):
            self._append_assistant_transcript(event.get("delta") or "")
            return
        if et in (
            "response.output_audio_transcript.done",
            "response.audio_transcript.done",
            "response.output_text.done",
        ):
            self._finish_assistant_transcript(
                event.get("transcript") or event.get("text") or ""
            )
            return
        if et in ("response.output_audio.delta", "response.audio.delta"):
            b64 = event.get("delta") or ""
            if not b64:
                return
            try:
                pcm = base64.b64decode(b64)
            except Exception:
                return
            if not self._tts_started:
                self._tts_started = True
                if LLMFullResponseStartFrame is not None:
                    await self._emit_to_output(LLMFullResponseStartFrame())
                if TTSStartedFrame is not None:
                    await self._emit_to_output(TTSStartedFrame())
            if pcm and OutputAudioRawFrame is not None:
                await self._emit_to_output(
                    OutputAudioRawFrame(audio=pcm, sample_rate=24000, num_channels=1)
                )
            return
        if et in (
            "response.output_audio.done",
            "response.audio.done",
            "response.cancelled",
            "response.canceled",
        ):
            await self._stop_output()
            if et in ("response.cancelled", "response.canceled"):
                self._response_active = False
                self._response_waiting_for_transcript = False
                self._cancel_response_timeout()
            return
        if et == "response.done":
            self._response_active = False
            self._response_waiting_for_transcript = False
            self._cancel_response_timeout()
            response = event.get("response") or {}
            if self._response_function_calls(response):
                await self._stop_output()
                await self._execute_realtime_tool_calls(response)
                return
            self._finish_assistant_transcript(self._response_transcript(event))
            await self._stop_output()
            return
        if et == "error":
            logger.error("Realtime S2S error: %s", event.get("error"))
            self._response_active = False
            self._response_waiting_for_transcript = False
            self._cancel_response_timeout()
            await self._stop_output()

    def _response_function_calls(self, response: dict) -> list[dict]:
        calls = []
        for item in response.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            if not call_id or not name or call_id in self._completed_tool_call_ids:
                continue
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": item.get("arguments") or "{}",
                    },
                }
            )
        return calls

    async def _execute_realtime_tool_calls(self, response: dict) -> bool:
        calls = self._response_function_calls(response)
        if not calls:
            return False
        ws = self._ws
        service = self.llm_service
        if not self._connected or ws is None:
            return True
        if service is None or not hasattr(service, "_execute_tool_calls"):
            results = [
                {
                    "tool_call_id": call["id"],
                    "content": "Tool execution is unavailable in this session.",
                }
                for call in calls
            ]
        else:
            allowed_calls = []
            blocked_results = []
            last_user_message = ""
            for message in reversed(getattr(service, "_messages", []) or []):
                if message.get("role") == "user":
                    last_user_message = str(message.get("content") or "")
                    break
            for call in calls:
                block_reason = ""
                if hasattr(service, "_tool_intent_block_reason"):
                    try:
                        block_reason = service._tool_intent_block_reason(
                            call["function"]["name"], last_user_message
                        )
                    except Exception:
                        logger.debug("S2S tool intent guard failed", exc_info=True)
                if block_reason:
                    blocked_results.append(
                        {
                            "tool_call_id": call["id"],
                            "content": block_reason,
                        }
                    )
                else:
                    allowed_calls.append(call)
            try:
                executed_results = (
                    await service._execute_tool_calls(allowed_calls)
                    if allowed_calls
                    else []
                )
                results = blocked_results + list(executed_results or [])
            except Exception as exc:
                logger.error("S2S tool execution failed: %s", exc, exc_info=True)
                results = [
                    {
                        "tool_call_id": call["id"],
                        "content": f"Tool execution failed: {exc}",
                    }
                    for call in calls
                ]
        by_id = {str(result.get("tool_call_id") or ""): result for result in results or []}
        for call in calls:
            call_id = call["id"]
            result = by_id.get(call_id) or {}
            output = str(result.get("content") or "Tool completed without output.")
            await ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output,
                        },
                    }
                )
            )
            self._completed_tool_call_ids.add(call_id)
        await ws.send(json.dumps({"type": "response.create"}))
        self._response_active = True
        return True

    async def _listen(self):
        ws = self._ws
        try:
            async for raw in ws:
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                await self._handle_realtime_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Realtime S2S listener ended: %s", exc)
        else:
            logger.warning("Realtime S2S listener ended without a close error")

        if self._ws is ws:
            self._connected = False
            self._response_active = False
            self._response_waiting_for_transcript = False
            self._cancel_response_timeout()
            self._ws = None
            try:
                await ws.close()
            except Exception:
                pass
            await self._stop_output()
            if self._enabled:
                # Restore the session proactively. _append_pcm16_16k also
                # reconnects on demand, so a failed retry never loses future
                # turns permanently.
                await asyncio.sleep(0.25)
                await self.connect()

    async def _append_pcm16_16k(self, audio_bytes: bytes):
        if not self._connected or self._ws is None:
            ok = await self.connect()
            if not ok:
                return
        if self._needs_input_reset:
            await self._reset_remote_input_for_new_turn()
        pcm24 = _resample_pcm16_16k_to_24k(audio_bytes)
        try:
            await self._ws.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm24).decode("ascii"),
                    }
                )
            )
            self._buffered_audio_bytes += len(pcm24)
        except Exception as exc:
            logger.warning("S2S append failed: %s", exc)
            self._connected = False

    async def _reset_remote_input_for_new_turn(self):
        """Atomically end old output before accepting the new turn's first PCM."""
        ws = self._ws
        if not self._connected or ws is None:
            return
        try:
            if self._response_active and not self._response_waiting_for_transcript:
                await ws.send(json.dumps({"type": "response.cancel"}))
            await ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
        except Exception as exc:
            logger.warning("S2S turn reset failed: %s", exc)
            self._connected = False
            return
        self._response_active = False
        self._response_waiting_for_transcript = False
        self._cancel_response_timeout()
        self._buffered_audio_bytes = 0
        self._needs_input_reset = False
        await self._stop_output()

    async def _commit_and_respond(self):
        if not self._connected or self._ws is None:
            return
        if self._buffered_audio_bytes < 4800:
            logger.info(
                "S2S: ignoring PTT release with %.1fms buffered audio",
                self._buffered_audio_bytes / 48.0,
            )
            self._queue_transcription_progress("", done=True, discard_live_preview=True)
            return
        if self._response_active:
            logger.info("S2S: response already active; skipping duplicate response.create")
            return
        try:
            await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            # Reserve the turn, then wait for finalized transcription so tool
            # retrieval is based on the actual utterance. A timeout still starts
            # a conversational response if transcription never arrives.
            self._response_active = True
            self._response_waiting_for_transcript = True
            self._buffered_audio_bytes = 0
            self._schedule_response_timeout()
        except Exception as exc:
            logger.warning("S2S commit/respond failed: %s", exc)
            self._connected = False
            self._queue_transcription_progress("", done=True, discard_live_preview=True)

    def _cancel_response_timeout(self):
        task = self._response_timeout_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._response_timeout_task = None

    def _schedule_response_timeout(self):
        self._cancel_response_timeout()

        async def _fallback():
            try:
                await asyncio.sleep(self._TRANSCRIPTION_WAIT_SECONDS)
                await self._begin_response_for_transcript("")
            except asyncio.CancelledError:
                return

        self._response_timeout_task = asyncio.create_task(_fallback())

    async def _begin_response_for_transcript(self, transcript: str):
        if not self._connected or self._ws is None:
            return
        if not self._response_waiting_for_transcript and not (
            self._is_hands_free and not self._response_active
        ):
            return
        self._cancel_response_timeout()
        tools = self._realtime_tool_schemas((transcript or "").strip()) if transcript else []
        await self._ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "tools": tools,
                        # The strict spoken-intent gate already established that
                        # this turn is an action. Requiring a call prevents the
                        # model from merely claiming it cannot perform the task.
                        "tool_choice": "required" if tools else "none",
                    },
                }
            )
        )
        await self._ws.send(json.dumps({"type": "response.create"}))
        self._response_waiting_for_transcript = False
        self._response_active = True

    async def _cancel_active_response(self):
        """Cancel current Realtime output and clear stale input before a new turn."""
        ws = self._ws
        if self._connected and ws is not None:
            try:
                if self._response_active and not self._response_waiting_for_transcript:
                    await ws.send(json.dumps({"type": "response.cancel"}))
                await ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
            except Exception as exc:
                logger.debug("S2S response cancellation failed: %s", exc)
        self._response_active = False
        self._response_waiting_for_transcript = False
        self._cancel_response_timeout()
        self._needs_input_reset = False
        self._buffered_audio_bytes = 0
        await self._stop_output()

    async def _emit_to_output(self, frame):
        """Play S2S audio via transport.output (chained TTS does not forward audio)."""
        out = self._audio_out
        if out is None:
            await self.push_frame(frame)
            return
        try:
            await out.queue_frame(frame)
        except Exception as exc:
            logger.warning("S2S output queue failed: %s", exc)

    async def _flush_pending_out(self):
        while True:
            try:
                kind, payload = self._pending_out.get_nowait()
            except asyncio.QueueEmpty:
                break
            if kind == "tts_start" and TTSStartedFrame is not None:
                await self._emit_to_output(TTSStartedFrame())
            elif kind == "tts_stop" and TTSStoppedFrame is not None:
                await self._emit_to_output(TTSStoppedFrame())
            elif kind == "audio" and payload and OutputAudioRawFrame is not None:
                await self._emit_to_output(
                    OutputAudioRawFrame(
                        audio=payload,
                        sample_rate=24000,
                        num_channels=1,
                    )
                )

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if self._event_loop is None:
            try:
                self._event_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        await self._flush_pending_out()

        if isinstance(frame, StartFrame):
            if self._enabled:
                await self.connect()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            await self.disconnect()
            await self.push_frame(frame, direction)
            return

        if SpeakingStartedFrames and isinstance(frame, SpeakingStartedFrames):
            self._user_speaking = True
            await self.push_frame(frame, direction)
            return

        if SpeakingStoppedFrames and isinstance(frame, SpeakingStoppedFrames):
            self._user_speaking = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            if self._agent_capture_active():
                audio = getattr(frame, "audio", None) or b""
                if audio:
                    await self._append_pcm16_16k(audio)
                await self._flush_pending_out()
                return  # do not forward to STT during agent S2S
            await self.push_frame(frame, direction)
            await self._flush_pending_out()
            return

        await self.push_frame(frame, direction)
        await self._flush_pending_out()
