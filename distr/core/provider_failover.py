"""Provider failover shared by chat, Development threads and automations.

One place decides three things:

* whether an error proves the *provider route* is down (credits, quota,
  rate limit, DNS, connection, a model that cannot chat) rather than a task
  failure;
* which already-configured, already-keyed provider/model to try next; and
* the single plain-language notice shown when a provider is down.

Nothing here creates accounts, buys credits or contacts a provider. Candidates
only come from the user's failover order file or from models the user has
already configured in Settings, and only when that provider has a key.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Provider -> settings column holding its key. ``None`` means keyless (local).
PROVIDER_KEYS: dict[str, str | None] = {
    "openrouter": "openrouter_key",
    "openai": "openai_key",
    "anthropic": "anthropic_key",
    "groq": "groq_key",
    "kilocode": "kilo_key",
    "gemini": "gemini_key",
    "nvidia": "nvidia_key",
    "ollama": None,
}

PROVIDER_LABELS = {
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "kilocode": "Kilo",
    "gemini": "Gemini",
    "nvidia": "NVIDIA",
    "ollama": "Ollama",
}

# Sensible default order. Entries are used only when the provider is keyed;
# a ``None`` model means "the model already configured for that provider".
DEFAULT_FAILOVER_ORDER: tuple[dict[str, Any], ...] = (
    {"provider": "openrouter", "model": None},
    {"provider": "openai", "model": None},
    {"provider": "kilocode", "model": None},
    {"provider": "nvidia", "model": None},
    {"provider": "groq", "model": None},
    {"provider": "gemini", "model": None},
    {"provider": "anthropic", "model": None},
)

FAILOVER_FILE_ENV = "DECISIONS_PROVIDER_FAILOVER_FILE"
NOTICE_COOLDOWN_SECONDS = 30 * 60

# Settings pairs (provider column, model column) that describe models the user
# has already chosen. They are the source of "already configured" models.
_CONFIGURED_MODEL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("conversational_llm_provider", "conversational_llm_model"),
    ("llm_provider", "llm_model"),
    ("coding_llm_provider", "coding_llm_model"),
    ("automation_cli_model_provider", "automation_cli_model"),
    ("project_cli_medium_model_provider", "project_cli_medium_model"),
    ("project_cli_low_model_provider", "project_cli_low_model"),
    ("project_cli_high_model_provider", "project_cli_high_model"),
    ("subagent_llm_provider", "subagent_llm_model"),
    ("step_runner_llm_provider", "step_runner_llm_model"),
)

_NON_CHAT_MODEL = re.compile(
    r"(:batch\b|[-/:]batch$|\bbatch\b|embed|embedding|rerank|moderation|whisper|"
    r"transcri|\btts\b|-tts|text-to-speech|speech|dall-e|image-gen|imagen|"
    r"stable-diffusion|flux|sora|veo|realtime-preview-audio)",
    re.IGNORECASE,
)


def normalize_provider(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    aliases = {
        "kilo": "kilocode",
        "kilo code": "kilocode",
        "google": "gemini",
        "google gemini": "gemini",
        "claude": "anthropic",
        "open router": "openrouter",
    }
    return aliases.get(value, value)


def is_chat_capable_model(model: str | None) -> bool:
    """Batch-only, embedding, speech and image models can never serve a chat turn."""
    value = str(model or "").strip()
    if not value or value.lower() == "auto":
        return False
    return not _NON_CHAT_MODEL.search(value)


# ---------------------------------------------------------------- classify --

_CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("unsupported_model", (
        "cannot be used with the chat/completions endpoint",
        "batchadapter",
        "not support chat",
        "does not support chat",
        "model is not supported",
        "model is unsupported",
        "not a chat model",
    )),
    ("credits", (
        "status code: 402",
        "status code 402",
        "error code: 402",
        "402:",
        "(402)",
        "payment required",
        "add usage credits",
        "add credits",
        "insufficient credit",
        "insufficient_quota",
        "credit balance",
        "out of credits",
        "exceeded your current quota",
    )),
    ("rate_limit", (
        "429",
        "rate limit",
        "rate_limit",
        "rate-limited",
        "too many requests",
        "request limit reached",
    )),
    ("quota", (
        "quota",
        "resource exhausted",
        "resourceexhausted",
        "usage limit",
    )),
    ("dns", (
        "no such host",
        "name or service not known",
        "nodename nor servname",
        "getaddrinfo",
        "name resolution",
        "temporary failure in name resolution",
        "could not resolve host",
        "dns",
    )),
    ("connection", (
        "connection refused",
        "connection reset",
        "connection error",
        "connection aborted",
        "remote end closed",
        "remotedisconnected",
        "connecterror",
        "apiconnectionerror",
        "failed to establish a new connection",
        "max retries exceeded",
        "network is unreachable",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "502",
        "503",
        "504",
        "timed out",
        "timeout",
    )),
)

FAILOVER_CATEGORIES = frozenset({"unsupported_model", "credits", "rate_limit", "quota", "dns", "connection"})

_NON_ROUTE_MARKERS = (
    "cancelled by the user",
    "stopped by the user",
    "terminated by user",
    "user stopped",
    "inspection budget exceeded",
    "required verification",
)

CATEGORY_TEXT = {
    "credits": "is out of credits",
    "quota": "has used up its quota",
    "rate_limit": "is rate limiting requests (429)",
    "dns": "cannot be reached (its address does not resolve)",
    "connection": "is not responding",
    "unsupported_model": "cannot run this model for chat",
}


def classify_provider_error(error: BaseException | str | None) -> str | None:
    """Return the failover category for ``error`` or ``None`` for task failures."""
    if error is None:
        return None
    status = getattr(error, "status_code", None)
    text = str(error or "")
    lowered = text.lower()
    if not lowered.strip():
        return None
    if any(marker in lowered for marker in _NON_ROUTE_MARKERS):
        return None
    if status == 402:
        return "credits"
    if status == 429:
        return "rate_limit"
    if isinstance(status, int) and status in {502, 503, 504}:
        return "connection"
    for category, patterns in _CATEGORY_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return category
    return None


def is_failover_error(error: BaseException | str | None) -> bool:
    return classify_provider_error(error) in FAILOVER_CATEGORIES


# --------------------------------------------------------------- candidates --

@dataclass(frozen=True)
class FailoverTarget:
    provider: str
    model: str
    reason: str = ""

    @property
    def label(self) -> str:
        return f"{provider_label(self.provider)} · {self.model}"


def provider_label(provider: str | None) -> str:
    norm = normalize_provider(provider)
    return PROVIDER_LABELS.get(norm, str(provider or "the model provider"))


def _failover_file() -> Path:
    override = os.environ.get(FAILOVER_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return Path("~/.decisions/settings/provider_failover.json").expanduser()


def load_failover_order(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """User order (settings key or JSON file) else ``DEFAULT_FAILOVER_ORDER``."""
    raw: Any = None
    if settings and settings.get("provider_failover_order"):
        raw = settings.get("provider_failover_order")
    else:
        path = _failover_file()
        try:
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Provider failover file is unreadable; using defaults", exc_info=True)
            raw = None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    if isinstance(raw, dict):
        if raw.get("enabled") is False:
            return []
        raw = raw.get("order")
    order: list[dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, dict) and item.get("provider"):
            order.append({"provider": normalize_provider(item.get("provider")), "model": item.get("model")})
        elif isinstance(item, str) and item.strip():
            order.append({"provider": normalize_provider(item), "model": None})
    return order or [dict(item) for item in DEFAULT_FAILOVER_ORDER]


def provider_is_keyed(provider: str, settings: dict[str, Any] | None) -> bool:
    norm = normalize_provider(provider)
    if norm not in PROVIDER_KEYS:
        return False
    column = PROVIDER_KEYS[norm]
    if column is None:
        return True
    value = str((settings or {}).get(column) or "").strip()
    if value:
        return True
    env_name = f"{norm.upper()}_API_KEY"
    return bool(str(os.environ.get(env_name) or "").strip())


def configured_models(settings: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(provider, model) pairs the user already picked somewhere in Settings."""
    pairs: list[tuple[str, str]] = []
    for provider_col, model_col in _CONFIGURED_MODEL_COLUMNS:
        provider = normalize_provider((settings or {}).get(provider_col))
        model = str((settings or {}).get(model_col) or "").strip()
        if provider and model and (provider, model) not in pairs:
            pairs.append((provider, model))
    return pairs


