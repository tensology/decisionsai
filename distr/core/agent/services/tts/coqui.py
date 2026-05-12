"""
Coqui TTS service using the local TTS package (coqui-tts).

Uses the VCTK multi-speaker model (tts_models/en/vctk/vits) which provides
~100 speaker IDs (p225, p226, …) matching the voices in coqui-ai-voices.json.

Install: pip install TTS
Model downloads automatically from HuggingFace on first use.

Same pipeline contract as KokoroTTSService.
"""

import asyncio
import logging
import os
import time
import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, TTSService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    TTSStartedFrame, TTSStoppedFrame, ErrorFrame, StartFrame, EndFrame,
    CancelFrame, InterruptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
    AudioRawFrame, OutputAudioRawFrame,
)
from distr.core.agent.services.llm.utils import clean_text_for_tts
from distr.core.agent.services.tts.sentence_split import (
    extract_complete_sentences,
    is_redundant_sentence,
)
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
        self._llm_response_started_at = 0  # Timestamp of last LLMFullResponseStartFrame; used to ignore stale InterruptionFrames
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

        use_gpu = self._device == "cuda"
        logger.info("Coqui TTS: loading model %s on %s (gpu=%s)", COQUI_MODEL, self._device, use_gpu)
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
        return extract_complete_sentences(text)

    def _is_duplicate_sentence(self, normalized: str) -> bool:
        return is_redundant_sentence(normalized, self._processed_sentences)

    def _generate_audio(self, text: str):
        """Blocking call — runs in executor. Returns (audio_float32, sample_rate)."""
        # Custom voice — use XTTS v2 with reference audio (never fall back to VCTK silently).
        if self.voice_id and self.voice_id.startswith("custom_"):
            from distr.core.agent.services.tts.coqui_descriptor import CoquiDescriptor

            ref_path = CoquiDescriptor._resolve_reference_audio(self.voice_id)
            if not ref_path or not os.path.isfile(ref_path):
                raise ValueError(CoquiDescriptor._missing_clone_message(self.voice_id))
            ref_path = os.path.abspath(ref_path)
            if not hasattr(self, '_xtts') or self._xtts is None:
                # Coqui TTS `gpu=True` means CUDA only (asserts torch.cuda.is_available()).
                # MPS/CPU must use gpu=False — same rule as VCTK init above.
                xtts_gpu = self._device == "cuda"
                if self._device == "mps":
                    logger.info(
                        "Coqui TTS: XTTS v2 on Apple Silicon — Coqui API has no MPS flag; "
                        "using CPU inference for XTTS (slower than CUDA)."
                    )
                logger.info("Coqui TTS: loading XTTS v2 for voice cloning (gpu=%s)", xtts_gpu)
                self._xtts = _CoquiTTS(
                    "tts_models/multilingual/multi-dataset/xtts_v2",
                    gpu=xtts_gpu,
                )
            # speaker=None avoids idiap default speaker_name="" triggering clone cache writes.
            clamped_speed = max(0.5, min(2.0, float(self.playback_speed)))
            try:
                wav = self._xtts.tts(
                    text=text,
                    speaker=None,
                    speaker_wav=ref_path,
                    language="en",
                    split_sentences=False,
                    speed=clamped_speed,
                )
            except TypeError:
                wav = self._xtts.tts(
                    text=text,
                    speaker=None,
                    speaker_wav=ref_path,
                    language="en",
                    split_sentences=False,
                )
            if hasattr(wav, "detach"):
                wav = wav.detach().cpu().numpy()
            elif hasattr(wav, "cpu"):
                wav = wav.cpu().numpy()
            audio = np.asarray(wav, dtype=np.float32).reshape(-1)
            if audio.size and np.max(np.abs(audio)) > 1.0:
                np.clip(audio, -1.0, 1.0, out=audio)
            sr = self._xtts.synthesizer.output_sample_rate
            return audio, sr

        # Built-in VCTK speaker
        from distr.core.agent.services.tts.coqui_descriptor import DEFAULT_COQUI_VOICE

        speaker = self.voice_id
        clamped_speed = max(0.5, min(2.0, float(self.playback_speed)))
        try:
            try:
                wav = self._tts.tts(text=text, speaker=speaker, speed=clamped_speed)
            except TypeError:
                wav = self._tts.tts(text=text, speaker=speaker)
        except KeyError as e:
            logger.warning(
                "Coqui TTS: synthesize failed (%s); retrying with %s",
                e,
                DEFAULT_COQUI_VOICE,
            )
            try:
                wav = self._tts.tts(text=text, speaker=DEFAULT_COQUI_VOICE, speed=clamped_speed)
            except TypeError:
                wav = self._tts.tts(text=text, speaker=DEFAULT_COQUI_VOICE)
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
            # Guard: ignore stale InterruptionFrames that arrive after the current response
            # has already started (e.g. from a pre-send interrupt_tts command that raced).
            now = time.monotonic()
            if self._llm_response_started_at > 0 and (now - self._llm_response_started_at) < 0.3:
                logger.debug(
                    "Coqui TTS: Ignoring stale InterruptionFrame (%.0fms since LLMFullResponseStartFrame)",
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
                    if (
                        is_audio
                        or isinstance(audio_frame, (TTSStartedFrame, TTSStoppedFrame, ErrorFrame))
                    ):
                        await self.push_frame(audio_frame, direction)
            return

        if isinstance(frame, LLMFullResponseStartFrame):
            self._cancelled = False
            self._in_response_after_start = True
            self._llm_response_started_at = time.monotonic()  # Timestamp to ignore stale InterruptionFrames
            self._text_buffer = ""
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
            self._llm_response_started_at = 0  # Reset so future InterruptionFrames are not treated as stale
            if self._text_buffer.strip() and not self._cancelled:
                text = self._text_buffer.strip()
                self._text_buffer = ""
                async for audio_frame in self.run_tts(text):
                    if self._cancelled:
                        break
                    is_audio = isinstance(audio_frame, AudioRawFrame) or (
                        OutputAudioRawFrame and isinstance(audio_frame, OutputAudioRawFrame)
                    )
                    if is_audio or isinstance(audio_frame, (TTSStartedFrame, TTSStoppedFrame)):
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
