#!/usr/bin/env python3
"""
Transcription Test Suite

Tests Whisper STT service with various scenarios:
1. Direct transcription of audio bytes
2. Audio device detection and fallback
3. Real-time audio recording and transcription
4. Sample rate handling
5. Device selection with sample rate validation
"""

import asyncio
import sys
import os

import pytest

pytest.importorskip("sounddevice")

import numpy as np
import sounddevice as sd
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from distr.core.agent.services.stt.whisper import WhisperSTTService
from distr.core.agent.session import AgentSession
from pipecat.frames.frames import AudioRawFrame, TranscriptionFrame

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TranscriptionTest:
    """Test suite for Whisper transcription"""
    
    def __init__(self):
        self.stt_service = None
        self.transcriptions = []
        
    async def setup(self):
        """Initialize Whisper STT service"""
        logger.info("Setting up Whisper STT service...")
        try:
            self.stt_service = WhisperSTTService(
                model_path="base.en",
                event_queue=None,
                is_hands_free=False
            )
            logger.info("✅ Whisper STT service initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize Whisper STT: {e}")
            return False
    
    def test_device_detection(self):
        """Test audio device detection and fallback mechanism"""
        logger.info("\n" + "="*60)
        logger.info("TEST 1: Audio Device Detection and Fallback")
        logger.info("="*60)
        
        # Create a test session
        settings = {
            'input_device': 'HDA Intel PCH: ALC3246 Analog (hw:0,0)',
            'output_device': 'HDA Intel PCH: ALC3246 Analog (hw:0,0)'
        }
        
        session = AgentSession(settings=settings)
        
        # Test input device with 16kHz requirement
        logger.info("Testing input device with 16kHz requirement...")
        input_idx = session._get_device_index(
            'HDA Intel PCH: ALC3246 Analog (hw:0,0)',
            is_input=True,
            required_sample_rate=16000
        )
        
        if input_idx is not None:
            devices = sd.query_devices()
            device_name = devices[input_idx]['name']
            logger.info(f"✅ Input device index: {input_idx}")
            logger.info(f"   Device name: {device_name}")
            
            if 'pulse' in device_name.lower() or 'default' in device_name.lower():
                logger.info("   ✅ Correctly fell back to virtual device for resampling")
            else:
                logger.warning(f"   ⚠️  Using hardware device directly: {device_name}")
        else:
            logger.error("❌ Failed to get input device index")
            return False
        
        # Test output device with 24kHz requirement
        logger.info("\nTesting output device with 24kHz requirement...")
        output_idx = session._get_device_index(
            'HDA Intel PCH: ALC3246 Analog (hw:0,0)',
            is_input=False,
            required_sample_rate=24000
        )
        
        if output_idx is not None:
            devices = sd.query_devices()
            device_name = devices[output_idx]['name']
            logger.info(f"✅ Output device index: {output_idx}")
            logger.info(f"   Device name: {device_name}")
        else:
            logger.error("❌ Failed to get output device index")
            return False
        
        logger.info("\n✅ Device detection test passed!")
        return True
    
    def test_list_audio_devices(self):
        """List all available audio devices"""
        logger.info("\n" + "="*60)
        logger.info("TEST 2: List Available Audio Devices")
        logger.info("="*60)
        
        try:
            devices = sd.query_devices()
            logger.info(f"\nFound {len(devices)} audio devices:\n")
            
            input_devices = []
            output_devices = []
            
            for i, device in enumerate(devices):
                name = device['name']
                max_in = device.get('max_input_channels', 0)
                max_out = device.get('max_output_channels', 0)
                default_sr = device.get('default_samplerate', 'N/A')
                
                device_type = []
                if max_in > 0:
                    device_type.append(f"IN({max_in})")
                    input_devices.append((i, name, default_sr))
                if max_out > 0:
                    device_type.append(f"OUT({max_out})")
                    output_devices.append((i, name, default_sr))
                
                if device_type:
                    logger.info(f"  [{i:2d}] {name}")
                    logger.info(f"       Type: {', '.join(device_type)}, Default SR: {default_sr}Hz")
            
            logger.info(f"\n✅ Found {len(input_devices)} input devices and {len(output_devices)} output devices")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to list devices: {e}")
            return False
    
    async def test_transcribe_silence(self):
        """Test transcription with silence (should produce empty or minimal output)"""
        logger.info("\n" + "="*60)
        logger.info("TEST 3: Transcribe Silence")
        logger.info("="*60)
        
        if not self.stt_service:
            logger.error("❌ STT service not initialized")
            return False
        
        # Generate 2 seconds of silence (16kHz, 16-bit PCM)
        sample_rate = 16000
        duration_sec = 2
        num_samples = sample_rate * duration_sec
        silence = np.zeros(num_samples, dtype=np.int16)
        audio_bytes = silence.tobytes()
        
        logger.info(f"Transcribing {duration_sec}s of silence ({len(audio_bytes)} bytes)...")
        
        try:
            transcriptions = []
            async for frame in self.stt_service.run_stt(audio_bytes):
                if isinstance(frame, TranscriptionFrame):
                    transcriptions.append(frame.text)
                    logger.info(f"   Transcription: '{frame.text}'")
            
            if not transcriptions:
                logger.info("✅ No transcription for silence (expected)")
            else:
                logger.warning(f"⚠️  Got transcription for silence: {transcriptions}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Transcription failed: {e}", exc_info=True)
            return False
    
    async def test_transcribe_audio_bytes(self):
        """Test transcription with generated audio (sine wave)"""
        logger.info("\n" + "="*60)
        logger.info("TEST 4: Transcribe Generated Audio")
        logger.info("="*60)
        
        if not self.stt_service:
            logger.error("❌ STT service not initialized")
            return False
        
        # Generate a simple tone (not speech, but tests the pipeline)
        sample_rate = 16000
        duration_sec = 2
        frequency = 440  # A4 note
        
        t = np.linspace(0, duration_sec, sample_rate * duration_sec)
        audio = np.sin(2 * np.pi * frequency * t)
        
        # Convert to int16 PCM
        audio_int16 = (audio * 32767 * 0.3).astype(np.int16)  # 30% volume
        audio_bytes = audio_int16.tobytes()
        
        logger.info(f"Transcribing {duration_sec}s tone at {frequency}Hz ({len(audio_bytes)} bytes)...")
        
        try:
            transcriptions = []
            async for frame in self.stt_service.run_stt(audio_bytes):
                if isinstance(frame, TranscriptionFrame):
                    transcriptions.append(frame.text)
                    logger.info(f"   Transcription: '{frame.text}'")
                else:
                    logger.debug(f"   Frame type: {type(frame).__name__}")
            
            logger.info(f"✅ Transcription completed. Got {len(transcriptions)} result(s)")
            return True
            
        except Exception as e:
            logger.error(f"❌ Transcription failed: {e}", exc_info=True)
            return False
    
    async def test_record_and_transcribe(self, duration=5):
        """Test recording audio from microphone and transcribing it"""
        logger.info("\n" + "="*60)
        logger.info(f"TEST 5: Record and Transcribe ({duration}s)")
        logger.info("="*60)
        
        if not self.stt_service:
            logger.error("❌ STT service not initialized")
            return False
        
        sample_rate = 16000
        
        # Find a suitable input device
        devices = sd.query_devices()
        input_device_idx = None
        
        # Try to use pulse or default first (for resampling)
        for fallback_name in ['pulse', 'default', 'pipewire']:
            for i, d in enumerate(devices):
                if fallback_name in d['name'].lower() and d.get('max_input_channels', 0) > 0:
                    input_device_idx = i
                    logger.info(f"Using device: {d['name']} (index {i})")
                    break
            if input_device_idx is not None:
                break
        
        if input_device_idx is None:
            # Fall back to first available input device
            for i, d in enumerate(devices):
                if d.get('max_input_channels', 0) > 0:
                    input_device_idx = i
                    logger.info(f"Using device: {d['name']} (index {i})")
                    break
        
        if input_device_idx is None:
            logger.error("❌ No input device found")
            return False
        
        logger.info(f"\nRecording {duration} seconds of audio...")
        logger.info("Please speak into your microphone...")
        
        try:
            # Record audio
            audio_data = sd.rec(
                int(sample_rate * duration),
                samplerate=sample_rate,
                channels=1,
                dtype='float32',
                device=input_device_idx
            )
            sd.wait()  # Wait until recording is finished
            
            # Check if we got any audio
            max_amplitude = np.max(np.abs(audio_data))
            logger.info(f"Recorded audio: max amplitude = {max_amplitude:.4f}")
            
            if max_amplitude < 0.001:
                logger.warning("⚠️  Very low audio level - microphone may be muted or not working")
            else:
                logger.info("✅ Audio recorded successfully")
            
            # Convert to int16 PCM bytes
            audio_int16 = (audio_data * 32767).astype(np.int16)
            audio_bytes = audio_int16.tobytes()
            
            logger.info(f"Transcribing {len(audio_bytes)} bytes of audio...")
            
            # Transcribe
            transcriptions = []
            async for frame in self.stt_service.run_stt(audio_bytes):
                if isinstance(frame, TranscriptionFrame):
                    transcriptions.append(frame.text)
                    logger.info(f"   ✅ Transcription: '{frame.text}'")
                else:
                    logger.debug(f"   Frame type: {type(frame).__name__}")
            
            if transcriptions:
                logger.info(f"\n✅ Transcription successful! Got {len(transcriptions)} result(s):")
                for i, text in enumerate(transcriptions, 1):
                    logger.info(f"   {i}. {text}")
            else:
                logger.warning("⚠️  No transcription produced (may be silence or too quiet)")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Recording/transcription failed: {e}", exc_info=True)
            return False
    
    def test_sample_rate_validation(self):
        """Test sample rate validation logic"""
        logger.info("\n" + "="*60)
        logger.info("TEST 6: Sample Rate Validation")
        logger.info("="*60)
        
        session = AgentSession(settings={})
        
        # Test with hardware device that doesn't support 16kHz
        test_cases = [
            ('HDA Intel PCH: ALC3246 Analog (hw:0,0)', 16000, True),  # Should fallback
            ('HDA Intel PCH: ALC3246 Analog (hw:0,0)', 44100, False),  # Should use hardware
            ('pulse', 16000, False),  # Virtual device, no fallback needed
            ('default', 24000, False),  # Virtual device, no fallback needed
        ]
        
        all_passed = True
        for device_name, required_sr, should_fallback in test_cases:
            logger.info(f"\nTesting: {device_name} with {required_sr}Hz...")
            
            try:
                idx = session._get_device_index(
                    device_name,
                    is_input=True,
                    required_sample_rate=required_sr
                )
                
                if idx is not None:
                    devices = sd.query_devices()
                    actual_device = devices[idx]['name']
                    
                    if should_fallback:
                        if 'pulse' in actual_device.lower() or 'default' in actual_device.lower():
                            logger.info(f"   ✅ Correctly fell back to: {actual_device}")
                        else:
                            logger.error(f"   ❌ Expected fallback but got: {actual_device}")
                            all_passed = False
                    else:
                        logger.info(f"   ✅ Using device: {actual_device}")
                else:
                    if device_name == 'HDA Intel PCH: ALC3246 Analog (hw:0,0)':
                        logger.warning(f"   ⚠️  Device not found (may not be available on this system)")
                    else:
                        logger.error(f"   ❌ Device not found: {device_name}")
                        all_passed = False
                        
            except Exception as e:
                logger.error(f"   ❌ Error: {e}")
                all_passed = False
        
        if all_passed:
            logger.info("\n✅ Sample rate validation test passed!")
        else:
            logger.error("\n❌ Some sample rate validation tests failed")
        
        return all_passed


