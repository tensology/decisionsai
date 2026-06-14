"""
Google Gemini LLM Service for Pipecat

Google Gemini provides access to Gemini models through an OpenAI-compatible API.
Uses the OpenAI-compatible endpoint at generativelanguage.googleapis.com.
"""

import logging

from ..openai_compat import OpenAICompatibleLLMService

logger = logging.getLogger(__name__)

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    AsyncOpenAI = None
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI library not available - Gemini service requires it")


class GeminiLLMService(OpenAICompatibleLLMService):
    """Google Gemini LLM service using OpenAI-compatible API"""

    SERVICE_NAME = "GeminiLLMService"
    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(self, api_key: str, model_name: str = None, **kwargs):
        if not OPENAI_AVAILABLE:
            raise ImportError("openai library is required for GeminiLLMService")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        super().__init__(api_key=api_key, model_name=model_name or self.DEFAULT_MODEL, **kwargs)

    def _get_provider_name(self) -> str:
        return "Google Gemini"
