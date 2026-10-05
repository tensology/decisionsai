"""Fish Audio cloud TTS — live PCM stream, OpenAI pipeline framing."""

from __future__ import annotations

import asyncio
import io
import logging

import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    PYDUB_AVAILABLE,
    SOUNDFILE_AVAILABLE,
    AudioRawFrame,
    ErrorFrame,
    OutputAudioRawFrame,
    TTSService,
    TTSStartedFrame,
    TTSStoppedFrame,
    sf,
)
from distr.core.agent.services.tts.fishaudio_config import resolve_fishaudio_tts_model
from distr.core.agent.services.tts.openai import OpenAITTSService

logger = logging.getLogger(__name__)

_FISH_PCM_RATE = 44100
_FISH_PCM_FRAME_BYTES = 1764  # 20 ms, mono s16le at 44.1 kHz


class FishAudioTTSService(OpenAITTSService):
    """Fish Audio S2 TTS. Inherits OpenAI pipeline framing; only synthesis differs."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        voice_name: str | None = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        model: str | None = None,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for FishAudioTTSService")
        if not SOUNDFILE_AVAILABLE and not PYDUB_AVAILABLE:
            raise ImportError("soundfile or pydub is required for FishAudioTTSService")

        TTSService.__init__(self, **kwargs)
        self._fishaudio_api_key = api_key
        self.voice_id = voice_id
        self.voice_name = voice_name or voice_id
        self.model = resolve_fishaudio_tts_model(model)
        self.playback_speed = playback_speed
        self._text_buffer = ""
        self._frame_id_counter = 10000
        self._stt_service = stt_service
        self._cancelled = False
        self._volume_in_run_tts = True
        self._init_tts_pipeline_state()
        self._is_hands_free = False
        self._ptt_active = False
        self.event_queue = event_queue
        self._tts_session_active = False
        self._total_audio_duration = 0.0
        self._tts_started_emitted = False
        self._processed_sentences = set()
        self._tts_sentence_batch_size = 1
        self._sentence_batch_hold: list[str] = []
        self._last_processed_text_hash = None
        self._llm_response_started_at = 0.0
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        logger.info(
            "FishAudioTTSService initialized voice=%s model=%s",
            self.voice_name,
            self.model,
        )

    def get_sample_rate(self) -> int:
        return _FISH_PCM_RATE

    def _pcm_frame(self, audio: bytes):
        if self._speech_volume != 1.0:
            samples = np.frombuffer(audio, dtype="<i2").astype(np.float32)
            samples = np.clip(samples * self._speech_volume, -32768, 32767)
            audio = samples.astype("<i2").tobytes()
        FrameClass = OutputAudioRawFrame if OutputAudioRawFrame else AudioRawFrame
        frame = FrameClass(audio=audio, sample_rate=_FISH_PCM_RATE, num_channels=1)
        if not hasattr(frame, "id") or frame.id is None:
            frame.id = self._frame_id_counter
            self._frame_id_counter += 1
        if not hasattr(frame, "transport_destination"):
            frame.transport_destination = None
        if not hasattr(frame, "pts"):
            frame.pts = None
        return frame

    def _emit_tts_started_event(self):
        if not self._tts_session_active or self._tts_started_emitted:
            return
        self._tts_started_emitted = True
        if self.event_queue:
            try:
                self.event_queue.put(
                    ("tts_started", {"source": "direct_desktop"}), block=False
                )
            except Exception:
                pass

    def _generate_audio(self, text: str):
        from distr.core.agent.services.tts.fishaudio_client import synthesize_audio
        from distr.core.agent.services.tts.fishaudio_descriptor import FishAudioDescriptor

        vid = FishAudioDescriptor().resolve_reference_id(self.voice_id)
        audio_bytes = synthesize_audio(
            self._fishaudio_api_key,
            text,
            reference_id=vid,
            model=self.model,
            speed=self.playback_speed,
            audio_format="wav",
            sample_rate=_FISH_PCM_RATE,
            latency="normal",
        )
        if not SOUNDFILE_AVAILABLE:
            raise ImportError("soundfile is required for Fish Audio WAV decode")
        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        if audio_data.ndim > 1:
            audio_data = np.mean(audio_data, axis=1)
        return audio_data.astype(np.float32), int(sample_rate)

    async def run_tts(self, text: str):
        if self._cancelled:
            logger.debug("TTS: run_tts() called but cancelled - returning")
            return

        yield TTSStartedFrame()
        if self._cancelled:
            return

        from distr.core.agent.services.tts.fishaudio_client import iter_pcm_audio
        from distr.core.agent.services.tts.fishaudio_descriptor import FishAudioDescriptor

        audio_duration_seconds = 0.0
        pending = b""
        yielded_audio_bytes = 0
        try:
            vid = FishAudioDescriptor().resolve_reference_id(self.voice_id)
            async for chunk in iter_pcm_audio(
                self._fishaudio_api_key,
                text,
                reference_id=vid,
                model=self.model,
                speed=self.playback_speed,
                sample_rate=_FISH_PCM_RATE,
                latency="low",
            ):
                if self._cancelled:
                    break
                pending += bytes(chunk)
                while len(pending) >= _FISH_PCM_FRAME_BYTES:
                    frame_bytes = pending[:_FISH_PCM_FRAME_BYTES]
                    pending = pending[_FISH_PCM_FRAME_BYTES:]
                    self._emit_tts_started_event()
                    yielded_audio_bytes += len(frame_bytes)
                    yield self._pcm_frame(frame_bytes)
                    if self._cancelled:
                        break
            if not self._cancelled and pending:
                aligned = pending[: len(pending) - (len(pending) % 2)]
                if aligned:
                    self._emit_tts_started_event()
                    yielded_audio_bytes += len(aligned)
                    yield self._pcm_frame(aligned)
            audio_duration_seconds = yielded_audio_bytes / 2 / _FISH_PCM_RATE
        except Exception as e:
            if yielded_audio_bytes == 0 and not self._cancelled:
                try:
                    logger.warning("Fish Audio streaming failed; using buffered synthesis: %s", e)
                    audio_data, sample_rate = await asyncio.to_thread(self._generate_audio, text)
                    if sample_rate != _FISH_PCM_RATE:
                        from distr.core.audio.tts_handler import _resample_audio

                        audio_data, sample_rate = _resample_audio(audio_data, sample_rate, _FISH_PCM_RATE)
                    pcm = (np.clip(audio_data, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                    for offset in range(0, len(pcm), _FISH_PCM_FRAME_BYTES):
                        if self._cancelled:
                            break
                        frame_bytes = pcm[offset:offset + _FISH_PCM_FRAME_BYTES]
                        if frame_bytes:
                            self._emit_tts_started_event()
                            yielded_audio_bytes += len(frame_bytes)
                            yield self._pcm_frame(frame_bytes)
                    audio_duration_seconds = yielded_audio_bytes / 2 / _FISH_PCM_RATE
                except Exception as fallback_error:
                    logger.error("Fish Audio synthesis failed: %s", fallback_error, exc_info=True)
                    yield ErrorFrame(error=str(fallback_error))
            else:
                logger.error("Fish Audio streaming failed after audio began: %s", e, exc_info=True)
                yield ErrorFrame(error=str(e))
        yield TTSStoppedFrame()
        self._total_audio_duration += audio_duration_seconds