async def run_all_tests():
    """Run all transcription tests"""
    logger.info("\n" + "="*80)
    logger.info("WHISPER TRANSCRIPTION TEST SUITE")
    logger.info("="*80)
    
    test = TranscriptionTest()
    
    # Setup
    if not await test.setup():
        logger.error("Failed to setup test suite")
        return False
    
    results = {}
    
    # Run tests
    results['device_list'] = test.test_list_audio_devices()
    results['device_detection'] = test.test_device_detection()
    results['sample_rate_validation'] = test.test_sample_rate_validation()
    results['transcribe_silence'] = await test.test_transcribe_silence()
    results['transcribe_audio'] = await test.test_transcribe_audio_bytes()
    
    # Interactive test - ask user if they want to test recording
    print("\n" + "="*80)
    print("Would you like to test real-time microphone recording? (y/n): ", end='')
    try:
        response = input().strip().lower()
        if response == 'y':
            results['record_and_transcribe'] = await test.test_record_and_transcribe(duration=5)
        else:
            logger.info("Skipping microphone recording test")
            results['record_and_transcribe'] = None
    except (EOFError, KeyboardInterrupt):
        logger.info("\nSkipping microphone recording test")
        results['record_and_transcribe'] = None
    
    # Summary
    logger.info("\n" + "="*80)
    logger.info("TEST SUMMARY")
    logger.info("="*80)
    
    passed = 0
    failed = 0
    skipped = 0
    
    for test_name, result in results.items():
        if result is None:
            logger.info(f"  {test_name:30s} SKIPPED")
            skipped += 1
        elif result:
            logger.info(f"  {test_name:30s} ✅ PASSED")
            passed += 1
        else:
            logger.info(f"  {test_name:30s} ❌ FAILED")
            failed += 1
    
    logger.info("="*80)
    logger.info(f"Total: {len(results)} tests | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
    logger.info("="*80)
    
    return failed == 0


if __name__ == "__main__":
    try:
        success = asyncio.run(run_all_tests())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n\nTest suite failed with error: {e}", exc_info=True)
        sys.exit(1)

