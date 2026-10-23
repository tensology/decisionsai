"""Single source of truth for Silero VADParams (session start + live slider)."""
from __future__ import annotations

from .constants import (
    VAD_CONFIDENCE_MIN, VAD_CONFIDENCE_MAX, VAD_START_SECS,
    HANDS_FREE_VAD_CONFIDENCE_MIN, HANDS_FREE_VAD_START_SECS, HANDS_FREE_VAD_MIN_VOLUME,
)


def vad_param_values(threshold, *, hands_free: bool) -> dict:
    """Return VADParams kwargs for a 0-100 slider threshold.

    Non-hands-free modes keep the historical behaviour (slider confidence,
    VAD_START_SECS). Hands-free raises the floor so speaker echo that leaks
    past AEC cannot open a user turn / barge-in.
    """
    confidence = max(VAD_CONFIDENCE_MIN, min(VAD_CONFIDENCE_MAX, float(threshold) / 100.0))
    if not hands_free:
        return {"confidence": confidence, "start_secs": VAD_START_SECS}
    return {
        "confidence": max(confidence, HANDS_FREE_VAD_CONFIDENCE_MIN),
        "start_secs": HANDS_FREE_VAD_START_SECS,
        "min_volume": HANDS_FREE_VAD_MIN_VOLUME,
    }


def build_vad_params(threshold, *, hands_free: bool):
    from .libs import VADParams
    return VADParams(**vad_param_values(threshold, hands_free=hands_free))
