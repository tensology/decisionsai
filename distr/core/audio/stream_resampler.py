"""Stateful streaming resampler for TTS playback.

Per-chunk resampling (e.g. 20 ms Kokoro frames 24 kHz -> 44.1 kHz) creates
boundary discontinuities that sound like crackling/static, especially on Bluetooth.
This resampler keeps phase continuity across chunks.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


class LinearStreamResampler:
    """Linear-interpolation resampler with cross-chunk phase continuity."""

    def __init__(self) -> None:
        self._source_rate = 0
        self._target_rate = 0
        self._carry = np.array([], dtype=np.float32)
        self._phase = 0.0

    def configure(self, source_rate: int, target_rate: int) -> None:
        """Configure rates; resets internal state when either rate changes."""
        source_rate = int(source_rate or 0)
        target_rate = int(target_rate or 0)
        if source_rate == self._source_rate and target_rate == self._target_rate:
            return
        self._source_rate = source_rate
        self._target_rate = target_rate
        self.reset()

    def reset(self) -> None:
        """Clear buffered samples and interpolation phase."""
        self._carry = np.array([], dtype=np.float32)
        self._phase = 0.0

    def process(self, chunk: np.ndarray) -> np.ndarray:
        """Resample one mono float32 chunk, preserving continuity with prior chunks."""
        if len(chunk) == 0:
            return np.array([], dtype=np.float32)

        chunk = chunk.astype(np.float32, copy=False)
        if self._source_rate <= 0 or self._target_rate <= 0:
            return chunk
        if self._source_rate == self._target_rate:
            return chunk

        data = np.concatenate((self._carry, chunk))
        if len(data) < 2:
            self._carry = data
            return np.array([], dtype=np.float32)

        step = self._source_rate / float(self._target_rate)
        pos = self._phase
        out: list[float] = []
        last_index = len(data) - 1
        while pos < last_index:
            index = int(pos)
            frac = pos - index
            sample = (data[index] * (1.0 - frac)) + (data[index + 1] * frac)
            out.append(float(sample))
            pos += step

        consumed = int(pos)
        if consumed >= len(data):
            self._carry = np.array([], dtype=np.float32)
            self._phase = 0.0
        else:
            self._carry = data[consumed:].copy()
            self._phase = pos - consumed

        if not out:
            return np.array([], dtype=np.float32)
        return np.asarray(out, dtype=np.float32)

    def flush(self) -> np.ndarray:
        """Drain any remaining buffered source samples."""
        if self._source_rate == self._target_rate:
            out = self._carry.copy()
            self.reset()
            return out
        if len(self._carry) <= 1:
            self.reset()
            return np.array([], dtype=np.float32)
        padded = np.concatenate((self._carry, self._carry[-1:]))
        self._carry = np.array([], dtype=np.float32)
        self._phase = 0.0
        return self.process(padded)
