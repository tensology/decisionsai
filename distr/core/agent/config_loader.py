"""
Config loader - pure functions for parsing configuration from settings.

Extracted from session.py to reduce duplication (STT parsing appears 2x)
and to make config logic testable independently.
"""

import json
import logging
import os
import subprocess
import sys
from typing import Optional, List, Tuple

from .constants import (
    DEFAULT_ASSEMBLYAI_MODEL, DEFAULT_OPENAI_WHISPER_MODEL,
    VALID_ASSEMBLYAI_MODELS,
)

logger = logging.getLogger(__name__)


def resolve_stt_config(transcription_model: str) -> dict:
    """Parse a transcription_model string into an STT config dict.

    Returns ``{'engine': ..., 'model': ...}`` (model key only present for
    engines that require it).

    Used by both ``_load_config`` and the ``update_stt_model`` command handler
    so the parsing logic lives in one place.
    """
    result = {}

    if 'Vosk' in transcription_model:
        result['engine'] = 'vosk'
    elif 'AssemblyAI' in transcription_model:
        result['engine'] = 'assemblyai'
        if '(' in transcription_model and ')' in transcription_model:
            start = transcription_model.find('(') + 1
            end = transcription_model.find(')')
            model_part = transcription_model[start:end].strip()
            result['model'] = model_part if model_part in VALID_ASSEMBLYAI_MODELS else DEFAULT_ASSEMBLYAI_MODEL
        else:
            result['model'] = DEFAULT_ASSEMBLYAI_MODEL
    elif 'OpenAI Whisper' in transcription_model:
        result['engine'] = 'openai_whisper'
        if '(' in transcription_model and ')' in transcription_model:
            start = transcription_model.find('(') + 1
            end = transcription_model.find(')')
            model_part = transcription_model[start:end]
            result['model'] = model_part if 'whisper' in model_part else DEFAULT_OPENAI_WHISPER_MODEL
        else:
            result['model'] = DEFAULT_OPENAI_WHISPER_MODEL
    elif 'Whisper' in transcription_model or 'whisper' in transcription_model.lower():
        result['engine'] = 'whisper'
    elif 'VibeVoice ASR' in transcription_model or (
        'vibevoice' in transcription_model.lower() and 'asr' in transcription_model.lower()
    ):
        result['engine'] = 'vibevoice_asr'
    else:
        # Caller should handle the fallback (check input_speech setting, etc.)
        result['engine'] = None

    return result


def _find_device_in_list(devices, device_name: str, is_input: bool):
    """Find device index in list. Returns (index, None) if found, (None, available_names) if not."""
    device_name_lower = device_name.lower()
    for i, device in enumerate(devices):
        if device['name'] == device_name:
            if is_input and device['max_input_channels'] > 0:
                return i, None
            if not is_input and device['max_output_channels'] > 0:
                return i, None
    for i, device in enumerate(devices):
        if device['name'].strip().lower() == device_name_lower:
            if is_input and device['max_input_channels'] > 0:
                return i, None
            if not is_input and device['max_output_channels'] > 0:
                return i, None
    for i, device in enumerate(devices):
        dname = device['name'].strip()
        if is_input and device['max_input_channels'] <= 0:
            continue
        if not is_input and device['max_output_channels'] <= 0:
            continue
        if device_name_lower in dname.lower() or dname.lower() in device_name_lower:
            logger.info("Audio device match (substring): requested '%s' -> using '%s' (index %d)", device_name, dname, i)
            return i, None
    available = [d['name'] for d in devices if (d['max_input_channels'] > 0 if is_input else d['max_output_channels'] > 0)]
    return None, available


def _query_devices_fresh_subprocess() -> Optional[List[Tuple[int, str, int, int]]]:
    """Query devices from a fresh Python subprocess (own PortAudio init = fresh device list).
    Returns [(index, name, max_input_ch, max_output_ch), ...] or None on failure.
    Safe: does not touch main process PortAudio or active streams."""
    script = """
import json
import sounddevice as sd
devices = sd.query_devices()
out = [(i, d.get('name',''), d.get('max_input_channels',0), d.get('max_output_channels',0))
       for i, d in enumerate(devices)]
print(json.dumps(out))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning("Fresh device query subprocess failed: %s", result.stderr or result.stdout)
            return None
        data = json.loads(result.stdout.strip())
        return [(int(x[0]), str(x[1]), int(x[2]), int(x[3])) for x in data]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        logger.warning("Could not query devices via subprocess: %s", e)
        return None


def _find_device_in_fresh_list(
    devices: List[Tuple[int, str, int, int]], device_name: str, is_input: bool
) -> Optional[int]:
    """Find device index in fresh subprocess device list."""
    device_name_lower = device_name.lower()
    for i, name, max_in, max_out in devices:
        if not name:
            continue
        if is_input and max_in <= 0:
            continue
        if not is_input and max_out <= 0:
            continue
        if (device_name == name or
            name.strip().lower() == device_name_lower or
            device_name_lower in name.lower() or
            name.lower() in device_name_lower):
            return i
    return None


def resolve_device_index(device_name: Optional[str], is_input: bool, sd_module=None) -> Optional[int]:
    """Get audio device index from device name.

    Pure function (no session state). Tries exact match, case-insensitive, then substring.
    If not found, queries devices from a fresh subprocess (own PortAudio init = picks up
    newly connected Bluetooth) and retries. Returns ``None`` for system default.
    """
    if not device_name or (isinstance(device_name, str) and device_name.strip() == '') or device_name == 'System Default':
        return None

    if sd_module is None:
        logger.warning("sounddevice not available, cannot get device index")
        return None

    device_name = device_name.strip()
    try:
        devices = sd_module.query_devices()
        idx, available = _find_device_in_list(devices, device_name, is_input)
        if idx is not None:
            return idx

        # Not found: query from fresh subprocess (own PortAudio = picks up newly connected Bluetooth)
        role = "input" if is_input else "output"
        logger.info("Requested %s device '%s' not in cached list. Querying fresh device list...", role, device_name)
        fresh = _query_devices_fresh_subprocess()
        if fresh:
            idx = _find_device_in_fresh_list(fresh, device_name, is_input)
            if idx is not None:
                logger.info("Found '%s' in fresh device list (index %d)", device_name, idx)
                return idx

        logger.warning(
            "Requested %s device '%s' not found. Using system default. Available: %s",
            role, device_name, available,
        )
    except Exception as e:
        logger.warning("Error finding device %s: %s", device_name, e)

    return None
