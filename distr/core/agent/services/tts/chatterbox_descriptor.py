"""Chatterbox provider descriptor."""

from __future__ import annotations

import logging
import os
import contextlib
import io
from typing import Any, Optional

import numpy as np

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)

DEFAULT_CHATTERBOX_VOICE = "default"
DEFAULT_CHATTERBOX_AGENT = "Chatterbox"


class ChatterboxDescriptor(TTSProviderDescriptor):
    """Provider descriptor for local Chatterbox TTS."""

    @property
    def id(self) -> str:
        return "chatterbox"

    @property
    def name(self) -> str:
        return "Chatterbox (Offline)"

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        try:
            import importlib.util

            return bool(
                importlib.util.find_spec("chatterbox.tts_mlx")
                or importlib.util.find_spec("chatterbox.tts")
            )
        except Exception:
            return False

    @property
    def default_voice(self) -> str:
        return DEFAULT_CHATTERBOX_VOICE

    @property
    def settings_key(self) -> str:
        return "chatterbox_voice"

    @property
    def sample_rate(self) -> int:
        return 24000

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
        from distr.core.agent.services.tts.chatterbox import ChatterboxTTSService

        voice_id = tts_config.get("voice_name") or tts_config.get("voice_id") or DEFAULT_CHATTERBOX_VOICE
        voice_id = self.normalize_voice(voice_id, settings)
        ref_audio = self._resolve_custom_reference_audio(voice_id) if voice_id.startswith("custom_") else None
        service = ChatterboxTTSService(
            voice_name=voice_id,
            reference_audio_path=ref_audio,
            stt_service=stt_service,
            playback_speed=max(0.5, min(2.0, float(settings.get("playback_speed", 1.0)))),
            event_queue=settings.get("_event_queue"),
            speech_volume=100,
        )
        service.set_hands_free(is_hands_free)
        return service

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        import soundfile as sf
        from distr.core.agent.services.tts.chatterbox import get_or_load_model, _to_numpy_audio
        from distr.core.audio.tts_handler import _resample_audio

        voice = self.normalize_voice(voice, {})
        ref_audio = self._resolve_custom_reference_audio(voice) if voice.startswith("custom_") else None
        if voice.startswith("custom_") and not ref_audio:
            raise FileNotFoundError(f"No Chatterbox reference audio found for custom voice {voice}")

        model, _backend = get_or_load_model()
        kwargs = {
            "text": text,
            "exaggeration": 0.5,
            "cfg_weight": 0.5,
            "show_progress": False,
            "use_sentence_chunking": False,
        }
        if ref_audio:
            kwargs["audio_prompt_path"] = ref_audio
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                wav = model.generate(**kwargs)
        except TypeError:
            kwargs.pop("cfg_weight", None)
            with contextlib.redirect_stdout(io.StringIO()):
                wav = model.generate(**kwargs)

        sample_rate = int(getattr(model, "sr", None) or getattr(model, "sample_rate", None) or 24000)
        audio = _to_numpy_audio(wav)
        if audio.size:
            audio = np.clip(audio, -1.0, 1.0)
        audio, sample_rate = _resample_audio(audio, sample_rate, 48000)
        sf.write(out_file, audio, sample_rate)
        logger.info("Wrote Chatterbox sample to %s", out_file)

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        vm = (voice_id or "").strip()
        if not vm:
            vm = (settings or {}).get("chatterbox_voice", DEFAULT_CHATTERBOX_VOICE)
        if vm.startswith("custom_"):
            name = self._resolve_custom_voice_name(vm)
            return name if name else "Custom Chatterbox Voice"
        return DEFAULT_CHATTERBOX_AGENT

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        raw = (raw_voice or "").strip()
        if raw.startswith("custom_"):
            return raw
        configured = ((settings or {}).get("chatterbox_voice") or "").strip()
        if configured.startswith("custom_"):
            return configured
        return DEFAULT_CHATTERBOX_VOICE

    def get_voices(self) -> list[dict]:
        return [{"id": DEFAULT_CHATTERBOX_VOICE, "name": "Default (Chatterbox)"}]

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        resolved = self.normalize_voice(voice_model, settings)
        config = {
            "engine": self.id,
            "voice_name": resolved,
            "voice_id": resolved,
            "in_place": False,
        }
        if resolved.startswith("custom_"):
            config["reference_audio_path"] = self._resolve_custom_reference_audio(resolved)
        return config

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        return ("chatterbox", "chatterbox_voice", DEFAULT_CHATTERBOX_VOICE, {})

    def get_telegram_voice_id(self, settings: dict) -> str:
        return (settings or {}).get("chatterbox_voice", DEFAULT_CHATTERBOX_VOICE)

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        v = (raw or "").strip().lower()
        if "chatterbox" in v or "chatter box" in v:
            return self.id
        return None

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Register a reference clip for Chatterbox zero-shot cloning."""
        from pydub import AudioSegment

        ref_path = None
        for fpath in audio_files:
            ext = os.path.splitext(fpath)[1].lower()
            if ext in {".wav", ".flac", ".ogg"}:
                ref_path = fpath
                break

        if not ref_path:
            for fpath in audio_files:
                ext = os.path.splitext(fpath)[1].lower()
                if ext in {".mp3", ".m4a", ".webm"}:
                    wav_path = os.path.splitext(fpath)[0] + ".wav"
                    logger.info(
                        "Chatterbox clone: converting %s -> %s",
                        os.path.basename(fpath),
                        os.path.basename(wav_path),
                    )
                    audio_seg = AudioSegment.from_file(fpath)
                    audio_seg = audio_seg.set_channels(1).set_frame_rate(24000).set_sample_width(2)
                    audio_seg.export(wav_path, format="wav")
                    ref_path = wav_path
                    break

        if not ref_path:
            voice.status = "failed"
            voice.error_message = "Upload a supported reference audio file for Chatterbox cloning"
            session.commit()
            return

        voice.provider_voice_id = f"custom_{voice.id}"
        voice.status = "ready"
        session.commit()
        logger.info("Chatterbox custom voice registered: %s -> %s", voice.name, voice.provider_voice_id)

    @staticmethod
    def _resolve_custom_reference_audio(voice_id: str) -> Optional[str]:
        try:
            from distr.core.db import get_session, CustomVoice

            db_id = int(voice_id.split("_", 1)[1])
            session = get_session()
            try:
                cv = session.query(CustomVoice).filter(
                    CustomVoice.id == db_id,
                    CustomVoice.provider == "chatterbox",
                    CustomVoice.status == "ready",
                ).first()
                if cv and cv.audio_dir and os.path.isdir(cv.audio_dir):
                    for fname in os.listdir(cv.audio_dir):
                        if fname.lower().endswith((".wav", ".flac", ".ogg", ".mp3", ".m4a", ".webm")):
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


DESCRIPTOR = ChatterboxDescriptor()
