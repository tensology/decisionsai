"""TTS service implementations."""
from .openai import OpenAITTSService
from .kokoro import KokoroTTSService
from .elevenlabs import ElevenLabsTTSService

try:
    from .supertonic import SupertonicTTSService
except ImportError:
    SupertonicTTSService = None

try:
    from .chatterbox import ChatterboxTTSService
except ImportError:
    ChatterboxTTSService = None
