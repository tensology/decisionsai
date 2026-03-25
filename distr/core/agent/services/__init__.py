"""
Pipecat Services Package
"""
import logging

logger = logging.getLogger(__name__)

# STT services
from .stt.whisper import WhisperSTTService

try:
    from .stt.vosk import VoskSTTService
except ImportError:
    VoskSTTService = None

try:
    from .stt.openai import OpenAIWhisperSTTService
except ImportError:
    OpenAIWhisperSTTService = None

try:
    from .stt.assemblyai import AssemblyAISTTService
except ImportError as e:
    logger.warning(f"Error importing AssemblyAISTTService: {e}")
    AssemblyAISTTService = None

# TTS services
from .tts.kokoro import KokoroTTSService
from .tts.elevenlabs import ElevenLabsTTSService

try:
    from .tts.openai import OpenAITTSService
except ImportError:
    OpenAITTSService = None

try:
    from .tts.qwen3 import Qwen3TTSService
except ImportError:
    Qwen3TTSService = None

try:
    from .tts.coqui import CoquiTTSService
except ImportError:
    CoquiTTSService = None

# LLM services
from .llm.providers.ollama import OllamaLLMService

try:
    from .llm.providers.openai import OpenAILLMService
except ImportError as e:
    logger.warning(f"Error importing OpenAILLMService: {e}")
    OpenAILLMService = None

try:
    from .llm.providers.openrouter import OpenRouterLLMService
except ImportError as e:
    logger.warning(f"Error importing OpenRouterLLMService: {e}")
    OpenRouterLLMService = None

try:
    from .llm.providers.anthropic import AnthropicLLMService
except ImportError as e:
    logger.warning(f"Error importing AnthropicLLMService: {e}")
    AnthropicLLMService = None

try:
    from .llm.providers.groq import GroqLLMService
except ImportError as e:
    logger.warning(f"Error importing GroqLLMService: {e}")
    GroqLLMService = None

try:
    from .llm.providers.kilocode import KiloCodeLLMService
except ImportError as e:
    logger.warning(f"Error importing KiloCodeLLMService: {e}")
    KiloCodeLLMService = None

__all__ = ["WhisperSTTService", "OllamaLLMService", "KokoroTTSService", "ElevenLabsTTSService"]
if OpenAITTSService:
    __all__.append("OpenAITTSService")
if Qwen3TTSService:
    __all__.append("Qwen3TTSService")
if CoquiTTSService:
    __all__.append("CoquiTTSService")
if OpenAILLMService:
    __all__.append("OpenAILLMService")
if OpenRouterLLMService:
    __all__.append("OpenRouterLLMService")
if AnthropicLLMService:
    __all__.append("AnthropicLLMService")
if GroqLLMService:
    __all__.append("GroqLLMService")
if KiloCodeLLMService:
    __all__.append("KiloCodeLLMService")
if VoskSTTService:
    __all__.append("VoskSTTService")
if OpenAIWhisperSTTService:
    __all__.append("OpenAIWhisperSTTService")
if AssemblyAISTTService:
    __all__.append("AssemblyAISTTService")