def _same_route(provider_a: str, model_a: str, provider_b: str, model_b: str) -> bool:
    return normalize_provider(provider_a) == normalize_provider(provider_b) and (
        str(model_a or "").strip().lower() == str(model_b or "").strip().lower()
    )


def failover_candidates(
    failed_provider: str,
    failed_model: str,
    settings: dict[str, Any] | None,
    *,
    tried: Iterable[tuple[str, str]] = (),
    model_only: bool = False,
) -> list[FailoverTarget]:
    """Ordered list of keyed, chat-capable routes other than the failed one.

    ``model_only`` is for a model that cannot chat (e.g. a ``:batch`` id) on a
    healthy provider: the same provider may serve a different model.
    """
    failed_norm = normalize_provider(failed_provider)
    tried_set = {(normalize_provider(p), str(m or "").strip().lower()) for p, m in tried}
    tried_set.add((failed_norm, str(failed_model or "").strip().lower()))
    configured = configured_models(settings)
    out: list[FailoverTarget] = []

    def add(provider: str, model: str, reason: str) -> None:
        norm = normalize_provider(provider)
        key = (norm, str(model or "").strip().lower())
        if not model or key in tried_set:
            return
        if norm == failed_norm and not model_only:
            return  # the provider itself is down: never "fail over" to it
        if norm == "ollama" and str(model).endswith(":cloud"):
            return  # Ollama cloud shares the failed account/credits path
        if not is_chat_capable_model(model) or not provider_is_keyed(norm, settings):
            return
        if any(_same_route(t.provider, t.model, norm, model) for t in out):
            return
        out.append(FailoverTarget(provider=norm, model=str(model).strip(), reason=reason))

    for item in load_failover_order(settings):
        provider = normalize_provider(item.get("provider"))
        model = str(item.get("model") or "").strip()
        if model:
            add(provider, model, "failover order")
            continue
        for cfg_provider, cfg_model in configured:
            if cfg_provider == provider:
                add(cfg_provider, cfg_model, "configured model")
    return out


