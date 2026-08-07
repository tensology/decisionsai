"""
OpenAIDescriptor — TTSProviderDescriptor for the OpenAI (Online) TTS provider.

Encapsulates ALL OpenAI-specific logic previously scattered across constants.py,
service_factory.py, session.py, tts_handler.py, and events.py.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor
from distr.core.agent.services.tts.openai_tts_config import (
    DEFAULT_OPENAI_TTS_MODEL,
    openai_tts_supports_instructions,
    resolve_openai_tts_model,
    voices_for_openai_tts_model,
)

logger = logging.getLogger(__name__)

# --- OpenAI defaults (moved from constants.py) ---
DEFAULT_OPENAI_VOICE = "alloy"
DEFAULT_OPENAI_AGENT = "Alloy"


class OpenAIDescriptor(TTSProviderDescriptor):
    """Provider descriptor for OpenAI (Online) TTS."""

    # ------------------------------------------------------------------
    # Static configuration
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return "openai"

    @property
    def name(self) -> str:
        return "OpenAI (Online)"

    @property
    def type(self) -> str:
        return "online"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def default_voice(self) -> str:
        return DEFAULT_OPENAI_VOICE

    @property
    def settings_key(self) -> str:
        return "openai_voice"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.25, 4.0)

    @property
    def supports_custom_voices(self) -> bool:
        return False

    @property
    def custom_voice_limit(self) -> int:
        return 0

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def create_service(
        self,
        tts_config: dict,
        *,
        settings: dict,
        stt_service: Any,
        is_hands_free: bool,
        models_dir: str,
    ) -> Any:
        """Create an OpenAITTSService instance.

        Replicates the OpenAI branch from service_factory.create_tts_service()
        and session._create_services().
        """
        try:
            from distr.core.agent.services import OpenAITTSService
        except ImportError:
            OpenAITTSService = None

        api_key = tts_config.get('api_key', '')
        voice_id = tts_config.get('voice_id', DEFAULT_OPENAI_VOICE)
        if not api_key:
            raise ValueError("OpenAI API key is required for TTS")
        if not OpenAITTSService:
            raise ImportError("OpenAITTSService is not available. Please ensure openai library is installed.")

        lo, hi = self.speed_bounds
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))
        model = resolve_openai_tts_model(settings.get("openai_tts_model"))
        instructions = (settings.get("openai_tts_instructions") or "").strip()
        if not openai_tts_supports_instructions(model):
            instructions = ""

        service = OpenAITTSService(
            api_key=api_key,
            voice_id=voice_id,
            voice_name=voice_id,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
            model=model,
            instructions=instructions,
        )
        service.set_hands_free(is_hands_free)
        return service

    # ------------------------------------------------------------------
    # Audio generation
    # ------------------------------------------------------------------

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        """Generate OpenAI TTS audio and write to WAV file.

        Replicates _generate_openai() from tts_handler.py.
        """
        import soundfile as sf
        from openai import OpenAI
        from distr.core.utils import load_settings_from_db

        settings = load_settings_from_db()
        api_key = settings.get('openai_key', '')
        if not api_key:
            raise ValueError("OpenAI API key not configured")

        client = OpenAI(api_key=api_key)
        model = resolve_openai_tts_model(settings.get("openai_tts_model"))
        create_kwargs = {
            "model": model,
            "voice": voice,
            "input": text,
            "speed": speed,
        }
        instructions = (settings.get("openai_tts_instructions") or "").strip()
        if instructions and openai_tts_supports_instructions(model):
            create_kwargs["instructions"] = instructions

        response = client.audio.speech.create(**create_kwargs)
        # OpenAI returns MP3 by default; write to temp then convert to WAV
        tmp_mp3 = out_file + ".tmp.mp3"
        response.stream_to_file(tmp_mp3)

        try:
            audio, sample_rate = sf.read(tmp_mp3, dtype='float32')
            from distr.core.audio.tts_handler import _resample_audio
            audio, sample_rate = _resample_audio(audio, sample_rate, 48000)
            sf.write(out_file, audio, sample_rate)
        finally:
            if os.path.exists(tmp_mp3):
                os.remove(tmp_mp3)

        logger.info("Wrote OpenAI sample to %s (model=%s)", out_file, model)

    # ------------------------------------------------------------------
    # Voice / display-name resolution
    # ------------------------------------------------------------------

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        """Resolve an OpenAI voice id to a human-readable display name.

        Replicates the OpenAI branches from service_factory.resolve_voice_to_display_name()
        and tts_handler._resolve_display_name().
        """
        vm = (voice_id or '').strip()

        # If no voice_id provided, resolve from settings
        if not vm:
            v = (settings or {}).get('openai_voice', DEFAULT_OPENAI_VOICE)
            return (v or DEFAULT_OPENAI_AGENT).capitalize()

        return vm.capitalize()

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        """Normalize a raw voice string into a valid OpenAI voice id.

        Replicates the OpenAI branch from tts_handler._normalize_voice_for_provider().
        """
        allowed = voices_for_openai_tts_model((settings or {}).get("openai_tts_model"))
        raw = (raw_voice or "").strip()
        v = raw.lower()
        if v in allowed:
            return v
        # Handle labels like "Kiran nova" by matching any token to a valid id
        tokens = re.split(r"[^a-zA-Z0-9_]+", v)
        for token in tokens:
            if token in allowed:
                return token
        configured = (settings.get("openai_voice") or "").strip().lower()
        if configured in allowed:
            return configured
        return DEFAULT_OPENAI_VOICE

    def get_voices(self) -> list[dict]:
        """Return the list of available OpenAI voices for the configured model."""
        try:
            from distr.core.utils import load_settings_from_db
            settings = load_settings_from_db()
            model = settings.get("openai_tts_model")
        except Exception:
            model = DEFAULT_OPENAI_TTS_MODEL
        allowed = voices_for_openai_tts_model(model)
        return [{"id": v, "name": v.capitalize()} for v in sorted(allowed)]

    # ------------------------------------------------------------------
    # Hot-swap support
    # ------------------------------------------------------------------

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        """Return hot-swap configuration for OpenAI.

        OpenAI requires full service replacement (not in-place swap).
        Replicates the OpenAI branch from session._hot_swap_tts_service().
        """
        return {
            'engine': 'openai',
            'voice_id': voice_model or DEFAULT_OPENAI_VOICE,
            'api_key': (settings.get('openai_key') or '').strip(),
            'in_place': False,
            'unload_kanade': True,
        }

    # ------------------------------------------------------------------
    # Voice settings entry
    # ------------------------------------------------------------------

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        """Return the _VOICE_SETTINGS tuple for OpenAI.

        Replicates _VOICE_SETTINGS['openai'] from session.py.
        """
        return ('openai', 'openai_voice', DEFAULT_OPENAI_VOICE, {'api_key': 'openai_key'})

    # ------------------------------------------------------------------
    # Telegram integration
    # ------------------------------------------------------------------

    def get_telegram_voice_id(self, settings: dict) -> str:
        """Resolve the OpenAI voice id from settings for Telegram.

        Replicates the OpenAI branch from events._telegram_resolve_voice_settings().
        """
        return settings.get('openai_voice', 'alloy')

    # ------------------------------------------------------------------
    # Provider name normalization
    # ------------------------------------------------------------------

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        """Check if *raw* matches OpenAI and return 'openai', or None.

        Replicates the OpenAI branch from constants.normalize_voice_provider().
        """
        v = (raw or '').strip().lower()
        if 'openai' in v:
            return 'openai'
        return None


# Module-level singleton for auto-discovery by the registry
DESCRIPTOR = OpenAIDescriptor()
