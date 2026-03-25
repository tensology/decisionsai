#!/usr/bin/env python3
"""
Live Transcription Test

Records audio from microphone and shows transcriptions in real-time.
"""

import asyncio
import sys
import os
import numpy as np
import sounddevice as sd
import logging
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from distr.core.agent.services.whisper import WhisperSTTService
from pipecat.frames.frames import TranscriptionFrame

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Suppress verbose logs
logging.getLogger("pipecat").setLevel(logging.WARNING)


async def test_live_transcription():
    """Test live transcription from microphone"""
    
    print("\n" + "="*80)
    print("LIVE TRANSCRIPTION TEST")
    print("="*80)
    print("\nThis will record audio and show transcriptions as they come through.")
    print("Speak clearly into your microphone.\n")
    
    # Initialize Whisper STT with error handling
    print("Initializing Whisper STT service...")
    stt_service = None
    try:
        # Suppress stderr during initialization but capture it to check for errors
        import sys
        from contextlib import redirect_stderr
        from io import StringIO
        
        stderr_capture = StringIO()
        with redirect_stderr(stderr_capture):
            stt_service = WhisperSTTService(
                model_path="base.en",
                event_queue=None,
                is_hands_free=False
            )
        
        # Check stderr for model loading errors
        stderr_output = stderr_capture.getvalue()
        if "ERROR not all tensors loaded" in stderr_output or "failed to load model" in stderr_output:
            print("❌ Model file appears corrupted!")
            print("   The model loaded with errors. This will cause a segfault during transcription.")
            print("\n   To fix, remove the corrupted model file:")
            print("   rm ~/.local/share/pywhispercpp/models/ggml-base.en.bin")
            print("   Then run this test again - the model will be re-downloaded automatically.\n")
            return False
        
        print("✅ Whisper STT service ready!\n")
    except Exception as e:
        print(f"❌ Failed to initialize Whisper STT: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    if stt_service is None:
        print("❌ STT service is None")
        return False
    
    # Find input device
    devices = sd.query_devices()
    input_device_idx = None
    
    # Try to use pulse or default first (for resampling)
    for fallback_name in ['pulse', 'default', 'pipewire']:
        for i, d in enumerate(devices):
            if fallback_name in d['name'].lower() and d.get('max_input_channels', 0) > 0:
                input_device_idx = i
                print(f"Using audio device: {d['name']} (index {i})\n")
                break
        if input_device_idx is not None:
            break
    
    if input_device_idx is None:
        for i, d in enumerate(devices):
            if d.get('max_input_channels', 0) > 0:
                input_device_idx = i
                print(f"Using audio device: {d['name']} (index {i})\n")
                break
    
    if input_device_idx is None:
        print("❌ No input device found")
        return False
    
    sample_rate = 16000
    
    # Record and transcribe loop
    try:
        while True:
            print("-" * 80)
            print("Recording 5 seconds... (speak now, or press Ctrl+C to exit)")
            print("-" * 80)
            
            # Record audio
            audio_data = sd.rec(
                int(sample_rate * 5),
                samplerate=sample_rate,
                channels=1,
                dtype='float32',
                device=input_device_idx
            )
            sd.wait()  # Wait until recording is finished
            
            # Check audio level
            max_amplitude = np.max(np.abs(audio_data))
            print(f"\n📊 Audio level: {max_amplitude:.4f} (max amplitude)")
            
            if max_amplitude < 0.001:
                print("⚠️  Very low audio - microphone may be muted or not working")
                print("   (This is normal if you didn't speak)\n")
                continue
            
            # Convert to int16 PCM bytes
            audio_int16 = (audio_data * 32767).astype(np.int16)
            audio_bytes = audio_int16.tobytes()
            
            print(f"🎤 Processing {len(audio_bytes)} bytes of audio...")
            print("\n" + "="*80)
            print("TRANSCRIPTION:")
            print("="*80)
            
            # Transcribe with error handling
            transcriptions = []
            try:
                print("🔄 Transcribing...")
                # Iterate directly over the async generator
                async for frame in stt_service.run_stt(audio_bytes):
                    if isinstance(frame, TranscriptionFrame):
                        transcriptions.append(frame.text)
                        # Show transcription immediately
                        print(f"\n✨ TRANSCRIPTION: '{frame.text}'\n")
                    elif hasattr(frame, 'error'):
                        print(f"\n❌ Error frame: {frame.error}\n")
                    else:
                        logger.debug(f"Frame type: {type(frame).__name__}")
            except Exception as e:
                print(f"\n❌ Transcription error: {e}")
                import traceback
                traceback.print_exc()
                # Check if it's a segfault-related issue
                if "segmentation" in str(e).lower() or "signal" in str(e).lower():
                    print("\n⚠️  Possible model corruption detected.")
                    print("   Try re-downloading the Whisper model:")
                    print("   rm ~/.local/share/pywhispercpp/models/ggml-base.en.bin")
                    print("   (The model will be re-downloaded on next run)")
                continue
            
            if transcriptions:
                print("="*80)
                print("FINAL RESULT:")
                print("="*80)
                for i, text in enumerate(transcriptions, 1):
                    print(f"{i}. {text}")
                print("="*80)
            else:
                print("\n⚠️  No transcription produced")
                print("   (May be silence, too quiet, or filtered as filler/artifact)\n")
            
            print("\n" + "="*80 + "\n")
            
    except KeyboardInterrupt:
        print("\n\n✅ Test stopped by user")
        return True
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_single_transcription(duration=5):
    """Test a single recording and transcription"""
    
    print("\n" + "="*80)
    print("SINGLE TRANSCRIPTION TEST")
    print("="*80)
    print(f"\nRecording {duration} seconds of audio...")
    print("Please speak clearly into your microphone.\n")
    
    # Initialize Whisper STT with error handling
    print("Initializing Whisper STT service...")
    stt_service = None
    try:
        # Suppress stderr during initialization to avoid model loading warnings
        import sys
        from contextlib import redirect_stderr
        from io import StringIO
        
        stderr_capture = StringIO()
        with redirect_stderr(stderr_capture):
            stt_service = WhisperSTTService(
                model_path="base.en",
                event_queue=None,
                is_hands_free=False
            )
        print("✅ Whisper STT service ready!\n")
    except Exception as e:
        print(f"❌ Failed to initialize Whisper STT: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    if stt_service is None:
        print("❌ STT service is None")
        return False
    
    # Find input device
    devices = sd.query_devices()
    input_device_idx = None
    
    for fallback_name in ['pulse', 'default', 'pipewire']:
        for i, d in enumerate(devices):
            if fallback_name in d['name'].lower() and d.get('max_input_channels', 0) > 0:
                input_device_idx = i
                print(f"Using audio device: {d['name']} (index {i})\n")
                break
        if input_device_idx is not None:
            break
    
    if input_device_idx is None:
        for i, d in enumerate(devices):
            if d.get('max_input_channels', 0) > 0:
                input_device_idx = i
                print(f"Using audio device: {d['name']} (index {i})\n")
                break
    
    if input_device_idx is None:
        print("❌ No input device found")
        return False
    
    sample_rate = 16000
    
    try:
        print("🎤 Recording...")
        
        # Record audio
        audio_data = sd.rec(
            int(sample_rate * duration),
            samplerate=sample_rate,
            channels=1,
            dtype='float32',
            device=input_device_idx
        )
        sd.wait()
        
        # Check audio level
        max_amplitude = np.max(np.abs(audio_data))
        print(f"📊 Audio level: {max_amplitude:.4f}")
        
        if max_amplitude < 0.001:
            print("⚠️  Very low audio - microphone may be muted")
            return False
        
        # Convert to int16 PCM bytes
        audio_int16 = (audio_data * 32767).astype(np.int16)
        audio_bytes = audio_int16.tobytes()
        
        print(f"🎤 Processing {len(audio_bytes)} bytes...\n")
        print("="*80)
        print("TRANSCRIPTION:")
        print("="*80)
        
        # Transcribe with error handling
        transcriptions = []
        try:
            print("🔄 Transcribing...")
            # Simply iterate over the async generator (run_stt returns an async generator)
            async for frame in stt_service.run_stt(audio_bytes):
                if isinstance(frame, TranscriptionFrame):
                    transcriptions.append(frame.text)
                    print(f"\n✨ TRANSCRIPTION: '{frame.text}'\n")
                elif hasattr(frame, 'error'):
                    print(f"\n❌ Error frame: {frame.error}\n")
                else:
                    logger.debug(f"Frame type: {type(frame).__name__}")
        except Exception as e:
            print(f"\n❌ Transcription error: {e}")
            import traceback
            traceback.print_exc()
            # Check if it's a segfault-related issue
            if "segmentation" in str(e).lower() or "signal" in str(e).lower():
                print("\n⚠️  Possible model corruption detected.")
                print("   Try re-downloading the Whisper model:")
                print("   rm ~/.local/share/pywhispercpp/models/ggml-base.en.bin")
                print("   (The model will be re-downloaded on next run)")
            return False
        
        if transcriptions:
            print("="*80)
            print("FINAL RESULT:")
            print("="*80)
            for i, text in enumerate(transcriptions, 1):
                print(f"{i}. {text}")
            print("="*80)
            print("\n✅ Transcription successful!")
            return True
        else:
            print("\n⚠️  No transcription produced")
            print("   (May be silence, too quiet, or filtered as filler/artifact)")
            return False
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Test Whisper transcription')
    parser.add_argument('--live', action='store_true', help='Run in live mode (continuous recording)')
    parser.add_argument('--duration', type=int, default=5, help='Recording duration in seconds (default: 5)')
    
    args = parser.parse_args()
    
    try:
        if args.live:
            success = asyncio.run(test_live_transcription())
        else:
            success = asyncio.run(test_single_transcription(duration=args.duration))
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)

