import asyncio
import sys
import os
import psutil
from loguru import logger

# Add project root to path
sys.path.append(os.getcwd())

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import EndFrame

# Import LocalAudioTransport
try:
    import pyaudio
    from pipecat.transports.local.audio import LocalAudioOutputTransport, LocalAudioTransport, LocalAudioTransportParams
except ImportError:
    logger.error("PyAudio or Pipecat transport not found")
    sys.exit(1)

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

    logger.info("--- INITIALIZING TRANSPORT ---")
    # Use default devices
    params = LocalAudioTransportParams(
        audio_out_enabled=True,
        audio_in_enabled=True, 
        output_sample_rate=16000,
        sample_rate=16000
    )
    
    transport = LocalAudioTransport(params)
    
    sink = MockSink()
    
    # Pipeline: Mic -> Sink
    # We don't output audio to speaker to avoid feedback loop if we are just testing leak
    # But user reported leak in full app.
    # Let's try Input -> Sink first.
    
    pipeline = Pipeline([transport.input(), sink])
    
    runner = PipelineRunner()
    task = PipelineTask(pipeline)
    
    logger.info("--- STARTING PIPELINE (Running for 30s) ---")
    
    # Run for 30 seconds
    try:
        await asyncio.wait_for(runner.run(task), timeout=30)
    except asyncio.TimeoutError:
        logger.info("--- TIMEOUT REACHED ---")
        await task.stop()
    
    logger.info("--- PIPELINE FINISHED ---")

if __name__ == "__main__":
    asyncio.run(main())
