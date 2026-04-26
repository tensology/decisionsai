"""Normalize ``speak`` / speaker flags from HTTP, Qt signals, JSON, or NumPy."""

from __future__ import annotations


def coerce_speak_enabled(val, *, default: bool = True) -> bool:
    """Return True if assistant TTS should run for this request.

    Qt and JSON occasionally pass values that are truthy but fail ``x is True``
    (e.g. ``numpy.bool_``, ``np.int64(1)``). Those cases previously produced
    ``speak_bool = False`` in strict ``is True`` checks → visible stream, no audio.
    """
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("false", "0", "no", "off"):
            return False
        if s in ("true", "1", "yes", "on"):
            return True
        return default
    try:
        import numpy as np

        if isinstance(val, np.generic):
            return bool(val.item()) if hasattr(val, "item") else bool(val)
    except ImportError:
        pass
    if isinstance(val, (int, float)):
        return bool(val)
    return default
