#!/usr/bin/env python3
"""
Test Device Combinations

Tests different combinations of input and output devices to find
which combinations work without the "Illegal combination" error.
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import pytest

pytestmark = pytest.mark.live_audio

pytest.importorskip("sounddevice")
pytest.importorskip("pyaudio")

import sounddevice as sd
import pyaudio
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_all_devices():
    """Get all available audio devices"""
    devices = sd.query_devices()
    input_devices = []
    output_devices = []
    
    for i, device in enumerate(devices):
        name = device['name']
        max_in = device.get('max_input_channels', 0)
        max_out = device.get('max_output_channels', 0)
        default_sr = device.get('default_samplerate', 0)
        
        if max_in > 0:
            input_devices.append({
                'index': i,
                'name': name,
                'max_input_channels': max_in,
                'default_samplerate': default_sr
            })
        
        if max_out > 0:
            output_devices.append({
                'index': i,
                'name': name,
                'max_output_channels': max_out,
                'default_samplerate': default_sr
            })
    
    return input_devices, output_devices


def test_device_combination(input_idx, output_idx, input_sr=None, output_sr=None):
    """Test if a specific device combination works"""
    try:
        pa = pyaudio.PyAudio()
        
        # Get device info to determine sample rates
        devices = sd.query_devices()
        input_device = devices[input_idx]
        output_device = devices[output_idx]
        
        # Use device default sample rates if not specified
        if input_sr is None:
            input_sr = int(input_device.get('default_samplerate', 44100))
        if output_sr is None:
            output_sr = int(output_device.get('default_samplerate', 44100))
        
        # For the test, use a common sample rate that both might support
        # Try 44100 first (most common), then 48000, then 16000
        test_rates = [44100, 48000, 16000]
        
        for test_rate in test_rates:
            try:
                # Try to open input stream with output device index specified
                # This is what Pipecat does - it passes both device indices
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=test_rate,
                    input=True,
                    output=False,
                    input_device_index=input_idx,
                    output_device_index=output_idx,  # This is the key - passing output index even for input stream
                    frames_per_buffer=320
                )
                stream.close()
                return True, f"Works at {test_rate}Hz"
            except Exception as e:
                error_str = str(e)
                if "-9993" in error_str or "Illegal combination" in error_str:
                    return False, "Illegal combination of I/O devices"
                elif "-9997" in error_str or "Invalid sample rate" in error_str:
                    # Try next sample rate
                    continue
                else:
                    # Other error - return it
                    return False, f"Error: {error_str}"
        
        # If we get here, all sample rates failed
        return False, "All sample rates failed"
        
    except Exception as e:
        return False, f"Exception: {e}"
    finally:
        try:
            pa.terminate()
        except:
            pass


def find_working_combinations():
    """Find all working device combinations"""
    logger.info("="*80)
    logger.info("DEVICE COMBINATION TEST")
    logger.info("="*80)
    
    input_devices, output_devices = get_all_devices()
    
    logger.info(f"\nFound {len(input_devices)} input devices and {len(output_devices)} output devices\n")
    
    logger.info("Input devices:")
    for dev in input_devices:
        logger.info(f"  [{dev['index']:2d}] {dev['name']} (SR: {dev['default_samplerate']}Hz)")
    
    logger.info("\nOutput devices:")
    for dev in output_devices:
        logger.info(f"  [{dev['index']:2d}] {dev['name']} (SR: {dev['default_samplerate']}Hz)")
    
    logger.info("\n" + "="*80)
    logger.info("Testing device combinations...")
    logger.info("="*80 + "\n")
    
    working_combinations = []
    failed_combinations = []
    
    # Test all combinations
    for input_dev in input_devices:
        for output_dev in output_devices:
            input_idx = input_dev['index']
            output_idx = output_dev['index']
            input_name = input_dev['name']
            output_name = output_dev['name']
            
            # Skip if same device
            if input_idx == output_idx:
                logger.info(f"⏭️  Skipping: {input_name} (same device for input and output)")
                continue
            
            logger.info(f"Testing: Input={input_name} [{input_idx}] + Output={output_name} [{output_idx}]...")
            
            works, message = test_device_combination(input_idx, output_idx)
            
            if works:
                logger.info(f"  ✅ WORKS")
                working_combinations.append({
                    'input': input_name,
                    'input_idx': input_idx,
                    'output': output_name,
                    'output_idx': output_idx
                })
            else:
                logger.info(f"  ❌ FAILED: {message}")
                failed_combinations.append({
                    'input': input_name,
                    'input_idx': input_idx,
                    'output': output_name,
                    'output_idx': output_idx,
                    'error': message
                })
    
    # Summary
    logger.info("\n" + "="*80)
    logger.info("RESULTS SUMMARY")
    logger.info("="*80)
    
    logger.info(f"\n✅ Working combinations: {len(working_combinations)}")
    if working_combinations:
        logger.info("\nRecommended device combinations:")
        for i, combo in enumerate(working_combinations, 1):
            logger.info(f"\n  {i}. Input:  {combo['input']} [{combo['input_idx']}]")
            logger.info(f"     Output: {combo['output']} [{combo['output_idx']}]")
    
    logger.info(f"\n❌ Failed combinations: {len(failed_combinations)}")
    if failed_combinations:
        logger.info("\nFailed combinations (for reference):")
        for i, combo in enumerate(failed_combinations[:10], 1):  # Show first 10
            logger.info(f"\n  {i}. Input:  {combo['input']} [{combo['input_idx']}]")
            logger.info(f"     Output: {combo['output']} [{combo['output_idx']}]")
            logger.info(f"     Error:  {combo['error']}")
        if len(failed_combinations) > 10:
            logger.info(f"\n     ... and {len(failed_combinations) - 10} more failed combinations")
    
    # Find best recommendations
    logger.info("\n" + "="*80)
    logger.info("RECOMMENDATIONS")
    logger.info("="*80)
    
    if working_combinations:
        # Prefer virtual devices (pulse, default, pipewire)
        virtual_combos = []
        hardware_combos = []
        
        for combo in working_combinations:
            input_name = combo['input'].lower()
            output_name = combo['output'].lower()
            
            is_virtual = any(v in input_name for v in ['pulse', 'default', 'pipewire', 'dmix']) or \
                        any(v in output_name for v in ['pulse', 'default', 'pipewire', 'dmix'])
            
            if is_virtual:
                virtual_combos.append(combo)
            else:
                hardware_combos.append(combo)
        
        if virtual_combos:
            logger.info("\n🌟 Best recommendations (virtual devices - support resampling):")
            for i, combo in enumerate(virtual_combos[:5], 1):
                logger.info(f"\n  {i}. Input:  {combo['input']}")
                logger.info(f"     Output: {combo['output']}")
        
        if hardware_combos:
            logger.info("\n💻 Hardware device combinations (may have sample rate limitations):")
            for i, combo in enumerate(hardware_combos[:5], 1):
                logger.info(f"\n  {i}. Input:  {combo['input']}")
                logger.info(f"     Output: {combo['output']}")
    else:
        logger.error("\n❌ No working combinations found!")
        logger.error("   This suggests a deeper audio system issue.")
    
    logger.info("\n" + "="*80)
    
    return working_combinations, failed_combinations


if __name__ == "__main__":
    try:
        working, failed = find_working_combinations()
        sys.exit(0 if working else 1)
    except KeyboardInterrupt:
        logger.info("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n\nTest failed with error: {e}", exc_info=True)
        sys.exit(1)
