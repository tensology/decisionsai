#!/usr/bin/env python3
"""
Simple Microphone Test

Tests microphone input to diagnose audio issues:
1. Detects available input devices
2. Records a short sample
3. Analyzes audio levels
4. Reports if audio is being captured correctly

Run: python tests/audio/test_microphone.py
"""

import sys
import os
import time
import numpy as np
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    import sounddevice as sd
except ImportError:
    print("❌ sounddevice not installed. Run: pip install sounddevice")
    sys.exit(1)


def list_audio_devices():
    """List all audio devices"""
    print("\n" + "=" * 80)
    print("AUDIO DEVICE DETECTION")
    print("=" * 80)
    
    devices = sd.query_devices()
    
    print("\n📱 All Audio Devices:")
    print("-" * 60)
    
    input_devices = []
    output_devices = []
    
    for i, d in enumerate(devices):
        device_type = []
        if d.get('max_input_channels', 0) > 0:
            device_type.append("INPUT")
            input_devices.append((i, d))
        if d.get('max_output_channels', 0) > 0:
            device_type.append("OUTPUT")
            output_devices.append((i, d))
        
        type_str = "/".join(device_type) if device_type else "UNKNOWN"
        print(f"  [{i:2d}] {d['name'][:50]:<50} ({type_str})")
        print(f"       In: {d.get('max_input_channels', 0)} ch, "
              f"Out: {d.get('max_output_channels', 0)} ch, "
              f"Rate: {d.get('default_samplerate', 'unknown')} Hz")
    
    print("\n" + "-" * 60)
    print(f"Total: {len(input_devices)} input device(s), {len(output_devices)} output device(s)")
    
    # Show default devices
    try:
        default_input = sd.default.device[0]
        default_output = sd.default.device[1]
        print(f"\nDefault Input:  [{default_input}] {devices[default_input]['name']}")
        print(f"Default Output: [{default_output}] {devices[default_output]['name']}")
    except Exception as e:
        print(f"\n⚠️  Could not determine default devices: {e}")
    
    return input_devices


