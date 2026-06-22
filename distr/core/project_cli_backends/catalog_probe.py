"""Shared model/catalog probing for workflow CLI backends."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from distr.core.project_cli_backends import get_backend, normalize_backend_id
from distr.core.project_cli_backends.ide_handoff import is_ide_backend
from distr.core.project_cli_backends.models_catalog import (
    dedupe_model_entries,
    model_catalog_summary,
    model_entry,
    opencode_models,
    pi_cli_models,
    recommend_cli_model,
)
from distr.core.project_cli_backends.registry import _cursor_api_key


CLI_BACKEND_IDS = ("codex", "claude_code", "opencode", "kiro", "cursor", "cline", "pi")
VERIFIED_MODEL_SOURCES = {
    "anthropic-api",
    "codex-cli",
    "cursor-api",
    "kiro-cli",
    "opencode-cli",
    "pi-models",
}
PARTIAL_MODEL_SOURCES = {
    "claude-code-aliases",
    "cline-config",
    "codex-unverified",
    "cursor-defaults",
    "kiro-unverified",
}

_MODEL_RESULT_CACHE_LOCK = threading.RLock()
_MODEL_RESULT_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_MODEL_RESULT_CACHE_TTL_SECONDS = 120.0


def _settings_fingerprint(settings: dict | None) -> str:
    settings = settings or {}
    keys = (
        "anthropic_key",
        "cursor_key",
        "gemini_enabled",
        "gemini_key",
        "groq_enabled",
        "groq_key",
        "kilo_enabled",
        "kilo_key",
        "nvidia_enabled",
        "nvidia_key",
        "ollama_enabled",
        "ollama_url",
        "openai_key",
        "openrouter_enabled",
        "openrouter_key",
    )
    payload = {key: settings.get(key) for key in keys}
    return json.dumps(payload, sort_keys=True, default=str)


def _clone_model_result(result: dict[str, Any]) -> dict[str, Any]:
    cloned = dict(result or {})
    cloned["models"] = [dict(model) for model in cloned.get("models") or []]
    return cloned


def kiro_models(settings: dict) -> tuple[list[dict], str, str]:
    try:
        backend = get_backend("kiro")
        status = backend.setup_status()
        if not status.path:
            raise RuntimeError("Kiro CLI not found")
        result = subprocess.run(
            [status.path, "chat", "--list-models", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=12,
        )
        if result.returncode == 0:
            payload = json.loads((result.stdout or "").strip() or "{}")
            rows = []
            for item in payload.get("models") or []:
                mid = (item.get("model_id") or item.get("model_name") or "").strip()
                name = (item.get("model_name") or mid).strip()
                if mid:
                    rows.append(model_entry(mid, "kiro", name, scope="available", free=False))
            if rows:
                return dedupe_model_entries(rows), "kiro-cli", ""
    except Exception as exc:
        return [model_entry("auto", "kiro", "Auto", scope="scoped")], "kiro-unverified", f"Could not fetch verified Kiro models: {exc}. Only Auto is available."
    return [model_entry("auto", "kiro", "Auto", scope="scoped")], "kiro-unverified", "Kiro did not return a verified model list. Only Auto is available."


def cursor_api_models(settings: dict) -> tuple[list[dict], str, str]:
    fallback = [
        model_entry("auto", "cursor", "Auto", scope="scoped"),
        model_entry("composer-2.5", "cursor", scope="scoped"),
        model_entry("composer-2.5-fast", "cursor", scope="scoped", tier="low"),
        model_entry("gpt-5.3-codex", "cursor", scope="available", free=False),
        model_entry("gpt-5.5-medium", "cursor", scope="available", free=False),
    ]
    api_key = _cursor_api_key()
    if not api_key:
        return fallback, "cursor-defaults", "No Cursor API key configured; showing Cursor defaults."
    req = urllib.request.Request(
        "https://api.cursor.com/v0/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return fallback, "cursor-defaults", f"Cursor models API returned HTTP {exc.code}; showing defaults."
    except Exception as exc:
        return fallback, "cursor-defaults", f"Could not fetch Cursor models API: {exc}; showing defaults."
    raw_models = payload.get("models") or payload.get("data") or []
    models = []
    for raw in raw_models:
        if isinstance(raw, str):
            mid = raw
            name = raw
            scoped = True
        elif isinstance(raw, dict):
            mid = raw.get("id") or raw.get("name") or raw.get("model") or ""
            name = raw.get("display_name") or raw.get("name") or mid
            scoped = bool(raw.get("scoped") or raw.get("enabled") or raw.get("selected"))
        else:
            continue
        if mid:
            models.append(model_entry(mid, "cursor", name, scope="scoped" if scoped else "available", free=False))
    if not models:
        return fallback, "cursor-defaults", "Cursor models API returned no models; showing defaults."
    return dedupe_model_entries([fallback[0]] + models), "cursor-api", ""


def anthropic_models(settings: dict) -> tuple[list[dict], str, str]:
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or settings.get("anthropic_key") or "").strip()
    aliases = [
        model_entry("default", "claude_code", "Default", scope="scoped"),
        model_entry("sonnet", "claude_code", "Sonnet", scope="scoped"),
        model_entry("opus", "claude_code", "Opus", scope="scoped", tier="high"),
        model_entry("haiku", "claude_code", "Haiku", scope="scoped", tier="low"),
        model_entry("sonnet[1m]", "claude_code", "Sonnet 1M", scope="scoped", tier="high"),
        model_entry("opusplan", "claude_code", "Opus plan", scope="scoped", tier="high"),
    ]
    if not api_key:
        return aliases, "claude-code-aliases", "No Anthropic API key configured; showing Claude Code aliases only."
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return aliases, "claude-code-aliases", f"Anthropic models API returned HTTP {exc.code}; showing aliases."
    except Exception as exc:
        return aliases, "claude-code-aliases", f"Could not fetch Anthropic models: {exc}; showing aliases."
    models = [
        model_entry(item.get("id") or "", "anthropic", item.get("display_name"), free=False, scope="available")
        for item in payload.get("data") or []
        if item.get("id")
    ]
    return dedupe_model_entries(aliases + models), "anthropic-api", ""


def codex_models(settings: dict | None = None) -> tuple[list[dict], str, str]:
    del settings
    try:
        result = subprocess.run(
            ["codex", "models"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if result.returncode == 0:
            text = (result.stdout or "").strip()
            models = []
            for line in text.splitlines():
                mid = line.strip().split()[0] if line.strip() else ""
                if mid and not mid.lower().startswith(("-", "usage", "error")):
                    models.append(model_entry(mid, "codex"))
            if models:
                return dedupe_model_entries(models), "codex-cli", ""
    except Exception:
        pass

    return (
        [model_entry("auto", "codex", "Auto", scope="scoped")],
        "codex-unverified",
        "Codex CLI is connected, but it did not expose a verified model list in this session. Use Auto for Codex-managed model selection.",
    )


def cli_model_metadata(models: list[dict], *, complexity: str = "medium") -> dict:
    return {
        "summary": model_catalog_summary(models),
        "recommended_model": recommend_cli_model(models, complexity=complexity),
    }


def backend_truth_contract(status: Any, model_result: dict[str, Any] | None = None) -> dict[str, Any]:
    model_result = model_result or {}
    source = str(model_result.get("source") or "").strip()
    kind = str(model_result.get("kind") or "cli").strip()
    supports_model_picker = bool(model_result.get("supports_model_picker", True))
    models = model_result.get("models") or []
    verified_model_count = len([m for m in models if (m.get("id") or "").strip().lower() not in {"", "auto", "default"}])
    catalog_verified = source in VERIFIED_MODEL_SOURCES
    model_catalog_state = "verified" if catalog_verified else ("partial" if source in PARTIAL_MODEL_SOURCES or model_result.get("message") else "unknown")
    auth_verified = bool(getattr(status, "ready", False)) and str(getattr(status, "state", "") or "") != "auth_required"

    if not getattr(status, "installed", False):
        health_state = "missing"
        workflow_ready = False
        health_message = str(getattr(status, "message", "") or "Backend is not installed.")
    elif not getattr(status, "ready", False) or str(getattr(status, "state", "") or "") == "auth_required":
        health_state = "setup"
        workflow_ready = False
        health_message = str(getattr(status, "message", "") or getattr(status, "setup_instructions", "") or "Backend setup is incomplete.")
    elif kind != "cli" or not supports_model_picker:
        health_state = "ready"
        workflow_ready = True
        health_message = str(getattr(status, "message", "") or "Backend is ready.")
    elif catalog_verified:
        health_state = "ready"
        workflow_ready = True
        health_message = str(getattr(status, "message", "") or "Backend is ready and model catalog is verified.")
    else:
        health_state = "setup"
        workflow_ready = False
        health_message = str(model_result.get("message") or getattr(status, "message", "") or "Backend model catalog is not verified yet.")

    return {
        "workflow_ready": workflow_ready,
        "health_state": health_state,
        "health_message": health_message,
        "catalog_verified": catalog_verified,
        "model_catalog_state": model_catalog_state,
        "auth_verified": auth_verified,
        "verified_model_count": verified_model_count,
        "models_source": source,
    }


def models_for_cli_backend(backend_id: str, settings: dict | None = None) -> dict[str, Any]:
    backend_id = normalize_backend_id(backend_id)
    settings = settings or {}
    cache_key = (backend_id, _settings_fingerprint(settings))
    now = time.time()
    with _MODEL_RESULT_CACHE_LOCK:
        cached = _MODEL_RESULT_CACHE.get(cache_key)
        if cached and now - cached[0] < _MODEL_RESULT_CACHE_TTL_SECONDS:
            return _clone_model_result(cached[1])
    if is_ide_backend(backend_id):
        return {
            "models": [],
            "source": "ide",
            "message": "IDE backends choose the model inside the editor.",
            "kind": "ide",
            "supports_model_picker": False,
        }
    if backend_id == "cursor":
        models, source, message = cursor_api_models(settings)
    elif backend_id == "claude_code":
        models, source, message = anthropic_models(settings)
    elif backend_id == "codex":
        models, source, message = codex_models(settings)
    elif backend_id == "cline":
        models, source, message = (
            [{"id": "auto", "name": "Auto", "provider": "cline", "backend_id": backend_id}],
            "cline-config",
            "Default model comes from cline auth; override per task with -m.",
        )
    elif backend_id == "opencode":
        models, source, message = opencode_models(settings)
    elif backend_id == "kiro":
        models, source, message = kiro_models(settings)
    else:
        models, source, message = pi_cli_models(settings), "pi-models", ""
    for model in models:
        model["backend_id"] = backend_id
    result = {
        "models": models,
        "source": source,
        "message": message,
        "kind": "cli",
        "supports_model_picker": True,
    }
    with _MODEL_RESULT_CACHE_LOCK:
        _MODEL_RESULT_CACHE[cache_key] = (now, _clone_model_result(result))
    return result


def backend_capability_contract(backend_id: str) -> list[dict[str, Any]]:
    backend_id = normalize_backend_id(backend_id)
    if backend_id == "codex":
        return [
            {"id": "model", "label": "Model", "source": "verified-if-codex-reports", "values": []},
            {"id": "reasoning_effort", "label": "Reasoning effort", "source": "app-integration", "values": ["low", "medium", "high"]},
            {"id": "service_tier", "label": "Service tier", "source": "app-integration", "values": ["default", "flex", "fast"]},
        ]
    if backend_id == "claude_code":
        return [
            {"id": "model", "label": "Model alias", "source": "verified-alias-or-api", "values": ["default", "sonnet", "opus", "haiku", "sonnet[1m]", "opusplan"]},
        ]
    if backend_id in {"cursor", "opencode", "kiro", "cline", "pi"}:
        return [
            {"id": "model", "label": "Model", "source": "verified-when-cli-or-provider-reports", "values": []},
        ]
    return []


def backend_probe_commands(backend_id: str, executable_path: str | None) -> list[list[str]]:
    backend_id = normalize_backend_id(backend_id)
    executable = executable_path or backend_id
    if backend_id == "codex":
        return [[executable, "models"], [executable, "exec", "--help"], [executable, "--help"]]
    if backend_id == "cursor":
        return [[executable, "--version"], [executable, "status"]]
    if backend_id == "claude_code":
        return [[executable, "--version"], [executable, "--help"]]
    if backend_id == "opencode":
        return [[executable, "models"], [executable, "--help"]]
    if backend_id == "kiro":
        return [[executable, "chat", "--list-models", "--format", "json"], [executable, "--help"]]
    if backend_id == "cline":
        return [[executable, "--version"], [executable, "--help"]]
    if backend_id == "pi":
        return [[executable, "--version"]]
    return [[executable, "--help"]]


def run_probe_command(argv: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=12,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        return {
            "argv": argv,
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": stdout[:4000],
            "stderr": stderr[:4000],
        }
    except FileNotFoundError as exc:
        return {"argv": argv, "ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    except Exception as exc:
        return {"argv": argv, "ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}


def probe_cli_backend(backend_id: str, settings: dict | None = None) -> dict[str, Any]:
    backend_id = normalize_backend_id(backend_id)
    backend = get_backend(backend_id)
    status = backend.setup_status()
    model_result = models_for_cli_backend(backend_id, settings)
    commands = [run_probe_command(argv) for argv in backend_probe_commands(backend_id, status.path)]
    next_step = ""
    if not status.ready:
        next_step = status.message or status.setup_instructions or ""
    elif model_result.get("message"):
        next_step = str(model_result.get("message") or "")
    truth = backend_truth_contract(status, model_result)
    return {
        "backend_id": backend_id,
        "backend_name": backend.name,
        "status": status.to_dict(),
        "model_result": model_result,
        "model_metadata": cli_model_metadata(model_result.get("models") or []),
        "truth": truth,
        "capabilities": backend_capability_contract(backend_id),
        "probe_commands": commands,
        "next_step": next_step,
    }
