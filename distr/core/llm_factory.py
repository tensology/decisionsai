"""
llm_factory - Single source of truth for multi-provider LLM streaming.

Eliminates duplicate streaming code across chat.py, web routes, etc.
"""

import logging
from typing import Dict, Any, Generator, List, Optional, Tuple

from distr.core.llm_errors import LLMModelError

logger = logging.getLogger(__name__)

_PROVIDER_NORMALIZE = {
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "openrouter": "OpenRouter",
    "kilocode": "KiloCode",
    "gemini": "Google Gemini",
    "google gemini": "Google Gemini",
    "nvidia": "NVIDIA",
}


def normalize_provider(provider: Optional[str]) -> str:
    """Canonical PascalCase provider name. Single source of truth."""
    if not provider or not str(provider).strip():
        return "Ollama"
    key = str(provider).strip().lower()
    return _PROVIDER_NORMALIZE.get(key, provider.strip())


def is_openai_model(model_name: str) -> bool:
    """Check if a model name belongs to OpenAI (gpt-*, o1*, o3*, o4*, chatgpt-*)."""
    if not model_name:
        return False
    m = model_name.lower()
    return m.startswith(('gpt-', 'o1', 'o3', 'o4', 'chatgpt-'))


def is_gemini_model(model_name: str) -> bool:
    """Check if a model name belongs to Google Gemini (gemini-*)."""
    if not model_name:
        return False
    return model_name.lower().startswith('gemini-')


def is_anthropic_model(model_name: str) -> bool:
    """Check if a model name belongs to Anthropic (claude-*)."""
    if not model_name:
        return False
    return model_name.lower().startswith('claude-')


def infer_provider_from_model(provider: str, model_name: str) -> str:
    """Correct provider when the model name clearly belongs to a different provider.

    E.g. provider='Ollama' + model='gpt-4o' → 'OpenAI'.
    Returns the (possibly corrected) canonical provider name.
    """
    if not model_name:
        return provider
    m = model_name.lower()
    _is_openai = is_openai_model(model_name)
    _is_anthropic = is_anthropic_model(model_name)
    _is_gemini = is_gemini_model(model_name)
    is_groq = m.startswith('llama-') and 'groq' in provider.lower()
    norm = normalize_provider(provider)
    if norm == 'Ollama':
        if _is_openai:
            return 'OpenAI'
        if _is_anthropic:
            return 'Anthropic'
        if _is_gemini:
            return 'Google Gemini'
    return norm


def resolve_settings_keys(settings: Dict[str, Any]) -> Tuple[str, str]:
    """Determine (provider, model) from a settings dict with fallback chain.

    If an LLM override context is active with non-empty orchestrator settings,
    those take precedence over global settings.

    Fallback order:
      provider: workflow_llm_provider -> conversational_llm_provider -> agent_provider -> llm_provider -> "Ollama"
      model:    workflow_llm_model   -> conversational_llm_model   -> agent_model   -> llm_model   -> ""
    """
    from distr.core.llm_override import get_llm_override

    # Check for board-level LLM override (orchestrator role)
    override = get_llm_override()
    if override and override.orchestrator_provider:
        return normalize_provider(override.orchestrator_provider), (override.orchestrator_model or "")

    # Workflow dedicated LLM
    workflow_provider = (
        (settings.get("workflow_llm_provider") or "").strip()
    )
    workflow_model = (
        (settings.get("workflow_llm_model") or "").strip()
    )
    if workflow_provider:
        return normalize_provider(workflow_provider), workflow_model

    provider = (
        (settings.get("conversational_llm_provider") or "").strip()
        or (settings.get("agent_provider") or "").strip()
        or (settings.get("llm_provider") or "").strip()
        or "Ollama"
    )
    model = (
        (settings.get("conversational_llm_model") or "").strip()
        or (settings.get("agent_model") or "").strip()
        or (settings.get("llm_model") or "").strip()
        or ""
    )
    return normalize_provider(provider), model


