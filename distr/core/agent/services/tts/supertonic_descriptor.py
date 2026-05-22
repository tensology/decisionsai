"""Supertonic provider descriptor."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import numpy as np

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)

DEFAULT_SUPERTONIC_VOICE = "M1"

SUPERTONIC_VOICES = {
    "M1": "Male 1 - Lively",
    "M2": "Male 2 - Deep",
    "M3": "Male 3 - Authoritative",
    "M4": "Male 4 - Gentle",
    "M5": "Male 5 - Storyteller",
    "F1": "Female 1 - Calm",
    "F2": "Female 2 - Cheerful",
    "F3": "Female 3 - Announcer",
    "F4": "Female 4 - Confident",
    "F5": "Female 5 - Soothing",
}

SUPERTONIC_VOICE_BY_DISPLAY_NAME = {v.lower(): k for k, v in SUPERTONIC_VOICES.items()}


class SupertonicDescriptor(TTSProviderDescriptor):
    """Provider descriptor for Supertonic local ONNX TTS."""

    @property
    def id(self) -> str:
        return "supertonic"

    @property
    def name(self) -> str:
        return "Supertonic (Offline)"

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def default_voice(self) -> str:
        return DEFAULT_SUPERTONIC_VOICE

    @property
    def settings_key(self) -> str:
        return "supertonic_voice"

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
        from distr.core.agent.services.tts.supertonic import SupertonicTTSService

        voice_id = tts_config.get("voice_name") or tts_config.get("voice_id") or DEFAULT_SUPERTONIC_VOICE
        voice_id = self.normalize_voice(voice_id, settings)
        style_path = self._resolve_custom_style_path(voice_id) if voice_id.startswith("custom_") else None
        service = SupertonicTTSService(
            voice_name=DEFAULT_SUPERTONIC_VOICE if style_path else voice_id,
            voice_style_path=style_path,
            stt_service=stt_service,
            playback_speed=max(0.5, min(2.0, float(settings.get("playback_speed", 1.0)))),
            event_queue=settings.get("_event_queue"),
            speech_volume=100,
        )
        service.set_hands_free(is_hands_free)
        return service

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        import soundfile as sf
        from distr.core.agent.services.tts.supertonic import get_or_load_tts
        from distr.core.audio.tts_handler import _resample_audio

        tts = get_or_load_tts()
        voice = self.normalize_voice(voice, {})
        if voice.startswith("custom_"):
            style_path = self._resolve_custom_style_path(voice)
            if not style_path:
                raise FileNotFoundError(
                    f"No Supertonic Voice Builder JSON found for custom voice {voice}"
                )
            style = tts.get_voice_style_from_path(style_path)
        else:
            style = tts.get_voice_style(voice_name=voice)

        api_speed = max(0.5, min(2.0, float(speed or 1.0)))
        wav, _duration = tts.synthesize(
            text=text,
            lang="en",
            voice_style=style,
            total_steps=8,
            speed=api_speed,
        )
        audio = np.asarray(wav, dtype=np.float32).reshape(-1)
        if audio.size and np.max(np.abs(audio)) > 1.0:
            np.clip(audio, -1.0, 1.0, out=audio)
        audio, sample_rate = _resample_audio(audio, int(getattr(tts, "sample_rate", 44100)), 48000)
        sf.write(out_file, audio, sample_rate)
        logger.info("Wrote Supertonic sample to %s", out_file)

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        vm = (voice_id or "").strip()
        if not vm:
            vm = (settings or {}).get("supertonic_voice", DEFAULT_SUPERTONIC_VOICE)
        if vm.startswith("custom_"):
            name = self._resolve_custom_voice_name(vm)
            return name if name else SUPERTONIC_VOICES[DEFAULT_SUPERTONIC_VOICE]
        return SUPERTONIC_VOICES.get(vm.upper(), vm or SUPERTONIC_VOICES[DEFAULT_SUPERTONIC_VOICE])

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        raw = (raw_voice or "").strip()
        if raw.startswith("custom_"):
            return raw
        if raw.upper() in SUPERTONIC_VOICES:
            return raw.upper()
        by_label = SUPERTONIC_VOICE_BY_DISPLAY_NAME.get(raw.lower())
        if by_label:
            return by_label
        configured = ((settings or {}).get("supertonic_voice") or "").strip()
        if configured.startswith("custom_"):
            return configured
        if configured.upper() in SUPERTONIC_VOICES:
            return configured.upper()
        return DEFAULT_SUPERTONIC_VOICE

    def get_voices(self) -> list[dict]:
        return [{"id": vid, "name": name} for vid, name in SUPERTONIC_VOICES.items()]

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        resolved = self.normalize_voice(voice_model, settings)
        config = {
            "engine": self.id,
            "voice_name": resolved,
            "voice_id": resolved,
            "in_place": False,
        }
        if resolved.startswith("custom_"):
            config["voice_style_path"] = self._resolve_custom_style_path(resolved)
        return config

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        return ("supertonic", "supertonic_voice", DEFAULT_SUPERTONIC_VOICE, {})

    def get_telegram_voice_id(self, settings: dict) -> str:
        return (settings or {}).get("supertonic_voice", DEFAULT_SUPERTONIC_VOICE)

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        v = (raw or "").strip().lower()
        if "supertonic" in v or "supertone" in v:
            return self.id
        return None

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Register a Voice Builder JSON file as a Supertonic custom voice."""
        json_files = [p for p in audio_files if p.lower().endswith(".json")]
        if not json_files:
            voice.status = "failed"
            voice.error_message = (
                "Supertonic does not clone locally from audio. "
                "Upload a Supertonic Voice Builder .json voice style file."
            )
            session.commit()
            return

        style_path = json_files[0]
        self._validate_style_json(style_path)
        voice.provider_voice_id = f"custom_{voice.id}"
        voice.status = "ready"
        session.commit()
        logger.info("Supertonic custom voice imported: %s -> %s", voice.name, voice.provider_voice_id)

    @staticmethod
    def _validate_style_json(path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "style_ttl" not in data or "style_dp" not in data:
            raise ValueError("Invalid Supertonic voice style JSON: expected style_ttl and style_dp")
        try:
            from distr.core.agent.services.tts.supertonic import get_or_load_tts
            get_or_load_tts().get_voice_style_from_path(path)
        except Exception as e:
            raise ValueError(f"Invalid Supertonic voice style JSON: {e}") from e

    @staticmethod
    def _resolve_custom_style_path(voice_id: str) -> Optional[str]:
        try:
            from distr.core.db import get_session, CustomVoice
            db_id = int(voice_id.split("_", 1)[1])
            session = get_session()
            try:
                cv = session.query(CustomVoice).filter(
                    CustomVoice.id == db_id,
                    CustomVoice.provider == "supertonic",
                    CustomVoice.status == "ready",
                ).first()
                if cv and cv.audio_dir and os.path.isdir(cv.audio_dir):
                    for fname in os.listdir(cv.audio_dir):
                        if fname.lower().endswith(".json"):
                            return os.path.join(cv.audio_dir, fname)
            finally:
                session.close()
        except Exception:
            pass
        return None

    @staticmethod
    def _resolve_custom_voice_name(voice_id: str) -> Optional[str]:
        try:
            from distr.core.db import get_session, CustomVoice
            db_id = int(voice_id.split("_", 1)[1])
            session = get_session()
            try:
                cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                if cv:
                    return cv.name
            finally:
                session.close()
        except Exception:
            pass
        return None


DESCRIPTOR = SupertonicDescriptor()
