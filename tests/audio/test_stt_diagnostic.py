#!/usr/bin/env python3
"""
STT Diagnostic Test Suite

Tests all available STT services to diagnose issues:
1. Microphone detection and audio capture
2. Vosk STT
3. Whisper.cpp STT
4. OpenAI Whisper STT (if API key available)
"""

import asyncio
import sys
import os
import numpy as np
import sounddevice as sd
import logging
from pathlib import Path
import json

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Suppress verbose logs
logging.getLogger('pipecat').setLevel(logging.WARNING)


class STTDiagnostic:
    """Diagnostic test suite for STT services"""
    
    def __init__(self):
        self.results = {}
        
    def test_microphone(self, duration=3):
        """Test if microphone is working and capturing audio"""
        print("\n" + "="*80)
        print("TEST 1: Microphone Detection and Audio Capture")
        print("="*80)
        
        try:
            devices = sd.query_devices()
            input_devices = [d for d in devices if d.get('max_input_channels', 0) > 0]
            
            print(f"\n📱 Found {len(input_devices)} input device(s):")
            for i, d in enumerate(input_devices):
                print(f"   {i}: {d['name']} (channels: {d.get('max_input_channels', 0)}, "
                      f"sample rate: {d.get('default_samplerate', 'unknown')})")
            
            if not input_devices:
                print("❌ No input devices found!")
                return False
            
            # Use first available input device
            input_device_idx = None
            for fallback_name in ['pulse', 'default', 'pipewire', 'macbook']:
                for i, d in enumerate(devices):
                    if fallback_name.lower() in d['name'].lower() and d.get('max_input_channels', 0) > 0:
                        input_device_idx = i
                        print(f"\n🎤 Using device: {d['name']} (index {i})")
                        break
                if input_device_idx is not None:
                    break
            
            if input_device_idx is None:
                input_device_idx = input_devices[0]['index']
                print(f"\n🎤 Using first available device: {devices[input_device_idx]['name']} (index {input_device_idx})")
            
            sample_rate = 16000
            print(f"\n🔊 Recording {duration} seconds at {sample_rate}Hz...")
            print("   Please speak into your microphone now!")
            
            # Record audio
            audio_data = sd.rec(
                int(sample_rate * duration),
                samplerate=sample_rate,
                channels=1,
                dtype='float32',
                device=input_device_idx
            )
            sd.wait()
            
            # Analyze audio
            max_amplitude = np.max(np.abs(audio_data))
            rms = np.sqrt(np.mean(audio_data**2))
            non_zero_samples = np.count_nonzero(audio_data)
            total_samples = len(audio_data)
            
            print(f"\n📊 Audio Analysis:")
            print(f"   Max amplitude: {max_amplitude:.6f}")
            print(f"   RMS level: {rms:.6f}")
            print(f"   Non-zero samples: {non_zero_samples}/{total_samples} ({100*non_zero_samples/total_samples:.1f}%)")
            
            if max_amplitude < 0.0001:
                print("\n❌ ERROR: No audio detected! Microphone may be:")
                print("   - Muted or disabled")
                print("   - Not connected")
                print("   - Permission denied")
                print("   - Volume too low")
                return False
            elif max_amplitude < 0.01:
                print("\n⚠️  WARNING: Very low audio level")
                print("   - Microphone may be too quiet")
                print("   - Try speaking louder or adjusting mic gain")
            else:
                print("\n✅ Microphone is working and capturing audio!")
            
            self.results['microphone'] = {
                'working': max_amplitude > 0.0001,
                'max_amplitude': float(max_amplitude),
                'rms': float(rms),
                'device': devices[input_device_idx]['name']
            }
            
            return max_amplitude > 0.0001
            
        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def test_vosk_stt(self, audio_bytes, sample_rate=16000):
        """Test Vosk STT service"""
        print("\n" + "="*80)
        print("TEST 2: Vosk STT Service")
        print("="*80)
        
        try:
            from distr.core.agent.services.vosk import VoskSTTService
            
            # Find Vosk model path (same logic as session.py)
            base_dir = Path(__file__).parent.parent.parent
            models_dir = base_dir / "distr" / "agent" / "models"
            vosk_model_path = models_dir / "vosk-model-en-us-0.22"
            
            if not vosk_model_path.exists():
                print(f"\n❌ Vosk model not found at {vosk_model_path}")
                print("   Please download it using Settings > AI > Transcription Model")
                self.results['vosk'] = {'working': False, 'error': 'Model not found'}
                return False
            
            print("\n🔄 Initializing Vosk STT...")
            print(f"   Model path: {vosk_model_path}")
            stt_service = VoskSTTService(
                model_path=str(vosk_model_path),
                event_queue=None,
                is_hands_free=False
            )
            print("✅ Vosk STT initialized")
            
            print(f"\n🔄 Transcribing {len(audio_bytes)} bytes of audio...")
            transcriptions = []
            async for frame in stt_service.run_stt(audio_bytes):
                if hasattr(frame, 'text'):
                    transcriptions.append(frame.text)
                    print(f"   📝 Transcription: '{frame.text}'")
            
            if transcriptions:
                result = " ".join(transcriptions).strip()
                print(f"\n✅ Vosk STT SUCCESS: '{result}'")
                self.results['vosk'] = {'working': True, 'transcription': result}
                return True
            else:
                print("\n❌ Vosk STT FAILED: No transcription produced")
                print("   This matches the issue you're experiencing!")
                self.results['vosk'] = {'working': False, 'error': 'No transcription'}
                return False
                
        except ImportError as e:
            print(f"\n❌ Vosk not available: {e}")
            self.results['vosk'] = {'working': False, 'error': str(e)}
            return False
        except Exception as e:
            print(f"\n❌ Vosk STT ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.results['vosk'] = {'working': False, 'error': str(e)}
            return False
    
    async def test_whisper_cpp_stt(self, audio_bytes, sample_rate=16000):
        """Test Whisper.cpp STT service"""
        print("\n" + "="*80)
        print("TEST 3: Whisper.cpp STT Service")
        print("="*80)
        
        try:
            from distr.core.agent.services.whisper import WhisperSTTService
            
            print("\n🔄 Initializing Whisper.cpp STT...")
            stt_service = WhisperSTTService(
                model_path="base.en",
                event_queue=None,
                is_hands_free=False
            )
            print("✅ Whisper.cpp STT initialized")
            
            print(f"\n🔄 Transcribing {len(audio_bytes)} bytes of audio...")
            transcriptions = []
            async for frame in stt_service.run_stt(audio_bytes):
                if hasattr(frame, 'text'):
                    transcriptions.append(frame.text)
                    print(f"   📝 Transcription: '{frame.text}'")
            
            if transcriptions:
                result = " ".join(transcriptions).strip()
                print(f"\n✅ Whisper.cpp STT SUCCESS: '{result}'")
                self.results['whisper_cpp'] = {'working': True, 'transcription': result}
                return True
            else:
                print("\n❌ Whisper.cpp STT FAILED: No transcription produced")
                self.results['whisper_cpp'] = {'working': False, 'error': 'No transcription'}
                return False
                
        except ImportError as e:
            print(f"\n❌ Whisper.cpp not available: {e}")
            self.results['whisper_cpp'] = {'working': False, 'error': str(e)}
            return False
        except Exception as e:
            print(f"\n❌ Whisper.cpp STT ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.results['whisper_cpp'] = {'working': False, 'error': str(e)}
            return False
    
    async def test_openai_whisper_stt(self, audio_bytes, sample_rate=16000):
        """Test OpenAI Whisper STT service"""
        print("\n" + "="*80)
        print("TEST 4: OpenAI Whisper STT Service")
        print("="*80)
        
        try:
            from distr.core.agent.services.openai_stt import OpenAIWhisperSTTService
            from distr.core.utils import load_settings_from_db
            
            settings = load_settings_from_db()
            api_key = settings.get('openai_key', '').strip()
            
            if not api_key:
                print("\n⚠️  OpenAI API key not found in settings")
                print("   Skipping OpenAI Whisper test")
                self.results['openai_whisper'] = {'working': False, 'error': 'No API key'}
                return False
            
            print("\n🔄 Initializing OpenAI Whisper STT...")
            stt_service = OpenAIWhisperSTTService(
                api_key=api_key,
                model='whisper-1',
                event_queue=None,
                is_hands_free=False
            )
            print("✅ OpenAI Whisper STT initialized")
            
            print(f"\n🔄 Transcribing {len(audio_bytes)} bytes of audio...")
            transcriptions = []
            async for frame in stt_service.run_stt(audio_bytes):
                if hasattr(frame, 'text'):
                    transcriptions.append(frame.text)
                    print(f"   📝 Transcription: '{frame.text}'")
            
            if transcriptions:
                result = " ".join(transcriptions).strip()
                print(f"\n✅ OpenAI Whisper STT SUCCESS: '{result}'")
                self.results['openai_whisper'] = {'working': True, 'transcription': result}
                return True
            else:
                print("\n❌ OpenAI Whisper STT FAILED: No transcription produced")
                self.results['openai_whisper'] = {'working': False, 'error': 'No transcription'}
                return False
                
        except ImportError as e:
            print(f"\n❌ OpenAI Whisper not available: {e}")
            self.results['openai_whisper'] = {'working': False, 'error': str(e)}
            return False
        except Exception as e:
            print(f"\n❌ OpenAI Whisper STT ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.results['openai_whisper'] = {'working': False, 'error': str(e)}
            return False
    
    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)
        
        for test_name, result in self.results.items():
            status = "✅ WORKING" if result.get('working') else "❌ FAILED"
            print(f"\n{test_name.upper()}: {status}")
            if 'transcription' in result:
                print(f"   Transcription: '{result['transcription']}'")
            elif 'error' in result:
                print(f"   Error: {result['error']}")
            if 'max_amplitude' in result:
                print(f"   Audio level: {result['max_amplitude']:.6f}")
        
        print("\n" + "="*80)
        print("RECOMMENDATIONS:")
        print("="*80)
        
        if not self.results.get('microphone', {}).get('working'):
            print("\n❌ Microphone is not working - fix this first!")
            print("   1. Check microphone permissions in System Settings")
            print("   2. Ensure microphone is not muted")
            print("   3. Try a different microphone")
        elif not self.results.get('vosk', {}).get('working'):
            print("\n⚠️  Vosk STT is not working (as expected from your logs)")
            if self.results.get('whisper_cpp', {}).get('working'):
                print("   ✅ RECOMMENDATION: Switch to Whisper.cpp STT")
                print("      It's working and doesn't require API keys")
            elif self.results.get('openai_whisper', {}).get('working'):
                print("   ✅ RECOMMENDATION: Switch to OpenAI Whisper STT")
                print("      It's working but requires an API key")
            else:
                print("   ⚠️  None of the STT services are working")
                print("      This suggests an audio format or processing issue")


async def main():
    """Run all diagnostic tests"""
    print("\n" + "="*80)
    print("STT DIAGNOSTIC TEST SUITE")
    print("="*80)
    print("\nThis will test:")
    print("  1. Microphone detection and audio capture")
    print("  2. Vosk STT (currently failing)")
    print("  3. Whisper.cpp STT (alternative)")
    print("  4. OpenAI Whisper STT (if API key available)")
    print("\nPress Ctrl+C to cancel\n")
    
    diagnostic = STTDiagnostic()
    
    # Test 1: Microphone
    if not diagnostic.test_microphone(duration=3):
        print("\n❌ Microphone test failed - cannot proceed with STT tests")
        diagnostic.print_summary()
        return
    
    # Get audio data for STT tests
    print("\n" + "="*80)
    print("Recording audio for STT tests...")
    print("="*80)
    print("\n🎤 Please speak clearly for 5 seconds...")
    
    devices = sd.query_devices()
    input_device_idx = None
    for fallback_name in ['pulse', 'default', 'pipewire', 'macbook']:
        for i, d in enumerate(devices):
            if fallback_name.lower() in d['name'].lower() and d.get('max_input_channels', 0) > 0:
                input_device_idx = i
                break
        if input_device_idx is not None:
            break
    
    if input_device_idx is None:
        for i, d in enumerate(devices):
            if d.get('max_input_channels', 0) > 0:
                input_device_idx = i
                break
    
    sample_rate = 16000
    audio_data = sd.rec(
        int(sample_rate * 5),
        samplerate=sample_rate,
        channels=1,
        dtype='float32',
        device=input_device_idx
    )
    sd.wait()
    
    # Convert to int16 PCM bytes
    audio_int16 = (audio_data * 32767).astype(np.int16)
    audio_bytes = audio_int16.tobytes()
    
    print(f"✅ Recorded {len(audio_bytes)} bytes of audio\n")
    
    # Test 2: Vosk
    await diagnostic.test_vosk_stt(audio_bytes, sample_rate)
    
    # Test 3: Whisper.cpp
    await diagnostic.test_whisper_cpp_stt(audio_bytes, sample_rate)
    
    # Test 4: OpenAI Whisper
    await diagnostic.test_openai_whisper_stt(audio_bytes, sample_rate)
    
    # Print summary
    diagnostic.print_summary()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Test cancelled by user")
    except Exception as e:
        print(f"\n\n❌ Test suite error: {e}")
        import traceback
        traceback.print_exc()

