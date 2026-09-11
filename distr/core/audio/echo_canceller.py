"""
Acoustic Echo Cancellation (AEC) filter for Pipecat pipelines.

Uses an NLMS (Normalized Least Mean Squares) adaptive filter to subtract
the known speaker output from the microphone input, removing echo before
it reaches the VAD.

Architecture:
  - The output transport pushes audio frames into a shared ring buffer
    (the "reference signal" — what's being played through the speaker).
  - This filter, plugged into Pipecat's audio_in_filter slot, reads the
    reference and subtracts the estimated echo from each mic frame.
  - The cleaned audio is what the VAD and STT see.

Pure numpy — no native dependencies.
"""

import numpy as np
import logging
import threading
import time

from pipecat.audio.filters.base_audio_filter import BaseAudioFilter

logger = logging.getLogger(__name__)


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


class NLMSEchoCanceller(BaseAudioFilter):
    """Block NLMS adaptive filter that cancels speaker echo from mic input.

    Uses block-mode processing: the filter weights are updated once per audio
    chunk rather than per sample. This allows fully vectorized numpy operations
    and runs 50-100x faster than sample-by-sample NLMS while providing
    comparable echo cancellation for the chunk sizes used in voice pipelines.

    Params:
        reference_buffer: Shared ReferenceBuffer fed by the output transport.
        filter_length:    Number of taps. Longer = handles more room reverb
                          but costs more CPU. 800 @ 16kHz = 50ms impulse
                          response, good for near-field (laptop) setups.
        mu:               Step size (0 < mu <= 1). Smaller = more stable but
                          slower convergence. 0.5 is a solid default for block mode.
        eps:              Regularization to avoid division by zero.
        output_sample_rate: Sample rate of the speaker output (for resampling
                            the reference to match mic input rate).
    """

    def __init__(
        self,
        reference_buffer: ReferenceBuffer,
        filter_length: int = 800,
        mu: float = 0.5,
        eps: float = 1e-8,
        output_sample_rate: int = 24000,
        reference_delay_ms: float = 0.0,
    ):
        self._ref_buf = reference_buffer
        self._filter_length = filter_length
        self._mu = mu
        self._eps = eps
        self._output_sample_rate = output_sample_rate
        self._input_sample_rate = 16000  # set in start()
        self._reference_delay_ms = max(0.0, float(reference_delay_ms))
        self._estimated_delay_ms: float | None = None
        self._delay_observations: list[float] = []
        self._last_metrics: dict[str, float | int] = {}

        # Adaptive filter weights
        self._w = np.zeros(filter_length, dtype=np.float32)
        # Tail of previous reference chunk for overlap (filter_length - 1 samples)
        self._ref_tail = np.zeros(filter_length - 1, dtype=np.float32)

        self._enabled = True

    async def start(self, sample_rate: int):
        self._input_sample_rate = sample_rate
        # Don't reset the reference buffer's sample rate here — it stores
        # output audio at _output_sample_rate and was sized correctly at init.
        self._w = np.zeros(self._filter_length, dtype=np.float32)
        self._ref_tail = np.zeros(self._filter_length - 1, dtype=np.float32)
        logger.info(
            f"AEC filter started: input_sr={sample_rate}, "
            f"output_sr={self._output_sample_rate}, "
            f"taps={self._filter_length}, mu={self._mu}, "
            f"reference_delay_ms={self._reference_delay_ms:.1f}"
        )

    def set_reference_delay_ms(self, delay_ms: float) -> None:
        """Set bounded reference delay without reallocating filter state."""
        self._reference_delay_ms = max(0.0, min(500.0, float(delay_ms)))

    @property
    def last_metrics(self) -> dict[str, float | int]:
        return dict(self._last_metrics)

    async def stop(self):
        logger.info("AEC filter stopped")

    async def process_frame(self, frame):
        """Handle runtime control frames (enable/disable)."""
        pass

    async def filter(self, audio: bytes) -> bytes:
        """Remove echo from mic audio using block NLMS.

        The entire chunk is processed in vectorized numpy ops:
        1. Build a reference matrix from the reference signal
        2. Estimate echo for all samples
        3. Compute the cleaned error signal
        4. Update weights once using the block gradient
        """
        if not self._enabled or not self._ref_buf.is_active:
            return audio

        # Reset filter state on new TTS session (inactive→active transition)
        # to avoid stale weights from a previous response causing artifacts.
        if self._ref_buf.consume_activation():
            self._w[:] = 0.0
            self._ref_tail[:] = 0.0
            self._estimated_delay_ms = None
            self._delay_observations.clear()
            logger.debug("AEC: Filter weights reset (new TTS session)")

        # Convert mic input to float32
        mic = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        num_samples = len(mic)
        fl = self._filter_length

        # Get reference signal and resample if needed. With no explicit
        # calibration, find the current speaker path before filtering this
        # exact raw microphone frame. Using delay zero here lets TTS leakage
        # reach VAD while the hardware path is still being learned.
        delay_ms = self._reference_delay_ms
        measured_delay = None
        measured_correlation = None
        measured_ref = None
        if delay_ms == 0.0 and self._estimated_delay_ms is None:
            measured_delay, measured_correlation, measured_ref = self._find_reference_alignment(mic)
            if measured_delay is not None:
                self._delay_observations.append(measured_delay)
                if len(self._delay_observations) >= 5:
                    self._estimated_delay_ms = float(np.median(self._delay_observations[-5:]))
                    logger.info("AEC: calibrated reference delay to %.1fms", self._estimated_delay_ms)
                else:
                    # Use the best measured alignment immediately. Do not
                    # wait for the five-sample median before suppressing echo.
                    delay_ms = measured_delay
        if self._estimated_delay_ms is not None:
            delay_ms = self._estimated_delay_ms
        if measured_delay is not None and measured_ref is not None and self._estimated_delay_ms is None:
            ref = measured_ref
        else:
            delay_samples = round(delay_ms * self._output_sample_rate / 1000.0)
            if self._output_sample_rate != self._input_sample_rate:
                ref_samples_needed = int(
                    num_samples * self._output_sample_rate / self._input_sample_rate
                )
                ref_raw = self._ref_buf.pull(ref_samples_needed, delay_samples)
                ref = self._resample(ref_raw, num_samples)
            else:
                ref = self._ref_buf.pull(num_samples, delay_samples)

        # Do not adapt to unrelated room noise or user speech. A mismatched
        # reference makes an adaptive filter amplify the microphone instead
        # of cancelling it, especially while the output device is starting.
        mic_centered = mic - np.mean(mic)
        ref_centered = ref - np.mean(ref)
        correlation = abs(float(np.dot(mic_centered, ref_centered))) / max(
            float(np.linalg.norm(mic_centered) * np.linalg.norm(ref_centered)),
            1e-8,
        )
        if measured_correlation is not None:
            correlation = max(correlation, measured_correlation)
        if correlation < 0.35:
            mic_rms = float(np.sqrt(np.mean(mic * mic))) if len(mic) else 0.0
            reference_rms = float(np.sqrt(np.mean(ref * ref))) if len(ref) else 0.0
            self._last_metrics = {
                "input_rms": mic_rms,
                "residual_rms": mic_rms,
                "reference_rms": reference_rms,
                "erle_db": 0.0,
                "clipped_samples": 0,
                "reference_delay_ms": self._reference_delay_ms,
                "reference_correlation": correlation,
            }
            return audio

        # Remove the immediately measurable direct speaker component before
        # the adaptive filter runs. NLMS starts with zero weights at every TTS
        # response and can take several hundred milliseconds to learn even a
        # simple laptop-speaker path. A bounded least-squares projection gives
        # the residual filter a clean starting point without muting the mic.
        ref_energy = float(np.dot(ref_centered, ref_centered))
        projection_gain = float(
            np.dot(mic_centered, ref_centered) / max(ref_energy, self._eps)
        )
        projection_gain = float(np.clip(projection_gain, -2.0, 2.0))
        projected_echo = projection_gain * ref
        mic_for_adaptive_filter = mic - projected_echo

        # Prepend tail from previous chunk for continuity
        ref_extended = np.concatenate([self._ref_tail, ref])

        # Update one sample at a time. The block gradient is fast, but it
        # becomes unstable when the reference is resampled and the physical
        # speaker path is delayed. Per-sample NLMS keeps each update locally
        # normalized and follows the measured acoustic path safely.
        error = np.empty(num_samples, dtype=np.float32)
        for index in range(num_samples):
            ref_vector = ref_extended[index:index + fl][::-1]
            estimate = float(np.dot(self._w, ref_vector))
            sample_error = mic_for_adaptive_filter[index] - estimate
            error[index] = sample_error
            energy = float(np.dot(ref_vector, ref_vector)) + self._eps
            self._w += (self._mu * sample_error / energy) * ref_vector

        # Save tail for next chunk
        if len(ref) >= fl - 1:
            self._ref_tail = ref[-(fl - 1):].copy()
        else:
            # Not enough new samples — shift old tail and append
            keep = (fl - 1) - len(ref)
            self._ref_tail = np.concatenate([self._ref_tail[len(ref):], ref]).copy()

        # Convert back to int16 bytes
        clipped_samples = int(np.count_nonzero(np.abs(error) > 1.0))
        mic_rms = float(np.sqrt(np.mean(mic * mic))) if len(mic) else 0.0
        reference_rms = float(np.sqrt(np.mean(ref * ref))) if len(ref) else 0.0
        error_rms = float(np.sqrt(np.mean(error * error))) if len(error) else 0.0
        erle_db = 20.0 * np.log10(max(mic_rms, 1e-8) / max(error_rms, 1e-8))
        self._last_metrics = {
            "input_rms": mic_rms,
            "residual_rms": error_rms,
            "reference_rms": reference_rms,
            "erle_db": float(erle_db),
            "clipped_samples": clipped_samples,
            "reference_delay_ms": delay_ms,
            "reference_correlation": correlation,
            "projection_gain": projection_gain,
        }
        error = np.clip(error, -1.0, 1.0)
        return (error * 32767.0).astype(np.int16).tobytes()

    def _find_reference_alignment(self, mic: np.ndarray) -> tuple[float | None, float, np.ndarray | None]:
        """Find the best speaker delay and aligned reference for one raw frame."""
        # MacBook speaker leakage can be only a few thousandths at the mic.
        # Rejecting everything below 0.01 prevents calibration on quiet but
        # valid TTS, which then lets the unaligned echo reach VAD.
        if len(mic) < 160 or float(np.sqrt(np.mean(mic * mic))) < 0.002:
            return None, 0.0, None
        if self._ref_buf.seconds_since_activation() < 0.25:
            return None, 0.0, None

        ref_len = int(len(mic) * self._output_sample_rate / self._input_sample_rate)
        mic_centered = mic - np.mean(mic)
        mic_norm = float(np.linalg.norm(mic_centered))
        if mic_norm < 1e-5:
            return None, 0.0, None
        best_corr = 0.0
        best_delay = None
        best_ref = None
        for delay_ms in range(0, 251, 10):
            raw = self._ref_buf.pull(
                ref_len,
                round(delay_ms * self._output_sample_rate / 1000.0),
            )
            ref = self._resample(raw, len(mic))
            ref_centered = ref - np.mean(ref)
            ref_norm = float(np.linalg.norm(ref_centered))
            if ref_norm < 1e-5:
                continue
            corr = abs(float(np.dot(mic_centered, ref_centered) / (mic_norm * ref_norm)))
            if corr > best_corr:
                best_corr = corr
                best_delay = delay_ms
                best_ref = ref
        if best_delay is None or best_corr < 0.35:
            return None, best_corr, best_ref
        return float(best_delay), best_corr, best_ref

    def _estimate_reference_delay(self, mic: np.ndarray) -> float | None:
        """Find a confident 0-250ms speaker-to-mic alignment."""
        if len(mic) < 160 or float(np.sqrt(np.mean(mic * mic))) < 0.01:
            return None
        if self._ref_buf.seconds_since_activation() < 0.25:
            return None

        best_delay, best_corr, _ = self._find_reference_alignment(mic)
        return float(best_delay) if best_delay is not None and best_corr >= 0.55 else None


    @staticmethod
    def _resample(data: np.ndarray, target_length: int) -> np.ndarray:
        """Simple linear interpolation resampling."""
        if len(data) == target_length:
            return data
        if len(data) == 0:
            return np.zeros(target_length, dtype=np.float32)
        indices = np.linspace(0, len(data) - 1, target_length)
        return np.interp(indices, np.arange(len(data)), data).astype(np.float32)