def next_failover(
    failed_provider: str,
    failed_model: str,
    settings: dict[str, Any] | None = None,
    *,
    tried: Iterable[tuple[str, str]] = (),
    error: BaseException | str | None = None,
) -> FailoverTarget | None:
    if settings is None:
        settings = _load_settings()
    model_only = classify_provider_error(error) == "unsupported_model" or (
        bool(failed_model) and not is_chat_capable_model(failed_model)
    )
    candidates = failover_candidates(failed_provider, failed_model, settings, tried=tried, model_only=model_only)
    return candidates[0] if candidates else None


def _load_settings() -> dict[str, Any]:
    try:
        from distr.core.settings import load_settings_from_db

        return dict(load_settings_from_db() or {})
    except Exception:
        logger.debug("Could not load settings for provider failover", exc_info=True)
        return {}


# ------------------------------------------------------------------ notices --

_notice_lock = threading.Lock()
_last_notice: dict[tuple[str, str], float] = {}


def provider_down_notice(
    provider: str,
    model: str,
    error: BaseException | str | None,
    target: FailoverTarget | None,
) -> str:
    """One plain sentence: what is down, and what happens next."""
    category = classify_provider_error(error) or "connection"
    what = CATEGORY_TEXT.get(category, "is unavailable")
    if category == "credits" and "402" in str(error or ""):
        what += " (402)"
    source = f"{provider_label(provider)} ({model})" if model else provider_label(provider)
    if target is not None:
        return f"{source} {what}, so I switched to {target.label} and carried on."
    return (
        f"{source} {what}, and no other configured provider with a key is available. "
        "Add credits or pick another model in Settings → LLMs."
    )


def should_emit_notice(provider: str, category: str | None, *, now: float | None = None) -> bool:
    """Emit the provider-down notice once per provider/category per 30 minutes."""
    key = (normalize_provider(provider), str(category or ""))
    current = time.time() if now is None else now
    with _notice_lock:
        last = _last_notice.get(key)
        if last is not None and current - last < NOTICE_COOLDOWN_SECONDS:
            return False
        _last_notice[key] = current
        return True


def reset_notice_state() -> None:
    with _notice_lock:
        _last_notice.clear()


def log_failover(route: str, provider: str, model: str, error: Any, target: FailoverTarget | None) -> None:
    logger.warning(
        "Provider failover [%s]: %s/%s failed (%s) -> %s",
        route,
        provider,
        model,
        classify_provider_error(error),
        target.label if target else "no keyed fallback",
    )
