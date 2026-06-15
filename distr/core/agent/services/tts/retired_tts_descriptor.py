"""Stub descriptors for retired TTS providers (name normalization only)."""

from __future__ import annotations

from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor


class RetiredTTSProviderDescriptor(TTSProviderDescriptor):
    """Metadata-only descriptor; live TTS is not supported."""

    provider_id: str
    display_name: str
    settings_key_name: str
    default_voice_id: str
    output_sample_rate: int
    agent_display_name: str
    name_match_tokens: tuple[str, ...]
    provider_type: str = "offline"
    playback_speed_bounds: tuple[float, float] = (0.5, 2.0)

    @property
    def id(self) -> str:
        return self.provider_id

    @property
    def name(self) -> str:
        return self.display_name

    @property
    def type(self) -> str:
        return self.provider_type

    @property
    def enabled(self) -> bool:
        return False

    @property
    def default_voice(self) -> str:
        return self.default_voice_id

    @property
    def settings_key(self) -> str:
        return self.settings_key_name

    @property
    def sample_rate(self) -> int:
        return self.output_sample_rate

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return self.playback_speed_bounds

    @property
    def supports_custom_voices(self) -> bool:
        return False

    @property
    def custom_voice_limit(self) -> int:
        return 0

    def _retired_error(self) -> RuntimeError:
        return RuntimeError(
            f"{self.display_name} has been removed from DecisionsAI "
            f"(too slow or unsupported on this platform). "
            f"Choose Kokoro, OpenAI, ElevenLabs, Coqui, or Supertonic in Settings → General."
        )

    def create_service(
        self,
        tts_config: dict,
        *,
        settings: dict,
        stt_service: Any,
        is_hands_free: bool,
        models_dir: str,
    ) -> Any:
        raise self._retired_error()

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        raise self._retired_error()

    def resolve_display_name(
        self,
        voice_id: str,
        settings: dict,
        voice_name: str | None = None,
    ) -> str:
        return self.agent_display_name

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        return self.default_voice_id

    def get_voices(self) -> list[dict]:
        return []

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        raise self._retired_error()

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        return (self.provider_id, self.settings_key_name, self.default_voice_id, {})

    def get_telegram_voice_id(self, settings: dict) -> str:
        return (settings or {}).get(self.settings_key_name, self.default_voice_id)

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        v = (raw or "").strip().lower()
        for token in self.name_match_tokens:
            if token in v:
                return self.provider_id
        return None
