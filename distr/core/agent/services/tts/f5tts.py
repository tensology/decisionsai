import asyncio
import logging
import time
import numpy as np
import os

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, TTSService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    StartFrame, CancelFrame, InterruptionFrame, OutputAudioRawFrame,
    SOUNDFILE_AVAILABLE,
    PYDUB_AVAILABLE,
)
from distr.core.agent.services.tts.sentence_split import extract_complete_sentences

logger = logging.getLogger(__name__)

# Check if f5-tts is available
try:
    from f5_tts.api import F5TTS
    F5TTS_AVAILABLE = True
except ImportError:
    F5TTS = None
    F5TTS_AVAILABLE = False

# Default reference audio bundled with f5-tts package
_DEFAULT_REF_AUDIO = None
_DEFAULT_REF_TEXT = "Some call me nature, others call me mother nature."

# Process-level model singleton — loaded once, reused across all service instances.
# F5-TTS model is ~1GB+ and takes 10-30s to load; we must never reload it per-chat.
_MODEL_CACHE: dict = {}  # model_name -> F5TTS instance
_MODEL_CACHE_LOCK = None  # threading.Lock, created lazily to avoid import-time issues


def _get_lock():
    global _MODEL_CACHE_LOCK
    if _MODEL_CACHE_LOCK is None:
        import threading
        _MODEL_CACHE_LOCK = threading.Lock()
    return _MODEL_CACHE_LOCK


def get_or_load_model(model_name: str = "F5TTS_v1_Base") -> "F5TTS":
    """Return the cached F5TTS model, loading it once if needed."""
    if model_name in _MODEL_CACHE:
        return _MODEL_CACHE[model_name]
    with _get_lock():
        # Double-check after acquiring lock
        if model_name in _MODEL_CACHE:
            return _MODEL_CACHE[model_name]
        logger.info("F5-TTS: loading model '%s' (one-time, will be cached)...", model_name)
        try:
            model = F5TTS(model=model_name)
        except OSError as e:
            if "torchcodec" in str(e) or "libtorchcodec" in str(e):
                raise RuntimeError(
                    "torchcodec is causing a conflict. Run: pip uninstall torchcodec -y"
                ) from e
            raise
        _MODEL_CACHE[model_name] = model
        logger.info("F5-TTS: model '%s' loaded and cached", model_name)
        return model


def _get_default_ref_audio() -> str:
    """Return path to the default reference audio shipped with f5-tts."""
    global _DEFAULT_REF_AUDIO
    if _DEFAULT_REF_AUDIO and os.path.isfile(_DEFAULT_REF_AUDIO):
        return _DEFAULT_REF_AUDIO
    try:
        import f5_tts
        pkg_dir = os.path.dirname(f5_tts.__file__)
        candidate = os.path.join(pkg_dir, "infer", "examples", "basic", "basic_ref_en.wav")
        if os.path.isfile(candidate):
            _DEFAULT_REF_AUDIO = candidate
            return candidate
    except Exception:
        pass
    return None


