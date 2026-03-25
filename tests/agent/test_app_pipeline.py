import asyncio
import sys
import os
import logging
from unittest.mock import MagicMock, patch
import numpy as np

# Add project root to path
sys.path.append(os.getcwd())

from distr.core.agent.session import AgentSession
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.frames.frames import (
    StartFrame, EndFrame, AudioRawFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
    TranscriptionFrame, TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame
)
from pipecat.processors.frame_processor import FrameProcessor

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class MockTransport(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.captured_frames = []
    
    async def process_frame(self, frame, direction):
        self.captured_frames.append(frame)
        if isinstance(frame, TranscriptionFrame):
            logger.info(f"[MockTransport] Transcription: {frame.text}")
        elif isinstance(frame, TextFrame):
            logger.info(f"[MockTransport] TTS Text: {frame.text}")
        elif isinstance(frame, AudioRawFrame):
            audio_data = np.frombuffer(frame.audio, dtype=np.int16)
            rms = np.sqrt(np.mean(audio_data**2)) if len(audio_data) > 0 else 0
            logger.info(f"[MockTransport] Audio Output: size={len(frame.audio)}, rms={rms:.2f}")
        
        elif isinstance(frame, StartFrame):
            logger.info("[MockTransport] Received StartFrame")
        elif isinstance(frame, EndFrame):
            logger.info("[MockTransport] Received EndFrame")
        
        await super().process_frame(frame, direction)
        
        # Propagate system frames
        if isinstance(frame, (StartFrame, EndFrame)):
            await self.push_frame(frame, direction)

    def input(self):
        return self

    def output(self):
        return self

class TestAgentSession(AgentSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mock_transport = MockTransport()

    def _create_pipeline(self):
        # Create services (using real services)
        self._create_services()
        
        # Use mock transport instead of LocalAudioTransport
        self.transport = self.mock_transport
        
        # Create pipeline
        self.pipeline = Pipeline(
            [
                self.transport.input(),
                self.stt_service,
                self.llm_service,
                self.tts_service,
                self.transport.output()
            ]
        )
        
        self.task = PipelineTask(self.pipeline)
        self.runner = PipelineRunner()
        logger.info("Test Pipeline created with MockTransport")

async def run_test():
    # Mock settings
    settings = {
        'hands_free_mode': True,
        'input_speech': 'Whisper',
        'agent_provider': 'Ollama',
        'agent_model': 'gemma3:4b',
        'tts_provider': 'Kokoro (Offline)',
        'kokoro_voice': 'af_heart',
        'vad': {'enabled': True}
    }
    
    session = TestAgentSession(settings=settings)
    
    # Start session in a separate task
    session_task = asyncio.create_task(session._run_pipeline())
    
    # Wait for startup
    await asyncio.sleep(5)
    
    logger.info("--- SIMULATING INPUT ---")
    
    # Simulate User Speech
    # We need to push frames to the pipeline's input (which is mock_transport)
    # But mock_transport is at the beginning of the pipeline.
    # So we call process_frame on the NEXT processor? 
    # No, we push to mock_transport's downstream.
    
    # Actually, in the pipeline: [transport.input(), stt, llm, tts, transport.output()]
    # transport.input() is the first processor.
    # If we push to it, it goes to STT.
    
    # Simulate "Hello" audio
    # We'll just send silence and hope Whisper picks it up? No, we need speech.
    # Or we can inject TranscriptionFrame directly to test the rest of the pipeline?
    # The user complained about "transportation of the transcription".
    # So let's inject TranscriptionFrame and see if it reaches LLM.
    
    logger.info("--- INJECTING TRANSCRIPTION FRAME ---")
    tf = TranscriptionFrame(text="Hello", user_id="user", timestamp=0)
    await session.transport.push_frame(tf)
    
    # Wait for processing
    await asyncio.sleep(10)
    
    # Check if we got audio output
    audio_frames = [f for f in session.mock_transport.captured_frames if isinstance(f, AudioRawFrame)]
    if audio_frames:
        logger.info(f"✅ SUCCESS: Received {len(audio_frames)} audio frames")
    else:
        logger.error("❌ FAILURE: No audio frames received")
        
    # Stop session
    await session.runner.stop()
    await session_task

if __name__ == "__main__":
    asyncio.run(run_test())
