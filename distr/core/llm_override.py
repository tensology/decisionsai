"""
LLM Override Context — per-run LLM provider/model overrides via contextvars.

Board-level LLM settings are propagated using Python's ``contextvars`` module.
This provides thread-safe, async-safe scoping without global state mutation.
"""
import contextvars
from typing import Optional
from dataclasses import dataclass


@dataclass
class LLMOverride:
    """Board-specific LLM provider/model overrides for orchestrator, coder, and sub-agent."""
    orchestrator_provider: str = ""
    orchestrator_model: str = ""
    coder_provider: str = ""
    coder_model: str = ""
    sub_provider: str = ""
    sub_model: str = ""


_llm_override_var: contextvars.ContextVar[Optional[LLMOverride]] = contextvars.ContextVar(
    'llm_override', default=None
)


def set_llm_override(override: LLMOverride) -> contextvars.Token:
    """Set the LLM override for the current context. Returns a token for later reset."""
    return _llm_override_var.set(override)


def get_llm_override() -> Optional[LLMOverride]:
    """Get the current LLM override, or None if not set."""
    return _llm_override_var.get()


def clear_llm_override(token: contextvars.Token) -> None:
    """Clear the LLM override by resetting to the previous value via token."""
    _llm_override_var.reset(token)
