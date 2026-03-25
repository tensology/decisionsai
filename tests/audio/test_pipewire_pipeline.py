#!/usr/bin/env python3
"""
Test PipeWire + Hardware Output Pipeline

Tests if creating a Pipecat pipeline with pipewire input and hardware output
causes a crash, reproducing the issue reported by the user.
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

import sounddevice as sd
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, AudioRawFrame
from pipecat.audio.vad.silero import SileroVADAnalyzer

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MockSink(FrameProcessor):
    """Simple sink that just consumes frames"""
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, AudioRawFrame):
            logger.debug(f"Received audio frame: {len(frame.audio)} samples")
        else:
            logger.debug(f"Received frame: {type(frame).__name__}")


def find_device_index(device_name: str, is_input: bool) -> int:
    """Find device index by name"""
    devices = sd.query_devices()
    for i, device in enumerate(devices):
        if device['name'] == device_name:
            if is_input and device.get('max_input_channels', 0) > 0:
                return i
            elif not is_input and device.get('max_output_channels', 0) > 0:
                return i
    return None


async def test_transport_creation_only():
    """Test ONLY creating a transport with pipewire input and hardware output (no pipeline execution)"""
    logger.info("=" * 60)
    logger.info("Testing PipeWire + Hardware Output Transport Creation")
    logger.info("=" * 60)
    
    # Find devices
    logger.info("\n1. Finding devices...")
    pipewire_idx = find_device_index("pipewire", is_input=True)
    hardware_output_idx = find_device_index("HDA Intel PCH: ALC3246 Analog (hw:0,0)", is_input=False)
    
    if pipewire_idx is None:
        logger.error("❌ pipewire device not found!")
        return False
    
    if hardware_output_idx is None:
        logger.error("❌ Hardware output device not found!")
        return False
    
    logger.info(f"✅ Found pipewire input device at index {pipewire_idx}")
    logger.info(f"✅ Found hardware output device at index {hardware_output_idx}")
    
    # Get device info
    devices = sd.query_devices()
    input_device = devices[pipewire_idx]
    output_device = devices[hardware_output_idx]
    
    logger.info(f"   Input device: {input_device['name']}")
    logger.info(f"     - Max input channels: {input_device.get('max_input_channels', 0)}")
    logger.info(f"     - Default sample rate: {input_device.get('default_samplerate', 0)}")
    logger.info(f"   Output device: {output_device['name']}")
    logger.info(f"     - Max output channels: {output_device.get('max_output_channels', 0)}")
    logger.info(f"     - Default sample rate: {output_device.get('default_samplerate', 0)}")
    
    # Test parameters (matching session.py)
    input_sample_rate = 16000  # Whisper requirement
    output_sample_rate = 24000  # Kokoro requirement
    
    logger.info(f"\n2. Creating VAD Analyzer...")
    try:
        vad_analyzer = SileroVADAnalyzer()
        logger.info("   Attempting to set VAD params...")
        try:
            vad_analyzer.set_params(start_secs=0.1)
            logger.info("✅ VAD analyzer created and configured")
        except Exception as e:
            logger.warning(f"⚠️  VAD set_params failed: {e}")
            logger.info("   Continuing with default VAD params...")
    except Exception as e:
        logger.error(f"❌ Failed to create VAD analyzer: {e}", exc_info=True)
        return False
    
    logger.info(f"\n3. Creating LocalAudioTransport...")
    logger.info(f"   Input sample rate: {input_sample_rate} Hz")
    logger.info(f"   Output sample rate: {output_sample_rate} Hz")
    logger.info(f"   Input device index: {pipewire_idx}")
    logger.info(f"   Output device index: {hardware_output_idx}")
    logger.info(f"   VAD analyzer: {vad_analyzer}")
    
    try:
        # Create transport with specific device indices and VAD analyzer
        logger.info("   Attempting to create transport...")
        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                sample_rate=input_sample_rate,
                output_sample_rate=output_sample_rate,
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_analyzer=vad_analyzer,
                input_device_index=pipewire_idx,
                output_device_index=hardware_output_idx
            )
        )
        logger.info("✅ Transport created successfully!")
        logger.info("   Transport object: %s", transport)
        
        # Check if streams are opened
        if hasattr(transport, '_in_stream'):
            in_stream = getattr(transport, '_in_stream', None)
            if in_stream:
                logger.info("✅ Input stream opened: %s", in_stream)
            else:
                logger.warning("⚠️  Input stream not opened yet")
        
        if hasattr(transport, '_out_stream'):
            out_stream = getattr(transport, '_out_stream', None)
            if out_stream:
                logger.info("✅ Output stream opened: %s", out_stream)
            else:
                logger.warning("⚠️  Output stream not opened yet")
        
        # Clean up
        logger.info("\n4. Cleaning up transport...")
        if hasattr(transport, 'cleanup'):
            try:
                await transport.cleanup()
            except:
                pass
        logger.info("✅ Transport cleaned up")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to create transport: {e}", exc_info=True)
        import traceback
        logger.error("Full traceback:\n%s", traceback.format_exc())
        return False


async def test_pipeline_creation():
    """Test creating a pipeline with pipewire input and hardware output"""
    logger.info("=" * 60)
    logger.info("Testing PipeWire + Hardware Output Pipeline")
    logger.info("=" * 60)
    
    # Find devices
    logger.info("\n1. Finding devices...")
    pipewire_idx = find_device_index("pipewire", is_input=True)
    hardware_output_idx = find_device_index("HDA Intel PCH: ALC3246 Analog (hw:0,0)", is_input=False)
    
    if pipewire_idx is None:
        logger.error("❌ pipewire device not found!")
        return False
    
    if hardware_output_idx is None:
        logger.error("❌ Hardware output device not found!")
        return False
    
    logger.info(f"✅ Found pipewire input device at index {pipewire_idx}")
    logger.info(f"✅ Found hardware output device at index {hardware_output_idx}")
    
    # Test parameters (matching session.py)
    input_sample_rate = 16000  # Whisper requirement
    output_sample_rate = 24000  # Kokoro requirement
    
    # REPLICATE THE EXACT SCENARIO FROM THE LOGS:
    # The hardware device falls back to None because sample rate doesn't match
    # This is what causes the "Illegal combination" error
    logger.info(f"\n1.5. Simulating device fallback (as in actual app)...")
    logger.info(f"   Hardware device default sample rate: 44100 Hz")
    logger.info(f"   Required output sample rate: {output_sample_rate} Hz")
    logger.info(f"   Difference: {abs(44100 - output_sample_rate)} Hz > 1000 Hz threshold")
    logger.info(f"   → Output device will fall back to None (system default)")
    
    # Use None for output to replicate the fallback behavior
    output_device_idx = None
    
    logger.info(f"\n2. Creating VAD Analyzer...")
    try:
        vad_analyzer = SileroVADAnalyzer()
        try:
            vad_analyzer.set_params(start_secs=0.1)
            logger.info("✅ VAD analyzer created and configured")
        except Exception as e:
            logger.warning(f"⚠️  VAD set_params failed: {e}")
    except Exception as e:
        logger.error(f"❌ Failed to create VAD analyzer: {e}", exc_info=True)
        return False
    
    logger.info(f"\n3. Creating LocalAudioTransport...")
    logger.info(f"   Input sample rate: {input_sample_rate} Hz")
    logger.info(f"   Output sample rate: {output_sample_rate} Hz")
    logger.info(f"   Input device index: {pipewire_idx} (pipewire)")
    logger.info(f"   Output device index: {output_device_idx} (None - system default, after fallback)")
    logger.info(f"   VAD analyzer: {vad_analyzer}")
    
    try:
        # Create transport with pipewire input and None output (replicating the fallback)
        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                sample_rate=input_sample_rate,
                output_sample_rate=output_sample_rate,
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_analyzer=vad_analyzer,
                input_device_index=pipewire_idx,
                output_device_index=output_device_idx  # None - this is the key!
            )
        )
        logger.info("✅ Transport created successfully!")
        
    except Exception as e:
        logger.error(f"❌ Failed to create transport: {e}", exc_info=True)
        import traceback
        logger.error("Full traceback:\n%s", traceback.format_exc())
        return False
    
    # Create a simple pipeline
    logger.info("\n4. Creating pipeline...")
    try:
        sink = MockSink()
        pipeline = Pipeline([
            transport.input(),
            sink,
            transport.output()
        ])
        logger.info("✅ Pipeline created successfully!")
        
    except Exception as e:
        logger.error(f"❌ Failed to create pipeline: {e}", exc_info=True)
        return False
    
    # Try to run the pipeline briefly
    logger.info("\n5. Testing pipeline execution (2 seconds)...")
    runner = PipelineRunner()
    task = PipelineTask(pipeline)
    
    try:
        # Run for only 2 seconds
        await asyncio.wait_for(runner.run(task), timeout=2.0)
        logger.info("⚠️  Pipeline completed before timeout")
    except asyncio.TimeoutError:
        logger.info("✅ Pipeline ran for 2 seconds without crashing!")
        logger.info("   Stopping pipeline...")
        await task.stop()
        logger.info("✅ Pipeline stopped successfully")
    except Exception as e:
        logger.error(f"❌ Pipeline crashed during execution: {e}", exc_info=True)
        try:
            await task.stop()
        except:
            pass
        return False
    
    logger.info("\n✅ Test completed successfully!")
    return True


async def test_with_defaults():
    """Test with default devices (None) for comparison"""
    logger.info("\n" + "=" * 60)
    logger.info("Testing with Default Devices (for comparison)")
    logger.info("=" * 60)
    
    logger.info("\n1. Creating LocalAudioTransport with default devices...")
    logger.info("   Input sample rate: 16000 Hz")
    logger.info("   Output sample rate: 24000 Hz")
    logger.info("   Input device index: None (system default)")
    logger.info("   Output device index: None (system default)")
    
    try:
        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                sample_rate=16000,
                output_sample_rate=24000,
                audio_in_enabled=True,
                audio_out_enabled=True,
                input_device_index=None,
                output_device_index=None
            )
        )
        logger.info("✅ Transport created successfully with defaults!")
        
        sink = MockSink()
        pipeline = Pipeline([
            transport.input(),
            sink,
            transport.output()
        ])
        logger.info("✅ Pipeline created successfully with defaults!")
        
        runner = PipelineRunner()
        task = PipelineTask(pipeline)
        
        logger.info("\n2. Testing pipeline execution (3 seconds)...")
        await asyncio.wait_for(runner.run(task), timeout=3.0)
        
    except asyncio.TimeoutError:
        logger.info("✅ Default pipeline ran for 3 seconds without crashing!")
        await task.stop()
        return True
    except Exception as e:
        logger.error(f"❌ Default pipeline failed: {e}", exc_info=True)
        try:
            await task.stop()
        except:
            pass
        return False


async def main():
    """Run all tests"""
    try:
        # First test: Just transport creation (no pipeline execution)
        logger.info("\n" + "=" * 80)
        logger.info("TEST 1: Transport Creation Only (No Pipeline Execution)")
        logger.info("=" * 80)
        success1 = await test_transport_creation_only()
        
        if not success1:
            logger.error("\n❌ Transport creation failed - this is where the crash happens!")
            return 1
        
        # Second test: Full pipeline creation and brief execution
        logger.info("\n" + "=" * 80)
        logger.info("TEST 2: Full Pipeline Creation and Execution")
        logger.info("=" * 80)
        success2 = await test_pipeline_creation()
        
        # Test defaults for comparison
        logger.info("\n" + "=" * 80)
        logger.info("TEST 3: Default Devices (for comparison)")
        logger.info("=" * 80)
        success3 = await test_with_defaults()
        
        logger.info("\n" + "=" * 60)
        logger.info("Test Results Summary")
        logger.info("=" * 60)
        logger.info(f"Transport Creation Only: {'✅ PASSED' if success1 else '❌ FAILED'}")
        logger.info(f"Full Pipeline: {'✅ PASSED' if success2 else '❌ FAILED'}")
        logger.info(f"Default devices: {'✅ PASSED' if success3 else '❌ FAILED'}")
        
        if not success1:
            logger.error("\n❌ The pipewire + hardware combination fails at transport creation!")
            logger.error("   This confirms the issue reported by the user.")
            return 1
        elif not success2:
            logger.error("\n⚠️  Transport creation works, but pipeline execution fails!")
            return 1
        else:
            logger.info("\n✅ The pipewire + hardware combination works!")
            return 0
            
    except KeyboardInterrupt:
        logger.info("\n⚠️  Test interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"❌ Test suite crashed: {e}", exc_info=True)
        import traceback
        logger.error("Full traceback:\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

