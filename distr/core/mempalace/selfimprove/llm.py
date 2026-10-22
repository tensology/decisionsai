"""Decisions-LLM reviewer: one OpenAI-compatible chat completion, no tools.

Last step of the nightly reviewer chain (codex exec -> claude -p -> this).
Default: OpenRouter with free models, tried in order:
  minimax/minimax-m3.1-flash, then thinkingmachines/inkling-small:free
Override with DECISIONS_SELFIMPROVE_LLM_PROVIDER (openrouter|kilocode|openai|groq|...)
and DECISIONS_SELFIMPROVE_LLM_MODELS (comma-separated). The API key comes from
the provider's env var (e.g. OPENROUTER_API_KEY) or Decisions' saved settings
(e.g. ``openrouter_key``); it is never logged or written anywhere.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

PROVIDER_ENV = "DECISIONS_SELFIMPROVE_LLM_PROVIDER"
MODELS_ENV = "DECISIONS_SELFIMPROVE_LLM_MODELS"
DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODELS = ["minimax/minimax-m3.1-flash", "thinkingmachines/inkling-small:free"]

_ENDPOINTS = {
    "openrouter": ("openrouter_key", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "kilocode": ("kilo_key", "KILO_API_KEY", "https://api.kilo.ai/api/gateway"),
    "openai": ("openai_key", "OPENAI_API_KEY", "https://api.openai.com/v1"),
    "groq": ("groq_key", "GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    "nvidia": ("nvidia_key", "NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1"),
    "ollama": (None, None, "http://127.0.0.1:11434/v1"),
}


def _api_key(provider: str) -> str:
    setting, env, _ = _ENDPOINTS[provider]
    if setting is None:
        return "ollama"
    if env and os.environ.get(env):
        return os.environ[env].strip()
    try:
        from distr.core.settings import load_settings_from_db

        return str(load_settings_from_db().get(setting) or "").strip()
    except Exception:
        return ""


def config() -> tuple[str, list[str]]:
    provider = (os.environ.get(PROVIDER_ENV) or DEFAULT_PROVIDER).strip().lower()
    models = [m.strip() for m in (os.environ.get(MODELS_ENV) or "").split(",") if m.strip()] or list(DEFAULT_MODELS)
    return provider, models


def complete(prompt: str, *, timeout: int = 240) -> tuple[str, str]:
    """Return (model_used, text). Tries each configured model; raises RuntimeError if none answers."""
    provider, models = config()
    if provider not in _ENDPOINTS:
        raise RuntimeError(f"unsupported provider {provider}")
    key = _api_key(provider)
    if not key:
        raise RuntimeError(f"no API key configured for {provider}")
    base = _ENDPOINTS[provider][2]
    errors = []
    for model in models:
        body = json.dumps({"model": model, "temperature": 0.2, "max_tokens": 2000,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "HTTP-Referer": "https://tensology.ai/decisions", "X-Title": "Decisions self-improve"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            if text.strip():
                return f"{provider}:{model}", text
            errors.append(f"{model}: empty reply")
        except urllib.error.HTTPError as exc:
            errors.append(f"{model}: HTTP {exc.code}")
        except Exception as exc:  # network, timeout, bad JSON
            errors.append(f"{model}: {type(exc).__name__}")
    raise RuntimeError("; ".join(errors))
