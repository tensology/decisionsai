"""Feature flags for the durable cost ledger.

``cost_ledger_enabled`` defaults **True** so local recording is on without a
Settings UI toggle. Recording is no-op-safe when disabled.

``cost_invoice_display`` is ``blended`` (default) or ``explicit``. The Reports
UI query param ``display=`` overrides for the view only.
"""

from __future__ import annotations

import os
from typing import Any

FLAG_KEY = "cost_ledger_enabled"
ENV_KEY = "DECISIONS_COST_LEDGER_ENABLED"
DISPLAY_KEY = "cost_invoice_display"
DISPLAY_ENV_KEY = "DECISIONS_COST_INVOICE_DISPLAY"
DEFAULT_ENABLED = True
DEFAULT_DISPLAY = "blended"
VALID_DISPLAY = frozenset({"blended", "explicit"})


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on", "enabled"}


def env_flag_override() -> bool | None:
    raw = os.environ.get(ENV_KEY)
    if raw is None or str(raw).strip() == "":
        return None
    return _truthy(raw)


def is_cost_ledger_enabled(settings: dict[str, Any] | None = None) -> bool:
    """Resolve cost_ledger_enabled (env wins; default True)."""
    env = env_flag_override()
    if env is not None:
        return env
    if settings is None:
        try:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        except Exception:
            settings = {}
    if FLAG_KEY in (settings or {}):
        return _truthy(settings.get(FLAG_KEY))
    return DEFAULT_ENABLED


def normalize_display(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in VALID_DISPLAY:
        return text
    return DEFAULT_DISPLAY


def get_invoice_display(settings: dict[str, Any] | None = None) -> str:
    """Resolve cost_invoice_display (env wins; default blended)."""
    raw = os.environ.get(DISPLAY_ENV_KEY)
    if raw is not None and str(raw).strip() != "":
        return normalize_display(raw)
    if settings is None:
        try:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        except Exception:
            settings = {}
    return normalize_display((settings or {}).get(DISPLAY_KEY, DEFAULT_DISPLAY))
