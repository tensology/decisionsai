"""
Qwen3-TTS service using the local qwen-tts package (no Replicate/cloud required).

Install: pip install qwen-tts
Models are downloaded automatically from HuggingFace on first use, or you can
point model_name at a local directory.

Same pipeline contract as KokoroTTSService: run_tts yields
TTSStartedFrame -> AudioRawFrame chunks -> TTSStoppedFrame.
"""

import asyncio
import logging
import os
import numpy as np

from distr.core.agent.libs import (
    PIPECAT_AVAILABLE, TTSService,
    TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
    TTSStartedFrame, TTSStoppedFrame, ErrorFrame, StartFrame, EndFrame,
    CancelFrame, InterruptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
    AudioRawFrame, OutputAudioRawFrame,
)
from distr.core.agent.services.llm.utils import clean_text_for_tts
from distr.core.agent.constants import SAMPLE_RATE_QWEN3

logger = logging.getLogger(__name__)

try:
    from qwen_tts import Qwen3TTSModel as _Qwen3TTSModel
    QWEN3_LOCAL_AVAILABLE = True
except ImportError:
    _Qwen3TTSModel = None
    QWEN3_LOCAL_AVAILABLE = False

# Default model — 0.6B is faster; swap to 1.7B for higher quality
DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"


