"""
Shared state for web UI audio device updates.

When the desktop app detects new audio devices (audio_device_lists_updated),
it increments the counter so the web UI can poll and refresh its dropdowns.
"""
import threading

_audio_devices_update_counter = 0
_lock = threading.Lock()


def increment_audio_devices_updated() -> None:
    """Call when new audio devices are detected (from signal handler)."""
    global _audio_devices_update_counter
    with _lock:
        _audio_devices_update_counter += 1


def get_audio_devices_update_counter() -> int:
    """Return current counter for web UI polling."""
    with _lock:
        return _audio_devices_update_counter
