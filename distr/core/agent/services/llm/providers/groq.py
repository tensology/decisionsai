"""
Groq LLM Service for Pipecat

Groq provides ultra-fast inference for LLM models through their API.
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
    logger.warning("OpenAI library not available - Groq service requires it")


class GroqLLMService(OpenAICompatibleLLMService):
    """Groq-based LLM service using Pipecat (OpenAI-compatible API)"""

    SERVICE_NAME = "GroqLLMService"
    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, api_key: str, model_name: str = None, **kwargs):
        if not OPENAI_AVAILABLE:
            raise ImportError("openai library is required for GroqLLMService")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
        )
        super().__init__(api_key=api_key, model_name=model_name or self.DEFAULT_MODEL, **kwargs)

    def _get_provider_name(self) -> str:
        return "Groq"
