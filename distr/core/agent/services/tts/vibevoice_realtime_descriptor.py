"""
VibeVoiceRealtimeDescriptor — TTSProviderDescriptor for Microsoft VibeVoice Realtime (local).

Requires: ``pip install`` the ``vibevoice`` package from GitHub and clone of the repo for
``demo/voices/streaming_model/*.pt``. See ``vibevoice_runtime.py`` and ``scripts/install_vibevoice.sh``.

Upstream: https://github.com/microsoft/VibeVoice
"""

from __future__ import annotations

import glob
import logging
import os
import re
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor
from distr.core.agent.services.tts.vibevoice_runtime import (
    DEFAULT_VIBEVOICE_REALTIME_VOICES,
    streaming_voices_dir,
    vibevoice_tts_runtime_ready,
)

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-carter_man"


class VibeVoiceRealtimeDescriptor(TTSProviderDescriptor):
    """Provider descriptor for VibeVoice Realtime 0.5B (offline / local HF)."""

    @property
    def id(self) -> str:
        return "vibevoice_realtime"

    @property
    def name(self) -> str:
        return "VibeVoice Realtime (Local)"

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        return vibevoice_tts_runtime_ready()

    @property
    def default_voice(self) -> str:
        return DEFAULT_VOICE

    @property
    def settings_key(self) -> str:
        return "vibevoice_realtime_voice"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.5, 2.0)

    @property
    def supports_custom_voices(self) -> bool:
        return False

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
        from distr.core.agent.services.tts.vibevoice_realtime import VibeVoiceRealtimeTTSService

        voice_id = (tts_config.get("voice_id") or settings.get(self.settings_key) or DEFAULT_VOICE).strip()
        device = (tts_config.get("device") or settings.get("vibevoice_device") or "").strip() or None
        lo, hi = self.speed_bounds
        playback_speed = max(lo, min(hi, float(settings.get("playback_speed", 1.0))))
        speech_volume = int(settings.get("speech_volume", 100) or 100)
        return VibeVoiceRealtimeTTSService(
            voice_id=voice_id,
            device=device,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get("_event_queue"),
            speech_volume=speech_volume,
        )

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        from distr.core.audio.tts_handler import _resample_audio

        voice = self.normalize_voice(voice, {})
        pt = self._voice_pt_path(voice)
        from distr.core.agent.services.tts.vibevoice_streaming_inference import synthesize_streaming_wav

        synthesize_streaming_wav(text, pt, out_file, cfg_scale=1.5)
        try:
            import numpy as np
            import soundfile as sf

            data, sr = sf.read(out_file, dtype="float32", always_2d=False)
            if data.ndim > 1:
                data = np.mean(data, axis=1)
            data, sr = _resample_audio(data.astype("float32"), int(sr), 48000)
            sf.write(out_file, data, sr)
        except Exception as res_e:
            logger.warning("VibeVoice Realtime: resample to 48k failed (%s); leaving native wav", res_e)

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        vm = (voice_id or "").strip()
        if voice_name and voice_name.strip() and "_" not in voice_name:
            return voice_name.strip().lstrip("⭐").strip()
        for vid, label in self._voices_list():
            if vid == vm.lower():
                return label.split("(")[0].strip()
        return vm or "VibeVoice"

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        raw = (raw_voice or "").strip().lower().replace(".pt", "")
        if raw:
            for vid, _ in self._voices_list():
                if vid == raw:
                    return vid
        configured = (settings.get(self.settings_key) or "").strip().lower().replace(".pt", "")
        if configured:
            for vid, _ in self._voices_list():
                if vid == configured:
                    return vid
        return DEFAULT_VOICE

    def get_voices(self) -> list[dict]:
        return [{"id": vid, "name": name} for vid, name in self._voices_list()]

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        resolved = self.normalize_voice(voice_model or "", settings)
        return {
            "engine": self.id,
            "voice_id": resolved,
            "voice_name": resolved,
            "device": settings.get("vibevoice_device") or None,
            "in_place": True,
        }

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        return (self.id, self.settings_key, DEFAULT_VOICE, {"device": "vibevoice_device"})

    def get_telegram_voice_id(self, settings: dict) -> str:
        return self.normalize_voice("", settings)

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        v = (raw or "").strip().lower()
        if "vibevoice" in v and "realtime" in v:
            return self.id
        if v in (self.id, "vibevoice-realtime", "vibevoicerealtime"):
            return self.id
        if "vibevoice" in v and "tts" in v:
            return self.id
        return None

    @staticmethod
    def _voices_list() -> list[tuple[str, str]]:
        d = streaming_voices_dir()
        out: list[tuple[str, str]] = []
        if d and os.path.isdir(d):
            for path in sorted(glob.glob(os.path.join(d, "*.pt"))):
                stem = os.path.splitext(os.path.basename(path))[0].lower()
                pretty = re.sub(r"^en-", "", stem).replace("_", " ").title()
                out.append((stem, pretty))
            if out:
                return out
        return list(DEFAULT_VIBEVOICE_REALTIME_VOICES)

    @staticmethod
    def _voice_pt_path(voice_id: str) -> str:
        vid = (voice_id or "").strip().lower().replace(".pt", "")
        d = streaming_voices_dir()
        if not d:
            raise ValueError(
                "VibeVoice: set environment variable DECISIONSAI_VIBEVOICE_ROOT to your local "
                "clone of https://github.com/microsoft/VibeVoice (must contain demo/voices/streaming_model/). "
                "Run: ./scripts/install_vibevoice.sh"
            )
        path = os.path.join(d, f"{vid}.pt")
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"VibeVoice: no speaker preset at {path}. Re-run install script or pick another voice."
            )
        return os.path.abspath(path)


DESCRIPTOR = VibeVoiceRealtimeDescriptor()
