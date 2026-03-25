"""
KiloCode LLM Service for Pipecat

KiloCode provides API access to various LLM models.
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
    logger.warning("OpenAI library not available - KiloCode service requires it")


class KiloCodeLLMService(OpenAICompatibleLLMService):
    """KiloCode-based LLM service using Pipecat (OpenAI-compatible API)"""

    SERVICE_NAME = "KiloCodeLLMService"
    DEFAULT_MODEL = "anthropic/claude-opus-4.5"

    def __init__(self, api_key: str, model_name: str = None, **kwargs):
        if not OPENAI_AVAILABLE:
            raise ImportError("openai library is required for KiloCodeLLMService")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.kilo.ai/api/gateway"
        )
        super().__init__(api_key=api_key, model_name=model_name or self.DEFAULT_MODEL, **kwargs)

    def _get_provider_name(self) -> str:
        return "KiloCode"
