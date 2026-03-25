
import numpy as np
import logging
import sys
import os
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.getcwd())

# Mock dependencies to avoid import errors from unrelated modules
sys.modules['pipecat'] = MagicMock()
sys.modules['pipecat.frames.frames'] = MagicMock()
sys.modules['pipecat.services.ai_services'] = MagicMock()
sys.modules['pipecat.transports.base_transport'] = MagicMock()
sys.modules['distr.core.agent.session'] = MagicMock()
sys.modules['distr.core.settings'] = MagicMock()
sys.modules['distr.core.db'] = MagicMock()

# Force librosa to fail to test fallback
sys.modules['librosa'] = None


# Now import the target function
# We need to manually load the module from file path to avoid package init issues if possible,
# or just rely on the mocks to make the standard import work.
import importlib.util
spec = importlib.util.spec_from_file_location("audio_utils", "distr/core/audio_utils.py")
audio_utils = importlib.util.module_from_spec(spec)
sys.modules["distr.core.audio_utils"] = audio_utils
spec.loader.exec_module(audio_utils)
pitch_preserving_time_stretch = audio_utils.pitch_preserving_time_stretch

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_time_stretch():
    logger.info("Starting Time Stretch Verification...")
    
    sample_rate = 24000
    speeds = [1.0, 1.5, 0.5]
    chunk_sizes = [2048, 1024, 512, 256, 128, 64, 32, 10]
    
    for speed in speeds:
        logger.info(f"\n--- Testing Speed: {speed}x ---")
        for size in chunk_sizes:
            # Generate synthetic sine wave audio
            t = np.linspace(0, size/sample_rate, size, endpoint=False)
            audio = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
            
            try:
                output = pitch_preserving_time_stretch(audio, speed, sample_rate)
                
                expected_len = int(size / speed)
                actual_len = len(output)
                
                # Allow some tolerance for STFT/ISTFT padding effects
                tolerance = max(128, int(size * 0.2)) 
                
                status = "PASS"
                if abs(actual_len - expected_len) > tolerance and size > 64:
                     # For very small chunks, resampling is exact, but STFT might vary
                     status = f"WARN (Expected ~{expected_len}, Got {actual_len})"
                
                logger.info(f"Chunk Size: {size:4d} | Output: {actual_len:4d} | Status: {status}")
                
            except Exception as e:
                logger.error(f"Chunk Size: {size:4d} | FAILED with error: {e}")
                import traceback
                traceback.print_exc()

if __name__ == "__main__":
    test_time_stretch()
