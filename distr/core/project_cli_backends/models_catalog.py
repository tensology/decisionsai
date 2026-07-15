"""Shared CLI model catalogs for Pi, OpenCode, and project model pickers."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def model_entry(
    model_id: str,
    provider: str,
    name: str | None = None,
    *,
    free: bool | None = None,
    tier: str = "",
    scope: str = "available",
    supports_chat: bool | None = None,
    reason: str = "",
) -> dict[str, Any]:
    model_id = (model_id or "").strip()
    row: dict[str, Any] = {
        "id": model_id,
        "name": (name or model_id).strip() or model_id,
        "provider": provider,
        "scope": scope,
        "tier": tier,
        "reason": reason,
    }
    if free is not None:
        row["free"] = bool(free)
    if supports_chat is not None:
        row["supports_chat"] = bool(supports_chat)
    return row


def enrich_model_entry(item: dict[str, Any], *, default_free: bool | None = None) -> dict[str, Any]:
    """Attach stable catalog metadata used by CLI pickers."""
    out = dict(item or {})
    model_id = (out.get("id") or "").strip().lower()
    provider = (out.get("provider") or "").strip().lower()
    if "free" not in out and "is_free" in out:
        out["free"] = bool(out.get("is_free"))
    if "free" not in out and "(free)" in str(out.get("name") or "").lower():
        out["free"] = True
    if "scope" not in out or not out.get("scope"):
        out["scope"] = "available"
    if "free" not in out and default_free is not None:
        out["free"] = bool(default_free)
    if "free" not in out:
        out["free"] = provider in {"ollama", "local", "pi"} or model_id.endswith(":latest")
    if "tier" not in out or not out.get("tier"):
        if any(token in model_id for token in ("pro", "opus", "max", "large", "70b")):
            out["tier"] = "high"
        elif any(token in model_id for token in ("mini", "nano", "fast", "small", "0.5b", "0.6b", "1.7b")):
            out["tier"] = "low"
        else:
            out["tier"] = "standard"
    if "supports_chat" not in out:
        out["supports_chat"] = not any(token in model_id for token in ("embed", "embedding", "whisper", "tts", "vision-only"))
    if "local" not in out:
        out["local"] = provider in {"ollama", "local", "pi"}
    if "usable" not in out:
        out["usable"] = bool(out.get("supports_chat", True)) and str(out.get("id") or "").strip() != ""
    return out


def model_catalog_summary(models: list[dict]) -> dict[str, int]:
    rows = [enrich_model_entry(m) for m in (models or [])]
    return {
        "total": len(rows),
        "usable": sum(1 for m in rows if m.get("usable")),
        "scoped": sum(1 for m in rows if m.get("scope") == "scoped"),
        "free": sum(1 for m in rows if m.get("free")),
        "local": sum(1 for m in rows if m.get("local")),
        "chat": sum(1 for m in rows if m.get("supports_chat", True)),
    }


def recommend_cli_model(
    models: list[dict],
    *,
    prefer_free: bool = True,
    prefer_local: bool = True,
    prefer_scoped: bool = True,
    complexity: str = "medium",
) -> dict[str, Any]:
    rows = [enrich_model_entry(m) for m in (models or [])]
    usable = [
        m for m in rows
        if m.get("usable") and str(m.get("id") or "").strip() and str(m.get("id")) != "auto"
    ]
    if not usable:
        return {
            "id": "auto",
            "provider": "",
            "reason": "No concrete chat-capable models were available, so Auto is safest.",
            "score": 0,
        }

    wanted_tier = "high" if str(complexity or "").lower() == "high" else ("low" if str(complexity or "").lower() == "low" else "standard")

    def score(model: dict[str, Any]) -> int:
        model_id = str(model.get("id") or "").lower()
        tier = str(model.get("tier") or "standard").lower()
        value = 0
        if prefer_scoped and model.get("scope") == "scoped":
            value += 80
        if prefer_free and model.get("free"):
            value += 60
        if prefer_local and model.get("local"):
            value += 50
        if tier == wanted_tier:
            value += 25
        if "coder" in model_id or "codex" in model_id or "code" in model_id:
            value += 20
        if wanted_tier == "high" and tier == "high":
            value += 20
        if wanted_tier == "low" and tier == "low":
            value += 15
        if tier == "high" and wanted_tier == "low":
            value -= 15
        # Kilo's moving aliases are deliberately stable as individual free
        # promotions expire. Prefer the free alias over a dated model that may
        # still linger in the provider catalog after its free period ends.
        if model_id == "openrouter/free" and str(model.get("provider") or "").lower() == "kilocode":
            value += 120
        return value

    selected = sorted(usable, key=score, reverse=True)[0]
    reasons: list[str] = []
    if selected.get("scope") == "scoped":
        reasons.append("scoped/enabled")
    if selected.get("free"):
        reasons.append("free")
    if selected.get("local"):
        reasons.append("local")
    if selected.get("supports_chat", True):
        reasons.append("chat-capable")
    if selected.get("tier"):
        reasons.append(f"{selected.get('tier')} tier")
    return {
        "id": selected.get("id") or "",
        "name": selected.get("name") or selected.get("id") or "",
        "provider": selected.get("provider") or "",
        "backend_id": selected.get("backend_id") or "",
        "reason": "Selected because it is " + ", ".join(reasons) + ".",
        "score": score(selected),
        "model": selected,
    }


def dedupe_model_entries(models: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in models:
        mid = (item.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(enrich_model_entry(item))
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
                        models.append(model_entry(mid, prov_name, mname, scope="scoped"))
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
            models.append(model_entry(mid, "openai", free=False, scope="available"))
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
        if provider == "kilocode":
            rows.extend(
                [
                    model_entry(
                        "openrouter/free",
                        "kilocode",
                        "Kilo Auto Free",
                        free=True,
                        tier="standard",
                        scope="scoped",
                        reason="Stable Kilo free router exposed by Pi's live catalog.",
                    ),
                ]
            )
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
                rows.append(model_entry(
                    mid,
                    provider,
                    name,
                    free=bool(item.get("is_free")) if isinstance(item, dict) and "is_free" in item else False,
                    scope="available",
                ))
    return rows


def pi_cli_models(settings: dict | None = None) -> list[dict]:
    """Pi/OpenCode shared catalog: local models.json + configured cloud providers."""
    from distr.core.settings import load_settings_from_db

    settings = settings or load_settings_from_db()
    return dedupe_model_entries(
        models_from_pi_json() + settings_backed_cloud_models(settings)
    )


def opencode_models(settings: dict) -> tuple[list[dict], str, str]:
    fallback = [model_entry("auto", "opencode", "Auto", scope="scoped")]
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