def resolve_llm_candidates(settings: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return provider/model pairs for retryable lightweight LLM calls.

    Matches WorkflowAgent resolution: workflow LLM when configured, otherwise
    conversational (Settings → Workflow → "Inherit from Conversational"), then
    legacy agent keys, then a local Ollama fallback.
    """
    from distr.core.llm_override import get_llm_override

    candidates: List[Tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(provider: Any, model: Any) -> None:
        raw_provider = str(provider or "").strip()
        if not raw_provider:
            return
        raw_model = str(model or "").strip()
        normalized = normalize_provider(raw_provider).lower()
        key = (normalized, raw_model.lower())
        if key in seen:
            return
        seen.add(key)
        candidates.append((normalized, raw_model))

    override = get_llm_override()
    if override and override.orchestrator_provider:
        add(override.orchestrator_provider, override.orchestrator_model or "")

    workflow_provider = (settings.get("workflow_llm_provider") or "").strip()
    if workflow_provider:
        add(workflow_provider, settings.get("workflow_llm_model") or "")

    conv_provider = (
        (settings.get("conversational_llm_provider") or "").strip()
        or (settings.get("agent_provider") or "").strip()
    )
    conv_model = (
        (settings.get("conversational_llm_model") or "").strip()
        or (settings.get("agent_model") or "").strip()
    )
    if conv_provider:
        add(conv_provider, conv_model)

    agent_provider = (settings.get("agent_provider") or "").strip()
    agent_model = (settings.get("agent_model") or "").strip()
    if agent_provider:
        add(agent_provider, agent_model)

    add("ollama", "llama3.2")
    return candidates


def resolve_computer_use_config(settings: Dict[str, Any]) -> Tuple[str, str]:
    """Resolve computer use provider/model. Returns ('', '') if not configured."""
    provider = (settings.get("computer_use_provider") or "").strip()
    model = (settings.get("computer_use_model") or "").strip()
    if provider:
        return normalize_provider(provider), model
    return "", ""


def create_stream(
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    settings: Dict[str, Any],
) -> Generator[str, None, None]:
    """Create a token-yielding generator for any supported LLM provider.

    DEPRECATED: No longer used by chat web routes (removed /stream endpoint).
    All web messages now go through the agent pipeline via send-to-agent.
    Kept for potential future use or other callers.

    Args:
        provider: PascalCase or lowercase provider name
        model: Model identifier
        messages: Chat messages [{role, content}, ...]
        settings: Full settings dict (for API keys, URLs, etc.)

    Yields:
        str: Individual tokens from the LLM response

    Raises:
        ValueError: If API key is missing or provider is unknown
    """
    prov = (provider or "ollama").strip().lower()

    try:
        if prov == "ollama":
            yield from _stream_ollama(model, messages, settings)
        elif prov == "openai":
            yield from _stream_openai_compat(
                model, messages, settings,
                key_name="openai_key",
                base_url=None,
            )
        elif prov == "anthropic":
            yield from _stream_anthropic(model, messages, settings)
        elif prov == "groq":
            yield from _stream_openai_compat(
                model, messages, settings,
                key_name="groq_key",
                base_url="https://api.groq.com/openai/v1",
            )
        elif prov == "openrouter":
            yield from _stream_openai_compat(
                model, messages, settings,
                key_name="openrouter_key",
                base_url="https://openrouter.ai/api/v1",
            )
        elif prov == "kilocode":
            yield from _stream_openai_compat(
                model, messages, settings,
                key_name="kilo_key",
                base_url="https://api.kilo.ai/api/gateway",
            )
        elif prov == "gemini" or prov == "google gemini":
            yield from _stream_openai_compat(
                model, messages, settings,
                key_name="gemini_key",
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
        elif prov == "nvidia":
            yield from _stream_openai_compat(
                model, messages, settings,
                key_name="nvidia_key",
                base_url="https://integrate.api.nvidia.com/v1",
            )
        else:
            raise ValueError(f"Unknown or unsupported LLM provider: {provider}")
    except LLMModelError:
        raise
    except Exception as exc:
        raise LLMModelError(
            exc,
            provider=normalize_provider(provider),
            model=model,
            operation="generate a streamed response",
        ) from exc


# ---- private streaming helpers ----


def _stream_ollama(
    model: str, messages: List[Dict[str, str]], settings: Dict[str, Any]
) -> Generator[str, None, None]:
    from ollama import Client

    ollama_url = settings.get("ollama_url", "http://localhost:11434/")
    client = Client(host=ollama_url)
    stream = client.chat(model=model, messages=messages, stream=True)
    for chunk in stream:
        if chunk and "message" in chunk:
            token = chunk["message"].get("content", "")
            if token:
                yield token


def _stream_openai_compat(
    model: str,
    messages: List[Dict[str, str]],
    settings: Dict[str, Any],
    key_name: str,
    base_url: Optional[str],
) -> Generator[str, None, None]:
    from openai import OpenAI

    api_key = (settings.get(key_name) or "").strip()
    if not api_key:
        label = key_name.replace("_key", "").replace("_", " ").title()
        raise ValueError(f"{label} API key not configured")
    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    resp = client.chat.completions.create(model=model, messages=messages, stream=True)
    for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def _stream_anthropic(
    model: str, messages: List[Dict[str, str]], settings: Dict[str, Any]
) -> Generator[str, None, None]:
    from anthropic import Anthropic

    api_key = (settings.get("anthropic_key") or "").strip()
    if not api_key:
        raise ValueError("Anthropic API key not configured")
    client = Anthropic(api_key=api_key)
    system = None
    chat_messages = []
    for m in messages:
        if m.get("role") == "system":
            system = m.get("content", "")
        else:
            chat_messages.append({"role": m["role"], "content": m.get("content", "") or ""})
    stream = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system or "",
        messages=chat_messages,
        stream=True,
    )
    for event in stream:
        if hasattr(event, "delta") and event.delta and getattr(event.delta, "text", None):
            yield event.delta.text
