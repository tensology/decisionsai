"""Translate the terminal setup selections into the app's existing settings."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class InstallProfile:
    chat_provider: str
    chat_model: str
    stt_provider: str = ""
    stt_model: str = ""
    tts_provider: str = ""
    tts_model: str = ""
    voice: str = ""
    input_device: str = "System Default"
    output_device: str = "System Default"
    download_ollama: bool = False

    def settings(self, keys: Mapping[str, str]) -> dict[str, str | bool]:
        result: dict[str, str | bool] = {
            "input_device": self.input_device,
            "output_device": self.output_device,
            "play_output": self.output_device,
            "chat_voice_enabled": bool(self.tts_provider),
        }
        if self.chat_provider:
            for prefix in ("conversational_llm", "llm", "agent"):
                result[f"{prefix}_provider"] = self.chat_provider
                result[f"{prefix}_model"] = self.chat_model
        if self.stt_provider:
            transcription = {
                "whisper": "Whisper.cpp (Local & Offline)",
                "vosk": "Vosk (Offline)",
                "assemblyai": f"AssemblyAI ({self.stt_model})",
                "openai": f"OpenAI ({self.stt_model})",
            }[self.stt_provider]
            result.update(transcription_model=transcription, input_speech=transcription)
        else:
            result.update(transcription_model="Disabled", input_speech="Disabled")
        if self.tts_provider:
            from distr.core.agent.constants import TTS_ELEVENLABS, TTS_KOKORO, TTS_OPENAI

            display_name = {
                "openai": TTS_OPENAI,
                "elevenlabs": TTS_ELEVENLABS,
                "kokoro": TTS_KOKORO,
            }[self.tts_provider]
            result.update(
                tts_provider=display_name,
                voice_provider=self.tts_provider,
                tts_voice=self.voice,
            )
            if self.tts_provider in ("openai", "elevenlabs"):
                result[f"{self.tts_provider}_tts_model"] = self.tts_model
            result[f"{self.tts_provider}_voice"] = self.voice

        providers = {self.chat_provider, self.stt_provider, self.tts_provider}
        for vendor in ("openai", "openrouter", "assemblyai", "elevenlabs", "anthropic", "groq"):
            if vendor in providers and keys.get(vendor):
                result[f"{vendor}_key"] = keys[vendor]
                result[f"{vendor}_enabled"] = True
        return result
