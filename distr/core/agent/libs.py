"""
Centralized dependency handling for distr.core.agent package.
This module handles optional imports and provides dummy implementations where needed
to avoid code redundancy and clutter in service modules.
"""
import asyncio
import logging
import sys
import os
import numpy as np

logger = logging.getLogger(__name__)

# --- Pipecat Imports ---
try:
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask, PipelineParams
    from pipecat.transports.local.audio import (
        LocalAudioTransport, 
        LocalAudioTransportParams,
        LocalAudioInputTransport,
        LocalAudioOutputTransport
    )
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    try:
        from pipecat.audio.vad.vad_analyzer import VADParams
    except ImportError:
        VADParams = None
    from pipecat.frames.frames import (
        Frame,
        AudioRawFrame,
        InputAudioRawFrame,
        TextFrame,
        TranscriptionFrame,
        LLMFullResponseStartFrame,
        LLMFullResponseEndFrame,
        TTSStartedFrame,
        TTSStoppedFrame,
        ErrorFrame,
        StartFrame,
        EndFrame,
        CancelFrame,
        InterruptionFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )

    # Pipecat 0.0.100: VAD pushes VADUserStartedSpeakingFrame, NOT UserStartedSpeakingFrame.
    # These are sibling classes (both inherit SystemFrame), so isinstance() doesn't match across them.
    # Import the VAD-specific frames so STT services can handle both.
    try:
        from pipecat.frames.frames import VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
    except ImportError:
        # Older Pipecat versions may not have these - fall back to User* frames
        VADUserStartedSpeakingFrame = UserStartedSpeakingFrame
        VADUserStoppedSpeakingFrame = UserStoppedSpeakingFrame

    # Tuple constants for isinstance() checks - matches both VAD-generated and manually-pushed frames
    SpeakingStartedFrames = (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)
    SpeakingStoppedFrames = (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)
    
    # OutputAudioRawFrame might be newer or optional in some versions
    try:
        from pipecat.frames.frames import OutputAudioRawFrame
    except ImportError:
        OutputAudioRawFrame = None

    from pipecat.processors.frame_processor import FrameProcessor

    from pipecat.services.stt_service import STTService as _PipecatSTTService
    from pipecat.services.tts_service import TTSService as _PipecatTTSService
    from pipecat.services.llm_service import LLMService as _PipecatLLMService
    
    # Wrapper classes to fix Pipecat 0.0.95+ compatibility issue
    # __process_queue attribute is accessed before __create_process_task() creates it
    # Solution: Pre-initialize the attribute so the hasattr check succeeds
    # Note: Don't set __started=True as that breaks normal initialization
    class STTService(_PipecatSTTService):
        def __init__(self, **kwargs):
            # Pre-initialize the attribute BEFORE super().__init__
            # This ensures it exists when __input_frame_task_handler starts
            if not hasattr(self, '_FrameProcessor__process_queue'):
                self._FrameProcessor__process_queue = None
            super().__init__(**kwargs)
    
    class TTSService(_PipecatTTSService):
        def __init__(self, **kwargs):
            if not hasattr(self, '_FrameProcessor__process_queue'):
                self._FrameProcessor__process_queue = None
            super().__init__(**kwargs)
    
    class LLMService(_PipecatLLMService):
        def __init__(self, **kwargs):
            if not hasattr(self, '_FrameProcessor__process_queue'):
                self._FrameProcessor__process_queue = None
            super().__init__(**kwargs)
    
    PIPECAT_AVAILABLE = True

except ImportError as e:
    PIPECAT_AVAILABLE = False
    logger.warning(f"Pipecat not available: {e}")
    
    # Dummy classes to prevent NameError in type hints and inheritance
    class Pipeline: pass
    class PipelineRunner: pass
    class PipelineTask: pass
    class PipelineParams: pass
    class LocalAudioTransport: pass
    class LocalAudioTransportParams: pass
    class LocalAudioInputTransport: pass
    class LocalAudioOutputTransport: pass
    class SileroVADAnalyzer: pass
    class VADParams: pass
    class FrameProcessor: pass
    
    class Frame: pass
    class AudioRawFrame: pass
    class InputAudioRawFrame: pass
    class TextFrame: pass
    class TranscriptionFrame: pass
    class LLMFullResponseStartFrame: pass
    class LLMFullResponseEndFrame: pass
    class TTSStartedFrame: pass
    class TTSStoppedFrame: pass
    class ErrorFrame: pass
    class StartFrame: pass
    class EndFrame: pass
    class CancelFrame: pass
    class InterruptionFrame: pass
    class UserStartedSpeakingFrame: pass
    class UserStoppedSpeakingFrame: pass
    class VADUserStartedSpeakingFrame: pass
    class VADUserStoppedSpeakingFrame: pass
    SpeakingStartedFrames = (UserStartedSpeakingFrame, VADUserStartedSpeakingFrame)
    SpeakingStoppedFrames = (UserStoppedSpeakingFrame, VADUserStoppedSpeakingFrame)
    OutputAudioRawFrame = None
    
    class STTService:
        def __init__(self, **kwargs): pass
        async def process_frame(self, frame, direction): pass
        async def push_frame(self, frame, direction): pass
        
    class TTSService:
        def __init__(self, **kwargs): pass
        async def process_frame(self, frame, direction): pass
        async def push_frame(self, frame, direction): pass
        
    class LLMService:
        def __init__(self, **kwargs): pass
        async def process_frame(self, frame, direction): pass
        async def push_frame(self, frame, direction): pass


# --- Optional External Libraries ---

# SoundDevice
try:
    import sounddevice as sd
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    sd = None
    SOUNDDEVICE_AVAILABLE = False

# Librosa
try:
    import librosa
    LIBROSA_AVAILABLE = True
except ImportError:
    librosa = None
    LIBROSA_AVAILABLE = False

# PyAutoGUI
try:
    import pyautogui
    pyautogui.FAILSAFE = False
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    pyautogui = None
    PYAUTOGUI_AVAILABLE = False

# ElevenLabs
try:
    from elevenlabs import ElevenLabs
    ELEVENLABS_AVAILABLE = True
except ImportError:
    ElevenLabs = None
    ELEVENLABS_AVAILABLE = False

# Kokoro
try:
    from kokoro_onnx import Kokoro
    KOKORO_AVAILABLE = True
except ImportError:
    Kokoro = None
    KOKORO_AVAILABLE = False

# Ollama
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    ollama = None
    OLLAMA_AVAILABLE = False

# Vosk
try:
    import vosk
    VOSK_AVAILABLE = True
except ImportError:
    vosk = None
    VOSK_AVAILABLE = False

# Soundfile
try:
    import soundfile as sf
    SOUNDFILE_AVAILABLE = True
except ImportError:
    sf = None
    SOUNDFILE_AVAILABLE = False

# Pydub
try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    AudioSegment = None
    PYDUB_AVAILABLE = False

# PyWhisperCPP
try:
    import pywhispercpp.model as pwc
    WHISPER_AVAILABLE = True
except ImportError:
    pwc = None
    WHISPER_AVAILABLE = False

# Scipy
try:
    from scipy.io import wavfile
    SCIPY_AVAILABLE = True
except ImportError:
    wavfile = None
    SCIPY_AVAILABLE = False

# PyAudio
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    pyaudio = None
    PYAUDIO_AVAILABLE = False


