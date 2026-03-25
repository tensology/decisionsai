"""Backward-compatible re-exports from split modules.

New code should import from:
  - base_service.py   → BaseLLMService
  - openai_compat.py  → OpenAICompatibleLLMService
"""

from distr.core.agent.services.llm.base_service import BaseLLMService
from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService

__all__ = ["BaseLLMService", "OpenAICompatibleLLMService"]
