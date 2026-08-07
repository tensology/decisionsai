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
    TTSStartedFrame,
    TTSStoppedFrame,
    SpeakingStartedFrames,
    SpeakingStoppedFrames,
)
from distr.core.openai_s2s import coerce_realtime_voice, is_openai_s2s_model

logger = logging.getLogger(__name__)

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

    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-realtime-2.1",
        voice: str = "marin",
        instructions: str = "",
        enabled: bool = False,
        event_queue=None,
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
        self.event_queue = event_queue
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
        self._enabled = bool(enabled)
        if model and is_openai_s2s_model(model):
            self.model = model
        if voice is not None:
            self.voice = coerce_realtime_voice(voice)
        if api_key is not None:
            self.api_key = (api_key or "").strip()
        if instructions is not None and instructions.strip():
            self.instructions = instructions.strip()
        if was and not self._enabled:
            self._schedule_coro(self.disconnect())
        elif self._enabled and not was:
            self._schedule_coro(self.connect())

    # --- mode flags (mirrored from STT/TTS command wiring) ---

    def set_ptt_active(self, active: bool, queue_interruption: bool = True):
        was = self._ptt_active
        self._ptt_active = bool(active)
        if not self._enabled:
            return
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
        self._is_hands_free = bool(enabled)

    def set_dictating(self, enabled: bool):
        self._is_dictating = bool(enabled)

    def _agent_capture_active(self) -> bool:
        if not self._enabled or self._is_dictating:
            return False
        if self._ptt_active:
            return True
        if self._is_hands_free and self._user_speaking:
            return True
        return False

    # --- websocket ---

    def _session_update_event(self) -> dict:
        turn = {"type": "semantic_vad"} if self._is_hands_free else None
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": self.model,
                "output_modalities": ["audio"],
                "instructions": self.instructions,
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "turn_detection": turn,
                    },
                    "output": {
                        "format": {"type": "audio/pcm"},
                        "voice": self.voice,
                    },
                },
            },
        }

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

    async def _listen(self):
        try:
            async for raw in self._ws:
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                et = event.get("type") or ""
                if et in (
                    "response.output_audio.delta",
                    "response.audio.delta",
                ):
                    b64 = event.get("delta") or ""
                    if not b64:
                        continue
                    try:
                        pcm = base64.b64decode(b64)
                    except Exception:
                        continue
                    if not self._tts_started:
                        self._tts_started = True
                        await self._pending_out.put(("tts_start", None))
                    await self._pending_out.put(("audio", pcm))
                elif et in (
                    "response.output_audio.done",
                    "response.audio.done",
                    "response.done",
                ):
                    if self._tts_started:
                        self._tts_started = False
                        await self._pending_out.put(("tts_stop", None))
                elif et == "error":
                    logger.error("Realtime S2S error: %s", event.get("error"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Realtime S2S listener ended: %s", exc)
            self._connected = False

    async def _append_pcm16_16k(self, audio_bytes: bytes):
        if not self._connected or self._ws is None:
            ok = await self.connect()
            if not ok:
                return
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
        except Exception as exc:
            logger.warning("S2S append failed: %s", exc)
            self._connected = False

    async def _commit_and_respond(self):
        if not self._connected or self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await self._ws.send(json.dumps({"type": "response.create"}))
        except Exception as exc:
            logger.warning("S2S commit/respond failed: %s", exc)
            self._connected = False

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
