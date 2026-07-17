"""Model-aware execution timing for project CLI workers.

Timeouts are safety ceilings, not estimates of how long work should take. Local
models need a larger ceiling when their weights are not already resident, while
small/cloud models should still fail reasonably quickly when genuinely stuck.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class WorkerTimingPolicy:
    timeout_seconds: int
    model_loaded: bool | None
    parameter_billions: float | None
    rationale: str


def model_parameter_billions(model: str) -> float | None:
    """Extract a model-size hint such as ``35`` from ``ornith:35b``."""
    matches = re.findall(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*b(?:\b|$)", str(model or "").lower())
    if not matches:
        return None
    try:
        return float(matches[-1])
    except (TypeError, ValueError):
        return None


def is_local_model_route(*, backend_id: str, model: str, provider: str = "") -> bool:
    provider_id = str(provider or "").strip().lower()
    model_id = str(model or "").strip().lower()
    if provider_id in {"ollama", "local"}:
        return True
    if provider_id in {"openrouter", "kilocode", "openai", "anthropic"}:
        return False
    return str(backend_id or "").strip().lower() == "pi" and (
        model_parameter_billions(model_id) is not None or "/" not in model_id
    )


def ollama_model_loaded(model: str, *, timeout_seconds: float = 0.6) -> bool | None:
    """Return Ollama residency when available; ``None`` means unknown."""
    model_id = str(model or "").strip().lower()
    if not model_id:
        return None
    host = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    try:
        req = Request(host + "/api/ps", headers={"Accept": "application/json"})
        with urlopen(req, timeout=timeout_seconds) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    requested_base = model_id.split(":", 1)[0]
    requested_has_tag = ":" in model_id
    for row in payload.get("models") or []:
        loaded = str((row or {}).get("name") or (row or {}).get("model") or "").strip().lower()
        if loaded == model_id:
            return True
        # A base-name match is safe only when one side omits a tag. Distinct
        # sizes such as ornith:9b and ornith:35b are different resident models.
        if (not requested_has_tag or ":" not in loaded) and loaded.split(":", 1)[0] == requested_base:
            return True
    return False


def resolve_worker_timing(
    *,
    backend_id: str,
    model: str,
    provider: str = "",
    complexity: str = "medium",
    configured_timeout_seconds: int | None = None,
    model_loaded: bool | None = None,
) -> WorkerTimingPolicy:
    """Resolve a conservative hard ceiling from route and runtime readiness."""
    configured = max(60, int(configured_timeout_seconds or 300))
    local = is_local_model_route(backend_id=backend_id, model=model, provider=provider)
    size = model_parameter_billions(model)
    if not local:
        timeout = max(configured, 900)
        return WorkerTimingPolicy(timeout, model_loaded, size, "cloud/remote CLI execution budget")

    # Warm/cold ceilings include generation and tool execution. Unknown
    # residency is deliberately treated as cold so a readiness probe failure
    # cannot recreate the old five-minute false timeout.
    if size is None:
        warm, cold = 900, 1500
    elif size <= 10:
        warm, cold = 600, 900
    elif size <= 20:
        warm, cold = 900, 1200
    elif size <= 40:
        warm, cold = 1200, 1800
    else:
        warm, cold = 1800, 2700
    selected = warm if model_loaded is True else cold
    if str(complexity or "").strip().lower() in {"high", "complex", "critical"}:
        selected = int(selected * 1.25)
    timeout = min(3600, max(configured, selected))
    residency = "warm/resident" if model_loaded is True else (
        "cold/not resident" if model_loaded is False else "residency unknown; cold allowance"
    )
    size_label = f"{size:g}B" if size is not None else "unknown-size"
    return WorkerTimingPolicy(
        timeout,
        model_loaded,
        size,
        f"local {size_label} model, {residency}, {complexity or 'medium'} complexity",
    )