class F5TTSTTSService(TTSService):
    """F5-TTS based TTS service with built-in voice cloning support."""

    def __init__(
        self,
        voice_name: str = "default",
        reference_audio_path: str = None,
        reference_text: str = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        model_name: str = "F5TTS_v1_Base",
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for F5TTSTTSService")
        if not F5TTS_AVAILABLE:
            raise ImportError("f5-tts is required. Install with: pip install f5-tts")
        if not SOUNDFILE_AVAILABLE and not PYDUB_AVAILABLE:
            raise ImportError("soundfile or pydub is required for F5TTSTTSService")

        super().__init__(**kwargs)

        self.voice_name = voice_name
        self.playback_speed = playback_speed
        self._text_buffer = ""
        self._frame_id_counter = 10000
        self._stt_service = stt_service
        self._cancelled = False
        self._is_hands_free = False
        self._ptt_active = False
        self._llm_response_started_at = 0  # Timestamp of last LLMFullResponseStartFrame; used to ignore stale InterruptionFrames
        self.event_queue = event_queue
        self._tts_session_active = False
        self._total_audio_duration = 0.0
        self._tts_started_emitted = False
        self._processed_sentences = set()
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))

        # Voice cloning: resolve reference audio
        self._reference_audio = reference_audio_path or _get_default_ref_audio()
        self._reference_text = reference_text or _DEFAULT_REF_TEXT

        # Lazy-load F5TTS model
        self._f5tts = None
        self._model_name = model_name

        logger.info(
            "F5TTSTTSService initialized: voice=%s, ref=%s, volume=%d%%",
            voice_name,
            os.path.basename(self._reference_audio) if self._reference_audio else "none",
            speech_volume,
        )

    def _get_model(self):
        """Return the process-level cached model (loads once, reused forever)."""
        if not F5TTS_AVAILABLE:
            raise ImportError("f5-tts is required. Install with: pip install f5-tts")
        return get_or_load_model(self._model_name)

    def set_playback_speed(self, speed: float):
        self.playback_speed = speed

    def set_speech_volume(self, volume: int):
        self._speech_volume = max(0.0, min(1.0, volume / 100.0))

    def set_hands_free(self, enabled: bool):
        self._is_hands_free = enabled

    def set_ptt_active(self, active: bool):
        self._ptt_active = active

    def set_reference_voice(self, audio_path: str, ref_text: str = None):
        """Hot-swap the reference voice for cloning."""
        self._reference_audio = audio_path
        if ref_text:
            self._reference_text = ref_text
        logger.info("F5-TTS: reference voice updated -> %s", os.path.basename(audio_path))

    def _synthesize(self, text: str) -> tuple:
        """Synthesize text and return (audio_np_float32, sample_rate)."""
        model = self._get_model()
        ref_audio = self._reference_audio
        ref_text = self._reference_text

        if not ref_audio or not os.path.isfile(ref_audio):
            raise FileNotFoundError(
                "F5-TTS requires a reference audio file. "
                "Upload a custom voice or ensure the default reference audio is available."
            )

        wav, sr, _ = model.infer(
            ref_file=ref_audio,
            ref_text=ref_text,
            gen_text=text,
            speed=self.playback_speed,
        )

        audio = np.array(wav, dtype=np.float32)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        # Normalize
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.95

        # Apply volume
        audio = audio * self._speech_volume
        return audio, sr

    def _extract_complete_sentences(self, text: str):
        return extract_complete_sentences(text)

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._tts_session_active = True
            self._total_audio_duration = 0.0
            self._tts_started_emitted = False
            self._processed_sentences = set()
            self._text_buffer = ""
            self._llm_response_started_at = time.monotonic()  # Timestamp to ignore stale InterruptionFrames

        elif isinstance(frame, TextFrame):
            if self._cancelled:
                return
            self._text_buffer += frame.text
            sentences, self._text_buffer = self._extract_complete_sentences(self._text_buffer)
            for sentence in sentences:
                norm = sentence.strip().lower()
                if norm in self._processed_sentences:
                    continue
                self._processed_sentences.add(norm)
                await self._speak_sentence(sentence)

        elif isinstance(frame, LLMFullResponseEndFrame):
            # Flush remaining buffer
            if self._text_buffer.strip() and not self._cancelled:
                norm = self._text_buffer.strip().lower()
                if norm not in self._processed_sentences:
                    self._processed_sentences.add(norm)
                    await self._speak_sentence(self._text_buffer.strip())
            self._text_buffer = ""
            self._tts_session_active = False
            self._llm_response_started_at = 0  # Reset so future InterruptionFrames are not treated as stale
            if self._tts_started_emitted and self.event_queue:
                try:
                    self.event_queue.put_nowait({"type": "tts_stopped"})
                except Exception:
                    pass

        elif isinstance(frame, (CancelFrame, InterruptionFrame)):
            # Guard: ignore stale CancelFrame/InterruptionFrame that arrive after current response started
            now = time.monotonic()
            if self._llm_response_started_at > 0 and (now - self._llm_response_started_at) < 0.3:
                logger.debug(
                    "F5-TTS: Ignoring stale %s (%.0fms since LLMFullResponseStartFrame)",
                    type(frame).__name__,
                    (now - self._llm_response_started_at) * 1000,
                )
                return
            self._cancelled = True
            self._text_buffer = ""

        elif isinstance(frame, StartFrame):
            self._cancelled = False

    async def _speak_sentence(self, text: str):
        try:
            loop = asyncio.get_event_loop()
            audio, sr = await loop.run_in_executor(None, self._synthesize, text)

            duration = len(audio) / sr
            self._total_audio_duration += duration

            if not self._tts_started_emitted and self.event_queue:
                try:
                    self.event_queue.put_nowait({"type": "tts_started"})
                    self._tts_started_emitted = True
                except Exception:
                    pass

            # Resample to 16-bit PCM for Pipecat frames
            audio_int16 = (audio * 32767).astype(np.int16)
            chunk_size = sr  # 1-second chunks
            for i in range(0, len(audio_int16), chunk_size):
                chunk = audio_int16[i:i + chunk_size]
                frame = OutputAudioRawFrame(
                    audio=chunk.tobytes(),
                    sample_rate=sr,
                    num_channels=1,
                )
                await self.push_frame(frame)

        except Exception as e:
            logger.error("F5-TTS synthesis error: %s", e, exc_info=True)
            if self.event_queue:
                try:
                    self.event_queue.put_nowait({"type": "tts_error", "error": str(e)})
                except Exception:
                    pass
