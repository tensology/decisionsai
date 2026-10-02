"""Acoustic Echo Cancellation (AEC) filter for Pipecat pipelines.

Production path uses WebRTC AEC3 via ``pywebrtc-audio`` (``WebRTCAECFilter``).
The older homemade NLMS implementation lives in
``echo_canceller_nlms_quarantine.py`` and is available only as a deprecated
fallback for tests / emergency use.

Architecture:
  - The output transport pushes audio frames into a shared ring buffer
    (the "reference signal" — what's being played through the speaker).
  - This filter, plugged into Pipecat's audio_in_filter slot, reads the
    reference at a calibrated delay, resamples it to the mic rate, and runs
    WebRTC AEC3 so the VAD / STT see cleaned near-end audio.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

from pipecat.audio.filters.base_audio_filter import BaseAudioFilter

logger = logging.getLogger(__name__)

# Default speaker→mic path hint when settings leave aec_reference_delay_ms at 0.
DEFAULT_AEC_STREAM_DELAY_MS = 40.0


class ReferenceBuffer:
    """Thread-safe ring buffer for the speaker reference signal.

    The output transport writes to this from the pipeline's async loop,
    while the input filter reads from it on the audio-in callback thread.
    """

    def __init__(self, max_duration_secs: float = 2.0, sample_rate: int = 16000):
        self._lock = threading.Lock()
        self._sample_rate = sample_rate
        self._max_samples = int(max_duration_secs * sample_rate)
        self._buf = np.zeros(self._max_samples, dtype=np.float32)
        self._write_pos = 0  # monotonic write cursor
        self._active = False  # True while TTS is playing

    def set_sample_rate(self, sample_rate: int):
        with self._lock:
            self._sample_rate = sample_rate
            self._max_samples = int(2.0 * sample_rate)
            self._buf = np.zeros(self._max_samples, dtype=np.float32)
            self._write_pos = 0

    def push(self, audio_f32: np.ndarray):
        """Push speaker audio into the ring buffer."""
        with self._lock:
            n = len(audio_f32)
            if n == 0:
                return
            start = self._write_pos % self._max_samples
            if start + n <= self._max_samples:
                self._buf[start:start + n] = audio_f32
            else:
                first = self._max_samples - start
                self._buf[start:] = audio_f32[:first]
                self._buf[:n - first] = audio_f32[first:]
            self._write_pos += n

    def pull(self, num_samples: int, delay_samples: int = 0) -> np.ndarray:
        """Pull a reference window, optionally shifted into the past.

        ``delay_samples`` models driver, hardware, and acoustic propagation
        delay. The default remains the previous most-recent behavior.
        """
        with self._lock:
            if self._write_pos == 0 or num_samples <= 0:
                return np.zeros(num_samples, dtype=np.float32)
            end_abs = max(0, self._write_pos - max(0, int(delay_samples)))
            start_abs = end_abs - num_samples
            available_start = max(0, self._write_pos - self._max_samples)
            output = np.zeros(num_samples, dtype=np.float32)
            read_start = max(start_abs, available_start)
            if read_start >= end_abs:
                return output
            count = end_abs - read_start
            destination = num_samples - count
            for offset in range(count):
                output[destination + offset] = self._buf[(read_start + offset) % self._max_samples]
            return output

    def set_active(self, active: bool):
        with self._lock:
            was_active = self._active
            self._active = active
            # Track transitions so the filter can reset on inactive→active
            if active and not was_active:
                self._just_activated = True
            # TTSStartedFrame can be emitted once per sentence. Keep the
            # activation epoch stable while one response is playing so a
            # sentence boundary does not restart the barge-in grace window.
            if active and not was_active:
                self._activated_at = time.time()

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def seconds_since_activation(self) -> float:
        """Return seconds elapsed since the last inactive→active transition.

        Returns a large value if never activated so callers can skip the
        grace-period check without special-casing.
        """
        with self._lock:
            t = getattr(self, '_activated_at', None)
            if t is None:
                return 999.0
            return time.time() - t

    def consume_activation(self) -> bool:
        """Return True (once) if the buffer just transitioned inactive→active."""
        with self._lock:
            if getattr(self, '_just_activated', False):
                self._just_activated = False
                return True
            return False


class WebRTCAECFilter(BaseAudioFilter):
    """Pipecat audio_in_filter backed by WebRTC AEC3 (``pywebrtc-audio``).

    Mirrors the NLMS filter's ReferenceBuffer contract so the output transport
    keeps pushing played audio unchanged. Near-end is mic PCM16 at the input
    sample rate (16 kHz); far-end is pulled from the reference buffer at the
    playback rate (typically 44.1 kHz), delayed by ``reference_delay_ms``, then
    resampled to match near before ``AudioProcessor.process(near, far)``.
    """

    def __init__(
        self,
        reference_buffer: ReferenceBuffer,
        output_sample_rate: int = 44100,
        reference_delay_ms: float = DEFAULT_AEC_STREAM_DELAY_MS,
        *,
        noise_suppression: bool = True,
        high_pass_filter: bool = True,
        auto_gain_control: bool = False,
        ns_level: int = 1,
        persist_delay_callback=None,
    ):
        self._ref_buf = reference_buffer
        self._output_sample_rate = int(output_sample_rate)
        self._input_sample_rate = 16000
        self._reference_delay_ms = self._clamp_delay_ms(reference_delay_ms)
        self._noise_suppression = bool(noise_suppression)
        self._high_pass_filter = bool(high_pass_filter)
        self._auto_gain_control = bool(auto_gain_control)
        self._ns_level = int(ns_level)
        self._persist_delay_callback = persist_delay_callback
        self._processor = None
        self._enabled = True
        self._last_metrics: dict[str, float | int] = {}

    @staticmethod
    def _clamp_delay_ms(delay_ms: float) -> float:
        return max(0.0, min(500.0, float(delay_ms)))

    def _ensure_processor(self, sample_rate: int):
        if self._processor is not None and self._input_sample_rate == sample_rate:
            return
        try:
            from pywebrtc_audio import AudioProcessor
        except ImportError as exc:
            raise ImportError(
                "pywebrtc-audio is required for WebRTCAECFilter. "
                "Install with: pip install pywebrtc-audio"
            ) from exc
        self._input_sample_rate = int(sample_rate)
        self._processor = AudioProcessor(
            sample_rate=self._input_sample_rate,
            num_channels=1,
            echo_cancellation=True,
            noise_suppression=self._noise_suppression,
            high_pass_filter=self._high_pass_filter,
            auto_gain_control=self._auto_gain_control,
            ns_level=self._ns_level,
            stream_delay_ms=int(round(self._reference_delay_ms)),
        )

    async def start(self, sample_rate: int):
        self._ensure_processor(sample_rate)
        logger.info(
            "WebRTC AEC started: input_sr=%s, output_sr=%s, stream_delay_ms=%.1f, "
            "ns=%s, hpf=%s, agc=%s",
            sample_rate,
            self._output_sample_rate,
            self._reference_delay_ms,
            self._noise_suppression,
            self._high_pass_filter,
            self._auto_gain_control,
        )

    def set_reference_delay_ms(self, delay_ms: float, *, persist: bool = False) -> None:
        """Update ReferenceBuffer pull delay and AudioProcessor.stream_delay_ms."""
        self._reference_delay_ms = self._clamp_delay_ms(delay_ms)
        if self._processor is not None:
            self._processor.stream_delay_ms = int(round(self._reference_delay_ms))
        if persist and self._persist_delay_callback is not None:
            try:
                self._persist_delay_callback(self._reference_delay_ms)
            except Exception as exc:
                logger.warning("WebRTC AEC: failed to persist delay: %s", exc)

    @property
    def reference_delay_ms(self) -> float:
        return self._reference_delay_ms

    @property
    def stream_delay_ms(self) -> int:
        if self._processor is not None:
            return int(self._processor.stream_delay_ms)
        return int(round(self._reference_delay_ms))

    @property
    def last_metrics(self) -> dict[str, float | int]:
        return dict(self._last_metrics)

    async def stop(self):
        logger.info("WebRTC AEC stopped")

    async def process_frame(self, frame):
        """Handle runtime control frames (enable/disable)."""
        pass

    async def filter(self, audio: bytes) -> bytes:
        """Cancel speaker echo from mic audio via WebRTC AEC3."""
        if not self._enabled or not self._ref_buf.is_active:
            return audio

        if self._processor is None:
            # start() should have run; fall back so a late attach still works.
            self._ensure_processor(self._input_sample_rate)

        if self._ref_buf.consume_activation():
            self._processor.reset()
            self._processor.stream_delay_ms = int(round(self._reference_delay_ms))
            logger.debug("WebRTC AEC: processor reset (new TTS session)")

        near = np.frombuffer(audio, dtype=np.int16)
        if near.size == 0:
            return audio
        # AudioProcessor requires a writable contiguous array.
        near = np.ascontiguousarray(near)

        delay_ms = self._reference_delay_ms
        delay_samples = int(round(delay_ms * self._output_sample_rate / 1000.0))
        num_samples = int(near.shape[0])

        if self._output_sample_rate != self._input_sample_rate:
            ref_samples_needed = max(
                1,
                int(round(num_samples * self._output_sample_rate / float(self._input_sample_rate))),
            )
            ref_raw = self._ref_buf.pull(ref_samples_needed, delay_samples)
            ref = self._resample(ref_raw, num_samples)
        else:
            ref = self._ref_buf.pull(num_samples, delay_samples)

        far = np.clip(np.round(ref * 32767.0), -32768, 32767).astype(np.int16)
        far = self._match_length(far, num_samples)

        # Keep processor stream_delay_ms aligned with the calibrated setting.
        # Far is already delay-aligned via ReferenceBuffer.pull; AEC3 still
        # accepts the same value as a delay-estimator hint.
        desired_delay = int(round(delay_ms))
        if int(self._processor.stream_delay_ms) != desired_delay:
            self._processor.stream_delay_ms = desired_delay

        try:
            cleaned = self._processor.process(near, far)
        except ValueError as exc:
            # Length mismatch or empty frame — pass through rather than drop mic.
            logger.debug("WebRTC AEC: process skipped (%s)", exc)
            return audio

        cleaned = np.ascontiguousarray(cleaned, dtype=np.int16)
        if cleaned.shape[0] != num_samples:
            cleaned = self._match_length(cleaned, num_samples)

        mic_f = near.astype(np.float32) / 32768.0
        out_f = cleaned.astype(np.float32) / 32768.0
        ref_rms = float(np.sqrt(np.mean(ref * ref))) if ref.size else 0.0
        mic_rms = float(np.sqrt(np.mean(mic_f * mic_f))) if mic_f.size else 0.0
        out_rms = float(np.sqrt(np.mean(out_f * out_f))) if out_f.size else 0.0
        erle_db = 20.0 * np.log10(max(mic_rms, 1e-8) / max(out_rms, 1e-8))
        speech_prob = 0.0
        try:
            speech_prob = float(self._processor.speech_probability)
        except Exception:
            pass
        self._last_metrics = {
            "input_rms": mic_rms,
            "residual_rms": out_rms,
            "reference_rms": ref_rms,
            "erle_db": float(erle_db),
            "clipped_samples": int(np.count_nonzero(np.abs(out_f) > 0.99)),
            "reference_delay_ms": delay_ms,
            "stream_delay_ms": desired_delay,
            "speech_probability": speech_prob,
            "reference_correlation": 0.0,
        }
        return cleaned.tobytes()

    @staticmethod
    def _match_length(data: np.ndarray, target: int) -> np.ndarray:
        if data.shape[0] == target:
            return data
        if data.shape[0] > target:
            return data[:target]
        if data.shape[0] == 0:
            return np.zeros(target, dtype=np.int16)
        return np.pad(data, (0, target - data.shape[0]))

    @staticmethod
    def _resample(data: np.ndarray, target_length: int) -> np.ndarray:
        """Simple linear interpolation resampling (float32)."""
        if len(data) == target_length:
            return data
        if len(data) == 0:
            return np.zeros(target_length, dtype=np.float32)
        indices = np.linspace(0, len(data) - 1, target_length)
        return np.interp(indices, np.arange(len(data)), data).astype(np.float32)


def resolve_aec_reference_delay_ms(raw_delay_ms: Any) -> float:
    """Map settings value to a concrete delay; 0/None → default 40ms."""
    try:
        value = float(raw_delay_ms)
    except (TypeError, ValueError):
        return DEFAULT_AEC_STREAM_DELAY_MS
    if value <= 0.0:
        return DEFAULT_AEC_STREAM_DELAY_MS
    return max(0.0, min(500.0, value))


def __getattr__(name: str):
    """Lazy deprecated re-export of NLMSEchoCanceller for older imports/tests."""
    if name == "NLMSEchoCanceller":
        from distr.core.audio.echo_canceller_nlms_quarantine import NLMSEchoCanceller
        return NLMSEchoCanceller
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
