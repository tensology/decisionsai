"""User-facing formatting for model/provider failures."""

from __future__ import annotations

import re


_SECRET_RE = re.compile(r"\b(?:sk|sk-proj|or|gsk|AIza)[A-Za-z0-9_\-]{12,}\b")


def redact_model_error_text(text: str) -> str:
    """Remove obvious API-key shaped strings before showing provider errors."""
    return _SECRET_RE.sub("[redacted-key]", text or "")


def extract_provider_error_message(exc: BaseException | str) -> str:
    """Pull the useful provider message out of SDK exceptions.

    OpenAI-compatible SDK exceptions often stringify as:
    "Error code: 404 - {'error': {'message': '...'}}".
    This keeps the part a human can act on.
    """
    raw = redact_model_error_text(str(exc or "")).strip()
    if not raw:
        return "Unknown model error"

    for pattern in (
        r"'message'\s*:\s*'([^']+)'",
        r'"message"\s*:\s*"([^"]+)"',
        r"message=([^,}]+)",
    ):
        match = re.search(pattern, raw)
        if match and match.group(1).strip():
            return match.group(1).strip()

    return raw


def classify_model_error(exc: BaseException | str) -> str:
    text = str(exc or "").lower()
    status = getattr(exc, "status_code", None)
    if status == 401 or "401" in text or "unauthorized" in text or "invalid api key" in text:
        return "authentication"
    if status == 402 or "insufficient_quota" in text or "exceeded your current quota" in text or "billing" in text:
        return "quota"
    if status == 404 or "not found" in text or "no endpoints found" in text or "no longer available" in text:
        return "model_unavailable"
    if status == 429 or "429" in text or "rate_limit" in text or "rate-limited" in text:
        return "rate_limit"
    if "unsupported" in text or "does not support" in text or "not support" in text:
        return "unsupported_model"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "connection" in text or "connect" in text:
        return "connection"
    return "model_error"


def format_model_error(
    exc: BaseException | str,
    *,
    provider: str = "",
    model: str = "",
    operation: str = "generate a response",
) -> str:
    """Return a concise message suitable for chat, TTS, modals, and workflows."""
    message = extract_provider_error_message(exc)
    kind = classify_model_error(exc)
    provider_text = provider or "configured provider"
    model_text = model or "configured model"

    if kind == "authentication":
        lead = "Model authentication failed"
        fix = "Check the API key in Settings -> LLMs / Third Party."
    elif kind == "quota":
        lead = "Model quota or billing failed"
        fix = "Check billing/tier limits or switch provider/model in Settings -> LLMs."
    elif kind == "rate_limit":
        lead = "Model rate limit hit"
        fix = "Wait and retry, or switch provider/model in Settings -> LLMs."
    elif kind == "model_unavailable":
        lead = "Model is unavailable or not supported"
        fix = "Choose a supported model in Settings -> LLMs."
    elif kind == "unsupported_model":
        lead = "Model does not support this request"
        fix = "Choose a different model for this task in Settings -> LLMs."
    elif kind == "timeout":
        lead = "Model request timed out"
        fix = "Retry, or switch to a faster/available provider in Settings -> LLMs."
    elif kind == "connection":
        lead = "Model provider connection failed"
        fix = "Check the provider service/network or switch provider in Settings -> LLMs."
    else:
        lead = "Model request failed"
        fix = "Check the model/provider settings or switch model in Settings -> LLMs."

    return (
        f"{lead} while trying to {operation}. "
        f"Provider: {provider_text}. Model: {model_text}. "
        f"Error: {message}. {fix}"
    )


_TTS_ERROR_MESSAGES = {
    "authentication": "That model API key is not working. Check settings.",
    "quota": "That model is out of quota. Check billing or switch models in settings.",
    "rate_limit": "I hit a rate limit on that model. Wait a moment, or switch models in settings.",
    "model_unavailable": "That model is not available right now. Try another one in settings.",
    "unsupported_model": "That model does not support this request. Try a different model.",
    "timeout": "The model took too long to respond. Try again or switch models.",
    "connection": "I could not reach the model provider. Check the connection or switch provider.",
    "model_error": "Something went wrong with the model. Try again or switch models in settings.",
}


def format_model_error_for_tts(
    exc: BaseException | str,
    *,
    provider: str = "",
    model: str = "",
    operation: str = "generate a response",
) -> str:
    """Short spoken line for voice mode — not the full technical chat message."""
    kind = classify_model_error(exc)
    return _TTS_ERROR_MESSAGES.get(kind, _TTS_ERROR_MESSAGES["model_error"])


def is_formatted_model_error_message(text: str) -> bool:
    """True when text matches ``format_model_error`` chat output (not for TTS)."""
    t = (text or "").strip()
    if not t:
        return False
    return (
        "Provider:" in t
        and "Model:" in t
        and "Error:" in t
        and "Settings -> LLMs" in t
        and " while trying to " in t
    )


class LLMModelError(RuntimeError):
    """Exception wrapper that preserves provider/model context for callers."""

    def __init__(
        self,
        exc: BaseException | str,
        *,
        provider: str = "",
        model: str = "",
        operation: str = "generate a response",
    ):
        self.original = exc
        self.provider = provider
        self.model = model
        self.operation = operation
        super().__init__(format_model_error(exc, provider=provider, model=model, operation=operation))

