"""Pixazo VoxCPM cloud TTS — sync REST, no local model."""

from __future__ import annotations

import io
import logging

import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    PYDUB_AVAILABLE,
    SOUNDFILE_AVAILABLE,
    TTSService,
    sf,
)
from distr.core.agent.services.tts.openai import OpenAITTSService

logger = logging.getLogger(__name__)


class PixazoTTSService(OpenAITTSService):
    """VoxCPM via Pixazo gateway. Inherits OpenAI pipeline framing; only synthesis differs."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        voice_name: str | None = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        reference_audio_url: str | None = None,
        prompt_text: str = "",
        dit_steps: int = 6,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for PixazoTTSService")
        if not SOUNDFILE_AVAILABLE and not PYDUB_AVAILABLE:
            raise ImportError("soundfile or pydub is required for PixazoTTSService")

        TTSService.__init__(self, **kwargs)
        self._pixazo_api_key = api_key
        self._reference_audio_url = reference_audio_url
        self._prompt_text = prompt_text or ""
        self._dit_steps = max(4, min(30, int(dit_steps)))
        self.voice_id = voice_id
        self.voice_name = voice_name or voice_id
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
        self._tts_sentence_batch_size = 3  # ponytail: Pixazo cloud TTS — fewer round-trips
        self._sentence_batch_hold: list[str] = []
        self._last_processed_text_hash = None
        self._llm_response_started_at = 0.0
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        logger.info(
            "PixazoTTSService initialized voice=%s ref=%s batch=%s",
            self.voice_name,
            bool(self._reference_audio_url),
            self._tts_sentence_batch_size,
        )

    def _generate_audio(self, text: str):
        from distr.core.pixazo_client import voxcpm_synthesize_wav_bytes

        wav_bytes = voxcpm_synthesize_wav_bytes(
            self._pixazo_api_key,
            text,
            voice_id=self.voice_id,
            reference_audio_url=self._reference_audio_url,
            prompt_text=self._prompt_text,
            dit_steps=self._dit_steps,
        )
        if not SOUNDFILE_AVAILABLE:
            raise ImportError("soundfile is required for Pixazo VoxCPM WAV decode")
        audio_data, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        if audio_data.ndim > 1:
            audio_data = np.mean(audio_data, axis=1)
        return audio_data.astype(np.float32), int(sample_rate)
