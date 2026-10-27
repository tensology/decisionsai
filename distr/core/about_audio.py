"""Cross-process marker for short-lived startup audio (the splash video, the About chime).

That audio plays in the GUI process; TTS speaks via ``sounddevice`` in the agent
process. They are different processes and different audio streams, so a tiny
timestamp file lets the TTS pipeline wait for it to finish instead of talking over it.
"""

from __future__ import annotations

import time
from pathlib import Path

# Length of assets/sounds/decisions.mp3 (seconds).
ABOUT_AUDIO_SECONDS = 7.08


def _marker_path() -> Path:
    return Path.home() / ".decisions" / "about_audio_until"


def mark_about_audio_started(duration: float = ABOUT_AUDIO_SECONDS) -> None:
    """Record when the chime is expected to finish."""
    try:
        path = _marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time() + max(0.0, float(duration))))
    except OSError:
        pass


def clear_about_audio() -> None:
    """Drop the marker (chime stopped or window closed)."""
    try:
        _marker_path().unlink()
    except OSError:
        pass


def about_audio_remaining() -> float:
    """Seconds left on the chime, or 0 when it is not playing."""
    try:
        until = float(_marker_path().read_text().strip())
    except (OSError, ValueError):
        return 0.0
    remaining = until - time.time()
    if remaining <= 0:
        clear_about_audio()
        return 0.0
    return remaining
