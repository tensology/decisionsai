"""
VoxCPM TTS service — tokenizer-free TTS with voice cloning.

On CUDA: uses VoxCPM2 (2B, 48kHz, streaming).
On CPU (macOS): uses VoxCPM-0.5B (0.5B, 16kHz, full generation per sentence).

Install: pip install voxcpm
"""

import asyncio
import logging
import re
import numpy as np
import os
import platform

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, TTSService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    TTSStartedFrame, TTSStoppedFrame, ErrorFrame, StartFrame, EndFrame,
    CancelFrame, InterruptionFrame, UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame, AudioRawFrame, OutputAudioRawFrame,
    sf, SOUNDFILE_AVAILABLE,
    AudioSegment, PYDUB_AVAILABLE,
)

logger = logging.getLogger(__name__)

# Force CPU on macOS BEFORE importing torch — MPS crashes with bfloat16 matmul
if platform.system() == "Darwin":
    os.environ.setdefault("VOXCPM_DEVICE", "cpu")

try:
    from voxcpm import VoxCPM
    VOXCPM_AVAILABLE = True
except ImportError:
    VoxCPM = None
    VOXCPM_AVAILABLE = False

# Detect if we have CUDA for the full model, or must use the lighter one
_HAS_CUDA = False
try:
    import torch
    _HAS_CUDA = torch.cuda.is_available()
except Exception:
    pass

# Model selection based on hardware
_DEFAULT_MODEL = "openbmb/VoxCPM2" if _HAS_CUDA else "openbmb/VoxCPM-0.5B"
_INFERENCE_STEPS = 10 if _HAS_CUDA else 5

_MODEL_CACHE: dict = {}
_MODEL_CACHE_LOCK = None


def _get_lock():
    global _MODEL_CACHE_LOCK
    if _MODEL_CACHE_LOCK is None:
        import threading
        _MODEL_CACHE_LOCK = threading.Lock()
    return _MODEL_CACHE_LOCK


def get_or_load_model(model_id: str = None) -> "VoxCPM":
    """Return the cached VoxCPM model, loading it once if needed."""
    if model_id is None:
        model_id = _DEFAULT_MODEL
    if model_id in _MODEL_CACHE:
        return _MODEL_CACHE[model_id]
    with _get_lock():
        if model_id in _MODEL_CACHE:
            return _MODEL_CACHE[model_id]
        logger.info("VoxCPM: loading model '%s' (one-time, cached)...", model_id)
        if platform.system() == "Darwin":
            import torch
            _orig = torch.backends.mps.is_available
            torch.backends.mps.is_available = lambda: False
            try:
                model = VoxCPM.from_pretrained(model_id, load_denoiser=False)
            finally:
                torch.backends.mps.is_available = _orig
        else:
            model = VoxCPM.from_pretrained(model_id, load_denoiser=False)
        _MODEL_CACHE[model_id] = model
        logger.info("VoxCPM: model '%s' loaded on %s",
                     model_id, next(model.tts_model.parameters()).device)
        return model


