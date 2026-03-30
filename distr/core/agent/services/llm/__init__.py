"""LLM service implementations.

Structure:
  providers/     — Provider-specific LLM services (Ollama, OpenAI, Anthropic, etc.)
  mixins/        — Reusable mixins (voice, telegram, fast actions, ollama response)
  base_service   — BaseLLMService (common init, tool loading)
  openai_compat  — OpenAICompatibleLLMService (streaming, tool chaining, error handling)
  core_mixin     — LLMSharedMixin (process_frame, process_chat_input, system prompt)
  prompt         — System prompt loading and tool description building
  text_utils     — TTS text cleaning, normalize_text, parse_tool_calls_from_content
  tool_routing   — detect_request_type, filter_tools_by_context, clipboard helpers
  tool_router    — Semantic ToolRouter (embedding-based tool selection)
  tool_format    — convert_tools_to_openai_format
  fast_action_detector — Regex-based fast action detection
  image_utils    — Vision/image helpers
"""

from .base_service import BaseLLMService
from .openai_compat import OpenAICompatibleLLMService
from .providers.ollama import OllamaLLMService
from .providers.openai import OpenAILLMService
from .providers.anthropic import AnthropicLLMService
from .providers.groq import GroqLLMService
from .providers.openrouter import OpenRouterLLMService
from .providers.kilocode import KiloCodeLLMService
from .providers.gemini import GeminiLLMService
from .core_mixin import LLMSharedMixin
