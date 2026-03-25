import asyncio
import sys
import os
import psutil
import numpy as np
from loguru import logger

# Add project root to path
sys.path.append(os.getcwd())

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.frames.frames import (
    StartFrame, EndFrame, AudioRawFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame
)
from pipecat.processors.frame_processor import FrameProcessor

# Imports for our services
from distr.core.agent.services.whisper import WhisperSTTService
from distr.core.agent.services.ollama import OllamaLLMService
from distr.core.agent.services.kokoro import KokoroTTSService

# Configure logging
logger.remove()
logger.add(sys.stderr, level="INFO")

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

class MockAudioSource(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._scenario_task = None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._scenario_task = asyncio.create_task(self._run_scenario())
            await self.push_frame(frame, direction)
        elif isinstance(frame, EndFrame):
            await self.push_frame(frame, direction)

    async def _run_scenario(self):
        logger.info("--- SCENARIO START ---")
        await asyncio.sleep(2)
        
        # Simulate sending audio frames (silence/noise)
        # Whisper expects 16kHz audio
        sample_rate = 16000
        duration_sec = 10
        chunk_size = 512 # frames per chunk
        
        # Create dummy audio (silence)
        # Actually, let's send silence so Whisper doesn't trigger (unless we want it to).
        # Or send speech-like noise?
        # If we send silence, Whisper VAD should filter it out.
        # This tests if Whisper/VAD leaks memory on silence.
        
        logger.info(f"--- SENDING {duration_sec}s OF SILENCE ---")
        num_chunks = int(duration_sec * sample_rate / chunk_size)
        silence = bytes(chunk_size * 2) # 16-bit PCM = 2 bytes per sample
        
        for i in range(num_chunks):
            frame = AudioRawFrame(audio=silence, sample_rate=sample_rate, num_channels=1)
            await self.push_frame(frame)
            await asyncio.sleep(chunk_size / sample_rate) # Real-time simulation
            
        # Now simulate "User Started Speaking" manually to trigger pipeline if VAD didn't?
        # Or just let it run.
        
        logger.info("--- WAITING ---")
        await asyncio.sleep(5)
        
        logger.info("--- SCENARIO END ---")
        await self.push_frame(EndFrame())

class MockSink(FrameProcessor):
    def __init__(self):
        super().__init__()
        
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        # Just consume frames
        pass

async def main():
    # Start memory monitor
    mem_task = asyncio.create_task(monitor_memory())

    # 1. Setup Services
    # Whisper
    # We need a model path. session.py uses config.
    # Let's assume default path or find one.
    # `pywhispercpp` usually downloads to `~/Library/Application Support/pywhispercpp/models/ggml-base.en.bin` on Mac.
    # We can pass "base.en" and it should load.
    whisper = WhisperSTTService(model_path="base.en")
    
    # Ollama
    llm = OllamaLLMService(
        model_name="gemma3:4b",
        base_url="http://localhost:11434"
    )
    
    # Kokoro
    base_dir = os.getcwd()
    models_dir = os.path.join(base_dir, "distr", "agent", "models")
    kokoro_model = os.path.join(models_dir, "kokoro-v1.0.onnx")
    kokoro_voices = os.path.join(models_dir, "voices-v1.0.bin")
    
    if not os.path.exists(kokoro_model):
        logger.warning(f"Kokoro model not found at {kokoro_model}, skipping Kokoro for now or using dummy")
        # If we skip Kokoro, we can't test full pipeline.
        # But we verified Kokoro exists in previous test.
    
    tts = KokoroTTSService(
        model_path=kokoro_model,
        voices_path=kokoro_voices,
        voice_name="af_heart"
    )
    
    # 2. Setup Pipeline
    source = MockAudioSource()
    sink = MockSink()
    
    # Source -> Whisper -> Ollama -> Kokoro -> Sink
    pipeline = Pipeline([source, whisper, llm, tts, sink])
    
    # 3. Run
    runner = PipelineRunner()
    task = PipelineTask(pipeline)
    
    logger.info("--- STARTING PIPELINE ---")
    await runner.run(task)
    logger.info("--- PIPELINE FINISHED ---")

if __name__ == "__main__":
    asyncio.run(main())
