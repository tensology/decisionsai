"""Shared LiteLLM configuration — keep background LLM calls quiet in the console."""

from __future__ import annotations

import contextlib
import io
import logging
import os
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

_configured = False

F = TypeVar("F", bound=Callable[..., Any])


def configure_litellm() -> None:
    """Apply process-wide LiteLLM quiet defaults (safe to call repeatedly)."""
    global _configured
    os.environ.setdefault("LITELLM_LOG", "ERROR")

    for name in ("LiteLLM", "litellm"):
        logging.getLogger(name).setLevel(logging.ERROR)

    try:
        import litellm
    except ImportError:
        return

    litellm.set_verbose = False
    if hasattr(litellm, "suppress_debug_info"):
        litellm.suppress_debug_info = True

    _configured = True


# litellm model prefix -> (settings column, env var). Background calls (planners,
# initiative, validators) pass only a model string; without this the key saved
# in Settings never reached OpenRouter ("No cookie auth credentials").
_PREFIX_KEYS: dict[str, tuple[str, str]] = {
    "openrouter/": ("openrouter_key", "OPENROUTER_API_KEY"),
    "groq/": ("groq_key", "GROQ_API_KEY"),
    "gemini/": ("gemini_key", "GEMINI_API_KEY"),
    "nvidia/": ("nvidia_key", "NVIDIA_API_KEY"),
    "nvidia_nim/": ("nvidia_key", "NVIDIA_NIM_API_KEY"),
    "anthropic/": ("anthropic_key", "ANTHROPIC_API_KEY"),
}


def provider_credentials(model: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return ``api_key``/``api_base`` kwargs for a litellm model string.

    The value comes from the environment or Decisions' saved Settings and is
    passed straight to the provider; it is never logged.
    """
    name = str(model or "").strip()
    lowered = name.lower()
    if lowered.startswith(("ollama/", "ollama_chat/")):
        base = str((settings or _load_settings()).get("ollama_url") or "").strip().rstrip("/")
        return {"api_base": base} if base else {}
    for prefix, (column, env_name) in _PREFIX_KEYS.items():
        if lowered.startswith(prefix):
            break
    else:
        if "/" in lowered:
            return {}
        if lowered.startswith("claude"):
            column, env_name = "anthropic_key", "ANTHROPIC_API_KEY"
        else:
            column, env_name = "openai_key", "OPENAI_API_KEY"
    value = str(os.environ.get(env_name) or "").strip()
    if not value:
        value = str((settings or _load_settings()).get(column) or "").strip()
    return {"api_key": value} if value else {}


def _load_settings() -> dict[str, Any]:
    try:
        from distr.core.utils import load_settings_from_db

        return load_settings_from_db() or {}
    except Exception:
        return {}


_ollama_tags_cache: tuple[float, frozenset[str]] | None = None


def ollama_installed_models(base_url: str = "", *, max_age_seconds: float = 120.0) -> frozenset[str] | None:
    """Names the local Ollama daemon can serve (``None`` when it is unreachable)."""
    global _ollama_tags_cache
    import time

    now = time.monotonic()
    if _ollama_tags_cache and now - _ollama_tags_cache[0] < max_age_seconds:
        return _ollama_tags_cache[1]
    host = (base_url or os.environ.get("OLLAMA_API_BASE") or "http://127.0.0.1:11434").rstrip("/")
    try:
        import json
        from urllib.request import urlopen

        with urlopen(host + "/api/tags", timeout=1.5) as response:
            rows = json.loads(response.read().decode("utf-8")).get("models") or []
    except Exception:
        return None
    names = frozenset(
        str(row.get("name") or row.get("model") or "").strip().lower() for row in rows if isinstance(row, dict)
    )
    _ollama_tags_cache = (now, names)
    return names


def ollama_model_available(model: str, base_url: str = "") -> bool | None:
    """True when ``model`` (bare id, ``:cloud`` included) is pulled locally."""
    names = ollama_installed_models(base_url)
    if names is None:
        return None
    wanted = str(model or "").strip().lower()
    if wanted.startswith(("ollama/", "ollama_chat/")):
        wanted = wanted.split("/", 1)[1]
    return wanted in names or f"{wanted}:latest" in names


def litellm_completion(*args, **kwargs):
    """Call ``litellm.completion`` without spamming stderr on provider failures."""
    configure_litellm()
    import litellm

    model = kwargs.get("model") or (args[0] if args else "")
    for key, value in provider_credentials(str(model or "")).items():
        kwargs.setdefault(key, value)

    with contextlib.redirect_stderr(io.StringIO()):
        return litellm.completion(*args, **kwargs)
