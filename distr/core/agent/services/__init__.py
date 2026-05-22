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
    from .tts.coqui import CoquiTTSService
except ImportError:
    CoquiTTSService = None

try:
    from .tts.f5tts import F5TTSTTSService
except ImportError:
    F5TTSTTSService = None

try:
    from .tts.voxcpm import VoxCPMTTSService
except ImportError:
    VoxCPMTTSService = None

try:
    from .tts.supertonic import SupertonicTTSService
except ImportError:
    SupertonicTTSService = None

try:
    from .tts.chatterbox import ChatterboxTTSService
except ImportError:
    ChatterboxTTSService = None

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

try:
    from .llm.providers.gemini import GeminiLLMService
except ImportError as e:
    logger.warning(f"Error importing GeminiLLMService: {e}")
    GeminiLLMService = None

__all__ = ["WhisperSTTService", "OllamaLLMService", "KokoroTTSService", "ElevenLabsTTSService"]
if OpenAITTSService:
    __all__.append("OpenAITTSService")
if CoquiTTSService:
    __all__.append("CoquiTTSService")
if F5TTSTTSService:
    __all__.append("F5TTSTTSService")
if VoxCPMTTSService:
    __all__.append("VoxCPMTTSService")
if SupertonicTTSService:
    __all__.append("SupertonicTTSService")
if ChatterboxTTSService:
    __all__.append("ChatterboxTTSService")
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
