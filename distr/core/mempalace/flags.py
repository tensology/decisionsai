"""Feature flag for MemPalace as Decisions memory backend.

Default ON. Disable via settings key ``mempalace_memory_backend: false`` or env
``DECISIONS_MEMPALACE_MEMORY_BACKEND=0|false|off|no``.

When OFF, all legacy memory surfaces keep their existing read/write paths.
When ON, wiring hooks prefer MemPalace (dual-write legacy for rollback).
Soft-fails if chromadb/mempalace are missing — see adapter / seed setup messages.
"""

from __future__ import annotations

import os
from typing import Any


FLAG_KEY = "mempalace_memory_backend"
ENV_KEY = "DECISIONS_MEMPALACE_MEMORY_BACKEND"
DEFAULT_ENABLED = True


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on", "enabled"}


def _falsy(value: Any) -> bool:
    if value is False:
        return True
    if value is True or value is None:
        return False
    if isinstance(value, (int, float)):
        return value == 0
    text = str(value).strip().lower()
    return text in {"0", "false", "no", "off", "disabled"}


def env_flag_override() -> bool | None:
    """Return True/False if env forces the flag, else None (defer to settings)."""
    raw = os.environ.get(ENV_KEY)
    if raw is None or str(raw).strip() == "":
        return None
    if _falsy(raw):
        return False
    return _truthy(raw)


def is_mempalace_memory_backend_enabled(settings: dict[str, Any] | None = None) -> bool:
    """Resolve mempalace_memory_backend (env wins over settings; default True)."""
    env = env_flag_override()
    if env is not None:
        return env
    if settings is None:
        try:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        except Exception:
            settings = {}
    if settings and FLAG_KEY in settings:
        return _truthy(settings.get(FLAG_KEY))
    return DEFAULT_ENABLED
