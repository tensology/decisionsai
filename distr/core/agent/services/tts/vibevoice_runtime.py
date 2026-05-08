"""Paths and capability checks for Microsoft VibeVoice (Realtime TTS + optional ASR).

The ``vibevoice`` package is **not** in ``requirements.txt`` (its transformers pin conflicts with
Coqui / other deps). Install with ``./scripts/install_vibevoice.sh`` into your venv. Streaming
speaker ``.pt`` files live under the repo ``demo/voices/streaming_model/``. If unset,
``DECISIONSAI_VIBEVOICE_ROOT`` is auto-detected from common clone paths (e.g. ``$VIRTUAL_ENV/src/VibeVoice``).

Environment:

- ``DECISIONSAI_VIBEVOICE_ROOT``: absolute path to a clone of https://github.com/microsoft/VibeVoice
  (must contain ``demo/voices/streaming_model/*.pt`` speaker caches). Optional if discover finds a clone.
- ``DECISIONSAI_VIBEVOICE_MODEL``: Hugging Face id for Realtime TTS (default ``microsoft/VibeVoice-Realtime-0.5B``).
- ``DECISIONSAI_VIBEVOICE_ASR_MODEL``: Hugging Face id for ASR (default ``microsoft/VibeVoice-ASR``).

See: https://github.com/microsoft/VibeVoice
"""

from __future__ import annotations

import glob
import os
from typing import List, Tuple

# Default English streaming voices shipped in the VibeVoice repo (ids = .pt stem, lowercased).
DEFAULT_VIBEVOICE_REALTIME_VOICES: List[Tuple[str, str]] = [
    ("en-carter_man", "Carter (EN)"),
    ("en-davis_man", "Davis (EN)"),
    ("en-emma_woman", "Emma (EN)"),
    ("en-frank_man", "Frank (EN)"),
    ("en-grace_woman", "Grace (EN)"),
    ("en-mike_man", "Mike (EN)"),
]


def _discover_vibevoice_repo_root() -> str:
    """Return repo root if a clone with streaming .pt voices exists in known locations."""
    candidates = []
    ve = (os.environ.get("VIRTUAL_ENV") or "").strip()
    if ve:
        candidates.append(os.path.join(ve, "src", "VibeVoice"))
    cp = (os.environ.get("CONDA_PREFIX") or "").strip()
    if cp:
        candidates.append(os.path.join(cp, "src", "VibeVoice"))
    cache_home = (os.environ.get("XDG_CACHE_HOME") or "").strip() or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    candidates.append(os.path.join(cache_home, "decisionsai", "VibeVoice"))
    for root in candidates:
        root = os.path.abspath(os.path.expanduser(root))
        d = os.path.join(root, "demo", "voices", "streaming_model")
        if os.path.isdir(d) and glob.glob(os.path.join(d, "*.pt")):
            return root
    return ""


def vibevoice_repo_root() -> str:
    explicit = (os.environ.get("DECISIONSAI_VIBEVOICE_ROOT") or "").strip()
    if explicit:
        return explicit
    return _discover_vibevoice_repo_root()


def streaming_voices_dir() -> str:
    root = vibevoice_repo_root()
    if not root:
        return ""
    return os.path.abspath(os.path.join(root, "demo", "voices", "streaming_model"))


def vibevoice_package_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("vibevoice") is not None
    except Exception:
        return False


def vibevoice_tts_runtime_ready() -> bool:
    """True when the PyPI/git ``vibevoice`` package is importable *and* speaker .pt files exist."""
    if not vibevoice_package_available():
        return False
    d = streaming_voices_dir()
    return bool(d) and os.path.isdir(d) and bool(glob.glob(os.path.join(d, "*.pt")))


def vibevoice_asr_runtime_ready() -> bool:
    """ASR uses the same ``vibevoice`` install; model downloads on first use (large)."""
    return vibevoice_package_available()