def test_microphone_recording(device_index=None, duration=3, sample_rate=16000):
    """Record audio from microphone and analyze"""
    print("\n" + "=" * 80)
    print("MICROPHONE RECORDING TEST")
    print("=" * 80)
    
    devices = sd.query_devices()
    
    # Determine which device to use
    if device_index is None:
        # Try to find a good default
        for fallback_name in ['macbook', 'built-in', 'default', 'pulse', 'pipewire']:
            for i, d in enumerate(devices):
                if fallback_name.lower() in d['name'].lower() and d.get('max_input_channels', 0) > 0:
                    device_index = i
                    break
            if device_index is not None:
                break
        
        if device_index is None:
            # Use system default
            device_index = sd.default.device[0]
    
    device = devices[device_index]
    print(f"\n🎤 Using device: [{device_index}] {device['name']}")
    print(f"   Channels: {device.get('max_input_channels', 0)}")
    print(f"   Sample rate: {sample_rate} Hz")
    print(f"   Duration: {duration} seconds")
    
    print(f"\n🔴 Recording... SPEAK NOW!")
    print("   " + "█" * 40)
    
    try:
        # Record audio
        audio = sd.rec(
            frames=int(duration * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype=np.float32,
            device=device_index
        )
        
        # Show progress
        for i in range(duration):
            time.sleep(1)
            progress = int((i + 1) / duration * 40)
            print(f"\r   {'█' * progress}{'░' * (40 - progress)} {i + 1}/{duration}s", end="", flush=True)
        
        sd.wait()
        print("\n\n✅ Recording complete!")
        
    except Exception as e:
        print(f"\n\n❌ Recording failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Analyze audio
    print("\n" + "-" * 60)
    print("AUDIO ANALYSIS")
    print("-" * 60)
    
    audio_flat = audio.flatten()
    
    # Calculate metrics
    max_amplitude = np.max(np.abs(audio_flat))
    mean_amplitude = np.mean(np.abs(audio_flat))
    rms = np.sqrt(np.mean(audio_flat ** 2))
    
    # Convert to dB
    if rms > 0:
        db_level = 20 * np.log10(rms)
    else:
        db_level = -100
    
    # Check for silence
    silence_threshold = 0.01
    non_silent_samples = np.sum(np.abs(audio_flat) > silence_threshold)
    silence_ratio = 1.0 - (non_silent_samples / len(audio_flat))
    
    print(f"   Max Amplitude:     {max_amplitude:.4f} (range: 0.0 - 1.0)")
    print(f"   Mean Amplitude:    {mean_amplitude:.4f}")
    print(f"   RMS Level:         {rms:.4f}")
    print(f"   dB Level:          {db_level:.1f} dB")
    print(f"   Silence Ratio:     {silence_ratio:.1%}")
    print(f"   Total Samples:     {len(audio_flat):,}")
    
    # Diagnose issues
    print("\n" + "-" * 60)
    print("DIAGNOSIS")
    print("-" * 60)
    
    issues = []
    
    if max_amplitude < 0.001:
        issues.append("❌ NO AUDIO DETECTED - Microphone may not be connected or muted")
    elif max_amplitude < 0.01:
        issues.append("⚠️  VERY LOW AUDIO - Microphone sensitivity may be too low")
    elif max_amplitude < 0.1:
        issues.append("⚠️  LOW AUDIO - Consider increasing microphone volume")
    
    if silence_ratio > 0.95:
        issues.append("❌ MOSTLY SILENCE - Did you speak during the recording?")
    elif silence_ratio > 0.8:
        issues.append("⚠️  HIGH SILENCE RATIO - Audio may be intermittent")
    
    if rms > 0.5:
        issues.append("⚠️  VERY HIGH LEVELS - Audio may be clipping/distorted")
    
    if db_level < -60:
        issues.append("⚠️  VERY QUIET - Audio level is very low")
    
    if issues:
        for issue in issues:
            print(f"   {issue}")
    else:
        print("   ✅ Audio levels look GOOD!")
        print("   ✅ Microphone is working correctly")
    
    # Show visual representation
    print("\n" + "-" * 60)
    print("WAVEFORM PREVIEW (first 1000 samples)")
    print("-" * 60)
    
    # Simple ASCII waveform
    preview_samples = audio_flat[:1000]
    if len(preview_samples) > 0:
        # Downsample to 50 points
        step = max(1, len(preview_samples) // 50)
        downsampled = preview_samples[::step][:50]
        
        for val in downsampled:
            # Scale to -20 to +20 range
            scaled = int(val * 20)
            if scaled >= 0:
                bar = " " * 20 + "│" + "█" * min(scaled, 20)
            else:
                bar = " " * max(20 + scaled, 0) + "█" * min(-scaled, 20) + "│"
            print(f"   {bar}")
    
    return max_amplitude > 0.01  # Return True if audio detected


def test_continuous_monitoring(device_index=None, duration=10):
    """Monitor microphone levels continuously"""
    print("\n" + "=" * 80)
    print("CONTINUOUS LEVEL MONITORING")
    print("=" * 80)
    print(f"\nMonitoring microphone levels for {duration} seconds...")
    print("Speak to see the level meter respond.\n")
    
    devices = sd.query_devices()
    
    if device_index is None:
        device_index = sd.default.device[0]
    
    device = devices[device_index]
    print(f"🎤 Monitoring: [{device_index}] {device['name']}\n")
    
    sample_rate = 16000
    block_size = 1600  # 100ms blocks
    
    level_history = []
    
    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"   ⚠️  {status}")
        
        rms = np.sqrt(np.mean(indata ** 2))
        level_history.append(rms)
        
        # Visual meter
        meter_width = 50
        level = min(int(rms * meter_width * 10), meter_width)
        
        if level > meter_width * 0.8:
            color = "🔴"
        elif level > meter_width * 0.5:
            color = "🟡"
        else:
            color = "🟢"
        
        meter = "█" * level + "░" * (meter_width - level)
        print(f"\r   {color} [{meter}] {rms:.4f}", end="", flush=True)
    
    try:
        with sd.InputStream(
            device=device_index,
            channels=1,
            samplerate=sample_rate,
            blocksize=block_size,
            callback=audio_callback,
            dtype=np.float32
        ):
            time.sleep(duration)
        
        print("\n\n✅ Monitoring complete!")
        
        if level_history:
            avg_level = np.mean(level_history)
            max_level = np.max(level_history)
            print(f"   Average Level: {avg_level:.4f}")
            print(f"   Peak Level:    {max_level:.4f}")
            
            if max_level < 0.001:
                print("   ❌ No audio detected during monitoring!")
            elif max_level < 0.01:
                print("   ⚠️  Very low audio levels detected")
            else:
                print("   ✅ Audio detected successfully!")
        
        return max_level > 0.01 if level_history else False
        
    except Exception as e:
        print(f"\n\n❌ Monitoring failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all microphone tests"""
    print("\n" + "=" * 80)
    print("         DECISIONS AI - MICROPHONE DIAGNOSTIC TEST")
    print("=" * 80)
    print("\nThis test will check if your microphone is working correctly.\n")
    
    # Parse arguments
    device_index = None
    auto_mode = False
    
    for arg in sys.argv[1:]:
        if arg in ('--auto', '-a'):
            auto_mode = True
        elif arg in ('--help', '-h'):
            print("Usage: python test_microphone.py [OPTIONS] [device_index]")
            print("\nOptions:")
            print("  --auto, -a    Run automatically without prompts")
            print("  --help, -h    Show this help")
            print("\nExamples:")
            print("  python test_microphone.py         # Interactive mode")
            print("  python test_microphone.py --auto  # Auto mode")
            print("  python test_microphone.py 2       # Use device index 2")
            sys.exit(0)
        else:
            try:
                device_index = int(arg)
                print(f"Using specified device index: {device_index}")
            except ValueError:
                print(f"Invalid argument: {arg}")
                print("Usage: python test_microphone.py [--auto] [device_index]")
                sys.exit(1)
    
    # Step 1: List devices
    input_devices = list_audio_devices()
    
    if not input_devices:
        print("\n❌ No input devices found! Cannot continue.")
        sys.exit(1)
    
    # Step 2: Test recording
    print("\n" + "-" * 80)
    if not auto_mode:
        input("\n📌 Press ENTER to start recording test (speak during recording)...")
    else:
        print("\n🤖 Auto mode: Starting recording test...")
    
    recording_ok = test_microphone_recording(device_index, duration=3)
    
    # Step 3: Continuous monitoring
    print("\n" + "-" * 80)
    if not auto_mode:
        input("\n📌 Press ENTER to start continuous monitoring (speak during monitoring)...")
    else:
        print("\n🤖 Auto mode: Starting continuous monitoring...")
    
    monitoring_ok = test_continuous_monitoring(device_index, duration=5)
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    if recording_ok and monitoring_ok:
        print("\n✅ All tests PASSED - Microphone is working correctly!")
        print("\n   If you're still having issues with DecisionsAI:")
        print("   1. Check that the correct audio device is selected in Settings > Audio")
        print("   2. Try increasing the microphone volume in system settings")
        print("   3. Check that no other apps are using the microphone")
    elif recording_ok or monitoring_ok:
        print("\n⚠️  Some tests passed - Microphone may have intermittent issues")
        print("\n   Try the following:")
        print("   1. Restart the application")
        print("   2. Check audio device settings")
        print("   3. Try a different input device")
    else:
        print("\n❌ All tests FAILED - Microphone is not working!")
        print("\n   Troubleshooting steps:")
        print("   1. Check that the microphone is connected")
        print("   2. Check system audio settings")
        print("   3. Check microphone permissions (System Preferences > Privacy > Microphone)")
        print("   4. Try a different audio device")
        print("   5. Restart your computer")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
