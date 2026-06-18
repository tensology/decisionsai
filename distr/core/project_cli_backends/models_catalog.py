"""Shared CLI model catalogs for Pi, OpenCode, and project model pickers."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def model_entry(model_id: str, provider: str, name: str | None = None) -> dict[str, str]:
    model_id = (model_id or "").strip()
    return {
        "id": model_id,
        "name": (name or model_id).strip() or model_id,
        "provider": provider,
    }


def dedupe_model_entries(models: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in models:
        mid = (item.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(item)
    return out


def models_from_pi_json() -> list[dict]:
    """Models declared in ~/.pi/agent/models.json plus built-in OpenAI ids."""
    models: list[dict] = []
    try:
        models_path = os.path.expanduser("~/.pi/agent/models.json")
        if os.path.exists(models_path):
            with open(models_path, encoding="utf-8") as f:
                cfg = json.load(f)
            for prov_name, prov in (cfg.get("providers") or {}).items():
                for m in (prov.get("models") or []):
                    mid = (m.get("id") or "").strip()
                    mname = (m.get("name") or mid).strip()
                    if mid:
                        models.append(model_entry(mid, prov_name, mname))
    except Exception as exc:
        logger.debug("Failed to load models.json: %s", exc)

    builtin_openai = [
        "gpt-5.4-pro", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
        "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2-codex", "gpt-5.2-pro",
        "gpt-5.1-codex-max", "gpt-5.1-codex", "gpt-5-pro", "gpt-5",
        "o3-pro", "o3", "o3-deep-research", "o4-mini", "o4-mini-deep-research",
        "o1", "o1-pro", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
        "gpt-4o", "gpt-4o-mini",
    ]
    for mid in builtin_openai:
        if not any(m["id"] == mid for m in models):
            models.append(model_entry(mid, "openai"))
    return models


def settings_backed_cloud_models(settings: dict) -> list[dict]:
    """Live catalog from DecisionsAI third-party keys (OpenRouter, NVIDIA, Kilo, …)."""
    from distr.core.services.settings_service import thirdparty_llm_provider_ready
    from distr.gui.utils.get_ollama_models import (
        get_gemini_models,
        get_groq_models,
        get_kilo_models,
        get_nvidia_models,
    )
    from distr.gui.utils.get_openrouter_models import get_openrouter_models

    rows: list[dict] = []
    catalog: list[tuple[str, str, str, Any]] = [
        ("openrouter", "openrouter_enabled", "openrouter_key", get_openrouter_models),
        ("kilocode", "kilo_enabled", "kilo_key", get_kilo_models),
        ("nvidia", "nvidia_enabled", "nvidia_key", get_nvidia_models),
        ("groq", "groq_enabled", "groq_key", get_groq_models),
        ("gemini", "gemini_enabled", "gemini_key", get_gemini_models),
    ]
    for provider, enabled_key, key_key, fetcher in catalog:
        if not thirdparty_llm_provider_ready(settings, enabled_key, key_key):
            continue
        api_key = (settings.get(key_key) or "").strip()
        try:
            fetched = fetcher(api_key) or []
        except Exception as exc:
            logger.debug("cloud cli models %s: %s", provider, exc)
            continue
        for item in fetched:
            if isinstance(item, dict):
                mid = (item.get("id") or "").strip()
                name = (item.get("name") or mid).strip()
            else:
                mid = str(item).strip()
                name = mid
            if mid:
                rows.append(model_entry(mid, provider, name))
    return rows


def pi_cli_models(settings: dict | None = None) -> list[dict]:
    """Pi/OpenCode shared catalog: local models.json + configured cloud providers."""
    from distr.core.settings import load_settings_from_db

    settings = settings or load_settings_from_db()
    return dedupe_model_entries(
        models_from_pi_json() + settings_backed_cloud_models(settings)
    )


def opencode_models(settings: dict) -> tuple[list[dict], str, str]:
    fallback = [model_entry("auto", "opencode", "Auto")]
    executable = shutil.which("opencode")
    if not executable:
        merged = dedupe_model_entries(fallback + pi_cli_models(settings))
        return merged, "opencode-missing", "OpenCode not on PATH; showing shared cloud catalog."
    try:
        result = subprocess.run(
            [executable, "models"],
            capture_output=True,
            text=True,
            timeout=25,
        )
        if result.returncode == 0:
            models = list(fallback)
            for line in (result.stdout or "").splitlines():
                mid = line.strip()
                if not mid:
                    continue
                provider = mid.split("/")[0] if "/" in mid else "opencode"
                tail = mid.rsplit("/", 1)[-1]
                models.append(model_entry(mid, provider, tail))
            if len(models) > 1:
                return dedupe_model_entries(models), "opencode-cli", ""
    except Exception as exc:
        logger.debug("opencode models: %s", exc)
    merged = dedupe_model_entries(fallback + pi_cli_models(settings))
    return merged, "opencode-fallback", "Could not list OpenCode models; showing shared cloud catalog."
