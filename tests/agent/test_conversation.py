import asyncio
import sys
import os
from loguru import logger
import numpy as np

# Add project root to path
sys.path.append(os.getcwd())

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.frames.frames import (
    StartFrame, EndFrame, TextFrame, TranscriptionFrame,
    UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
    LLMFullResponseEndFrame, CancelFrame
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.local.audio import LocalAudioOutputTransport
from pipecat.frames.frames import AudioRawFrame

# Imports for our services
from distr.core.agent.services.ollama import OllamaLLMService
from distr.core.agent.services.kokoro import KokoroTTSService

# Configure logging
logger.remove()
logger.add(sys.stderr, level="DEBUG")

logger.add(sys.stderr, level="DEBUG")

class DebugAudioTransport(LocalAudioOutputTransport):
    async def process_frame(self, frame, direction):
        if isinstance(frame, AudioRawFrame):
            audio_data = np.frombuffer(frame.audio, dtype=np.int16)
            if len(audio_data) > 0:
                rms = np.sqrt(np.mean(audio_data**2))
                max_val = np.max(np.abs(audio_data))
                logger.info(f"[DebugTransport] Audio Frame: size={len(frame.audio)}, rms={rms:.2f}, max={max_val}")
            else:
                logger.warning("[DebugTransport] Empty Audio Frame")
        await super().process_frame(frame, direction)

class MockConversationalSource(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._scenario_task = None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            # Start the scenario
            self._scenario_task = asyncio.create_task(self._run_scenario())
            # Propagate StartFrame
            await self.push_frame(frame, direction)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self.push_frame(frame, direction)

    async def _run_scenario(self):
        logger.info("--- SCENARIO START ---")
        
        # 1. Wait for Welcome Message (Ollama should trigger this on StartFrame if configured, 
        #    but our Ollama service waits for a specific trigger or just sends it? 
        #    Let's check Ollama service. It sends welcome message if `_run_messages` is called? 
        #    No, it sends it if it's in the messages list? 
        #    Actually, `OllamaLLMService` has `_generate_welcome_message` but it's not called automatically in `process_frame`.
        #    Wait, `AgentSession` calls `llm.generate_welcome_message()`? No.
        #    Let's assume for this test we trigger it manually or simulate the trigger.
        #    Actually, `OllamaLLMService` doesn't have a public `generate_welcome_message`.
        #    It has `_generate_welcome_message`.
        #    Let's see how `session.py` does it.
        #    `session.py` doesn't seem to call it explicitly. 
        #    Ah, `OllamaLLMService` might send it on initialization? No.
        #    Let's look at `ollama.py` again.
        #    It has `_generate_welcome_message`.
        #    It seems it's not called automatically.
        #    Wait, `session.py` has `await self._llm.process_frame(TextFrame(text="..."), ...)`?
        #    No.
        #    Let's just simulate user input first.
        
        # Wait a bit for startup
        await asyncio.sleep(2)
        
        # 2. Simulate "Hello"
        logger.info("--- SIMULATING USER INPUT: 'Hello' ---")
        await self.push_frame(UserStartedSpeakingFrame())
        await asyncio.sleep(0.5)
        await self.push_frame(UserStoppedSpeakingFrame())
        await self.push_frame(TranscriptionFrame(text="Hello", user_id="user", timestamp=0))
        
        # Wait for response to play out (approx 5 seconds)
        await asyncio.sleep(5)
        
        # 3. Simulate "Tell me a story about a dog named spot"
        logger.info("--- SIMULATING USER INPUT: 'Tell me a story about a dog named spot' ---")
        await self.push_frame(UserStartedSpeakingFrame())
        await asyncio.sleep(1.0)
        await self.push_frame(UserStoppedSpeakingFrame())
        await self.push_frame(TranscriptionFrame(text="Tell me a story about a dog named spot", user_id="user", timestamp=0))
        
        # Wait 5 seconds into the story
        logger.info("--- WAITING 5 SECONDS ---")
        await asyncio.sleep(5)
        
        # 4. Interruption
        logger.info("--- SIMULATING INTERRUPTION ---")
        await self.push_frame(UserStartedSpeakingFrame())
        # This should trigger CancelFrame in Ollama and Kokoro
        
        await asyncio.sleep(1.0)
        await self.push_frame(UserStoppedSpeakingFrame())
        await self.push_frame(TranscriptionFrame(text="Stop, tell me about a cat instead", user_id="user", timestamp=0))
        
        # Wait for new response
        logger.info("--- WAITING FOR NEW RESPONSE ---")
        await asyncio.sleep(10)
        
        logger.info("--- SCENARIO END ---")
        # End the pipeline
        await self.push_frame(EndFrame())

import psutil

async def monitor_memory():
    process = psutil.Process(os.getpid())
    logger.info("--- MEMORY MONITOR STARTED ---")
    try:
        while True:
            mem_info = process.memory_info()
            rss_mb = mem_info.rss / 1024 / 1024
            logger.info(f"MEMORY: {rss_mb:.2f} MB")
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("--- MEMORY MONITOR STOPPED ---")

async def main():
    # Start memory monitor
    mem_task = asyncio.create_task(monitor_memory())

    # 1. Setup Services
    # We need to mock the settings or pass dummy ones
    # Ollama needs a 'messages' list? No, it manages its own history.
    
    llm = OllamaLLMService(
        model_name="gemma3:4b", # Using default from session.py
        base_url="http://localhost:11434"
    )
    
    # Paths to models
    base_dir = os.getcwd()
    models_dir = os.path.join(base_dir, "distr", "agent", "models")
    kokoro_model = os.path.join(models_dir, "kokoro-v1.0.onnx")
    kokoro_voices = os.path.join(models_dir, "voices-v1.0.bin")
    
    if not os.path.exists(kokoro_model) or not os.path.exists(kokoro_voices):
        logger.error(f"Models not found in {models_dir}")
        return

    tts = KokoroTTSService(
        model_path=kokoro_model,
        voices_path=kokoro_voices,
        voice_name="af_heart"
    )
    
    # 2. Setup Transport
    # We use LocalAudioOutputTransport to hear the result
    import pyaudio
    from pipecat.transports.local.audio import LocalAudioOutputTransport, LocalAudioTransportParams
    
    # 2. Setup Transport
    # We use LocalAudioOutputTransport to hear the result
    # We need to create a PyAudio instance
    pa = pyaudio.PyAudio()
    
    params = LocalAudioTransportParams(
        audio_out_enabled=True,
        audio_in_enabled=False, # We are mocking input
        output_sample_rate=24000 # Kokoro uses 24kHz
    )
    
    transport = DebugAudioTransport(py_audio=pa, params=params)
    
    # 3. Setup Source
    source = MockConversationalSource()
    
    # 4. Build Pipeline
    pipeline = Pipeline([source, llm, tts, transport])
    
    # 5. Run Pipeline
    runner = PipelineRunner()
    task = PipelineTask(pipeline)
    
    logger.info("--- STARTING PIPELINE ---")
    await runner.run(task)
    logger.info("--- PIPELINE FINISHED ---")

if __name__ == "__main__":
    asyncio.run(main())
