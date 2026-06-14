"""
Audio utility functions for device detection and management.

This module provides functions for detecting and managing audio devices
using native OS methods (macOS/Unix/Windows).
"""

import logging
import json
import platform
import subprocess
from distr.core.settings import load_settings_from_db, save_settings_to_db
from distr.core.db import get_session
import numpy as np

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
except ImportError:
    sd = None

# Cache for query_macos_devices — system_profiler is expensive (hammers coreaudiod).
# Only re-run it at most once every 30 seconds.
_macos_device_cache: tuple = ([], [])   # (outputs, inputs)
_macos_device_cache_ts: float = 0.0
_MACOS_DEVICE_CACHE_TTL: float = 30.0  # seconds


def get_device_type(device_name: str) -> str:
    """Determine device type from device name."""
    name_lower = device_name.lower()
    if 'bluetooth' in name_lower or 'bt' in name_lower:
        return "Bluetooth"
    elif 'built-in' in name_lower or 'internal' in name_lower or 'macbook' in name_lower:
        return "Built-in"
    elif 'speakers' in name_lower or 'microphone' in name_lower or 'realtek' in name_lower:
        return "Built-in"
    elif 'usb' in name_lower:
        return "USB"
    elif 'airpods' in name_lower:
        return "Bluetooth"
    elif 'headphone' in name_lower or 'headset' in name_lower:
        return "Headphones"
    else:
        return "Other"


def format_devices_for_api(devices: list, default_id: int = -1) -> list:
    """Format device list for API response. Prepends System Default."""
    result = [{"name": "System Default", "id": default_id, "type": "Other"}]
    for d in devices:
        if isinstance(d, dict):
            result.append({
                "name": d.get("name", ""),
                "id": d.get("id", d.get("name", "")),
                "type": d.get("type", get_device_type(d.get("name", "")))
            })
    return result


def query_native_devices():
    """
    Query devices using native OS methods (macOS/Unix/Windows).
    Returns (output_devices, input_devices) where each is a list of dicts:
    [{'name': str, 'id': str, 'type': str}, ...]
    """
    system = platform.system()
    if system == 'Darwin':  # macOS
        return query_macos_devices()
    elif system == 'Windows':
        return query_windows_devices()
    else:  # Linux/Unix
        return query_unix_devices()


