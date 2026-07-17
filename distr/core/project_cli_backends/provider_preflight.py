"""Financial/readiness checks performed before a model route is dispatched."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


_OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
_OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models?sort=intelligence-high-to-low"
_OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
_COMPLEXITY_BUFFER_USD = {
    "low": 0.02,
    "small": 0.02,
    "medium": 0.10,
    "high": 0.50,
    "complex": 0.50,
    "critical": 1.00,
}


@dataclass(frozen=True)
class ProviderPreflight:
    provider: str
    model: str
    status: str
    ready: bool | None
    message: str
    available_credit_usd: float | None = None
    required_buffer_usd: float | None = None
    http_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _price_is_zero(pricing: dict[str, Any]) -> bool:
    try:
        return all(float(pricing.get(key) or 0) == 0 for key in ("prompt", "completion", "request"))
    except (TypeError, ValueError):
        return False


def rank_openrouter_free_models(
    *, api_key: str = "", complexity: str = "medium",
    required_capabilities: list[str] | None = None, limit: int = 5,
    timeout_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    """Fetch and rank the current concrete free catalogue for coding work."""
    headers = {"Accept": "application/json"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    request = Request(_OPENROUTER_MODELS_URL, headers=headers)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    complexity_id = str(complexity or "medium").lower()
    min_context = 32768 if complexity_id in {"low", "small"} else (131072 if complexity_id in {"high", "complex", "critical"} else 65536)
    required = {str(item).strip().lower() for item in (required_capabilities or []) if str(item).strip()}
    require_tools = bool(required.intersection({"tools", "files", "shell", "cli", "computer_use"}))
    candidates: list[dict[str, Any]] = []
    for raw in payload.get("data") or []:
        if not isinstance(raw, dict):
            continue
        model_id = str(raw.get("id") or "").strip()
        pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
        supported = {str(item).strip().lower() for item in (raw.get("supported_parameters") or [])}
        architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
        outputs = {str(item).lower() for item in (architecture.get("output_modalities") or ["text"])}
        context = int(raw.get("context_length") or 0)
        if not model_id.endswith(":free") or not _price_is_zero(pricing) or "text" not in outputs:
            continue
        if require_tools and "tools" not in supported:
            continue
        if context < min_context:
            continue
        benchmarks = raw.get("benchmarks") if isinstance(raw.get("benchmarks"), dict) else {}
        aa = benchmarks.get("artificial_analysis") if isinstance(benchmarks.get("artificial_analysis"), dict) else {}
        coding = float(aa.get("coding_index") or 0)
        agentic = float(aa.get("agentic_index") or 0)
        intelligence = float(aa.get("intelligence_index") or 0)
        context_score = min(12.0, context / 65536.0)
        score = round((coding * 0.5) + (agentic * 0.25) + (intelligence * 0.2) + context_score, 3)
        reasons = []
        if "tools" in supported:
            reasons.append("tool calling")
        reasons.append(f"{context // 1024}K context")
        if coding:
            reasons.append(f"coding index {coding:g}")
        if agentic:
            reasons.append(f"agentic index {agentic:g}")
        candidates.append({
            "backend": "pi",
            "model_provider": "openrouter",
            "model": model_id,
            "name": str(raw.get("name") or model_id).strip(),
            "context_length": context,
            "supports_tools": "tools" in supported,
            "input_modalities": architecture.get("input_modalities") or ["text"],
            "coding_index": coding,
            "agentic_index": agentic,
            "intelligence_index": intelligence,
            "score": score,
            "reason": ", ".join(reasons),
        })
    candidates.sort(key=lambda item: (-float(item.get("score") or 0), str(item.get("model") or "")))
    for index, item in enumerate(candidates[: max(1, int(limit))], start=1):
        item["rank"] = index
    return candidates[: max(1, int(limit))]


def probe_openrouter_model_readiness(
    *, model: str, api_key: str, timeout_seconds: float = 12.0,
) -> ProviderPreflight:
    """Use a minimal completion to prove the selected free model is callable."""
    if not api_key.strip():
        return ProviderPreflight("openrouter", model, "blocked", False, "No OpenRouter API key is configured.")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply OK"}],
        "max_tokens": 1,
        "temperature": 0,
    }).encode("utf-8")
    request = Request(
        _OPENROUTER_CHAT_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response.read()
        return ProviderPreflight(
            "openrouter", model, "ready", True,
            f"{model} accepted a minimal readiness request.",
        )
    except HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            detail = str((payload.get("error") or {}).get("message") or "").strip()
        except Exception:
            detail = ""
        reason = detail or {
            402: "insufficient credit, including for this free route",
            429: "the free-route rate limit is currently exhausted",
            502: "the selected model endpoint is currently failing",
            503: "no provider endpoint is currently available for this model",
        }.get(exc.code, f"HTTP {exc.code}")
        return ProviderPreflight(
            "openrouter", model, "blocked", False,
            f"{model} failed readiness: {reason}.", http_status=exc.code,
        )
    except Exception as exc:
        return ProviderPreflight(
            "openrouter", model, "unverified", None,
            f"{model} readiness could not be verified: {type(exc).__name__}.",
        )


def _openrouter_preflight(
    *, model: str, api_key: str, complexity: str, timeout_seconds: float
) -> ProviderPreflight:
    provider = "openrouter"
    required = _COMPLEXITY_BUFFER_USD.get(str(complexity or "medium").lower(), 0.10)
    if not api_key.strip():
        return ProviderPreflight(
            provider, model, "blocked", False,
            "OpenRouter is selected but no API key is configured.",
            required_buffer_usd=required,
        )
    headers = {"Authorization": f"Bearer {api_key.strip()}", "Accept": "application/json"}
    account_remaining: float | None = None
    credits_request = Request(_OPENROUTER_CREDITS_URL, headers=headers)
    try:
        with urlopen(credits_request, timeout=timeout_seconds) as response:
            credits_payload = json.loads(response.read().decode("utf-8"))
        credits_data = credits_payload.get("data") if isinstance(credits_payload, dict) else {}
        credits_data = credits_data if isinstance(credits_data, dict) else {}
        account_remaining = round(
            float(credits_data.get("total_credits")) - float(credits_data.get("total_usage")),
            6,
        )
    except HTTPError as exc:
        if exc.code in {401, 402}:
            reason = (
                "OpenRouter rejected the configured API key."
                if exc.code == 401
                else "OpenRouter reports insufficient credit on the account."
            )
            return ProviderPreflight(
                provider, model, "blocked", False, reason,
                required_buffer_usd=required, http_status=exc.code,
            )
        # The credits endpoint can require a management-capable key. Fall back
        # to the current-key limit endpoint when access is forbidden.
        if exc.code != 403:
            account_remaining = None
    except Exception:
        account_remaining = None

    request = Request(
        _OPENROUTER_KEY_URL,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in {401, 402, 403}:
            reason = {
                401: "OpenRouter rejected the configured API key.",
                402: "OpenRouter reports insufficient credit for this API key.",
                403: "OpenRouter reports that this API key cannot inspect or use the requested route.",
            }[exc.code]
            return ProviderPreflight(
                provider, model, "blocked", False, reason,
                required_buffer_usd=required, http_status=exc.code,
            )
        return ProviderPreflight(
            provider, model, "unverified", None,
            f"OpenRouter financial preflight could not be verified (HTTP {exc.code}).",
            required_buffer_usd=required, http_status=exc.code,
        )
    except Exception as exc:
        return ProviderPreflight(
            provider, model, "unverified", None,
            f"OpenRouter financial preflight is temporarily unavailable: {type(exc).__name__}.",
            required_buffer_usd=required,
        )

    data = payload.get("data") if isinstance(payload, dict) else {}
    data = data if isinstance(data, dict) else {}
    remaining_raw = data.get("limit_remaining")
    if remaining_raw is None:
        if account_remaining is None:
            return ProviderPreflight(
                provider, model, "unverified", None,
                "OpenRouter key is valid, but account credit could not be verified.",
                required_buffer_usd=required,
            )
        remaining_raw = account_remaining
    try:
        remaining = float(remaining_raw)
        if account_remaining is not None:
            remaining = min(remaining, account_remaining)
    except (TypeError, ValueError):
        return ProviderPreflight(
            provider, model, "unverified", None,
            "OpenRouter returned an unreadable credit balance.",
            required_buffer_usd=required,
        )

    free_route = model.strip().lower() == "openrouter/free" or model.strip().lower().endswith(":free")
    minimum = 0.0 if free_route else required
    if remaining < minimum:
        return ProviderPreflight(
            provider, model, "blocked", False,
            (
                f"OpenRouter has ${remaining:.2f} available; this {complexity or 'medium'} "
                f"route requires at least a ${minimum:.2f} safety buffer."
            ),
            available_credit_usd=remaining,
            required_buffer_usd=minimum,
        )
    return ProviderPreflight(
        provider, model, "ready", True,
        f"OpenRouter financial preflight passed with ${remaining:.2f} available.",
        available_credit_usd=remaining,
        required_buffer_usd=minimum,
    )


def preflight_provider_route(
    route: dict[str, Any], *, settings: dict[str, Any] | None = None,
    complexity: str = "medium", timeout_seconds: float = 3.0,
) -> ProviderPreflight:
    """Check a provider without spending tokens or starting model work."""
    provider = str(route.get("model_provider") or "").strip().lower()
    model = str(route.get("model") or "auto").strip()
    if not provider:
        return ProviderPreflight("", model, "not_required", True, "No metered API provider is selected.")
    if provider == "openrouter":
        if settings is None:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db()
        return _openrouter_preflight(
            model=model,
            api_key=str((settings or {}).get("openrouter_key") or ""),
            complexity=complexity,
            timeout_seconds=timeout_seconds,
        )
    # Provider-specific probes can be added without changing workflow routing.
    return ProviderPreflight(
        provider, model, "unverified", None,
        f"{provider} does not expose a configured credit preflight; authentication will still be checked.",
    )
