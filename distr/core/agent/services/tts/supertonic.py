"""Supertonic TTS service using the local ``supertonic`` package."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    TTSService,
    TextFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    ErrorFrame,
    StartFrame,
    CancelFrame,
    InterruptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    AudioRawFrame,
    OutputAudioRawFrame,
)
from distr.core.agent.services.llm.utils import clean_text_for_tts
from distr.core.agent.services.tts.sentence_split import extract_complete_sentences
from distr.core.agent.services.tts.tts_pipeline_mixin import TTSPipelineMixin

logger = logging.getLogger(__name__)

try:
    from supertonic import TTS as _SupertonicTTS
    SUPERTONIC_AVAILABLE = True
except ImportError:
    _SupertonicTTS = None
    SUPERTONIC_AVAILABLE = False


_TTS_CACHE: dict[tuple[str, str | None], object] = {}
_TTS_CACHE_LOCK = None


def _get_lock():
    global _TTS_CACHE_LOCK
    if _TTS_CACHE_LOCK is None:
        import threading
        _TTS_CACHE_LOCK = threading.Lock()
    return _TTS_CACHE_LOCK


def get_or_load_tts(model: str = "supertonic-3", model_dir: str | None = None):
    """Return a process-level cached Supertonic TTS instance."""
    if not SUPERTONIC_AVAILABLE:
        raise ImportError("supertonic is required. Install with: pip install supertonic")
    key = (model, model_dir)
    if key in _TTS_CACHE:
        return _TTS_CACHE[key]
    with _get_lock():
        if key not in _TTS_CACHE:
            logger.info("Supertonic TTS: loading model=%s model_dir=%s", model, model_dir or "default")
            _TTS_CACHE[key] = _SupertonicTTS(
                model=model,
                model_dir=model_dir,
                auto_download=True,
            )
        return _TTS_CACHE[key]


class SupertonicTTSService(TTSPipelineMixin, TTSService):
    """Pipecat-compatible Supertonic TTS service."""

    def __init__(
        self,
        voice_name: str = "M1",
        voice_style_path: str | None = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        total_steps: int = 8,
        model_name: str = "supertonic-3",
        model_dir: str | None = None,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for SupertonicTTSService")
        if not SUPERTONIC_AVAILABLE:
            raise ImportError("supertonic is required. Install with: pip install supertonic")

        super().__init__(**kwargs)

        self.voice_name = (voice_name or "M1").strip()
        self.voice_style_path = voice_style_path
        self.playback_speed = playback_speed
        self.total_steps = int(total_steps or 8)
        self._model_name = model_name
        self._model_dir = model_dir
        self._tts = get_or_load_tts(model_name, model_dir)
        self._style = self._load_style()
        self._stt_service = stt_service
        self.event_queue = event_queue
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        self._cancelled = False
        self._volume_in_run_tts = True
        self._init_tts_pipeline_state()
        self._is_hands_free = False
        self._ptt_active = False
        self._text_buffer = ""
        self._processed_sentences = set()
        self._tts_session_active = False
        self._tts_started_emitted = False
        self._total_audio_duration = 0.0
        self._llm_response_started_at = 0.0
        self._frame_id_counter = 10000

        logger.info(
            "SupertonicTTSService initialized: voice=%s style_path=%s volume=%d%%",
            self.voice_name,
            Path(voice_style_path).name if voice_style_path else "built-in",
            speech_volume,
        )

    def _load_style(self):
        if self.voice_style_path:
            return self._tts.get_voice_style_from_path(self.voice_style_path)
        return self._tts.get_voice_style(voice_name=self.voice_name)

    def set_voice(self, voice_name: str, voice_style_path: str | None = None):
        self.voice_name = (voice_name or "M1").strip()
        self.voice_style_path = voice_style_path
        self._style = self._load_style()
        logger.info("Supertonic TTS: voice switched to '%s'", self.voice_name)

    def set_playback_speed(self, speed: float):
        self.playback_speed = speed

    def set_speech_volume(self, volume: int):
        self._speech_volume = max(0.0, min(1.0, volume / 100.0))

    def set_hands_free(self, enabled: bool):
        self._is_hands_free = enabled

    def set_ptt_active(self, active: bool):
        self._ptt_active = active

    def _extract_complete_sentences(self, text: str):
        return extract_complete_sentences(text)

    def _synthesize(self, text: str) -> tuple[np.ndarray, int]:
        api_speed = max(0.5, min(2.0, float(self.playback_speed or 1.0)))
        total_steps = max(5, min(12, int(self.total_steps or 8)))
        wav, _duration = self._tts.synthesize(
            text=text,
            lang="en",
            voice_style=self._style,
            total_steps=total_steps,
            speed=api_speed,
        )
        audio = np.asarray(wav, dtype=np.float32).reshape(-1)
        if audio.size and np.max(np.abs(audio)) > 1.0:
            np.clip(audio, -1.0, 1.0, out=audio)
        return audio, int(getattr(self._tts, "sample_rate", 44100) or 44100)

    async def run_tts(self, text: str):
        if self._cancelled:
            return

        text = clean_text_for_tts(text)
        if not text.strip():
            return

        yield TTSStartedFrame()
        if self._cancelled:
            return

        audio_duration_seconds = 0.0
        try:
            loop = asyncio.get_running_loop()
            audio, sample_rate = await loop.run_in_executor(
                None, lambda: self._synthesize(text)
            )
            if self._cancelled:
                return

            if audio is not None and len(audio) > 0:
                audio = np.clip(audio * self._speech_volume, -1.0, 1.0)
                audio_int16 = (audio * 32767.0).astype(np.int16)
                audio_bytes = audio_int16.tobytes()
                chunk_size = max(int(sample_rate * 0.02 * 2), 320)
                FrameClass = OutputAudioRawFrame if OutputAudioRawFrame else AudioRawFrame
                frames_yielded = 0

                for i in range(0, len(audio_bytes), chunk_size):
                    if self._cancelled:
                        break
                    chunk = audio_bytes[i:i + chunk_size]
                    if not chunk:
                        continue

                    if frames_yielded == 0 and self._tts_session_active and not self._tts_started_emitted:
                        self._tts_started_emitted = True
                        if self.event_queue:
                            try:
                                self.event_queue.put(("tts_started", {}), block=False)
                            except Exception:
                                pass

                    frame = FrameClass(audio=chunk, sample_rate=sample_rate, num_channels=1)
                    if not hasattr(frame, "id") or frame.id is None:
                        frame.id = self._frame_id_counter
                        self._frame_id_counter += 1
                    if not hasattr(frame, "transport_destination"):
                        frame.transport_destination = None
                    if not hasattr(frame, "pts"):
                        frame.pts = None
                    yield frame
                    frames_yielded += 1

                audio_duration_seconds = len(audio_bytes) / (sample_rate * 2) if sample_rate else 0
        except Exception as e:
            logger.error("Supertonic TTS error: %s", e, exc_info=True)
            yield ErrorFrame(error=str(e))
        finally:
            yield TTSStoppedFrame()
            self._total_audio_duration += audio_duration_seconds

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._cancelled = False
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._tts_session_active = True
            self._tts_started_emitted = False
            self.reset_tts_response_start()
            return

        if isinstance(frame, TextFrame):
            if not self.maybe_clear_stale_cancelled_for_text():
                return
            self._text_buffer += frame.text
            sentences, self._text_buffer = self._extract_complete_sentences(self._text_buffer)
            for sentence in sentences:
                norm = sentence.strip().lower()
                if norm in self._processed_sentences:
                    continue
                self._processed_sentences.add(norm)
                await self._enqueue_sentence(sentence, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._text_buffer.strip() and not self._cancelled:
                norm = self._text_buffer.strip().lower()
                if norm not in self._processed_sentences:
                    self._processed_sentences.add(norm)
                    await self._enqueue_sentence(self._text_buffer.strip(), direction)
            self._text_buffer = ""
            if not self._cancelled:
                await self._drain_speak_queue()
            self._tts_session_active = False
            self._llm_response_started_at = 0.0
            return

        if isinstance(frame, (CancelFrame, InterruptionFrame)):
            if self.is_stale_interrupt_frame():
                logger.debug("Supertonic TTS: ignoring stale %s", type(frame).__name__)
                return
            self.abort_pending_synthesis()
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            return
