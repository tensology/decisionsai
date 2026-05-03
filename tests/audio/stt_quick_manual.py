#!/usr/bin/env python3
"""
Quick STT Diagnostic Test

Run this from your terminal with: python test_stt_quick.py
Make sure you're in the decisions virtual environment first.
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

print("="*80)
print("STT QUICK DIAGNOSTIC TEST")
print("="*80)
print("\nThis will test:")
print("  1. Microphone detection")
print("  2. Vosk STT (currently failing)")
print("  3. Whisper.cpp STT (alternative)")
print("\nPress Ctrl+C to cancel\n")

try:
    import numpy as np
    import sounddevice as sd
    print("✅ sounddevice imported")
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("   Make sure you're in the virtual environment!")
    sys.exit(1)

# Test 1: List devices
print("\n" + "="*80)
print("TEST 1: Audio Device Detection")
print("="*80)
try:
    devices = sd.query_devices()
    input_devices = [d for d in devices if d.get('max_input_channels', 0) > 0]
    
    print(f"\n📱 Found {len(input_devices)} input device(s):")
    for i, d in enumerate(input_devices):
        print(f"   {i}: {d['name']}")
        print(f"      Channels: {d.get('max_input_channels', 0)}, "
              f"Sample Rate: {d.get('default_samplerate', 'unknown')}")
    
    if not input_devices:
        print("\n❌ No input devices found!")
        sys.exit(1)
    
    # Find MacBook Pro Microphone
    input_device_idx = None
    for i, d in enumerate(devices):
        if 'macbook' in d['name'].lower() and d.get('max_input_channels', 0) > 0:
            input_device_idx = i
            print(f"\n🎤 Using: {d['name']} (index {i})")
            break
    
    if input_device_idx is None:
        input_device_idx = input_devices[0]['index']
        print(f"\n🎤 Using first available: {devices[input_device_idx]['name']} (index {input_device_idx})")
    
except Exception as e:
    print(f"❌ Error detecting devices: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 2: Record audio
print("\n" + "="*80)
print("TEST 2: Audio Recording")
print("="*80)
print("\n🎤 Recording 3 seconds... Please speak now!")
try:
    sample_rate = 16000
    audio_data = sd.rec(
        int(sample_rate * 3),
        samplerate=sample_rate,
        channels=1,
        dtype='float32',
        device=input_device_idx
    )
    sd.wait()
    
    max_amplitude = np.max(np.abs(audio_data))
    rms = np.sqrt(np.mean(audio_data**2))
    
    print(f"\n📊 Audio Analysis:")
    print(f"   Max amplitude: {max_amplitude:.6f}")
    print(f"   RMS level: {rms:.6f}")
    
    if max_amplitude < 0.0001:
        print("\n❌ ERROR: No audio detected!")
        print("   Microphone may be muted or not working")
        sys.exit(1)
    elif max_amplitude < 0.01:
        print("\n⚠️  WARNING: Very low audio level")
        print("   This may cause STT to fail!")
        print("   Try:")
        print("   1. Speaking louder")
        print("   2. Moving closer to microphone")
        print("   3. Increasing microphone gain in System Settings")
    else:
        print("\n✅ Microphone is working!")
    
    # Convert to bytes for STT
    audio_int16 = (audio_data * 32767).astype(np.int16)
    audio_bytes = audio_int16.tobytes()
    print(f"   Audio bytes: {len(audio_bytes)}")
    
except Exception as e:
    print(f"❌ Error recording audio: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Vosk STT
print("\n" + "="*80)
print("TEST 3: Vosk STT")
print("="*80)
try:
    from distr.core.agent.services.vosk import VoskSTTService
    import asyncio
    
    print("\n🔄 Initializing Vosk STT...")
    # Find default Vosk model path
    base_dir = Path(__file__).parent
    vosk_model_path = base_dir / "distr" / "agent" / "models" / "vosk-model-en-us-0.22"
    if not vosk_model_path.exists():
        print(f"❌ Vosk model not found at {vosk_model_path}")
        print("   Skipping Vosk test")
    else:
        stt_service = VoskSTTService(
            model_path=str(vosk_model_path),
            event_queue=None,
            is_hands_free=False
        )
        print("✅ Vosk initialized")
        
        print(f"\n🔄 Transcribing {len(audio_bytes)} bytes...")
        
        async def test_vosk():
            transcriptions = []
            async for frame in stt_service.run_stt(audio_bytes):
                if hasattr(frame, 'text'):
                    transcriptions.append(frame.text)
                    print(f"   📝 '{frame.text}'")
            
            if transcriptions:
                result = " ".join(transcriptions).strip()
                print(f"\n✅ Vosk SUCCESS: '{result}'")
                return True
            else:
                print("\n❌ Vosk FAILED: No transcription (matches your issue!)")
                return False
        
        result = asyncio.run(test_vosk())
    
except ImportError as e:
    print(f"❌ Vosk not available: {e}")
except Exception as e:
    print(f"❌ Vosk ERROR: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Whisper.cpp STT
print("\n" + "="*80)
print("TEST 4: Whisper.cpp STT")
print("="*80)
try:
    from distr.core.agent.services.whisper import WhisperSTTService
    import asyncio
    
    print("\n🔄 Initializing Whisper.cpp STT...")
    stt_service = WhisperSTTService(
        model_path="base.en",
        event_queue=None,
        is_hands_free=False
    )
    print("✅ Whisper.cpp initialized")
    
    print(f"\n🔄 Transcribing {len(audio_bytes)} bytes...")
    
    async def test_whisper():
        transcriptions = []
        async for frame in stt_service.run_stt(audio_bytes):
            if hasattr(frame, 'text'):
                transcriptions.append(frame.text)
                print(f"   📝 '{frame.text}'")
        
        if transcriptions:
            result = " ".join(transcriptions).strip()
            print(f"\n✅ Whisper.cpp SUCCESS: '{result}'")
            print("\n💡 RECOMMENDATION: Switch to Whisper.cpp STT in settings!")
            return True
        else:
            print("\n❌ Whisper.cpp FAILED: No transcription")
            return False
    
    result = asyncio.run(test_whisper())
    
except ImportError as e:
    print(f"❌ Whisper.cpp not available: {e}")
except Exception as e:
    print(f"❌ Whisper.cpp ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)
print("\nIf Vosk failed but Whisper.cpp worked, switch to Whisper.cpp in:")
print("   Settings → Third Party Providers → Speech Recognition Model")

