"""LiveKit WebRTC APM echo canceller — transport-level (keeps PyAudio).

Drop-in alternative to ``WebRTCAECFilter`` for Pipecat's ``audio_in_filter``
slot. Instead of pulling a pre-delayed reference out of a ring buffer (the old
engine, which guessed the delay), this feeds the *real rendered audio* into the
same WebRTC AudioProcessingModule as the microphone, in 10 ms frames, exactly
like ``livekit.rtc.MediaDevices`` does. The APM then estimates/aligns the delay
itself from ``set_stream_delay_ms``.

Why this fixes self-interruption: the old engine applied a guessed delay to the
reference *and* told the APM the same delay (double-counted), so the far-end was
misaligned and echo leaked at speech level. Here the far-end is the freshly
rendered audio and the delay is a single hint.

Public surface mirrors ``WebRTCAECFilter``:
    filter(audio) -> bytes            (near-end, cleans mic audio)
    push_reverse(audio_bytes)         (far-end, called by the output transport)
    set_reference_delay_ms(ms)
    calibrated / last_metrics / reference_delay_ms
"""

from __future__ import annotations

import logging
import time

import numpy as np

from pipecat.audio.filters.base_audio_filter import BaseAudioFilter

logger = logging.getLogger(__name__)


class LiveKitAECFilter(BaseAudioFilter):
    """Pipecat ``audio_in_filter`` backed by ``livekit.rtc.AudioProcessingModule``."""

    def __init__(
        self,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 44100,
        reference_delay_ms: float = 95.0,
        *,
        reference_buffer=None,
        persist_delay_callback=None,
        noise_suppression: bool = True,
        high_pass_filter: bool = True,
        auto_gain_control: bool = False,
    ):
        from livekit import rtc  # raises ImportError if unavailable -> caller falls back

        self._rtc = rtc
        self._input_sample_rate = int(input_sample_rate)
        self._output_sample_rate = int(output_sample_rate)
        self._ref_buf = reference_buffer
        self._far_read = None
        self._reference_delay_ms = self._clamp(reference_delay_ms)
        self._persist_delay_callback = persist_delay_callback

        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True,
            noise_suppression=bool(noise_suppression),
            high_pass_filter=bool(high_pass_filter),
            auto_gain_control=bool(auto_gain_control),
        )
        try:
            self._apm.set_stream_delay_ms(int(round(self._reference_delay_ms)))
        except Exception:
            pass

        self._last_metrics: dict[str, float | int] = {}
        self._last_reverse = np.zeros(0, dtype=np.float32)
        self._reference_rms = 0.0

        # Diagnostic per-response summary (grep: AEC summary).
        self._erle_samples: list[float] = []
        self._last_reverse_at = 0.0
        self._reverse_seen = False

    # ------------------------------------------------------------------
    # Pipecat BaseAudioFilter surface
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(delay_ms: float) -> float:
        return max(0.0, min(500.0, float(delay_ms)))

    def _frame_samples(self) -> int:
        return max(1, int(self._input_sample_rate * 0.01))  # 10 ms

    async def start(self, sample_rate: int) -> None:
        self._input_sample_rate = int(sample_rate)
        logger.info(
            "LiveKit AEC started: input_sr=%s output_sr=%s stream_delay_ms=%.1f",
            self._input_sample_rate,
            self._output_sample_rate,
            self._reference_delay_ms,
        )

    async def stop(self) -> None:
        pass

    async def process_frame(self, frame) -> None:
        pass

    @property
    def calibrated(self) -> bool:
        # The APM owns delay estimation; we are always ready to run.
        return True

    @property
    def last_metrics(self) -> dict[str, float | int]:
        return dict(self._last_metrics)

    @property
    def reference_delay_ms(self) -> float:
        return self._reference_delay_ms

    def set_reference_delay_ms(self, delay_ms: float, *, persist: bool = False) -> None:
        self._reference_delay_ms = self._clamp(delay_ms)
        try:
            self._apm.set_stream_delay_ms(int(round(self._reference_delay_ms)))
        except Exception:
            pass
        if persist and self._persist_delay_callback is not None:
            try:
                self._persist_delay_callback(self._reference_delay_ms)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Reverse (far-end) stream — fed by the output transport as audio renders
    # ------------------------------------------------------------------

    def push_reverse(self, audio, sample_rate: int | None = None) -> None:
        """Feed rendered speaker audio into the AEC reverse stream.

        Accepts either a float32 numpy array in [-1, 1] (what the transport's
        ``processed_audio`` is) or raw int16 PCM bytes.
        """
        if audio is None:
            return
        sr = int(sample_rate or self._output_sample_rate)
        if sr <= 0:
            return
        if isinstance(audio, np.ndarray):
            far = np.asarray(audio, dtype=np.float32).reshape(-1)
        elif isinstance(audio, (bytes, bytearray, memoryview)):
            far = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            return
        if far.size == 0:
            return
        if sr != self._input_sample_rate:
            far = self._resample(far, int(round(far.size * self._input_sample_rate / sr)))
        self._last_reverse = far
        self._reference_rms = float(np.sqrt(np.mean(far * far))) if far.size else 0.0
        self._last_reverse_at = time.monotonic()
        self._reverse_seen = True

        n = self._frame_samples()
        for i in range(0, far.size - n + 1, n):
            chunk = np.clip(far[i:i + n] * 32767.0, -32768, 32767).astype(np.int16)
            try:
                self._apm.process_reverse_stream(
                    self._rtc.AudioFrame(
                        data=chunk.tobytes(),
                        sample_rate=self._input_sample_rate,
                        num_channels=1,
                        samples_per_channel=n,
                    )
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Near-end — cleans mic audio
    # ------------------------------------------------------------------

    async def filter(self, audio: bytes) -> bytes:
        # Emit the per-response summary once playback has stopped.
        if self._reverse_seen and (time.monotonic() - self._last_reverse_at) > 0.4:
            self._log_summary()
            self._reverse_seen = False

        near = np.frombuffer(audio, dtype=np.int16)
        if near.size == 0:
            return audio
        near = np.ascontiguousarray(near)
        n = self._frame_samples()
        cleaned = near.copy()

        for i in range(0, near.size - n + 1, n):
            # Feed the far-end in LOCKSTEP with the near-end. Feeding the far
            # at render time (bursty/jittery) collapsed cancellation from ~40 dB
            # to ~12 dB in the app; consuming it one 10 ms window at a time from
            # the shared reference buffer keeps far and near advancing together.
            far = self._pull_far(n)
            if far is not None:
                self._last_reverse = far
                self._reference_rms = float(np.sqrt(np.mean(far * far))) if far.size else 0.0
                self._last_reverse_at = time.monotonic()
                self._reverse_seen = True
                far_i16 = np.clip(far * 32767.0, -32768, 32767).astype(np.int16)
                try:
                    self._apm.process_reverse_stream(
                        self._rtc.AudioFrame(
                            data=far_i16.tobytes(),
                            sample_rate=self._input_sample_rate,
                            num_channels=1,
                            samples_per_channel=n,
                        )
                    )
                except Exception:
                    pass

            chunk = near[i:i + n]
            frame = self._rtc.AudioFrame(
                data=chunk.tobytes(),
                sample_rate=self._input_sample_rate,
                num_channels=1,
                samples_per_channel=n,
            )
            try:
                self._apm.process_stream(frame)
                processed = np.frombuffer(frame.data, dtype=np.int16)
                if processed.size >= n:
                    cleaned[i:i + n] = processed[:n]
            except Exception:
                pass  # leave the raw chunk in place

        self._record_metrics(near, cleaned)
        return cleaned.tobytes()

    def _pull_far(self, num_in: int):
        """Pull the next lockstep far-end window from the reference buffer."""
        if self._ref_buf is None:
            return None
        num_out = max(1, int(round(num_in * self._output_sample_rate / self._input_sample_rate)))
        try:
            wp = int(self._ref_buf.write_pos)
        except Exception:
            return None
        if self._far_read is None:
            if wp < num_out:
                return None
            self._far_read = wp - num_out
        delay = max(0, wp - (self._far_read + num_out))
        raw = np.asarray(self._ref_buf.pull(num_out, delay), dtype=np.float32)
        self._far_read += num_out
        if raw.size == 0:
            return np.zeros(num_in, dtype=np.float32)
        if raw.size != num_in:
            raw = self._resample(raw, num_in)
        return raw.astype(np.float32)

    # ------------------------------------------------------------------
    # Metrics (for the STT barge-in gate + diagnostics)
    # ------------------------------------------------------------------

    def _record_metrics(self, near: np.ndarray, cleaned: np.ndarray) -> None:
        near_f = near.astype(np.float32) / 32768.0
        out_f = cleaned.astype(np.float32) / 32768.0
        mic_rms = float(np.sqrt(np.mean(near_f * near_f))) if near_f.size else 0.0
        out_rms = float(np.sqrt(np.mean(out_f * out_f))) if out_f.size else 0.0
        erle_db = 20.0 * np.log10(max(mic_rms, 1e-8) / max(out_rms, 1e-8))
        correlation = self._correlation(near_f, self._last_reverse)
        self._last_metrics = {
            "input_rms": mic_rms,
            "residual_rms": out_rms,
            "reference_rms": self._reference_rms,
            "erle_db": float(erle_db),
            "clipped_samples": int(np.count_nonzero(np.abs(out_f) > 0.99)),
            "reference_delay_ms": self._reference_delay_ms,
            "stream_delay_ms": int(round(self._reference_delay_ms)),
            "speech_probability": 0.0,
            "reference_correlation": float(correlation),
        }
        if self._reference_rms > 0.0:
            self._erle_samples.append(float(erle_db))

    def _log_summary(self) -> None:
        samples = self._erle_samples
        self._erle_samples = []
        if not samples:
            return
        arr = np.asarray(samples, dtype=np.float32)
        logger.info(
            "LiveKit AEC summary: frames=%d erle_median=%.1f erle_p10=%.1f erle_min=%.1f delay_ms=%.1f",
            int(arr.size),
            float(np.median(arr)),
            float(np.percentile(arr, 10)),
            float(arr.min()),
            self._reference_delay_ms,
        )

    @staticmethod
    def _correlation(a: np.ndarray, b: np.ndarray) -> float:
        if a.size == 0 or b.size == 0:
            return 0.0
        n = min(a.size, b.size)
        a_c = a[-n:] - np.mean(a[-n:])
        b_c = b[-n:] - np.mean(b[-n:])
        denom = float(np.linalg.norm(a_c) * np.linalg.norm(b_c))
        if denom < 1e-8:
            return 0.0
        return abs(float(np.dot(a_c, b_c))) / denom

    @staticmethod
    def _resample(data: np.ndarray, target_length: int) -> np.ndarray:
        if target_length <= 0:
            return np.zeros(0, dtype=np.float32)
        if len(data) == target_length:
            return data
        if len(data) == 0:
            return np.zeros(target_length, dtype=np.float32)
        indices = np.linspace(0, len(data) - 1, target_length)
        return np.interp(indices, np.arange(len(data)), data).astype(np.float32)
