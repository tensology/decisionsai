"""
OpenRouter LLM Service for Pipecat

OpenRouter provides access to multiple LLM providers through a unified API.
Uses OpenAI-compatible API format.
"""

import logging
from typing import Optional

from ..openai_compat import OpenAICompatibleLLMService

logger = logging.getLogger(__name__)

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    AsyncOpenAI = None
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI library not available - OpenRouter service requires it")


class OpenRouterLLMService(OpenAICompatibleLLMService):
    """OpenRouter-based LLM service using Pipecat (OpenAI-compatible API)"""

    SERVICE_NAME = "OpenRouterLLMService"
    DEFAULT_MODEL = "openai/gpt-4o"

    def __init__(self, api_key: str, model_name: str = None, **kwargs):
        if not OPENAI_AVAILABLE:
            raise ImportError("openai library is required for OpenRouterLLMService")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1"
        )
        super().__init__(api_key=api_key, model_name=model_name or self.DEFAULT_MODEL, **kwargs)

    def _get_provider_name(self) -> str:
        return "OpenRouter"
