"""
Coqui TTS service using the local TTS package (coqui-tts).

Uses the VCTK multi-speaker model (tts_models/en/vctk/vits) which provides
~100 speaker IDs (p225, p226, …) matching the voices in coqui-ai-voices.json.

Install: pip install TTS
Model downloads automatically from HuggingFace on first use.

Same pipeline contract as KokoroTTSService / Qwen3TTSService.
"""

import asyncio
import logging
import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, TTSService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    TTSStartedFrame, TTSStoppedFrame, ErrorFrame, StartFrame, EndFrame,
    CancelFrame, InterruptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
    AudioRawFrame, OutputAudioRawFrame,
)
from distr.core.agent.services.llm.utils import clean_text_for_tts
from distr.core.agent.constants import SAMPLE_RATE_COQUI, DEFAULT_COQUI_VOICE

logger = logging.getLogger(__name__)

COQUI_MODEL = "tts_models/en/vctk/vits"

try:
    from TTS.api import TTS as _CoquiTTS
    COQUI_AVAILABLE = True
except ImportError:
    _CoquiTTS = None
    COQUI_AVAILABLE = False


class CoquiTTSService(TTSService):
    """Coqui TTS running locally via the TTS package (VCTK multi-speaker model).

    Args:
        voice_id:   Speaker ID, e.g. "p225", "p226". Defaults to DEFAULT_COQUI_VOICE.
        device:     "cuda", "mps", or "cpu". Auto-detected if None.
    """

    def __init__(
        self,
        voice_id: str = None,
        voice_name: str = None,
        device: str = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for CoquiTTSService")
        if not COQUI_AVAILABLE:
            raise ImportError(
                "TTS package is required for Coqui TTS. "
                "Install with: pip install TTS"
            )

        super().__init__(**kwargs)

        self.voice_id = (voice_id or DEFAULT_COQUI_VOICE).strip()
        self.voice_name = (voice_name or self.voice_id).strip()
        self.playback_speed = playback_speed
        self._stt_service = stt_service
        self.event_queue = event_queue
        self._speech_volume = max(0.0, min(1.0, speech_volume / 100.0))
        self._cancelled = False
        self._in_response_after_start = False
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

        # Resolve device
        if device:
            self._device = device
        else:
            try:
                import torch
                if torch.cuda.is_available():
                    self._device = "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    self._device = "mps"
                else:
                    self._device = "cpu"
            except ImportError:
                self._device = "cpu"

        use_gpu = self._device != "cpu"
        logger.info("Coqui TTS: loading model %s on %s", COQUI_MODEL, self._device)
        try:
            self._tts = _CoquiTTS(COQUI_MODEL, gpu=use_gpu)
            logger.info("Coqui TTS: model loaded (speaker=%s)", self.voice_id)
        except Exception as e:
            logger.error("Coqui TTS: failed to load model: %s", e)
            raise

    # ------------------------------------------------------------------
    # Public setters
    # ------------------------------------------------------------------

    def set_voice(self, voice_id: str):
        self.voice_id = voice_id.strip()
        self.voice_name = self.voice_id
        logger.info("Coqui TTS: speaker switched to '%s'", self.voice_id)

    def set_playback_speed(self, speed: float):
        self.playback_speed = speed

    def set_speech_volume(self, volume: int):
        self._speech_volume = max(0.0, min(1.0, volume / 100.0))

    def set_hands_free(self, enabled: bool):
        self._is_hands_free = enabled

    def set_ptt_active(self, active: bool):
        self._ptt_active = active

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_complete_sentences(self, text: str):
        import re
        sentences, remaining = [], text
        while True:
            m = re.search(r'([^\.!\?]*\w[^\.!\?]*[\.!\?]+)(\s+|$)', remaining)
            if not m:
                break
            s = m.group(1).strip()
            if s:
                sentences.append(s)
            remaining = remaining[len(m.group(0)):]
        return sentences, remaining

    def _is_duplicate_sentence(self, normalized: str) -> bool:
        if normalized in self._processed_sentences:
            return True
        if len(normalized) > 20:
            for processed in self._processed_sentences:
                if len(processed) > 20:
                    if normalized in processed:
                        return True
                    words1 = set(normalized.split())
                    words2 = set(processed.split())
                    if len(words1) > 4 and len(words2) > 4:
                        overlap = len(words1 & words2)
                        total_unique = len(words1 | words2)
                        if total_unique > 0 and overlap / total_unique > 0.9:
                            return True
        return False

    def _generate_audio(self, text: str):
        """Blocking call — runs in executor. Returns (audio_float32, sample_rate)."""
        wav = self._tts.tts(text=text, speaker=self.voice_id)
        audio = np.array(wav, dtype=np.float32)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        sr = self._tts.synthesizer.output_sample_rate
        return audio, sr

    # ------------------------------------------------------------------
    # Pipeline frame contract
    # ------------------------------------------------------------------

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
                None, lambda: self._generate_audio(text)
            )
            if self._cancelled:
                return

            if audio is not None and len(audio) > 0:
                audio_int16 = (audio * 32767).astype(np.int16)
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
            logger.error("Coqui TTS error: %s", e, exc_info=True)
            yield ErrorFrame(error=str(e))
        finally:
            yield TTSStoppedFrame()
            self._total_audio_duration += audio_duration_seconds

    async def process_frame(self, frame, direction):
        if isinstance(frame, CancelFrame):
            if self._in_response_after_start:
                logger.debug("Coqui TTS: CancelFrame ignored (stale)")
                return
            self._cancelled = True
            self._text_buffer = ""
            if self._is_hands_free:
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
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
                if getattr(cur, 'telegram_request', False):
                    self._current_telegram_request = True
                else:
                    for t in threading.enumerate():
                        if getattr(t, 'telegram_request', False):
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
                    logger.debug("Coqui TTS: skipping duplicate: '%s'", sentence[:50])
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
                        self.event_queue.put(("tts_stopped", {"duration": self._total_audio_duration}), block=False)
                    except Exception:
                        pass
            await self.push_frame(frame, direction)
            return

        if not isinstance(frame, TextFrame):
            await super().process_frame(frame, direction)
            if isinstance(frame, (StartFrame, EndFrame)):
                await self.push_frame(frame, direction)

    def get_sample_rate(self) -> int:
        return SAMPLE_RATE_COQUI
