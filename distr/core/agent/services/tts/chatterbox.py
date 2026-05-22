"""Chatterbox TTS service with reference-audio voice cloning."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import platform
import time
from pathlib import Path
from typing import Any

import numpy as np

if platform.system() == "Darwin":
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

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

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_CACHE_LOCK = None


def _get_lock():
    global _MODEL_CACHE_LOCK
    if _MODEL_CACHE_LOCK is None:
        import threading

        _MODEL_CACHE_LOCK = threading.Lock()
    return _MODEL_CACHE_LOCK


def _preferred_backend() -> str:
    if platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        return "mlx"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _load_model(backend: str):
    """Load Chatterbox, preferring the MLX package on Apple Silicon."""
    if backend == "mlx":
        try:
            from chatterbox.tts_mlx import ChatterboxTTSMLX

            return ChatterboxTTSMLX.from_pretrained(device="mps"), "mlx"
        except Exception as e:
            logger.warning("Chatterbox MLX unavailable, falling back to PyTorch: %s", e)

    try:
        from chatterbox.tts import ChatterboxTTS
    except ImportError as e:
        raise ImportError(
            "Chatterbox TTS is required. Install with: pip install chatterbox-tts"
        ) from e

    device = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
    except Exception:
        pass

    return ChatterboxTTS.from_pretrained(device=device), device


def get_or_load_model(backend: str | None = None):
    """Return a process-level cached Chatterbox model."""
    backend = backend or _preferred_backend()
    cache_key = ("chatterbox", backend)
    if cache_key in _MODEL_CACHE:
        return _MODEL_CACHE[cache_key]

    with _get_lock():
        if cache_key not in _MODEL_CACHE:
            logger.info("Chatterbox: loading model backend=%s", backend)
            model, resolved_backend = _load_model(backend)
            _MODEL_CACHE[cache_key] = (model, resolved_backend)
            logger.info("Chatterbox: model loaded backend=%s", resolved_backend)
        return _MODEL_CACHE[cache_key]


def _to_numpy_audio(wav: Any) -> np.ndarray:
    try:
        import torch

        if isinstance(wav, torch.Tensor):
            wav = wav.detach().cpu().numpy()
    except Exception:
        pass
    audio = np.asarray(wav, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=0 if audio.shape[0] <= audio.shape[-1] else 1)
    audio = audio.reshape(-1)
    if audio.size:
        peak = float(np.max(np.abs(audio)))
        if peak > 1.0:
            audio = audio / peak * 0.95
    return audio


class ChatterboxTTSService(TTSService):
    """Pipecat-compatible Chatterbox TTS service.

    Chatterbox generates complete utterances. For the live agent we synthesize
    sentence-by-sentence, then push the resulting PCM frames immediately.
    """

    def __init__(
        self,
        voice_name: str = "default",
        reference_audio_path: str | None = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
        backend: str | None = None,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for ChatterboxTTSService")

        super().__init__(**kwargs)

        self.voice_name = (voice_name or "default").strip()
        self.reference_audio_path = reference_audio_path
        self.playback_speed = playback_speed
        self.exaggeration = float(exaggeration)
        self.cfg_weight = float(cfg_weight)
        self._backend = backend or _preferred_backend()
        self._stt_service = stt_service
        self.event_queue = event_queue
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        self._cancelled = False
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
            "ChatterboxTTSService initialized: voice=%s ref=%s backend=%s volume=%d%%",
            self.voice_name,
            Path(reference_audio_path).name if reference_audio_path else "default",
            self._backend,
            speech_volume,
        )

    def set_reference_voice(self, audio_path: str | None):
        self.reference_audio_path = audio_path
        logger.info(
            "Chatterbox TTS: reference voice updated -> %s",
            os.path.basename(audio_path) if audio_path else "default",
        )

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
        model, resolved_backend = get_or_load_model(self._backend)
        kwargs = {
            "text": text,
            "exaggeration": max(0.0, min(1.0, self.exaggeration)),
            "cfg_weight": max(0.0, min(1.0, self.cfg_weight)),
            "show_progress": False,
            "use_sentence_chunking": False,
        }
        if self.reference_audio_path and os.path.isfile(self.reference_audio_path):
            kwargs["audio_prompt_path"] = self.reference_audio_path
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                wav = model.generate(**kwargs)
        except TypeError:
            kwargs.pop("cfg_weight", None)
            with contextlib.redirect_stdout(io.StringIO()):
                wav = model.generate(**kwargs)
        audio = _to_numpy_audio(wav)
        if audio.size:
            audio = np.clip(audio * self._speech_volume, -1.0, 1.0)
        sample_rate = int(getattr(model, "sr", None) or getattr(model, "sample_rate", None) or 24000)
        logger.debug("Chatterbox synthesized %.2fs via %s", len(audio) / sample_rate, resolved_backend)
        return audio, sample_rate

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
            logger.error("Chatterbox TTS error: %s", e, exc_info=True)
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
            self._processed_sentences = set()
            self._text_buffer = ""
            self._llm_response_started_at = time.monotonic()
            return

        if isinstance(frame, TextFrame):
            if self._cancelled:
                return
            self._text_buffer += frame.text
            sentences, self._text_buffer = self._extract_complete_sentences(self._text_buffer)
            for sentence in sentences:
                norm = sentence.strip().lower()
                if norm in self._processed_sentences:
                    continue
                self._processed_sentences.add(norm)
                async for out_frame in self.run_tts(sentence):
                    await self.push_frame(out_frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._text_buffer.strip() and not self._cancelled:
                norm = self._text_buffer.strip().lower()
                if norm not in self._processed_sentences:
                    self._processed_sentences.add(norm)
                    async for out_frame in self.run_tts(self._text_buffer.strip()):
                        await self.push_frame(out_frame, direction)
            self._text_buffer = ""
            self._tts_session_active = False
            self._llm_response_started_at = 0.0
            return

        if isinstance(frame, (CancelFrame, InterruptionFrame)):
            now = time.monotonic()
            if self._llm_response_started_at > 0 and (now - self._llm_response_started_at) < 2.0:
                logger.debug("Chatterbox TTS: ignoring stale %s", type(frame).__name__)
                return
            self._cancelled = True
            self._text_buffer = ""
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            return
