"""STT service implementations — lazy exports so importing one backend does not load all."""

from __future__ import annotations

__all__ = (
    "AssemblyAISTTService",
    "OpenAIWhisperSTTService",
    "VibeVoiceAsrSTTService",
    "VoskSTTService",
    "WhisperSTTService",
)


def __getattr__(name: str):
    if name == "WhisperSTTService":
        from .whisper import WhisperSTTService as _WhisperSTTService

        return _WhisperSTTService
    if name == "VoskSTTService":
        from .vosk import VoskSTTService as _VoskSTTService

        return _VoskSTTService
    if name == "OpenAIWhisperSTTService":
        from .openai import OpenAIWhisperSTTService as _OpenAIWhisperSTTService

        return _OpenAIWhisperSTTService
    if name == "AssemblyAISTTService":
        from .assemblyai import AssemblyAISTTService as _AssemblyAISTTService

        return _AssemblyAISTTService
    if name == "VibeVoiceAsrSTTService":
        from .vibevoice_asr import VibeVoiceAsrSTTService as _VibeVoiceAsrSTTService

        return _VibeVoiceAsrSTTService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
