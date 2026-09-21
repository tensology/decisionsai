"""Fish Audio cloud TTS — sync REST, OpenAI pipeline framing."""

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
from distr.core.agent.services.tts.fishaudio_config import resolve_fishaudio_tts_model
from distr.core.agent.services.tts.openai import OpenAITTSService

logger = logging.getLogger(__name__)


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
        self._tts_sentence_batch_size = 2
        self._sentence_batch_hold: list[str] = []
        self._last_processed_text_hash = None
        self._llm_response_started_at = 0.0
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        logger.info(
            "FishAudioTTSService initialized voice=%s model=%s",
            self.voice_name,
            self.model,
        )

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
            sample_rate=44100,
            latency="balanced",
        )
        if not SOUNDFILE_AVAILABLE:
            raise ImportError("soundfile is required for Fish Audio WAV decode")
        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        if audio_data.ndim > 1:
            audio_data = np.mean(audio_data, axis=1)
        return audio_data.astype(np.float32), int(sample_rate)
