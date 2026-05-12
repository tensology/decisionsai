"""
Pipecat TTS service for Microsoft VibeVoice Realtime (local HF).

See ``vibevoice_streaming_inference`` and ``vibevoice_runtime``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time

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
    EndFrame,
    CancelFrame,
    InterruptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    AudioRawFrame,
    OutputAudioRawFrame,
)
from distr.core.agent.services.llm.utils import clean_text_for_tts
from distr.core.agent.services.tts.sentence_split import (
    extract_complete_sentences,
    is_redundant_sentence,
)

logger = logging.getLogger(__name__)


class VibeVoiceRealtimeTTSService(TTSService):
    """Local VibeVoice-Realtime TTS (non-streaming model, sentence-chunked pipeline)."""

    def __init__(
        self,
        voice_id: str = None,
        voice_name: str = None,
        device: str | None = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for VibeVoiceRealtimeTTSService")

        super().__init__(**kwargs)

        self.voice_id = (voice_id or "en-carter_man").strip().lower().replace(".pt", "")
        self.voice_name = (voice_name or self.voice_id).strip()
        self.playback_speed = max(0.5, min(2.0, float(playback_speed)))
        self._device_override = (device or "").strip() or None
        self._stt_service = stt_service
        self.event_queue = event_queue
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        self._cancelled = False
        self._in_response_after_start = False
        self._llm_response_started_at = 0.0
        self._is_hands_free = False
        self._ptt_active = False
        self._text_buffer = ""
        self._processed_sentences = set()
        self._tts_session_active = False
        self._total_audio_duration = 0.0
        self._tts_started_emitted = False
        self._session_text = ""
        self._current_telegram_request = False
        self._telegram_file_sent = False
        self._frame_id_counter = 10000

        from distr.core.agent.services.tts.vibevoice_realtime_descriptor import (
            VibeVoiceRealtimeDescriptor,
        )

        self._voice_pt_path = VibeVoiceRealtimeDescriptor._voice_pt_path(self.voice_id)
        logger.info(
            "VibeVoice Realtime TTS: voice=%s preset=%s",
            self.voice_id,
            self._voice_pt_path,
        )

    def set_voice(self, voice_id: str):
        from distr.core.agent.services.tts.vibevoice_realtime_descriptor import (
            VibeVoiceRealtimeDescriptor,
        )

        self.voice_id = (voice_id or "").strip().lower().replace(".pt", "")
        self.voice_name = self.voice_id
        self._voice_pt_path = VibeVoiceRealtimeDescriptor._voice_pt_path(self.voice_id)
        logger.info("VibeVoice Realtime TTS: voice switched to '%s'", self.voice_id)

    def set_playback_speed(self, speed: float):
        self.playback_speed = max(0.5, min(2.0, float(speed)))

    def set_speech_volume(self, volume: int):
        self._speech_volume = max(0.0, min(1.0, volume / 100.0))

    def set_hands_free(self, enabled: bool):
        self._is_hands_free = enabled

    def set_ptt_active(self, active: bool):
        self._ptt_active = active

    def get_sample_rate(self) -> int:
        return 48000

    @staticmethod
    def _extract_complete_sentences(text: str):
        return extract_complete_sentences(text)

    def _is_duplicate_sentence(self, normalized: str) -> bool:
        return is_redundant_sentence(normalized, self._processed_sentences)

    def _synthesize_to_48k_mono(self, text: str) -> tuple[np.ndarray, int]:
        from distr.core.audio.tts_handler import _resample_audio
        from distr.core.agent.services.tts.vibevoice_streaming_inference import (
            synthesize_streaming_wav,
        )

        fd, path = tempfile.mkstemp(suffix=".wav", prefix="vvr_")
        os.close(fd)
        try:
            synthesize_streaming_wav(text, self._voice_pt_path, path, cfg_scale=1.5)
            import soundfile as sf

            data, sr = sf.read(path, dtype="float32", always_2d=False)
            if data.ndim > 1:
                data = np.mean(data, axis=1)
            data, sr = _resample_audio(data.astype("float32"), int(sr), 48000)
            if abs(self.playback_speed - 1.0) > 0.02 and data.size:
                new_len = max(1, int(len(data) / self.playback_speed))
                x_old = np.linspace(0.0, 1.0, num=len(data), dtype=np.float64)
                x_new = np.linspace(0.0, 1.0, num=new_len, dtype=np.float64)
                data = np.interp(x_new, x_old, data.astype(np.float64)).astype(np.float32)
            return data, int(sr)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

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
                None, lambda: self._synthesize_to_48k_mono(text)
            )
            if self._cancelled:
                return

            if audio is not None and len(audio) > 0:
                audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
                audio_bytes = audio_int16.tobytes()
                chunk_size = max(int(sample_rate * 0.02 * 2), 320)
                FrameClass = OutputAudioRawFrame if OutputAudioRawFrame else AudioRawFrame
                frames_yielded = 0

                for i in range(0, len(audio_bytes), chunk_size):
                    if self._cancelled:
                        break
                    chunk = audio_bytes[i : i + chunk_size]
                    if not chunk:
                        continue

                    if frames_yielded == 0 and self._tts_session_active and not self._tts_started_emitted:
                        if not self._current_telegram_request:
                            self._tts_started_emitted = True
                            if self.event_queue:
                                try:
                                    self.event_queue.put(("tts_started", {}), block=False)
                                except Exception:
                                    pass
                        else:
                            self._tts_started_emitted = True

                    arr = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32767.0
                    arr *= self._speech_volume
                    arr = np.clip(arr, -1.0, 1.0)
                    chunk = (arr * 32767.0).astype(np.int16).tobytes()

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
            logger.error("VibeVoice Realtime TTS error: %s", e, exc_info=True)
            yield ErrorFrame(error=str(e))
        finally:
            yield TTSStoppedFrame()
            self._total_audio_duration += audio_duration_seconds

    async def process_frame(self, frame, direction):
        if isinstance(frame, CancelFrame):
            if self._in_response_after_start:
                logger.debug("VibeVoice Realtime TTS: CancelFrame ignored (stale)")
                return
            self._cancelled = True
            self._text_buffer = ""
            if self._is_hands_free:
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            now = time.monotonic()
            if self._llm_response_started_at > 0 and (now - self._llm_response_started_at) < 0.3:
                logger.debug(
                    "VibeVoice Realtime TTS: Ignoring stale InterruptionFrame (%.0fms since LLM start)",
                    (now - self._llm_response_started_at) * 1000,
                )
                return
            self._cancelled = True
            self._text_buffer = ""
            self._processed_sentences.clear()
            if self._tts_session_active:
                self._tts_session_active = False
                self._total_audio_duration = 0.0
                self._tts_started_emitted = False
            if not self._current_telegram_request and self.event_queue:
                try:
                    self.event_queue.put(("tts_stopped", {"duration": 0.0}), block=False)
                except Exception:
                    pass
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            if self._is_hands_free:
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            if self._cancelled:
                return

            import threading

            if not self._current_telegram_request:
                cur = threading.current_thread()
                if getattr(cur, "telegram_request", False):
                    self._current_telegram_request = True
                else:
                    for t in threading.enumerate():
                        if getattr(t, "telegram_request", False):
                            self._current_telegram_request = True
                            break

            self._text_buffer += frame.text
            self._session_text += frame.text

            if self._cancelled:
                self._text_buffer = ""
                return

            sentences, remaining = self._extract_complete_sentences(self._text_buffer)
            self._text_buffer = remaining

            for sentence in sentences:
                if self._cancelled:
                    self._text_buffer = ""
                    break
                norm = sentence.strip().lower()
                if self._is_duplicate_sentence(norm):
                    logger.debug("VibeVoice Realtime TTS: skipping duplicate: '%s'", sentence[:50])
                    continue
                self._processed_sentences.add(norm)
                if len(self._processed_sentences) > 100:
                    self._processed_sentences = set(list(self._processed_sentences)[-50:])
                async for audio_frame in self.run_tts(sentence):
                    if self._cancelled:
                        break
                    is_audio = isinstance(audio_frame, AudioRawFrame) or (
                        OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)
                    )
                    if is_audio or isinstance(audio_frame, ErrorFrame):
                        await self.push_frame(audio_frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._cancelled = False
            self._in_response_after_start = True
            self._llm_response_started_at = time.monotonic()
            self._processed_sentences.clear()
            self._tts_session_active = True
            self._total_audio_duration = 0.0
            self._tts_started_emitted = False
            self._session_text = ""
            self._current_telegram_request = False
            self._telegram_file_sent = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            self._in_response_after_start = False
            self._llm_response_started_at = 0.0
            if self._text_buffer.strip() and not self._cancelled:
                text = self._text_buffer.strip()
                self._text_buffer = ""
                async for audio_frame in self.run_tts(text):
                    if self._cancelled:
                        break
                    is_audio = isinstance(audio_frame, AudioRawFrame) or (
                        OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)
                    )
                    if is_audio:
                        await self.push_frame(audio_frame, direction)
            if self._tts_session_active:
                self._tts_session_active = False
                if not self._current_telegram_request and self.event_queue:
                    try:
                        self.event_queue.put(
                            ("tts_stopped", {"duration": self._total_audio_duration}),
                            block=False,
                        )
                    except Exception:
                        pass
            await self.push_frame(frame, direction)
            return

        if not isinstance(frame, TextFrame):
            await super().process_frame(frame, direction)
            if isinstance(frame, (StartFrame, EndFrame)):
                await self.push_frame(frame, direction)
