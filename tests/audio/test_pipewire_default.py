import asyncio
import logging
import os
import sys

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

import pytest

pytestmark = pytest.mark.live_audio

pytest.importorskip("pipecat.transports.local.audio")

from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.frames.frames import StartFrame, EndFrame
from pipecat.processors.frame_processor import FrameProcessor

import sounddevice as sd

class MockSink(FrameProcessor):
    async def process_frame(self, frame, direction):
        pass

async def test_pipewire_default_combo():
    """Test creating a pipeline with pipewire input and default output"""
    
    print(f"\n{'='*60}")
    print(f"Testing Pipeline: PipeWire Input + Default Output")
    print(f"{'='*60}")
    
    # 1. Find devices
    logger.info("1. Finding devices...")
    devices = sd.query_devices()
    
    pipewire_idx = None
    default_idx = None
    
    for i, d in enumerate(devices):
        name = d['name'].lower()
        # Find pipewire
        if 'pipewire' in name and d['max_input_channels'] > 0:
            pipewire_idx = i
        
        # Find default (exact match preferred)
        if name == 'default' and d['max_output_channels'] > 0:
            default_idx = i
            
    if pipewire_idx is None:
        logger.error("❌ Could not find 'pipewire' input device")
        return False
        
    if default_idx is None:
        # Fallback to searching for anything with 'default'
        for i, d in enumerate(devices):
            if 'default' in d['name'].lower() and d['max_output_channels'] > 0:
                default_idx = i
                break
    
    if default_idx is None:
        logger.error("❌ Could not find 'default' output device")
        return False
        
    logger.info(f"   Input: pipewire (index {pipewire_idx})")
    logger.info(f"   Output: default (index {default_idx})")
    
    # 2. Create Transport
    logger.info("\n2. Creating LocalAudioTransport...")
    try:
        vad_analyzer = SileroVADAnalyzer()
        
        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                sample_rate=16000,
                output_sample_rate=24000,
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_analyzer=vad_analyzer,
                input_device_index=pipewire_idx,
                output_device_index=default_idx
            )
        )
        logger.info("✅ Transport created successfully")
        
        # Check streams immediately (should be None)
        in_stream = getattr(transport.input(), '_in_stream', None)
        logger.info(f"   Immediate check - Input stream: {in_stream}")
        
    except Exception as e:
        logger.error(f"❌ Failed to create transport: {e}", exc_info=True)
        return False
        
    # 3. Run Pipeline
    logger.info("\n3. Running Pipeline...")
    try:
        pipeline = Pipeline([
            transport.input(),
            MockSink(),
            transport.output()
        ])
        
        task = PipelineTask(pipeline)
        runner = PipelineRunner()
        
        # Run in background task
        logger.info("   Starting pipeline runner...")
        run_task = asyncio.create_task(runner.run(task))
        
        # Monitor streams
        for i in range(10):
            await asyncio.sleep(0.5)
            in_stream = getattr(transport.input(), '_in_stream', None)
            out_stream = getattr(transport.output(), '_out_stream', None)
            
            in_status = "OPEN" if in_stream else "None"
            out_status = "OPEN" if out_stream else "None"
            
            logger.info(f"   [{i*0.5}s] Streams: Input={in_status}, Output={out_status}")
            
            if in_stream and out_stream:
                logger.info("✅ Both streams opened successfully!")
                break
        
        # Stop
        logger.info("   Stopping pipeline...")
        await task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
            
        logger.info("✅ Test finished successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Pipeline error: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    try:
        asyncio.run(test_pipewire_default_combo())
    except KeyboardInterrupt:
        pass
