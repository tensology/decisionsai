"""
VibeVoice-ASR STT service (local). Batch inference per utterance (PTT / hands-free stop).

Requires ``pip install -e`` the ``vibevoice`` package from GitHub and GPU recommended.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
import wave
from typing import Optional

import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE,
    AudioRawFrame,
    InputAudioRawFrame,
    TranscriptionFrame,
    ErrorFrame,
    UserStoppedSpeakingFrame,
    StartFrame,
    EndFrame,
    CancelFrame,
    InterruptionFrame,
    SpeakingStartedFrames,
    SpeakingStoppedFrames,
)
from distr.core.agent.services.stt.base import BaseSTTService

logger = logging.getLogger(__name__)


def _normalize_pcm_to_16k_mono_int16(audio: bytes, sample_rate: int, num_channels: int) -> bytes:
    """Mic transport may emit stereo or a non-16 kHz rate; VibeVoice ASR expects 16 kHz mono s16le."""
    if not audio:
        return audio
    sr = int(sample_rate) if sample_rate else 16000
    ch = int(num_channels) if num_channels else 1
    if sr == 16000 and ch == 1:
        return audio
    try:
        x = np.frombuffer(audio, dtype=np.int16).copy()
    except Exception:
        return audio
    if ch > 1:
        n = len(x) // ch
        if n * ch != len(x):
            logger.warning("VibeVoice ASR: dropping partial stereo frame (%d samples, ch=%d)", len(x), ch)
            return audio
        x = x.reshape(n, ch).mean(axis=1).astype(np.int16)
    if sr == 16000:
        return x.tobytes()
    f32 = x.astype(np.float32) / 32768.0
    n_out = max(1, int(round(len(f32) * 16000 / sr)))
    try:
        from scipy import signal

        out = signal.resample(f32, n_out)
    except Exception:
        idx = np.linspace(0, len(f32) - 1, n_out).astype(np.float64)
        il = np.floor(idx).astype(np.int64)
        ir = np.minimum(il + 1, len(f32) - 1)
        w = idx - il
        out = f32[il] * (1.0 - w) + f32[ir] * w
    pcm = np.clip(out, -1.0, 1.0)
    return (pcm * 32767.0).astype(np.int16).tobytes()


class VibeVoiceAsrSTTService(BaseSTTService):
    """VibeVoice ASR: batch transcription on accumulated 16 kHz PCM."""

    def __init__(self, event_queue=None, is_hands_free: bool = False, **kwargs):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for VibeVoiceAsrSTTService")
        super().__init__(event_queue=event_queue, is_hands_free=is_hands_free, **kwargs)
        self._min_audio_duration_ms = 1000
        logger.info(
            "VibeVoiceAsrSTTService initialized (lazy model load on first transcription), "
            "is_hands_free=%s",
            is_hands_free,
        )

    async def _process_ptt_buffer_immediate(self, direction):
        chunk_count = len(self._ptt_buffer_accumulator)
        total_bytes = sum(len(c) for c in self._ptt_buffer_accumulator) if self._ptt_buffer_accumulator else 0
        pre_duration_ms = (total_bytes / (16000 * 2)) * 1000 if total_bytes > 0 else 0
        logger.info(
            "STT: Processing PTT buffer (%d chunks, ~%.0fms) [VibeVoice ASR]",
            chunk_count,
            pre_duration_ms,
        )

        if not self._ptt_buffer_accumulator:
            logger.warning("STT: _process_ptt_buffer_immediate() called but buffer is empty")
            return

        audio_bytes = b"".join(self._ptt_buffer_accumulator)
        self._ptt_buffer_accumulator = []

        if len(audio_bytes) > 0:
            sample_rate = 16000
            bytes_per_second = sample_rate * 2
            duration_ms = (len(audio_bytes) / bytes_per_second) * 1000
            if duration_ms < 1000:
                silence_needed_ms = 1000 - duration_ms
                silence_bytes = int((silence_needed_ms / 1000) * bytes_per_second)
                audio_bytes = audio_bytes + b"\x00" * silence_bytes

            try:
                stop_frame = UserStoppedSpeakingFrame()
                await self.push_frame(stop_frame, direction)
            except Exception as e:
                logger.error("STT: Error sending UserStoppedSpeakingFrame: %s", e)

            frame_count = 0
            try:
                async for result_frame in self.run_stt(audio_bytes):
                    if self._stt_cancelled:
                        break
                    frame_count += 1
                    try:
                        await self.push_frame(result_frame, direction)
                    except Exception as ex:
                        logger.error("STT: Error pushing frame: %s", ex, exc_info=True)
                if frame_count == 0 and not self._is_hands_free:
                    try:
                        await self.push_frame(
                            TranscriptionFrame(text="", user_id="", timestamp=time.time()),
                            direction,
                        )
                    except Exception as ex:
                        logger.error("STT: Error sending empty TranscriptionFrame: %s", ex)
            except Exception as e:
                logger.error("STT: Error in run_stt() loop: %s", e, exc_info=True)

    async def run_stt(self, audio: bytes):
        self._stt_cancelled = False
        sample_rate = 16000
        bytes_per_second = sample_rate * 2
        duration_ms = (len(audio) / bytes_per_second) * 1000

        if duration_ms < self._min_audio_duration_ms:
            silence_needed_ms = self._min_audio_duration_ms - duration_ms
            silence_bytes = int((silence_needed_ms / 1000) * bytes_per_second)
            audio = audio + b"\x00" * silence_bytes

        if self._stt_cancelled:
            return

        loop = asyncio.get_running_loop()

        def _write_wav_and_transcribe() -> str:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            buf.seek(0)
            import tempfile

            fd, path = tempfile.mkstemp(suffix=".wav", prefix="vvasr_")
            os.close(fd)
            try:
                with open(path, "wb") as out:
                    out.write(buf.getvalue())
                from distr.core.agent.services.tts.vibevoice_asr_inference import transcribe_audio_file

                return transcribe_audio_file(path, max_new_tokens=8192)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

        try:
            text = await loop.run_in_executor(None, _write_wav_and_transcribe)
            if self._stt_cancelled:
                return
            text = (text or "").strip()
            if text:
                logger.debug("[STT] PICKED UP: %s", text)
                logger.info("TRANSCRIPTION [VibeVoice ASR]: '%s'", text)
                if self._is_meaningful_text(text):
                    yield TranscriptionFrame(text=text, user_id="", timestamp=time.time())
                else:
                    logger.warning("STT Rejected (artifact/filler): '%s'", text)
            else:
                logger.warning("STT: run_stt() produced empty text")
        except Exception as e:
            logger.error("VibeVoice ASR transcription error: %s", e, exc_info=True)
            yield ErrorFrame(error=str(e))

    async def _on_speaking_stopped(self, frame, direction):
        if not self._audio_buffer:
            return
        audio_bytes = b"".join(self._audio_buffer)
        self._audio_buffer = []
        duration_ms = (len(audio_bytes) / (16000 * 2)) * 1000
        logger.debug(
            "STT: Processing hands-free audio: %.0fms (%d bytes) [VibeVoice ASR]",
            duration_ms,
            len(audio_bytes),
        )
        async for result_frame in self.run_stt(audio_bytes):
            if self._stt_cancelled:
                break
            await self.push_frame(result_frame, direction)

    async def process_frame(self, frame, direction):
        if self._should_filter_interruption(frame):
            return
        await self._handle_pending_interruption(direction)
        self._store_pipeline_context(direction)

        if self._pending_ptt_process and self._ptt_buffer_accumulator:
            self._pending_ptt_process = False
            await self._process_ptt_buffer_immediate(direction)

        if await self._handle_speaking_started(frame, direction):
            return
        if await self._handle_speaking_stopped(frame, direction):
            return

        if isinstance(frame, (AudioRawFrame, InputAudioRawFrame)):
            sr = getattr(frame, "sample_rate", 16000) or 16000
            ch = getattr(frame, "num_channels", 1) or 1
            pcm = _normalize_pcm_to_16k_mono_int16(frame.audio, sr, ch)
            if sr != 16000 or ch != 1:
                logger.debug("VibeVoice ASR: normalized mic chunk sr=%s ch=%s -> 16k mono", sr, ch)

            if self._ptt_active:
                self._ptt_buffer_accumulator.append(pcm)
            elif not self._ptt_active and self._ptt_buffer_accumulator:
                logger.warning("STT: PTT buffer exists but PTT inactive - processing as fallback")
                await self._process_ptt_buffer_immediate(direction)
            elif (self._is_hands_free or self._is_dictating) and self._user_speaking:
                self._audio_buffer.append(pcm)
                await self._check_continuous_speech_interruption(pcm, direction)
            elif self._is_hands_free or self._is_dictating:
                self._pre_buffer.append(pcm)
                await self._check_pending_bargein(pcm, direction)
            return

        await super().process_frame(frame, direction)

        if isinstance(frame, (StartFrame, EndFrame, CancelFrame, InterruptionFrame)):
            if isinstance(frame, InterruptionFrame):
                logger.debug(
                    "STT: Pushing InterruptionFrame downstream (hands_free=%s, ptt_active=%s)",
                    self._is_hands_free,
                    self._ptt_active,
                )
            await self.push_frame(frame, direction)
        elif isinstance(frame, TranscriptionFrame):
            logger.debug("STT: Passing through TranscriptionFrame: '%s'", frame.text)
            await self.push_frame(frame, direction)

    def get_sample_rate(self) -> int:
        return 16000

    def transcribe_file(self, audio_file_path: str) -> Optional[str]:
        try:
            from distr.core.agent.services.tts.vibevoice_asr_inference import transcribe_audio_file

            logger.info("VibeVoiceAsrSTTService: transcribing file %s", audio_file_path)
            text = transcribe_audio_file(os.path.abspath(audio_file_path), max_new_tokens=8192)
            text = (text or "").strip()
            return text if text else None
        except Exception as e:
            logger.error("VibeVoiceAsrSTTService: transcribe_file failed: %s", e, exc_info=True)
            return None
