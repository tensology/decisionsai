"""LLM service mixins."""

from .fast_actions import FastActionMixin
from .telegram import TelegramMixin
from .voice import VoiceDictationMixin
from .ollama_response import OllamaResponseMixin

__all__ = [
    "FastActionMixin",
    "TelegramMixin",
    "VoiceDictationMixin",
    "OllamaResponseMixin",
]
