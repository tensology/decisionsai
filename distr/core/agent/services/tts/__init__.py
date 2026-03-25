"""TTS service implementations."""
from .openai import OpenAITTSService
from .kokoro import KokoroTTSService
from .elevenlabs import ElevenLabsTTSService
try:
    from .qwen3 import Qwen3TTSService
except ImportError:
    Qwen3TTSService = None
