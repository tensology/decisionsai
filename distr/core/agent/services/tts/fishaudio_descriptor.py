"""FishAudioDescriptor — cloud TTS via Fish Audio (S2.1 Pro / voice library)."""

from __future__ import annotations

import io
import logging
from typing import Any, Optional

from distr.core.agent.services.tts.fishaudio_config import (
    DEFAULT_FISHAUDIO_AGENT,
    DEFAULT_FISHAUDIO_VOICE,
    resolve_fishaudio_tts_model,
)
from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)


class FishAudioDescriptor(TTSProviderDescriptor):
    @property
    def id(self) -> str:
        return "fishaudio"

    @property
    def name(self) -> str:
        return "Fish Audio (Online)"

    @property
    def type(self) -> str:
        return "online"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def default_voice(self) -> str:
        return DEFAULT_FISHAUDIO_VOICE

    @property
    def settings_key(self) -> str:
        return "fishaudio_voice"

    @property
    def sample_rate(self) -> int:
        return 44100

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.5, 2.0)

    @property
    def supports_custom_voices(self) -> bool:
        return True

    @property
    def custom_voice_limit(self) -> int:
        return 0

    def create_service(
        self,
        tts_config: dict,
        *,
        settings: dict,
        stt_service: Any,
        is_hands_free: bool,
        models_dir: str,
    ) -> Any:
        from distr.core.agent.services.tts.fishaudio import FishAudioTTSService

        api_key = (tts_config.get("api_key") or (settings or {}).get("fishaudio_key") or "").strip()
        if not api_key:
            raise ValueError("Fish Audio API key is required for TTS")
        voice_id = self.resolve_reference_id(
            tts_config.get("voice_id") or DEFAULT_FISHAUDIO_VOICE,
            settings or {},
        )
        lo, hi = self.speed_bounds
        playback_speed = max(lo, min(hi, (settings or {}).get("playback_speed", 1.0)))
        model = resolve_fishaudio_tts_model(
            tts_config.get("model") or (settings or {}).get("fishaudio_tts_model")
        )
        service = FishAudioTTSService(
            api_key=api_key,
            voice_id=voice_id,
            voice_name=voice_id,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=(settings or {}).get("_event_queue"),
            speech_volume=100,
            model=model,
        )
        service.set_hands_free(is_hands_free)
        return service

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        import soundfile as sf

        from distr.core.audio.tts_handler import _resample_audio
        from distr.core.agent.services.tts.fishaudio_client import synthesize_audio
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        api_key = (settings.get("fishaudio_key") or "").strip()
        if not api_key:
            raise ValueError("Fish Audio API key not configured")
        vid = self.resolve_reference_id(
            voice or settings.get("fishaudio_voice") or DEFAULT_FISHAUDIO_VOICE,
            settings,
        )
        model = resolve_fishaudio_tts_model(settings.get("fishaudio_tts_model"))
        wav_bytes = synthesize_audio(
            api_key,
            text,
            reference_id=vid,
            model=model,
            speed=speed,
            audio_format="wav",
            sample_rate=44100,
            latency="normal",
        )
        audio, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        audio, sample_rate = _resample_audio(audio, sample_rate, 48000)
        sf.write(out_file, audio, sample_rate)
        logger.info("Wrote Fish Audio sample to %s", out_file)

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        if voice_name and voice_name.strip() and voice_name.strip() != voice_id:
            return voice_name.strip()
        return (voice_id or "").strip() or DEFAULT_FISHAUDIO_AGENT

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        return self.resolve_reference_id(raw_voice, settings or {})

    def get_voices(self) -> list[dict]:
        from distr.core.agent.services.tts.fishaudio_client import list_voices
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        api_key = (settings.get("fishaudio_key") or "").strip()
        if not api_key:
            return []
        try:
            return list_voices(api_key)
        except Exception as exc:
            logger.warning("Could not fetch Fish Audio voices: %s", exc)
            return [{"id": DEFAULT_FISHAUDIO_VOICE, "name": DEFAULT_FISHAUDIO_AGENT}]

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        return {
            "engine": "fishaudio",
            "voice_id": self.resolve_reference_id(voice_model, settings or {}),
            "model": resolve_fishaudio_tts_model((settings or {}).get("fishaudio_tts_model")),
            "api_key": ((settings or {}).get("fishaudio_key") or "").strip(),
            "in_place": False,
            "unload_kanade": True,
        }

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        return (
            "fishaudio",
            "fishaudio_voice",
            DEFAULT_FISHAUDIO_VOICE,
            {"api_key": "fishaudio_key"},
        )

    def get_telegram_voice_id(self, settings: dict) -> str:
        return (settings or {}).get("fishaudio_voice", DEFAULT_FISHAUDIO_VOICE) or DEFAULT_FISHAUDIO_VOICE

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        v = (raw or "").strip().lower()
        if "fishaudio" in v or "fish audio" in v or v == "fish":
            return "fishaudio"
        return None

    def resolve_reference_id(self, voice: str, settings: dict | None = None) -> str:
        """Map custom_<id> / blanks to a Fish model id for TTS reference_id."""
        raw = (voice or "").strip()
        if raw.startswith("custom_"):
            try:
                from distr.core.db import CustomVoice, get_session

                db_id = int(raw.split("_", 1)[1])
                session = get_session()
                try:
                    cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                    if cv and (cv.provider_voice_id or "").strip():
                        return cv.provider_voice_id.strip()
                finally:
                    session.close()
            except Exception:
                pass
        if raw and raw.lower() not in {"default", "fish", "fish audio"}:
            return raw
        return (
            (settings or {}).get("fishaudio_voice", DEFAULT_FISHAUDIO_VOICE)
            or DEFAULT_FISHAUDIO_VOICE
        )

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Create a private Fish studio model from uploaded clips (POST /model)."""
        from distr.core.agent.services.tts.fishaudio_client import (
            clone_audio_paths,
            create_voice_model,
        )
        from distr.core.settings import load_settings_from_db

        settings = load_settings_from_db()
        api_key = (settings.get("fishaudio_key") or "").strip()
        if not api_key:
            voice.status = "failed"
            voice.error_message = "Fish Audio API key not configured"
            session.commit()
            return
        clips = clone_audio_paths(audio_files)
        if not clips:
            voice.status = "failed"
            voice.error_message = "Fish Audio cloning needs at least one audio sample"
            session.commit()
            return
        payload = create_voice_model(
            api_key,
            voice.name,
            clips,
            description=(getattr(voice, "personality", None) or f"Custom voice: {voice.name}"),
            visibility="private",
        )
        voice.provider_voice_id = str(payload["_id"])
        voice.status = "ready"
        voice.error_message = ""
        session.commit()
        logger.info("Fish Audio custom voice created: %s -> %s", voice.name, voice.provider_voice_id)


DESCRIPTOR = FishAudioDescriptor()