def query_windows_devices():
    """Query devices using sounddevice on Windows (cross-platform, works on all OSes)."""
    if sd is None:
        logger.warning("sounddevice not available, cannot query Windows audio devices")
        return [], []
    try:
        outputs = []
        inputs = []
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            name = dev.get('name', '')
            if not name:
                continue
            device_type = get_device_type(name)
            device_info = {'name': name, 'id': str(i), 'type': device_type}
            if dev.get('max_output_channels', 0) > 0:
                outputs.append(device_info)
            if dev.get('max_input_channels', 0) > 0:
                inputs.append(device_info)
        logger.info(f"query_windows_devices: Found {len(outputs)} outputs, {len(inputs)} inputs")
        return outputs, inputs
    except Exception as e:
        logger.error(f"Error querying Windows devices: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return [], []


def query_macos_devices():
    """Query devices using system_profiler on macOS. Results are cached for 30s to avoid hammering coreaudiod."""
    import time
    global _macos_device_cache, _macos_device_cache_ts
    now = time.monotonic()
    if now - _macos_device_cache_ts < _MACOS_DEVICE_CACHE_TTL:
        return _macos_device_cache
    try:
        # Use system_profiler to get audio devices
        result = subprocess.run(
            ['system_profiler', '-json', 'SPAudioDataType'],
            capture_output=True,
            text=True,
            timeout=15  # Can be slow on Macs with many USB/Bluetooth devices
        )
        
        if result.returncode != 0:
            logger.error(f"system_profiler failed: {result.stderr}")
            return [], []
        
        data = json.loads(result.stdout)
        sp_audio_data = data.get('SPAudioDataType', [])
        
        outputs = []
        inputs = []
        
        # The structure is: SPAudioDataType[0]['_items'] contains the actual devices
        for top_level_item in sp_audio_data:
            device_items = top_level_item.get('_items', [])
            
            for item in device_items:
                name = item.get('_name', '')
                if not name or name == 'Unknown':
                    continue
                
                # Get device ID - use name as ID since system_profiler doesn't provide UID
                device_id = name
                
                # Determine type from transport
                transport = item.get('coreaudio_device_transport', '').lower()
                device_type = get_device_type(name)
                
                # Check if it has input/output capabilities
                has_input = item.get('coreaudio_device_input', 0) > 0
                has_output = item.get('coreaudio_device_output', 0) > 0
                
                # Also check for input/output source fields as backup
                if not has_input:
                    has_input = 'coreaudio_input_source' in item or 'coreaudio_default_audio_input_device' in item
                if not has_output:
                    has_output = 'coreaudio_output_source' in item or 'coreaudio_default_audio_output_device' in item
                
                device_info = {
                    'name': name,
                    'id': device_id,
                    'type': device_type
                }
                
                if has_input:
                    inputs.append(device_info)
                    logger.debug(f"Found input device: {name} (type: {device_type})")
                if has_output:
                    outputs.append(device_info)
                    logger.debug(f"Found output device: {name} (type: {device_type})")
        
        logger.debug(f"query_macos_devices: Found {len(outputs)} outputs, {len(inputs)} inputs")
        _macos_device_cache = (outputs, inputs)
        _macos_device_cache_ts = now
        return outputs, inputs

    except subprocess.TimeoutExpired:
        logger.warning("system_profiler timed out (using sounddevice fallback)")
        return _macos_device_cache  # return stale cache rather than empty
    except Exception as e:
        logger.error(f"Error querying macOS devices: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return _macos_device_cache  # return stale cache rather than empty


def query_unix_devices():
    """Query devices using PulseAudio on Linux/Unix."""
    try:
        outputs = []
        inputs = []
        
        # Try PulseAudio first (most common on modern Linux)
        try:
            # Get output devices (sinks)
            result = subprocess.run(
                ['pactl', 'list', 'short', 'sinks'],
                capture_output=True,
                text=True,
                timeout=3
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line:
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            device_id = parts[0]
                            device_name = parts[1]
                            device_type = get_device_type(device_name)
                            outputs.append({
                                'name': device_name,
                                'id': device_id,
                                'type': device_type
                            })
            
            # Get input devices (sources)
            result = subprocess.run(
                ['pactl', 'list', 'short', 'sources'],
                capture_output=True,
                text=True,
                timeout=3
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line and '.monitor' not in line:  # Skip monitor sources
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            device_id = parts[0]
                            device_name = parts[1]
                            device_type = get_device_type(device_name)
                            inputs.append({
                                'name': device_name,
                                'id': device_id,
                                'type': device_type
                            })
        except FileNotFoundError:
            # PulseAudio not available, try ALSA
            logger.debug("PulseAudio not found, trying ALSA")
            # ALSA is more complex, would need to parse /proc/asound/cards
            # For now, return empty lists
            pass
        
        logger.info(f"query_unix_devices: Found {len(outputs)} outputs, {len(inputs)} inputs")
        return outputs, inputs
        
    except Exception as e:
        logger.error(f"Error querying Unix devices: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return [], []


def refresh_device_lists():
    """
    Unified function to refresh device lists from native OS methods.
    Returns (output_devices, input_devices) as lists of dicts with name, id, type.
    """
    logger.info("Querying devices using native OS methods...")
    
    # Query devices using native OS methods (macOS/Unix)
    outputs, inputs = query_native_devices()
    
    logger.info(f"Found {len(outputs)} output devices and {len(inputs)} input devices")
    
    return outputs, inputs


def detect_devices():
    """
    Detect new devices and merge with existing list.
    Returns (newly_added_outputs, newly_added_inputs, merged_outputs, merged_inputs)
    """
    logger.info("Starting device detection...")
    
    # Get newly detected devices
    newly_detected_outputs, newly_detected_inputs = refresh_device_lists()
    
    # Load existing device lists from database
    settings = load_settings_from_db()
    existing_outputs = []
    existing_inputs = []
    
    try:
        locked_output_list_json = settings.get('locked_output_list')
        locked_input_list_json = settings.get('locked_input_list')
        
        if locked_output_list_json:
            existing_outputs = json.loads(locked_output_list_json)
        if locked_input_list_json:
            existing_inputs = json.loads(locked_input_list_json)
    except Exception as e:
        logger.warning(f"Error loading existing device lists: {e}")
    
    # Merge: Add new devices that aren't already in the list (by name, case-insensitive)
    merged_outputs = list(existing_outputs)
    merged_inputs = list(existing_inputs)
    
    # Track newly added devices
    newly_added_outputs = []
    newly_added_inputs = []
    
    # Helper to check if device exists in list
    def device_exists(device_list, device_name):
        device_name_lower = device_name.lower().strip()
        for dev in device_list:
            existing_name = dev['name'] if isinstance(dev, dict) else dev[0]
            if existing_name.lower().strip() == device_name_lower:
                return True
        return False
    
    # Add new output devices
    for new_dev in newly_detected_outputs:
        if not device_exists(merged_outputs, new_dev['name']):
            merged_outputs.append(new_dev)
            newly_added_outputs.append(new_dev)
            logger.info(f"Added new output device: {new_dev['name']}")
    
    # Add new input devices
    for new_dev in newly_detected_inputs:
        if not device_exists(merged_inputs, new_dev['name']):
            merged_inputs.append(new_dev)
            newly_added_inputs.append(new_dev)
            logger.info(f"Added new input device: {new_dev['name']}")
    
    # Save merged lists to database
    save_output_list_to_db(merged_outputs)
    save_input_list_to_db(merged_inputs)
    
    # Force commit
    with get_session() as session:
        session.commit()
    
    logger.info(f"Detection complete: {len(newly_added_outputs)} new outputs, {len(newly_added_inputs)} new inputs")
    
    return newly_added_outputs, newly_added_inputs, merged_outputs, merged_inputs


def reset_devices():
    """
    Reset device lists - clear all and repopulate with current system devices.
    Returns (output_devices, input_devices)
    """
    logger.info("Resetting device lists...")
    
    settings = load_settings_from_db()
    
    # Clear the remembered device selections
    settings['locked_output'] = None
    settings['locked_input'] = None
    
    # Clear the lists in database
    settings['locked_output_list'] = None
    settings['locked_input_list'] = None
    save_settings_to_db(settings)
    
    # Force a flush by committing the session
    with get_session() as session:
        session.commit()
    
    # Query current system devices
    output_devices, input_devices = refresh_device_lists()
    
    # Save the new lists
    save_output_list_to_db(output_devices)
    save_input_list_to_db(input_devices)
    
    logger.info(f"Reset complete: {len(output_devices)} outputs, {len(input_devices)} inputs")
    
    return output_devices, input_devices


def save_output_list_to_db(output_devices):
    """Save output device list to database with full device info (name, id, type)."""
    settings = load_settings_from_db()
    # Convert to dict format if needed
    device_list = []
    for dev in output_devices:
        if isinstance(dev, dict):
            device_list.append(dev)
        else:
            # It's a tuple (name, type) - use name as ID
            name, dev_type = dev
            device_list.append({'name': name, 'id': name, 'type': dev_type})
    
    settings['locked_output_list'] = json.dumps(device_list)
    save_settings_to_db(settings)
    logger.info(f"Saved {len(device_list)} output devices to database (with IDs)")


def save_input_list_to_db(input_devices):
    """Save input device list to database with full device info (name, id, type)."""
    settings = load_settings_from_db()
    # Convert to dict format if needed
    device_list = []
    for dev in input_devices:
        if isinstance(dev, dict):
            device_list.append(dev)
        else:
            # It's a tuple (name, type) - use name as ID
            name, dev_type = dev
            device_list.append({'name': name, 'id': name, 'type': dev_type})
    
    settings['locked_input_list'] = json.dumps(device_list)
    save_settings_to_db(settings)
    logger.info(f"Saved {len(device_list)} input devices to database (with IDs)")


def set_system_default_device(device_name: str, is_output: bool):
    """
    Set system default audio device on macOS or Unix/Linux.
    Returns True if successful, False otherwise.
    
    macOS: Uses SwitchAudioSource
    Unix/Linux: Uses pactl (PulseAudio)
    """
    system = platform.system()
    
    # macOS: Use SwitchAudioSource
    if system == 'Darwin':
        try:
            # Try using SwitchAudioSource if available (recommended method)
            try:
                result = subprocess.run(
                    ['which', 'SwitchAudioSource'],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    # For output devices, use -s with device name
                    # For input devices, we need to find the ID or UID first
                    if is_output:
                        # Output: use -s with device name
                        result = subprocess.run(
                            ['SwitchAudioSource', '-s', device_name],
                            capture_output=True,
                            text=True,
                            timeout=3
                        )
                        if result.returncode == 0:
                            logger.info(f"Set macOS default output to: {device_name}")
                            return True
                        else:
                            logger.warning(f"SwitchAudioSource -s failed: {result.stderr or result.stdout}")
                    else:
                        # Input: need to find device ID or UID
                        # Query all input devices in CLI format: name,type,id,uid
                        result = subprocess.run(
                            ['SwitchAudioSource', '-a', '-f', 'cli', '-t', 'input'],
                            capture_output=True,
                            text=True,
                            timeout=3
                        )
                        if result.returncode == 0:
                            device_found = False
                            for line in result.stdout.strip().split('\n'):
                                if line:
                                    parts = line.split(',')
                                    if len(parts) >= 4:
                                        name = parts[0].strip()
                                        device_id = parts[2].strip() if len(parts) > 2 else None
                                        device_uid = parts[3].strip() if len(parts) > 3 else None
                                        
                                        # Match by name (case-insensitive)
                                        if name.lower().strip() == device_name.lower().strip():
                                            device_found = True
                                            # Try using UID first (more reliable), then ID
                                            # Must specify -t input for input devices
                                            if device_uid:
                                                result = subprocess.run(
                                                    ['SwitchAudioSource', '-t', 'input', '-u', device_uid],
                                                    capture_output=True,
                                                    text=True,
                                                    timeout=3
                                                )
                                                if result.returncode == 0:
                                                    logger.info(f"Set macOS default input to: {device_name} (UID: {device_uid})")
                                                    return True
                                            
                                            # Fallback to ID (must specify -t input)
                                            if device_id:
                                                result = subprocess.run(
                                                    ['SwitchAudioSource', '-t', 'input', '-i', device_id],
                                                    capture_output=True,
                                                    text=True,
                                                    timeout=3
                                                )
                                                if result.returncode == 0:
                                                    logger.info(f"Set macOS default input to: {device_name} (ID: {device_id})")
                                                    return True
                                            
                                            logger.warning(f"Found device '{device_name}' but failed to set it (ID: {device_id}, UID: {device_uid})")
                                            break
                            
                            if not device_found:
                                logger.warning(f"Input device '{device_name}' not found in SwitchAudioSource list")
                            else:
                                logger.warning(f"Failed to set input device '{device_name}'")
                        else:
                            logger.warning(f"Failed to query input devices: {result.stderr or result.stdout}")
            except FileNotFoundError:
                pass
            except subprocess.TimeoutExpired:
                logger.warning("SwitchAudioSource timed out")
            
            # Fallback: Log that we need SwitchAudioSource
            logger.warning(f"Cannot set macOS default device without SwitchAudioSource. Device: {device_name}")
            logger.info("Install SwitchAudioSource: brew install switchaudio-osx")
            return False
            
        except Exception as e:
            logger.error(f"Error setting macOS default device: {e}")
            return False
    
    # Unix/Linux: Use PulseAudio (pactl)
    elif system == 'Linux' or system.startswith('Unix'):
        try:
            # Check if pactl is available
            result = subprocess.run(
                ['which', 'pactl'],
                capture_output=True,
                text=True,
                timeout=1
            )
            if result.returncode != 0:
                logger.warning("pactl not found. PulseAudio may not be installed.")
                logger.info("Install PulseAudio: sudo apt-get install pulseaudio-utils (Debian/Ubuntu) or equivalent")
                return False
            
            # Get device ID from device name
            # First, query devices to find the ID
            device_list_type = 'sinks' if is_output else 'sources'
            result = subprocess.run(
                ['pactl', 'list', 'short', device_list_type],
                capture_output=True,
                text=True,
                timeout=3
            )
            
            if result.returncode != 0:
                logger.warning(f"Failed to query {device_list_type}: {result.stderr}")
                return False
            
            # Find device by name (case-insensitive)
            device_id = None
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        candidate_id = parts[0]
                        candidate_name = parts[1]
                        if candidate_name.lower().strip() == device_name.lower().strip():
                            device_id = candidate_id
                            break
            
            if not device_id:
                logger.warning(f"Device '{device_name}' not found in {device_list_type}")
                return False
            
            # Set default device using device ID
            command = 'set-default-sink' if is_output else 'set-default-source'
            result = subprocess.run(
                ['pactl', command, device_id],
                capture_output=True,
                text=True,
                timeout=3
            )
            
            if result.returncode == 0:
                logger.info(f"Set Linux/Unix default {'output' if is_output else 'input'} to: {device_name} (ID: {device_id})")
                return True
            else:
                logger.warning(f"pactl {command} failed: {result.stderr}")
                return False
                
        except FileNotFoundError:
            logger.warning("pactl not found. PulseAudio may not be installed.")
            logger.info("Install PulseAudio: sudo apt-get install pulseaudio-utils (Debian/Ubuntu) or equivalent")
            return False
        except Exception as e:
            logger.error(f"Error setting Linux/Unix default device: {e}")
            return False
    
    elif system == 'Windows':
        # Windows has no standard CLI for switching default device.
        # Would require pycaw (Python Core Audio Windows) or registry edits.
        logger.info("Setting system default audio device is not supported on Windows. "
                    "Use Windows Settings > Sound to change the default device.")
        return False
    
    else:
        logger.warning(f"set_system_default_device not supported on {system}")
        return False


def is_system_default_device_name(device_name: str) -> bool:
    """Return True when the saved selection means follow the OS default route."""
    text = (device_name or "").strip().lower()
    return not text or text in ("system default", "system_default", "default")


def get_system_default_device_fingerprint() -> str:
    """Fingerprint of the current OS default input/output (fresh PortAudio query).

    Used by the periodic device checker to detect Bluetooth handoffs where the
    device list hash is unchanged but the system default route moved.
    """
    import hashlib
    from distr.core.agent.config_loader import (
        resolve_system_default_input_device,
        resolve_system_default_output_device,
    )

    in_idx, in_name = resolve_system_default_input_device()
    out_idx, out_name = resolve_system_default_output_device()
    payload = f"in:{in_idx}:{in_name}|out:{out_idx}:{out_name}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def find_device_in_list(device_list, device_name):
    """
    Find a device in the device list by name (case-insensitive).
    Returns the device dict if found, None otherwise.
    """
    device_name_lower = device_name.lower().strip()
    for dev in device_list:
        dev_name = dev['name'] if isinstance(dev, dict) else dev[0]
        if dev_name.lower().strip() == device_name_lower:
            return dev
    return None


def restore_locked_devices(settings: dict):
    """
    Restore locked audio devices to system defaults if "Remember my Audio Settings" is enabled.
    
    Args:
        settings: Dictionary containing lock_sound, locked_input, and locked_output
        
    Returns:
        dict with 'output_restored' and 'input_restored' boolean values
    """
    result = {'output_restored': False, 'input_restored': False}
    
    lock_sound_enabled = settings.get('lock_sound', False)
    if not lock_sound_enabled:
        return result
    
    locked_input = settings.get('locked_input')
    locked_output = settings.get('locked_output')
    
    if not locked_input and not locked_output:
        return result
    
    # Get current system devices
    try:
        outputs, inputs = query_native_devices()
        
        # Try to restore locked devices if they exist
        if locked_output:
            if is_system_default_device_name(locked_output):
                # Follow OS route; agent transport refresh picks up the new default.
                result['output_restored'] = True
            else:
                output_device = find_device_in_list(outputs, locked_output)
                if output_device:
                    success = set_system_default_device(locked_output, is_output=True)
                    if success:
                        logger.info(f"Set '{locked_output}' as system default output")
                        result['output_restored'] = True

        if locked_input:
            if is_system_default_device_name(locked_input):
                result['input_restored'] = True
            else:
                input_device = find_device_in_list(inputs, locked_input)
                if input_device:
                    success = set_system_default_device(locked_input, is_output=False)
                    if success:
                        logger.info(f"Set '{locked_input}' as system default input")
                        result['input_restored'] = True
    except Exception as e:
        logger.error(f"Error restoring locked devices: {e}", exc_info=True)
    
    return result


def get_current_device_list_hash():
    """
    Get an MD5 hash of the current device list for change detection.
    Uses sd.query_devices() (lightweight) instead of system_profiler to avoid
    hammering coreaudiod every 5 seconds.
    """
    import hashlib
    try:
        if sd is not None:
            devices = sd.query_devices()
            out_names = sorted([d['name'] for d in devices if d.get('max_output_channels', 0) > 0])
            in_names = sorted([d['name'] for d in devices if d.get('max_input_channels', 0) > 0])
            device_names = sorted(set(out_names + in_names))
            return hashlib.md5('|'.join(device_names).encode('utf-8')).hexdigest()
        # Fallback to native query (cached) if sounddevice unavailable
        outputs, inputs = query_native_devices()
        device_names = sorted([dev['name'] for dev in outputs] + [dev['name'] for dev in inputs])
        return hashlib.md5('|'.join(device_names).encode('utf-8')).hexdigest()
    except Exception as e:
        logger.error(f"Error getting device list hash: {e}")
        return ''


def pitch_preserving_time_stretch(audio, speed, sample_rate=24000):
    """
    Change the speed of audio without changing its pitch (time stretching).
    
    Args:
        audio (np.ndarray): Audio data (float32).
        speed (float): Speed factor (e.g., 1.5 for 1.5x speed).
        sample_rate (int): Sample rate of the audio.
        
    Returns:
        np.ndarray: Time-stretched audio.
    """
    if speed == 1.0 or len(audio) == 0:
        return audio
        
    # 1. Try librosa (best quality, but requires system libraries)
    try:
        import librosa
        # Librosa expects float32 audio
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
            
        # Explicitly load numba/llvmlite to trigger potential OSErrors early
        import numba
        import llvmlite.binding
            
        return librosa.effects.time_stretch(audio, rate=speed)
    except (OSError, ImportError, Exception):
        # Fail silently and use fallback
        pass

    # 2. Fallback: WSOLA (Waveform Similarity Overlap-Add)
    # Best quality for speech without librosa. Preserves pitch and natural timbre.
    try:
        from scipy.signal import correlate
        
        n_samples = len(audio)
        # Frame size: ~42ms at 24kHz. Good for speech.
        frame_len = 1024
        overlap = frame_len // 2
        
        # If chunk is too small for WSOLA, fallback to simple resampling
        if n_samples < frame_len * 2:
             from scipy import signal
             new_len = int(n_samples / speed)
             if new_len > 0:
                 return signal.resample(audio, new_len).astype(np.float32)
             return audio

        output_len = int(n_samples / speed)
        # Initialize output buffers
        output = np.zeros(output_len + frame_len, dtype=np.float32)
        norm_factor = np.zeros(output_len + frame_len, dtype=np.float32)
        
        win = np.hanning(frame_len)
        
        # Analysis hop varies with speed
        ana_hop = overlap * speed
        
        # Handle multi-channel for correlation
        if audio.ndim > 1:
            search_audio = audio[:, 0]
        else:
            search_audio = audio

        prev_pos = 0.0
        out_pos = 0
        
        while out_pos < output_len:
            # 1. Determine ideal input position based on speed
            # For the first frame, we start at 0
            if out_pos == 0:
                best_pos = 0
            else:
                # Ideal analysis position is strictly advanced by speed
                ideal_pos = int(prev_pos + ana_hop)
                
                # 2. Similarity Search (WSOLA)
                # We want to maintain phase continuity with the previous frame.
                # We look for a segment in the input, around ideal_pos, 
                # that matches the "natural continuation" of the previous frame.
                
                # Natural continuation starts at: prev_pos + overlap
                # (This is the part of the input that followed the segment we used last time)
                nat_cont_start = int(prev_pos) + overlap
                
                # Search range around ideal_pos
                search_dist = 512 
                start_s = max(0, ideal_pos - search_dist)
                end_s = min(n_samples - frame_len, ideal_pos + search_dist)
                
                # Validate bounds
                if nat_cont_start + overlap >= n_samples or end_s <= start_s:
                    best_pos = ideal_pos
                else:
                    # Template: The "overlap" region of the natural continuation
                    template = search_audio[nat_cont_start : nat_cont_start + overlap]
                    
                    # Candidates region: where we search for the match
                    # We need to extract enough context to slide template over
                    # region must cover [start_s ... end_s + overlap]
                    region = search_audio[start_s : end_s + overlap]
                    
                    if len(region) < len(template):
                        best_pos = ideal_pos
                    else:
                        # Correlate to find best match
                        cc = correlate(region, template, mode='valid')
                        best_offset = np.argmax(cc)
                        best_pos = start_s + best_offset
            
            # 3. Bounds check
            if best_pos + frame_len > n_samples:
                break
                
            # 4. Overlap-Add to output
            output[out_pos : out_pos + frame_len] += audio[best_pos : best_pos + frame_len] * win
            norm_factor[out_pos : out_pos + frame_len] += win
            
            # 5. Advance
            prev_pos = best_pos
            out_pos += overlap
            
        # Normalize overlap-add
        mask = norm_factor > 1e-5
        output[mask] /= norm_factor[mask]
        
        return output[:output_len]

    except Exception as e:
        logger.error(f"Time stretch WSOLA fallback failed: {e}")
        # Ultimate fallback: simple resampling
        try:
            from scipy import signal
            new_len = int(len(audio) / speed)
            if new_len > 0:
                return signal.resample(audio, new_len).astype(np.float32)
            return audio
        except Exception as e2:
            logger.error(f"Time stretch Resampling fallback failed: {e2}")
            return audio
