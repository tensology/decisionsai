import asyncio
import logging
import sys
import os

import pytest

pytest.importorskip("pipecat.frames.frames")

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from pipecat.frames.frames import StartFrame, TextFrame, EndFrame, AudioRawFrame, TTSStartedFrame, TTSStoppedFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from distr.core.agent.services.tts.kokoro import KokoroTTSService

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("test_kokoro")

class MockOutput(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.received_frames = []

    async def process_frame(self, frame, direction):
        print(f"MockOutput received: {type(frame)}")
        self.received_frames.append(frame)
        await super().process_frame(frame, direction)

async def main():
    print("--- Testing KokoroTTSService ---")
    
    # Mock paths (ensure these exist or use dummy ones if Kokoro supports it, 
    # but Kokoro needs real models. Assuming user has them at the usual place)
    model_path = "distr/core/agent/models/kokoro-v1.0.onnx"
    voices_path = "distr/core/agent/models/voices-v1.0.bin"
    
    if not os.path.exists(model_path):
        print(f"WARNING: Model not found at {model_path}. Test might fail if Kokoro tries to load it.")
        # We can try to mock Kokoro if needed, but better to test with real init if possible.
        # For now, let's assume paths are correct relative to project root.

    try:
        tts = KokoroTTSService(
            model_path=model_path,
            voices_path=voices_path,
            voice_name="af_heart"
        )
        print("✅ Initialization successful")
    except Exception as e:
        print(f"❌ Initialization failed: {e}")
        return

    output = MockOutput()
    
    # Manually link for simple test (or use Pipeline)
    # tts.link(output) # FrameProcessor.link is complex
    
    # Use Pipeline to ensure correct setup
    pipeline = Pipeline([tts, output])
    task = PipelineTask(pipeline)
    runner = PipelineRunner()

    print("\n--- Running Pipeline ---")
    
    # We need to inject frames. PipelineTask usually pulls from source.
    # But we want to push to TTS.
    # We can use a MockSource.
    
    class MockSource(FrameProcessor):
        def __init__(self):
            super().__init__()
        
        async def process_frame(self, frame, direction):
            print(f"MockSource received: {type(frame)}")
            if isinstance(frame, StartFrame):
                # Initialize self (start tasks)
                await super().process_frame(frame, direction)
                # Propagate StartFrame
                print("MockSource pushing StartFrame")
                await self.push_frame(frame, direction)
            else:
                # Just push other frames (bypass queue/super for simplicity in test)
                print(f"MockSource pushing {type(frame)}")
                await self.push_frame(frame, direction)

    source = MockSource()
    pipeline = Pipeline([source, tts, output])
    task = PipelineTask(pipeline)
    
    # Run runner in background
    runner_task = asyncio.create_task(runner.run(task))
    
    # Wait for start
    await asyncio.sleep(1)
    
    print("\n--- Sending TextFrame ---")
    # Push TextFrame through source
    # Note: PipelineTask manages the loop. We need to inject into the pipeline.
    # Usually we push to the first processor.
    
    await source.process_frame(TextFrame(text="Hello world."), FrameDirection.DOWNSTREAM)
    
    # Wait for processing
    await asyncio.sleep(5)
    
    print("\n--- Checking Output ---")
    audio_frames = [f for f in output.received_frames if isinstance(f, AudioRawFrame)]
    print(f"Received {len(audio_frames)} AudioRawFrames")
    
    if len(audio_frames) > 0:
        print("✅ TTS Generation successful")
    else:
        print("❌ No audio generated")

    await task.stop()
    await runner_task

if __name__ == "__main__":
    # Change cwd to project root to find models
    os.chdir(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../../')))
    asyncio.run(main())
