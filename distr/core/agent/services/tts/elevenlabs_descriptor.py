"""
ElevenLabsDescriptor — TTSProviderDescriptor for the ElevenLabs (Online) TTS provider.

Encapsulates ALL ElevenLabs-specific logic previously scattered across constants.py,
service_factory.py, session.py, tts_handler.py, events.py, and voice_cloning.py.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Optional

from distr.core.agent.services.tts.elevenlabs_config import ELEVENLABS_TTS_MODEL_ID
from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)

# --- ElevenLabs defaults (moved from constants.py) ---
ELEVENLABS_DEFAULTS = {
    "stability": 0.5,
    "similarity_boost": 0.6,
    "style": 0.25,
    "use_speaker_boost": True,
}

DEFAULT_ELEVENLABS_AGENT = "Heart"


def _clean_elevenlabs_descriptor_error(error: Exception) -> tuple[str, str]:
    body = getattr(error, "body", None)
    status_code = getattr(error, "status_code", None)
    error_str = str(error)
    error_lower = error_str.lower()
    detail = body.get("detail") if isinstance(body, dict) else None
    provider_status = detail.get("status") if isinstance(detail, dict) else None
    provider_message = detail.get("message") if isinstance(detail, dict) else None

    if provider_status == "quota_exceeded" or "quota_exceeded" in error_lower or ("quota" in error_lower and "exceeded" in error_lower):
        return "quota_exceeded", provider_message or "ElevenLabs quota exceeded. Switch to another TTS provider or add ElevenLabs credits."
    if status_code == 429 or "rate limit" in error_lower or "too many requests" in error_lower:
        return "rate_limited", "ElevenLabs is rate limiting requests. Please wait a moment or switch to another TTS provider."
    if status_code in (401, 403) or "unauthorized" in error_lower or "forbidden" in error_lower:
        return "auth_failed", "ElevenLabs rejected the request. Check the ElevenLabs API key and billing status."
    return "unknown", "ElevenLabs TTS failed. Please try again or switch to another TTS provider."


class ElevenLabsDescriptor(TTSProviderDescriptor):
    """Provider descriptor for ElevenLabs (Online) TTS."""

    # ------------------------------------------------------------------
    # Static configuration
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return "elevenlabs"

    @property
    def name(self) -> str:
        return "ElevenLabs (Online)"

    @property
    def type(self) -> str:
        return "online"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def default_voice(self) -> str:
        return "default"

    @property
    def settings_key(self) -> str:
        return "elevenlabs_voice"

    @property
    def sample_rate(self) -> int:
        return 44100

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.7, 1.2)

    @property
    def supports_custom_voices(self) -> bool:
        return True

    @property
    def custom_voice_limit(self) -> int:
        return 5

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
        """Create an ElevenLabsTTSService instance.

        Replicates the ElevenLabs branch from service_factory.create_tts_service()
        and session._create_services().
        """
        from distr.core.agent.services import ElevenLabsTTSService
        from distr.core.agent.service_factory import resolve_elevenlabs_voice

        api_key = tts_config.get('api_key', '')
        voice_id_or_name = tts_config.get('voice_id', '')
        if not api_key:
            raise ValueError("ElevenLabs API key is required")
        if not voice_id_or_name:
            raise ValueError("ElevenLabs voice ID is required")

        voice_id, voice_name = resolve_elevenlabs_voice(api_key, voice_id_or_name)
        playback_speed = settings.get('playback_speed', 1.0)

        service = ElevenLabsTTSService(
            api_key=api_key,
            voice_id=voice_id,
            voice_name=voice_name,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
            stability=float(settings.get('elevenlabs_stability', ELEVENLABS_DEFAULTS['stability'])),
            similarity_boost=float(settings.get('elevenlabs_similarity_boost', ELEVENLABS_DEFAULTS['similarity_boost'])),
            style=float(settings.get('elevenlabs_style', ELEVENLABS_DEFAULTS['style'])),
            use_speaker_boost=bool(settings.get('elevenlabs_use_speaker_boost', ELEVENLABS_DEFAULTS['use_speaker_boost'])),
            on_quota_exceeded=settings.get('_on_quota_exceeded'),
        )
        # Stash resolved voice name on the service so caller can read it
        service._resolved_voice_name = voice_name
        service.set_hands_free(is_hands_free)
        return service

    # ------------------------------------------------------------------
    # Audio generation
    # ------------------------------------------------------------------

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        """Generate ElevenLabs TTS audio. Never uses cache; uses current DB voice settings.

        Replicates _generate_elevenlabs() from tts_handler.py.
        """
        import numpy as np
        import soundfile as sf
        from elevenlabs import ElevenLabs
        from distr.core.utils import load_settings_from_db

        settings = load_settings_from_db()
        api_key = settings.get('elevenlabs_key', '')
        if not api_key:
            raise ValueError("ElevenLabs API key not configured")
        stability = float(settings.get("elevenlabs_stability", 0.5))
        similarity_boost = float(settings.get("elevenlabs_similarity_boost", 0.6))
        style = float(settings.get("elevenlabs_style", 0.25))
        use_speaker_boost = bool(settings.get("elevenlabs_use_speaker_boost", True))

        client = ElevenLabs(api_key=api_key)
        requested_voice = (voice or "").strip()
        resolved_voice = requested_voice

        # Defensive resolution: some web/chat paths may pass display names or "default"
        # instead of a valid ElevenLabs voice_id.
        try:
            voices = client.voices.get_all().voices or []
            if voices:
                by_id = {v.voice_id: v.voice_id for v in voices if getattr(v, "voice_id", None)}
                by_name = {
                    (v.name or "").strip().lower(): v.voice_id
                    for v in voices
                    if getattr(v, "voice_id", None) and getattr(v, "name", None)
                }
                configured_voice = (settings.get("elevenlabs_voice", "") or "").strip()
                fallback_voice = voices[0].voice_id
                req_lower = requested_voice.lower()
                if requested_voice in by_id:
                    resolved_voice = requested_voice
                elif req_lower and req_lower in by_name:
                    resolved_voice = by_name[req_lower]
                elif configured_voice in by_id:
                    resolved_voice = configured_voice
                elif fallback_voice:
                    resolved_voice = fallback_voice
        except Exception as resolve_err:
            logger.warning("ElevenLabs voice resolution failed, using raw voice '%s': %s", requested_voice, resolve_err)

        api_speed = max(0.7, min(1.2, float(speed)))

        try:
            audio_stream = client.text_to_speech.convert(
                text=text,
                voice_id=resolved_voice,
                model_id=ELEVENLABS_TTS_MODEL_ID,
                output_format="mp3_44100_128",
                voice_settings={
                    "stability": stability,
                    "similarity_boost": similarity_boost,
                    "style": style,
                    "use_speaker_boost": use_speaker_boost,
                    "speed": api_speed,
                },
            )
            audio_bytes = b"".join(audio_stream)
        except Exception as err:
            kind, message = _clean_elevenlabs_descriptor_error(err)
            if kind in {"quota_exceeded", "rate_limited", "auth_failed"}:
                logger.warning("ElevenLabs TTS unavailable: %s", message)
                raise ValueError(message) from None
            raise

        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_mp3(io.BytesIO(audio_bytes))
            if seg.channels > 1:
                seg = seg.set_channels(1)
            sample_rate = seg.frame_rate
            samples = seg.get_array_of_samples()
            audio = np.array(samples, dtype=np.float32) / 32768.0
        except ImportError:
            with io.BytesIO(audio_bytes) as f:
                audio, sample_rate = sf.read(f)
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1)
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32) / (32768.0 if audio.dtype == np.int16 else 2147483648.0)

        from distr.core.audio.tts_handler import _resample_audio
        audio, sample_rate = _resample_audio(audio, sample_rate, 48000)
        sf.write(out_file, audio, sample_rate)
        logger.info("Wrote ElevenLabs sample to %s", out_file)

    # ------------------------------------------------------------------
    # Voice / display-name resolution
    # ------------------------------------------------------------------

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        """Resolve an ElevenLabs voice id to a human-readable display name.

        Replicates the ElevenLabs branches from service_factory.resolve_voice_to_display_name()
        and tts_handler._resolve_display_name().
        """
        vm = (voice_id or '').strip()

        # If no voice_id provided, resolve from settings
        if not vm:
            return (settings or {}).get('elevenlabs_voice', '') or DEFAULT_ELEVENLABS_AGENT

        # Check custom voices DB first (fast, no API call)
        try:
            from distr.core.db import get_session
            from sqlalchemy import text as sa_text
            session = get_session()
            try:
                row = session.execute(sa_text(
                    "SELECT name FROM custom_voices "
                    "WHERE provider = 'elevenlabs' AND provider_voice_id = :vid AND status = 'ready' LIMIT 1"
                ), {"vid": vm}).fetchone()
                if row:
                    return row[0]
            finally:
                session.close()
        except Exception:
            pass

        # Try resolving via ElevenLabs API
        api_key = ((settings or {}).get('elevenlabs_key') or '').strip()
        if not api_key:
            return vm
        try:
            from distr.core.agent.service_factory import resolve_elevenlabs_voice
            _, resolved_name = resolve_elevenlabs_voice(api_key, vm)
            return resolved_name
        except Exception as e:
            logger.debug("Could not resolve ElevenLabs voice %s: %s", vm[:20], e)
            return vm

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        """Normalize a raw voice string for ElevenLabs.

        ElevenLabs voice IDs are resolved again inside generate_audio, so we
        keep the raw value here. Replicates the ElevenLabs branch from
        tts_handler._normalize_voice_for_provider().
        """
        return (raw_voice or "").strip()

    def get_voices(self) -> list[dict]:
        """Return the list of available ElevenLabs voices.

        Requires an API call to ElevenLabs. Returns empty list on failure.
        """
        try:
            from distr.core.utils import load_settings_from_db
            settings = load_settings_from_db()
            api_key = (settings.get('elevenlabs_key') or '').strip()
            if not api_key:
                return []
            from elevenlabs import ElevenLabs
            client = ElevenLabs(api_key=api_key)
            voices = client.voices.get_all().voices or []
            return [{"id": v.voice_id, "name": v.name} for v in voices if getattr(v, "voice_id", None)]
        except Exception as e:
            logger.warning("Could not fetch ElevenLabs voices: %s", e)
            return []

    # ------------------------------------------------------------------
    # Hot-swap support
    # ------------------------------------------------------------------

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        """Return hot-swap configuration for ElevenLabs.

        ElevenLabs requires full service replacement (not in-place swap).
        Replicates the ElevenLabs branch from session._hot_swap_tts_service().
        """
        return {
            'engine': 'elevenlabs',
            'voice_id': voice_model or '',
            'api_key': (settings.get('elevenlabs_key') or '').strip(),
            'in_place': False,
            'unload_kanade': True,
        }

    # ------------------------------------------------------------------
    # Voice settings entry
    # ------------------------------------------------------------------

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        """Return the _VOICE_SETTINGS tuple for ElevenLabs.

        Replicates _VOICE_SETTINGS['elevenlabs'] from session.py.
        """
        return ('elevenlabs', 'elevenlabs_voice', '', {'api_key': 'elevenlabs_key'})

    # ------------------------------------------------------------------
    # Telegram integration
    # ------------------------------------------------------------------

    def get_telegram_voice_id(self, settings: dict) -> str:
        """Resolve the ElevenLabs voice id from settings for Telegram.

        Replicates the ElevenLabs branch from events._telegram_resolve_voice_settings().
        """
        return settings.get('elevenlabs_voice', '')

    # ------------------------------------------------------------------
    # Provider name normalization
    # ------------------------------------------------------------------

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        """Check if *raw* matches ElevenLabs and return 'elevenlabs', or None.

        Replicates the ElevenLabs branch from constants.normalize_voice_provider().
        """
        v = (raw or '').strip().lower()
        if 'elevenlabs' in v:
            return 'elevenlabs'
        return None

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Clone voice via ElevenLabs Instant Voice Cloning (IVC) API.

        Replicates _clone_elevenlabs() from voice_cloning.py.
        """
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        api_key = (settings.get("elevenlabs_key") or "").strip()
        if not api_key:
            voice.status = "failed"
            voice.error_message = "ElevenLabs API key not configured"
            session.commit()
            return

        from elevenlabs import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        description = f"Custom voice: {voice.name}"

        file_handles = []
        try:
            for path in audio_files:
                file_handles.append(open(path, 'rb'))

            result = client.voices.ivc.create(
                name=voice.name,
                description=description,
                files=file_handles,
            )
            voice.provider_voice_id = result.voice_id
            voice.status = "ready"
            session.commit()
            logger.info("ElevenLabs custom voice created: %s -> %s", voice.name, result.voice_id)
        finally:
            for fh in file_handles:
                fh.close()


# Module-level singleton for auto-discovery by the registry
DESCRIPTOR = ElevenLabsDescriptor()
