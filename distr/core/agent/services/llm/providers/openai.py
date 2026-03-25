"""
OpenAI LLM Service

Thin provider layer on top of OpenAICompatibleLLMService.

Only contains:
- __init__ (OpenAI client setup)
- _validate_messages_for_openai (OpenAI-specific message validation)
- _format_vision_message (OpenAI vision format)
- _generate_welcome_summary (OpenAI-specific API call with max_completion_tokens fallback)
"""

import logging
import os
from typing import Optional

from ..openai_compat import OpenAICompatibleLLMService

logger = logging.getLogger(__name__)

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    AsyncOpenAI = None
    OPENAI_AVAILABLE = False
    logger.warning("OPENAI not available")


class OpenAILLMService(OpenAICompatibleLLMService):
    """OpenAI-based LLM service.

    Inherits full streaming, tool execution, Telegram, error handling, and
    cleanup from OpenAICompatibleLLMService.  Only overrides client init
    and the OpenAI-specific welcome summary (max_completion_tokens fallback).
    """

    SERVICE_NAME = "OpenAILLMService"
    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, api_key: str, model_name: str = "gpt-4o", system_prompt: str = None,
                 event_queue=None, is_listening=True, chat_manager=None, tts_service=None,
                 agent_name: str = "Heart", command_queue=None, confirmation_results_dict=None, **kwargs):
        if not OPENAI_AVAILABLE:
            raise ImportError("openai library is required for OpenAILLMService")

        # Set up the OpenAI client BEFORE calling super().__init__()
        # (BaseLLMService.__init__ expects self.client to exist)
        self.client = AsyncOpenAI(api_key=api_key)

        super().__init__(
            api_key=api_key, model_name=model_name, system_prompt=system_prompt,
            event_queue=event_queue, is_listening=is_listening,
            chat_manager=chat_manager, tts_service=tts_service,
            agent_name=agent_name, command_queue=command_queue,
            confirmation_results_dict=confirmation_results_dict, **kwargs,
        )

    # ------------------------------------------------------------------
    #  Provider-specific overrides
    # ------------------------------------------------------------------

    def _format_vision_message(self, text: str, base64_image: str, mime_type: str):
        """OpenAI vision format."""
        return [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}},
        ]