class VoxCPMTTSService(TTSService):
    """VoxCPM TTS with automatic model selection based on hardware."""

    def __init__(self, voice_name="default", reference_audio_path=None,
                 reference_text=None, stt_service=None, playback_speed=1.0,
                 event_queue=None, speech_volume=100, model_id=None, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required")
        if not VOXCPM_AVAILABLE:
            raise ImportError("voxcpm is required. Install with: pip install voxcpm")

        super().__init__(**kwargs)

        self.voice_name = voice_name
        self.playback_speed = playback_speed
        self._text_buffer = ""
        self._frame_id_counter = 10000
        self._stt_service = stt_service
        self._cancelled = False
        self._is_hands_free = False
        self._ptt_active = False
        self.event_queue = event_queue
        self._tts_session_active = False
        self._total_audio_duration = 0.0
        self._tts_started_emitted = False
        self._in_response_after_start = False
        self._processed_sentences = set()
        self._session_text = ""
        self._current_telegram_request = False
        self._telegram_file_sent = False
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        self._reference_audio = reference_audio_path
        self._reference_text = reference_text
        self._model_id = model_id or _DEFAULT_MODEL

        logger.info("VoxCPMTTSService: model=%s, voice=%s, cuda=%s",
                     self._model_id, voice_name, _HAS_CUDA)

    def _get_model(self):
        return get_or_load_model(self._model_id)

    def _get_sample_rate(self):
        model = self._get_model()
        return model.tts_model.sample_rate if hasattr(model, 'tts_model') else 24000

    def get_sample_rate(self) -> int:
        return self._get_sample_rate()

    def set_playback_speed(self, speed: float):
        self.playback_speed = speed

    def set_speech_volume(self, volume: int):
        self._speech_volume = max(0.0, min(1.0, volume / 100.0))

    def set_hands_free(self, enabled: bool):
        self._is_hands_free = enabled

    def set_ptt_active(self, active: bool):
        self._ptt_active = active

    def set_reference_voice(self, audio_path: str, ref_text: str = None):
        self._reference_audio = audio_path
        if ref_text:
            self._reference_text = ref_text

    def _build_gen_kwargs(self, text: str) -> dict:
        kw = {"text": text, "cfg_value": 2.0, "inference_timesteps": _INFERENCE_STEPS}
        if self._reference_audio and os.path.isfile(self._reference_audio):
            if _HAS_CUDA:
                # VoxCPM2: supports reference_wav_path for isolated cloning
                if self._reference_text:
                    kw["prompt_wav_path"] = self._reference_audio
                    kw["prompt_text"] = self._reference_text
                    kw["reference_wav_path"] = self._reference_audio
                else:
                    kw["reference_wav_path"] = self._reference_audio
            else:
                # VoxCPM-0.5B: only supports prompt_wav_path + prompt_text (continuation cloning)
                kw["prompt_wav_path"] = self._reference_audio
                kw["prompt_text"] = self._reference_text or ""
        return kw

    def _synthesize(self, text: str) -> tuple:
        """Generate full audio for text. Returns (audio_float32, sample_rate).

        On the first call without a reference audio, saves the output as a
        reference clip so all subsequent calls use the same voice via cloning.
        """
        model = self._get_model()
        kw = self._build_gen_kwargs(text)
        wav = model.generate(**kw)
        audio = np.array(wav, dtype=np.float32)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.95
        sr = model.tts_model.sample_rate if hasattr(model, 'tts_model') else 24000

        # Bootstrap: save first output as reference clip for voice consistency
        if not self._reference_audio or not os.path.isfile(self._reference_audio):
            try:
                import tempfile
                import soundfile as _sf
                ref_dir = os.path.join(tempfile.gettempdir(), "voxcpm_ref")
                os.makedirs(ref_dir, exist_ok=True)
                ref_path = os.path.join(ref_dir, "default_voice.wav")
                _sf.write(ref_path, audio, sr)
                self._reference_audio = ref_path
                self._reference_text = text
                logger.info("VoxCPM: saved first output as reference voice -> %s", ref_path)
            except Exception as e:
                logger.warning("VoxCPM: could not save reference clip: %s", e)

        audio = audio * self._speech_volume
        return audio, sr

    def _extract_complete_sentences(self, text: str):
        sentences = []
        remaining = text
        while True:
            match = re.search(r'([^.!?]*\w[^.!?]*[.!?]+)(\s+|$)', remaining)
            if not match:
                break
            sentences.append(match.group(1).strip())
            remaining = remaining[match.end():]
        return sentences, remaining

    async def run_tts(self, text: str):
        """Generate full audio for a sentence, then yield all frames at once.

        On CPU this avoids the streaming gap-play-gap problem — the entire
        sentence is synthesized first, then pushed as a continuous block.
        """
        if self._cancelled:
            return

        yield TTSStartedFrame()
        if self._cancelled:
            return

        audio_duration = 0.0
        frames_yielded = 0

        try:
            loop = asyncio.get_running_loop()
            audio, sr = await loop.run_in_executor(None, self._synthesize, text)

            if self._cancelled:
                return

            audio_duration = len(audio) / sr

            # Emit tts_started on first audio
            if self._tts_session_active and not self._tts_started_emitted:
                if not getattr(self, '_current_telegram_request', False):
                    self._tts_started_emitted = True
                    if self.event_queue:
                        try:
                            self.event_queue.put(('tts_started', {}), block=False)
                        except Exception:
                            pass

            # Convert to 16-bit PCM and yield in 20ms frames
            audio_int16 = (audio * 32767).astype(np.int16)
            audio_bytes = audio_int16.tobytes()
            frame_size = int(sr * 0.02) * 2  # 20ms
            frame_size = max(frame_size, 640)

            FrameClass = OutputAudioRawFrame or AudioRawFrame

            for i in range(0, len(audio_bytes), frame_size):
                if self._cancelled:
                    break
                raw = audio_bytes[i:i + frame_size]
                if not raw:
                    continue

                frame = FrameClass(
                    audio=raw,
                    sample_rate=sr,
                    num_channels=1,
                )
                if not hasattr(frame, 'id') or frame.id is None:
                    frame.id = self._frame_id_counter
                    self._frame_id_counter += 1
                if not hasattr(frame, 'transport_destination'):
                    frame.transport_destination = None
                if not hasattr(frame, 'pts'):
                    frame.pts = None

                yield frame
                frames_yielded += 1

        except Exception as e:
            logger.error("VoxCPM TTS error: %s", e, exc_info=True)
            yield ErrorFrame(error=str(e))
            audio_duration = 0
        finally:
            yield TTSStoppedFrame()
            self._total_audio_duration += audio_duration

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._tts_session_active = True
            self._total_audio_duration = 0.0
            self._tts_started_emitted = False
            self._processed_sentences = set()
            self._text_buffer = ""
            self._cancelled = False
            self._in_response_after_start = True

        elif isinstance(frame, TextFrame):
            if self._cancelled:
                return
            # Just accumulate — don't generate yet
            self._text_buffer += frame.text

        elif isinstance(frame, LLMFullResponseEndFrame):
            # NOW generate the entire response as one TTS call
            full_text = self._text_buffer.strip()
            self._text_buffer = ""
            self._tts_session_active = False
            self._in_response_after_start = False

            if full_text and not self._cancelled:
                async for audio_frame in self.run_tts(full_text):
                    await self.push_frame(audio_frame)

            if self._tts_started_emitted and self.event_queue:
                try:
                    self.event_queue.put_nowait({"type": "tts_stopped"})
                except Exception:
                    pass

        elif isinstance(frame, (CancelFrame, InterruptionFrame)):
            if self._in_response_after_start and isinstance(frame, CancelFrame):
                return
            self._cancelled = True
            self._text_buffer = ""

        elif isinstance(frame, StartFrame):
            self._cancelled = False
