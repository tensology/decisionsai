
import numpy as np
import logging
from .utils import pitch_preserving_time_stretch

logger = logging.getLogger(__name__)

class TimeStretcher:
    """
    Stateful time stretcher that buffers audio to ensure sufficient chunk size
    for high-quality processing and uses overlap-add to prevent artifacts.
    """
    def __init__(self, sample_rate=24000, buffer_duration_ms=100, overlap_duration_ms=20):
        self.sample_rate = sample_rate
        self.buffer_size = int(sample_rate * buffer_duration_ms / 1000)
        self.overlap_size = int(sample_rate * overlap_duration_ms / 1000)
        
        # Buffer for incoming audio
        self.input_buffer = np.array([], dtype=np.float32)
        
        # Buffer for overlap-add (tail of the previous processed block)
        self.overlap_buffer = np.zeros(self.overlap_size, dtype=np.float32)
        self._last_speed = 1.0
        
        logger.info(f"TimeStretcher initialized: Buffer={self.buffer_size}, Overlap={self.overlap_size}")

    @staticmethod
    def _is_unity_speed(speed: float) -> bool:
        return abs(float(speed) - 1.0) < 0.01

    def process(self, chunk: np.ndarray, speed: float) -> np.ndarray:
        """
        Add chunk to buffer and process if buffer is full.
        Returns processed audio (empty if buffer not full yet).
        """
        if len(chunk) == 0:
            return np.array([], dtype=np.float32)

        self._last_speed = float(speed)

        # At 1.0x, do not accumulate 100ms blocks — hard block boundaries cause crackling.
        if self._is_unity_speed(speed):
            pending = self.input_buffer
            self.input_buffer = np.array([], dtype=np.float32)
            self.overlap_buffer = np.zeros(self.overlap_size, dtype=np.float32)
            chunk = chunk.astype(np.float32, copy=False)
            if len(pending) == 0:
                return chunk
            return np.concatenate((pending, chunk))
            
        # Optimization: If speed is 1.0, bypass processing to avoid artifacts
        # But we must still buffer if we have leftover overlap from previous operations?
        # Or we should flush and switch to passthrough?
        # Switching modes is complex due to overlap-add continuity.
        # It's safer to just process at 1.0x (which should be identity in pitch_preserving_time_stretch).
        # However, buffering adds latency. At 1.0x, latency matters most.
        # Ideally, at 1.0x we should pass through immediately.
        
        # If speed is effectively 1.0 and buffer is empty, just pass through?
        # No, we might have overlap buffer state.
        # For simplicity and robustness, we'll stick to the pipeline.
        # But we should ensure pitch_preserving_time_stretch(..., 1.0) is fast and transparent.
        # (It returns input audio immediately if speed=1.0).
        
        # 1. Append new chunk to input buffer
        self.input_buffer = np.concatenate((self.input_buffer, chunk))
        
        output_audio = np.array([], dtype=np.float32)
        
        # 2. Process while we have enough data
        # We process in fixed-size blocks to ensure consistency
        while len(self.input_buffer) >= self.buffer_size:
            # Extract a block
            block = self.input_buffer[:self.buffer_size]
            self.input_buffer = self.input_buffer[self.buffer_size:]
            
            # Time-stretch the block
            # We assume pitch_preserving_time_stretch handles the core DSP
            stretched_block = pitch_preserving_time_stretch(block, speed, self.sample_rate)
            
            # 3. Overlap-Add Logic
            if len(stretched_block) < self.overlap_size:
                # If stretched block is tiny (e.g. high speed), just append it
                # This is a rare edge case, but we must handle it to avoid crashes
                output_audio = np.concatenate((output_audio, stretched_block))
                # Reset overlap buffer as we can't effectively crossfade
                self.overlap_buffer = np.zeros(self.overlap_size, dtype=np.float32)
            else:
                # Cross-fade the start of the new block with the tail of the previous block
                fade_in = stretched_block[:self.overlap_size]
                
                # Linear cross-fade
                fade_curve = np.linspace(0, 1, self.overlap_size, dtype=np.float32)
                crossfaded = (self.overlap_buffer * (1 - fade_curve)) + (fade_in * fade_curve)
                
                # The valid part of the new block (after the cross-fade region)
                new_part = stretched_block[self.overlap_size:]
                
                # Save the tail for the next overlap
                # We need to save exactly overlap_size from the end
                if len(new_part) >= self.overlap_size:
                    self.overlap_buffer = new_part[-self.overlap_size:]
                    # Output everything up to the overlap region
                    to_output = np.concatenate((crossfaded, new_part[:-self.overlap_size]))
                else:
                    # If the remainder is smaller than overlap (unlikely with reasonable settings),
                    # we just output the crossfaded part and keep the rest for overlap
                    # This is complex, so for simplicity in this edge case:
                    to_output = crossfaded
                    self.overlap_buffer = np.zeros(self.overlap_size, dtype=np.float32) # Reset
                    
                output_audio = np.concatenate((output_audio, to_output))
                
        return output_audio

    async def async_process(self, chunk: np.ndarray, speed: float) -> np.ndarray:
        """
        Async wrapper for process() that runs in an executor to prevent blocking the event loop.
        Crucial for real-time agent performance.
        """
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.process, chunk, speed)

    def flush(self) -> np.ndarray:
        """
        Process any remaining data in the buffer.
        Call this when playback stops.
        """
        if len(self.input_buffer) == 0:
            return np.array([], dtype=np.float32)

        if self._is_unity_speed(self._last_speed):
            remaining = self.input_buffer
            self.input_buffer = np.array([], dtype=np.float32)
            self.overlap_buffer = np.zeros(self.overlap_size, dtype=np.float32)
            return remaining
            
        # Process whatever is left
        stretched_block = pitch_preserving_time_stretch(self.input_buffer, self._last_speed, self.sample_rate)
        self.input_buffer = np.array([], dtype=np.float32)
        
        # Apply overlap if possible
        if len(stretched_block) >= self.overlap_size:
            fade_in = stretched_block[:self.overlap_size]
            fade_curve = np.linspace(0, 1, self.overlap_size, dtype=np.float32)
            crossfaded = (self.overlap_buffer * (1 - fade_curve)) + (fade_in * fade_curve)
            return np.concatenate((crossfaded, stretched_block[self.overlap_size:]))
        else:
            return stretched_block

    async def async_flush(self) -> np.ndarray:
        """
        Async wrapper for flush() that runs in an executor.
        """
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.flush)
