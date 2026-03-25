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

    def pull(self, num_samples: int) -> np.ndarray:
        """Pull the most recent num_samples from the buffer as reference."""
        with self._lock:
            if self._write_pos == 0:
                return np.zeros(num_samples, dtype=np.float32)
            end = self._write_pos % self._max_samples
            if num_samples <= end:
                return self._buf[end - num_samples:end].copy()
            else:
                # Wrap around
                tail = self._buf[:end].copy()
                head_needed = num_samples - end
                head = self._buf[self._max_samples - head_needed:].copy()
                return np.concatenate([head, tail])

    def set_active(self, active: bool):
        with self._lock:
            was_active = self._active
            self._active = active
            # Track transitions so the filter can reset on inactive→active
            if active and not was_active:
                self._just_activated = True
            # Reset activation timestamp on EVERY set_active(True) call, not
            # just transitions.  Each new TTS sentence produces a fresh echo
            # burst that the NLMS filter needs time to converge on.  Without
            # this, the grace period (used by the barge-in gate in STT) only
            # applies to the first sentence — subsequent sentences within the
            # same response have an expired grace period and echo residual
            # immediately triggers false deferred barge-ins.
            if active:
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
    ):
        self._ref_buf = reference_buffer
        self._filter_length = filter_length
        self._mu = mu
        self._eps = eps
        self._output_sample_rate = output_sample_rate
        self._input_sample_rate = 16000  # set in start()

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
            f"taps={self._filter_length}, mu={self._mu}"
        )

    async def stop(self):
        logger.info("AEC filter stopped")

    async def process_frame(self, frame):
        """Handle runtime control frames (enable/disable)."""
        pass

    async def filter(self, audio: bytes) -> bytes:
        """Remove echo from mic audio using block NLMS.

        The entire chunk is processed in vectorized numpy ops:
        1. Build a reference matrix (Toeplitz-like) from the reference signal
        2. Estimate echo for all samples: y_hat = ref_matrix @ w
        3. Compute error: e = mic - y_hat
        4. Update weights once using the block gradient
        """
        if not self._enabled or not self._ref_buf.is_active:
            return audio

        # Reset filter state on new TTS session (inactive→active transition)
        # to avoid stale weights from a previous response causing artifacts.
        if self._ref_buf.consume_activation():
            self._w[:] = 0.0
            self._ref_tail[:] = 0.0
            logger.debug("AEC: Filter weights reset (new TTS session)")

        # Convert mic input to float32
        mic = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        num_samples = len(mic)
        fl = self._filter_length

        # Get reference signal and resample if needed
        if self._output_sample_rate != self._input_sample_rate:
            ref_samples_needed = int(
                num_samples * self._output_sample_rate / self._input_sample_rate
            )
            ref_raw = self._ref_buf.pull(ref_samples_needed)
            ref = self._resample(ref_raw, num_samples)
        else:
            ref = self._ref_buf.pull(num_samples)

        # Prepend tail from previous chunk for continuity
        ref_extended = np.concatenate([self._ref_tail, ref])

        # Build reference matrix: each row i is ref_extended[i : i + fl] reversed
        # This is the convolution matrix for the FIR filter.
        # Shape: (num_samples, fl)
        # Using stride tricks for zero-copy view (fast, no allocation)
        from numpy.lib.stride_tricks import as_strided
        strides = (ref_extended.strides[0], ref_extended.strides[0])
        ref_matrix = as_strided(
            ref_extended, shape=(num_samples, fl), strides=strides
        )
        # Flip each row so index 0 = most recent sample (FIR convention)
        ref_matrix = ref_matrix[:, ::-1]

        # Vectorized echo estimate: y_hat[i] = w . ref_matrix[i]
        y_hat = ref_matrix @ self._w

        # Error signal (cleaned audio)
        error = mic - y_hat

        # Block weight update (NLMS):
        # gradient = ref_matrix^T @ error  (correlation of reference with error)
        # norm = sum of ref energy across the block + regularization
        ref_energy = np.sum(ref_matrix * ref_matrix)
        norm = ref_energy + self._eps * num_samples
        gradient = ref_matrix.T @ error
        self._w = self._w + (self._mu / norm) * gradient

        # Save tail for next chunk
        if len(ref) >= fl - 1:
            self._ref_tail = ref[-(fl - 1):].copy()
        else:
            # Not enough new samples — shift old tail and append
            keep = (fl - 1) - len(ref)
            self._ref_tail = np.concatenate([self._ref_tail[len(ref):], ref]).copy()

        # Convert back to int16 bytes
        error = np.clip(error, -1.0, 1.0)
        return (error * 32767.0).astype(np.int16).tobytes()

    @staticmethod
    def _resample(data: np.ndarray, target_length: int) -> np.ndarray:
        """Simple linear interpolation resampling."""
        if len(data) == target_length:
            return data
        if len(data) == 0:
            return np.zeros(target_length, dtype=np.float32)
        indices = np.linspace(0, len(data) - 1, target_length)
        return np.interp(indices, np.arange(len(data)), data).astype(np.float32)
