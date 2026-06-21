"""PixazoDescriptor — cloud VoxCPM TTS via Pixazo API key."""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)

DEFAULT_PIXAZO_VOICE = "voxcpm"
DEFAULT_PIXAZO_AGENT = "VoxCPM"
PIXAZO_VOICES = [
    {"id": "voxcpm", "name": "VoxCPM 2 (default, free)"},
]


class PixazoDescriptor(TTSProviderDescriptor):
    @property
    def id(self) -> str:
        return "pixazo"

    @property
    def name(self) -> str:
        return "Pixazo (VoxCPM)"

    @property
    def type(self) -> str:
        return "online"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def default_voice(self) -> str:
        return DEFAULT_PIXAZO_VOICE

    @property
    def settings_key(self) -> str:
        return "pixazo_voice"

    @property
    def sample_rate(self) -> int:
        return 48000

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.5, 2.0)

    @property
    def supports_custom_voices(self) -> bool:
        return True

    @property
    def custom_voice_limit(self) -> int:
        return 10

    def create_service(
        self,
        tts_config: dict,
        *,
        settings: dict,
        stt_service: Any,
        is_hands_free: bool,
        models_dir: str,
    ) -> Any:
        from distr.core.agent.services.tts.pixazo import PixazoTTSService
        from distr.core.third_party_keys import pixazo_api_key

        api_key = (tts_config.get("api_key") or pixazo_api_key() or "").strip()
        if not api_key:
            raise ValueError("Pixazo API key is required for TTS")
        voice_id = (tts_config.get("voice_id") or DEFAULT_PIXAZO_VOICE).strip()
        ref_url, prompt_text = self._resolve_clone_context(voice_id)
        lo, hi = self.speed_bounds
        playback_speed = max(lo, min(hi, (settings or {}).get("playback_speed", 1.0)))
        from distr.core.pixazo_client import pixazo_dit_steps_from_settings

        dit_steps = pixazo_dit_steps_from_settings(settings)
        service = PixazoTTSService(
            api_key=api_key,
            voice_id=voice_id,
            voice_name=voice_id,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=(settings or {}).get("_event_queue"),
            speech_volume=100,
            reference_audio_url=ref_url,
            prompt_text=prompt_text,
            dit_steps=dit_steps,
        )
        service.set_hands_free(is_hands_free)
        return service

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        import soundfile as sf

        from distr.core.audio.tts_handler import _resample_audio
        from distr.core.pixazo_client import pixazo_dit_steps_from_settings, voxcpm_synthesize_wav_bytes
        from distr.core.third_party_keys import pixazo_api_key
        from distr.core.settings import load_settings_from_db

        api_key = pixazo_api_key()
        if not api_key:
            raise ValueError("Pixazo API key not configured")
        settings = load_settings_from_db()
        dit_steps = pixazo_dit_steps_from_settings(settings)
        vid = (voice or DEFAULT_PIXAZO_VOICE).strip()
        ref_url, prompt_text = self._resolve_clone_context(vid)
        wav_bytes = voxcpm_synthesize_wav_bytes(
            api_key,
            text,
            voice_id=vid,
            reference_audio_url=ref_url,
            prompt_text=prompt_text,
            dit_steps=dit_steps,
        )
        audio, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        audio, sample_rate = _resample_audio(audio, sample_rate, self.sample_rate)
        sf.write(out_file, audio, sample_rate)
        logger.info("Wrote Pixazo VoxCPM sample to %s", out_file)

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        vm = (voice_id or "").strip()
        if vm.startswith("custom_"):
            name = self._resolve_custom_voice_name(vm)
            if name:
                return name
        if not vm:
            return DEFAULT_PIXAZO_AGENT
        for entry in PIXAZO_VOICES:
            if entry["id"] == vm:
                return entry["name"]
        return vm

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        raw = (raw_voice or "").strip()
        if not raw:
            return (settings or {}).get("pixazo_voice", DEFAULT_PIXAZO_VOICE) or DEFAULT_PIXAZO_VOICE
        if raw.startswith("custom_"):
            return raw
        if any(v["id"] == raw for v in PIXAZO_VOICES):
            return raw
        return DEFAULT_PIXAZO_VOICE

    def get_voices(self) -> list[dict]:
        return [dict(v) for v in PIXAZO_VOICES]

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        return {
            "engine": "pixazo",
            "voice_id": voice_model or DEFAULT_PIXAZO_VOICE,
            "api_key": (settings.get("pixazo_key") or "").strip(),
            "in_place": False,
            "unload_kanade": True,
        }

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        return ("pixazo", "pixazo_voice", DEFAULT_PIXAZO_VOICE, {"api_key": "pixazo_key"})

    def get_telegram_voice_id(self, settings: dict) -> str:
        return (settings or {}).get("pixazo_voice", DEFAULT_PIXAZO_VOICE)

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        v = (raw or "").strip().lower()
        if "pixazo" in v:
            return "pixazo"
        # ponytail: map cloud VoxCPM label to Pixazo when user picks "VoxCPM" online
        if v == "voxcpm" or v == "vox cpm":
            from distr.core.third_party_keys import pixazo_enabled

            if pixazo_enabled():
                return "pixazo"
        return None

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        from pydub import AudioSegment

        for fpath in audio_files:
            if fpath.lower().endswith(".json"):
                continue
            ext = os.path.splitext(fpath)[1].lower()
            if ext != ".wav":
                wav_path = os.path.splitext(fpath)[0] + ".wav"
                logger.info(
                    "Pixazo clone: converting %s -> %s",
                    os.path.basename(fpath),
                    os.path.basename(wav_path),
                )
                audio_seg = AudioSegment.from_file(fpath)
                audio_seg = audio_seg.set_channels(1).set_frame_rate(24000).set_sample_width(2)
                audio_seg.export(wav_path, format="wav")
                try:
                    os.remove(fpath)
                except OSError:
                    pass

        audio_dir = voice.audio_dir if voice.audio_dir and os.path.isdir(voice.audio_dir) else None
        if audio_dir:
            wav_files = [
                os.path.join(audio_dir, f)
                for f in sorted(os.listdir(audio_dir))
                if f.lower().endswith(".wav")
            ]
        else:
            wav_files = [p for p in audio_files if p.lower().endswith(".wav")]
        if not wav_files:
            voice.status = "failed"
            voice.error_message = "Pixazo VoxCPM cloning requires at least one audio reference file"
            session.commit()
            return
        ref_path = wav_files[0]
        if not voice.audio_dir or not os.path.isdir(voice.audio_dir):
            voice.status = "failed"
            voice.error_message = "Custom voice audio directory missing"
            session.commit()
            return
        try:
            from distr.core.integrations.relay_media import (
                upload_pixazo_voice_reference,
                write_relay_reference_meta,
            )

            record = upload_pixazo_voice_reference(ref_path, label=f"custom_{voice.id}")
            write_relay_reference_meta(voice.audio_dir, record)
        except Exception as exc:
            logger.warning(
                "Pixazo relay staging failed for voice %s: %s; deferring reference staging until first use",
                voice.id,
                exc,
            )
        voice.provider_voice_id = f"custom_{voice.id}"
        voice.status = "ready"
        voice.error_message = ""
        session.commit()
        logger.info("Pixazo custom voice registered: %s -> %s", voice.name, voice.provider_voice_id)

    @staticmethod
    def _resolve_custom_voice_name(voice_id: str) -> Optional[str]:
        try:
            from distr.core.db import CustomVoice, get_session

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

    @staticmethod
    def _resolve_clone_context(voice_id: str) -> tuple[Optional[str], str]:
        """Return (reference_audio_url, prompt_text) for custom_* voices."""
        vid = (voice_id or "").strip()
        if not vid.startswith("custom_"):
            return None, ""
        try:
            from distr.core.db import CustomVoice, get_session

            db_id = int(vid.split("_", 1)[1])
            session = get_session()
            try:
                cv = session.query(CustomVoice).filter(
                    CustomVoice.id == db_id,
                    CustomVoice.provider == "pixazo",
                    CustomVoice.status == "ready",
                ).first()
                if not cv:
                    return None, ""
                ref_path = PixazoDescriptor.reference_audio_path(vid)
                prompt = (cv.system_prompt or "").strip()
                if ref_path and cv.audio_dir:
                    try:
                        from distr.core.integrations.relay_media import ensure_pixazo_reference_url

                        ref_url = ensure_pixazo_reference_url(
                            ref_path,
                            cv.audio_dir,
                            label=f"custom_{db_id}",
                        )
                        return ref_url, prompt
                    except Exception as exc:
                        logger.warning("Pixazo relay reference refresh failed for %s: %s", vid, exc)
                ref_url = public_custom_voice_reference_url(db_id)
                return ref_url, prompt
            finally:
                session.close()
        except Exception:
            return None, ""

    @staticmethod
    def reference_audio_path(voice_id: str) -> Optional[str]:
        vid = (voice_id or "").strip()
        if not vid.startswith("custom_"):
            return None
        try:
            from distr.core.db import CustomVoice, get_session

            db_id = int(vid.split("_", 1)[1])
            session = get_session()
            try:
                cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                if not cv or not cv.audio_dir or not os.path.isdir(cv.audio_dir):
                    return None
                for fname in sorted(os.listdir(cv.audio_dir)):
                    if fname.lower().endswith(".wav"):
                        return os.path.join(cv.audio_dir, fname)
            finally:
                session.close()
        except Exception:
            pass
        return None


def public_custom_voice_reference_url(voice_db_id: int) -> Optional[str]:
    """Fallback when relay staging is unavailable: local serve via DECISIONS_PUBLIC_URL."""
    base = (os.environ.get("DECISIONS_PUBLIC_URL") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/api/custom-voices/{voice_db_id}/reference-audio"


DESCRIPTOR = PixazoDescriptor()