class Qwen3TTSService(TTSService):
    """Qwen3-TTS running locally via the qwen-tts package.

    Args:
        voice_id:   Speaker name (e.g. "Aiden", "Ryan", "Vivian"). Case-insensitive.
        model_name: HuggingFace model id or local path. Defaults to 0.6B-CustomVoice.
        device:     "cuda", "mps", or "cpu". Auto-detected if None.
    """

    def __init__(
        self,
        voice_id: str = "Aiden",
        voice_name: str = None,
        model_name: str = None,
        device: str = None,
        stt_service=None,
        playback_speed: float = 1.0,
        event_queue=None,
        speech_volume: int = 100,
        **kwargs,
    ):
        if not PIPECAT_AVAILABLE:
            raise ImportError("Pipecat is required for Qwen3TTSService")
        if not QWEN3_LOCAL_AVAILABLE:
            raise ImportError(
                "qwen-tts package is required for local Qwen3-TTS. "
                "Install with: pip install qwen-tts"
            )

        super().__init__(**kwargs)

        self.voice_id = (voice_id or "Aiden").strip()
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
                    self._device = "cuda:0"
                else:
                    # MPS crashes on Qwen3-TTS grouped-query attention (16 Q heads vs 8 KV heads)
                    # — MPSGraph matmul can't handle mismatched head counts. Force CPU.
                    self._device = "cpu"
            except ImportError:
                self._device = "cpu"

        self._model_name = model_name or DEFAULT_MODEL

        # Prefer local model in distr/core/agent/models/qwen3-tts/ (no HF cache validation)
        _local_model_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            '..', '..', '..', 'models', 'qwen3-tts'
        )
        _local_model_dir = os.path.abspath(_local_model_dir)
        if os.path.isfile(os.path.join(_local_model_dir, 'config.json')):
            self._model_name = _local_model_dir

        # Load model eagerly so first inference isn't slow
        logger.info("Qwen3-TTS: loading model %s on %s", self._model_name, self._device)
        try:
            import torch
            # MPS (Apple Silicon) matmul crashes with bfloat16 on grouped-query attention
            # (different num_heads vs num_kv_heads triggers "incompatible dimensions" in MPSGraph).
            # Use bfloat16 only on CUDA; float32 everywhere else.
            dtype = torch.bfloat16 if self._device.startswith("cuda") else torch.float32
            self._model = _Qwen3TTSModel.from_pretrained(
                self._model_name,
                device_map=self._device,
                dtype=dtype,
            )
            logger.info("Qwen3-TTS: model loaded (voice=%s)", self.voice_id)
        except Exception as e:
            logger.error("Qwen3-TTS: failed to load model: %s", e)
            raise

    # ------------------------------------------------------------------
    # Public setters (hot-swap compatible)
    # ------------------------------------------------------------------

    def set_voice(self, voice_id: str):
        self.voice_id = voice_id.strip()
        self.voice_name = self.voice_id
        logger.info("Qwen3-TTS: voice switched to '%s'", self.voice_id)

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
            # Require at least one word character before the terminal punctuation to avoid
            # matching single-char fragments like "g." from mid-stream tokens (e.g. "ing.")
            m = re.search(r'([^\.!\?]*\w[^\.!\?]*[\.!\?]+)(\s+|$)', remaining)
            if not m:
                break
            s = m.group(1).strip()
            if s:
                sentences.append(s)
            remaining = remaining[len(m.group(0)):]
        return sentences, remaining

    def _is_duplicate_sentence(self, normalized: str) -> bool:
        """Check for exact match, subset overlap, or high word-overlap (mirrors Kokoro logic)."""
        if normalized in self._processed_sentences:
            return True
        if len(normalized) > 20:
            for processed in self._processed_sentences:
                if len(processed) > 20:
                    # Current is a subset of an already-processed longer sentence
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
        # Custom voice cloning: voice ID is "custom_<db_id>"
        if self.voice_id.startswith("custom_"):
            wavs, sr = self._generate_clone_audio(text)
        else:
            wavs, sr = self._model.generate_custom_voice(
                text=text,
                language="Auto",
                speaker=self.voice_id,
            )
        audio = wavs[0]  # numpy float32 array
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        return audio, sr

    def _generate_clone_audio(self, text: str):
        """Generate speech using a custom cloned voice via reference audio.

        Uses a separate Base model because the CustomVoice model does not
        support generate_voice_clone().
        Uses a cached voice prompt (.pt) when available to skip re-processing audio.
        """
        db_id_str = self.voice_id.split("_", 1)[1]
        try:
            db_id = int(db_id_str)
        except ValueError:
            raise ValueError(f"Invalid custom voice ID: {self.voice_id}")

        from distr.core.db import get_session, CustomVoice
        session = get_session()
        try:
            cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
            if not cv or cv.status != "ready":
                raise ValueError(f"Custom voice {db_id} not available")
            audio_dir = cv.audio_dir
            ref_text = cv.system_prompt or ""
        finally:
            session.close()

        if not audio_dir or not os.path.isdir(audio_dir):
            raise ValueError(f"Audio directory not found for custom voice {db_id}")

        base_model = self._get_base_model()

        # Try cached voice prompt first (instant) — falls back to raw audio (slow)
        cached_prompt = self._get_cached_voice_prompt(audio_dir, base_model)
        if cached_prompt is not None:
            logger.info("Qwen3-TTS: using cached voice prompt for custom voice %d", db_id)
            return base_model.generate_voice_clone(
                text=text,
                language="Auto",
                voice_clone_prompt=[cached_prompt],
            )

        # Fallback: process from raw audio
        ref_files = [
            os.path.join(audio_dir, f)
            for f in sorted(os.listdir(audio_dir))
            if f.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm'))
        ]
        if not ref_files:
            raise ValueError(f"No reference audio for custom voice {db_id}")

        logger.info("Qwen3-TTS: generating clone from raw audio (no cache) for voice %d", db_id)
        return base_model.generate_voice_clone(
            text=text,
            language="Auto",
            ref_audio=ref_files[0],
            ref_text=ref_text if ref_text else None,
        )

    @staticmethod
    def _get_cached_voice_prompt(audio_dir: str, base_model):
        """Load a cached VoiceClonePromptItem from disk, or return None."""
        cache_path = os.path.join(audio_dir, "voice_prompt.pt")
        if not os.path.isfile(cache_path):
            return None
        try:
            import torch
            from qwen_tts import VoiceClonePromptItem
            data = torch.load(cache_path, map_location=base_model.device, weights_only=True)
            item = VoiceClonePromptItem(
                ref_code=data["ref_code"],
                ref_spk_embedding=data["ref_spk_embedding"].to(base_model.device),
                x_vector_only_mode=bool(data.get("x_vector_only_mode", False)),
                icl_mode=bool(data.get("icl_mode", True)),
                ref_text=data.get("ref_text"),
            )
            logger.info("Loaded cached voice prompt from %s", cache_path)
            return item
        except Exception as e:
            logger.warning("Failed to load cached voice prompt %s: %s", cache_path, e)
            return None

    def _get_base_model(self):
        """Lazily load and cache the Qwen3-TTS Base model for voice cloning."""
        if not hasattr(self, '_base_model') or self._base_model is None:
            import torch
            # Prefer local base model in distr/core/agent/models/qwen3-tts-base/
            _local_base_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                '..', '..', '..', 'models', 'qwen3-tts-base'
            )
            _local_base_dir = os.path.abspath(_local_base_dir)
            if os.path.isfile(os.path.join(_local_base_dir, 'config.json')):
                base_model_name = _local_base_dir
            else:
                base_model_name = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
            dtype = torch.bfloat16 if self._device.startswith("cuda") else torch.float32
            logger.info("Qwen3-TTS: loading Base model %s on %s for voice cloning...", base_model_name, self._device)
            self._base_model = _Qwen3TTSModel.from_pretrained(
                base_model_name,
                device_map=self._device,
                dtype=dtype,
            )
            logger.info("Qwen3-TTS: Base model loaded for voice cloning.")
        return self._base_model

    # ------------------------------------------------------------------
    # Pipeline frame contract
    # ------------------------------------------------------------------

    async def run_tts(self, text: str):
        """Yield TTSStartedFrame -> AudioRawFrame chunks -> TTSStoppedFrame."""
        if self._cancelled:
            return

        # Strip markdown / emojis before synthesis
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

                    # Emit tts_started on first audio frame (desktop only)
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

                    # Apply volume
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
            logger.error("Qwen3-TTS error: %s", e, exc_info=True)
            yield ErrorFrame(error=str(e))
        finally:
            yield TTSStoppedFrame()
            self._total_audio_duration += audio_duration_seconds

    async def process_frame(self, frame, direction):
        if isinstance(frame, CancelFrame):
            # Ignore stale CancelFrames that arrive after a new response has started
            if self._in_response_after_start:
                logger.debug("Qwen3-TTS: CancelFrame ignored (stale - already in new response)")
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

            # Detect telegram context from thread locals
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
                    logger.debug("Qwen3-TTS: skipping duplicate sentence: '%s'", sentence[:50])
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
        return SAMPLE_RATE_QWEN3
