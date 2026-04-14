"""
CoquiDescriptor — TTSProviderDescriptor for the Coqui TTS (Offline) provider.

Encapsulates ALL Coqui-specific logic previously scattered across constants.py,
service_factory.py, session.py, tts_handler.py, events.py, and voice_cloning.py.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)

# --- Coqui defaults (moved from constants.py) ---
DEFAULT_COQUI_VOICE = "p225"
DEFAULT_COQUI_AGENT = "Sarah"

# Coqui VCTK voices — loaded from data/coqui-voices.json
_COQUI_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', 'data', 'coqui-voices.json',
)
try:
    with open(_COQUI_JSON, 'r') as _f:
        _coqui_raw = json.load(_f)
    COQUI_VOICES: dict[str, str] = {
        v["id"]: v["name"] for v in _coqui_raw if v.get("id") and v.get("name")
    }
except Exception:
    COQUI_VOICES = {"p225": "Sarah", "p226": "Tim", "p227": "Isabelle"}


class CoquiDescriptor(TTSProviderDescriptor):
    """Provider descriptor for Coqui TTS (Offline)."""

    # ------------------------------------------------------------------
    # Static configuration
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return "coqui"

    @property
    def name(self) -> str:
        return "Coqui TTS (Offline)"

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def default_voice(self) -> str:
        return DEFAULT_COQUI_VOICE

    @property
    def settings_key(self) -> str:
        return "coqui_voice"

    @property
    def sample_rate(self) -> int:
        return 22050

    @property
    def speed_bounds(self) -> tuple[float, float]:
        return (0.5, 2.0)

    @property
    def supports_custom_voices(self) -> bool:
        return True

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
        """Create a CoquiTTSService instance.

        Replicates the Coqui branch from service_factory.create_tts_service()
        and session._create_services().
        """
        try:
            from distr.core.agent.services import CoquiTTSService
        except ImportError:
            CoquiTTSService = None

        if not CoquiTTSService:
            raise ImportError("CoquiTTSService is not available. Install with: pip install TTS")

        voice_id = tts_config.get('voice_id', DEFAULT_COQUI_VOICE)
        voice_name = tts_config.get('voice_name') or voice_id
        device = tts_config.get('device') or settings.get('coqui_device') or None

        lo, hi = self.speed_bounds
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))

        service = CoquiTTSService(
            voice_id=voice_id,
            voice_name=voice_name,
            device=device,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
        )
        service.set_hands_free(is_hands_free)
        return service

    # ------------------------------------------------------------------
    # Audio generation
    # ------------------------------------------------------------------

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        """Generate Coqui TTS audio and write WAV file.

        Uses XTTS v2 for custom voices (voice cloning from reference audio).
        Uses VCTK VITS for built-in speaker IDs (p225, p226, etc.).
        Replicates _generate_coqui() from tts_handler.py.
        """
        import numpy as np
        import soundfile as sf

        try:
            from TTS.api import TTS as CoquiTTS
        except ImportError:
            raise ImportError("TTS package is required for Coqui TTS. Install with: pip install TTS")

        # Custom voice — use XTTS v2 with reference audio
        if voice and voice.startswith("custom_"):
            ref_path = self._resolve_reference_audio(voice)
            if ref_path:
                tts = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
                wav = tts.tts(text=text, speaker_wav=ref_path, language="en")
                audio = np.array(wav, dtype=np.float32)
                if audio.ndim > 1:
                    audio = np.mean(audio, axis=1)
                sr = tts.synthesizer.output_sample_rate
                from distr.core.audio.tts_handler import _resample_audio
                audio, sr = _resample_audio(audio, sr, 48000)
                sf.write(out_file, audio, sr)
                logger.info("Wrote Coqui XTTS cloned voice sample to %s", out_file)
                return
            else:
                logger.warning("Coqui clone: no reference audio found for %s, falling back to VCTK", voice)
                voice = DEFAULT_COQUI_VOICE

        # Built-in VCTK speaker
        tts = CoquiTTS("tts_models/en/vctk/vits", gpu=False)
        wav = tts.tts(text=text, speaker=voice)
        audio = np.array(wav, dtype=np.float32)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        sr = tts.synthesizer.output_sample_rate
        from distr.core.audio.tts_handler import _resample_audio
        audio, sr = _resample_audio(audio, sr, 48000)
        sf.write(out_file, audio, sr)
        logger.info("Wrote Coqui TTS sample to %s", out_file)

    # ------------------------------------------------------------------
    # Voice / display-name resolution
    # ------------------------------------------------------------------

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        """Resolve a Coqui voice id to a human-readable display name.

        Replicates the Coqui branches from service_factory.resolve_voice_to_display_name()
        and tts_handler._resolve_display_name().
        """
        vm = (voice_id or '').strip()

        # If no voice_id provided, resolve from settings
        if not vm:
            v = (settings or {}).get('coqui_voice', DEFAULT_COQUI_VOICE)
            if v and v.startswith('custom_'):
                name = self._resolve_custom_voice_name(v)
                return name if name else DEFAULT_COQUI_AGENT
            return COQUI_VOICES.get(v, v) if v else DEFAULT_COQUI_AGENT

        # Custom voice — look up name from DB
        if vm.startswith('custom_'):
            name = self._resolve_custom_voice_name(vm)
            return name if name else DEFAULT_COQUI_AGENT

        return COQUI_VOICES.get(vm, vm) if vm else DEFAULT_COQUI_AGENT

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        """Normalize a raw voice string into a valid Coqui voice id.

        Validates speaker ID against known VCTK voices, passes through custom_ voices.
        Replicates the Coqui branch from tts_handler._normalize_voice_for_provider().
        """
        raw = (raw_voice or "").strip()

        # Custom cloned voices pass through directly
        if raw.startswith("custom_"):
            return raw

        if raw in COQUI_VOICES:
            return raw

        configured = (settings.get("coqui_voice") or "").strip()
        if configured.startswith("custom_"):
            return configured
        if configured in COQUI_VOICES:
            return configured

        return DEFAULT_COQUI_VOICE

    def get_voices(self) -> list[dict]:
        """Return the list of available Coqui VCTK voices."""
        return [{"id": vid, "name": name} for vid, name in COQUI_VOICES.items()]

    # ------------------------------------------------------------------
    # Hot-swap support
    # ------------------------------------------------------------------

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        """Return hot-swap configuration for Coqui.

        Coqui requires full service replacement (VCTK ↔ XTTS are different models).
        Replicates the Coqui branch from session._hot_swap_tts_service().
        """
        resolved = voice_model or DEFAULT_COQUI_VOICE
        return {
            'engine': 'coqui',
            'voice_id': resolved,
            'voice_name': resolved,
            'device': settings.get('coqui_device') or None,
            'in_place': False,
        }

    # ------------------------------------------------------------------
    # Voice settings entry
    # ------------------------------------------------------------------

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        """Return the _VOICE_SETTINGS tuple for Coqui.

        Replicates _VOICE_SETTINGS['coqui'] from session.py.
        """
        return ('coqui', 'coqui_voice', DEFAULT_COQUI_VOICE, {'device': 'coqui_device'})

    # ------------------------------------------------------------------
    # Telegram integration
    # ------------------------------------------------------------------

    def get_telegram_voice_id(self, settings: dict) -> str:
        """Resolve the Coqui voice id from settings for Telegram.

        Replicates the Coqui branch from events._telegram_resolve_voice_settings().
        """
        return settings.get('coqui_voice', DEFAULT_COQUI_VOICE)

    # ------------------------------------------------------------------
    # Provider name normalization
    # ------------------------------------------------------------------

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        """Check if *raw* matches Coqui and return 'coqui', or None.

        Replicates the Coqui branch from constants.normalize_voice_provider().
        """
        v = (raw or '').strip().lower()
        if 'coqui' in v:
            return 'coqui'
        return None

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Register Coqui XTTS v2 custom voice for zero-shot voice cloning.

        XTTS v2 clones from a reference audio clip at inference time — no training
        needed. The reference audio should be 6-15 seconds of clean speech.
        Converts any non-WAV files to WAV using pydub (ffmpeg backend).
        Replicates _clone_coqui() from voice_cloning.py.
        """
        from pydub import AudioSegment

        _NATIVE_EXTS = {'.wav', '.flac', '.ogg'}

        for fpath in audio_files:
            ext = os.path.splitext(fpath)[1].lower()
            if ext not in _NATIVE_EXTS:
                wav_path = os.path.splitext(fpath)[0] + '.wav'
                logger.info("Coqui clone: converting %s -> %s", os.path.basename(fpath), os.path.basename(wav_path))
                audio_seg = AudioSegment.from_file(fpath)
                # XTTS v2 works best with 22050Hz mono 16-bit WAV
                audio_seg = audio_seg.set_channels(1).set_frame_rate(22050).set_sample_width(2)
                audio_seg.export(wav_path, format='wav')
                try:
                    os.remove(fpath)
                except OSError:
                    pass

        voice.provider_voice_id = f"custom_{voice.id}"
        voice.status = "ready"
        session.commit()
        logger.info("Coqui XTTS custom voice registered: %s -> %s", voice.name, voice.provider_voice_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_reference_audio(voice_id: str) -> Optional[str]:
        """Find the reference audio file for a Coqui custom voice."""
        try:
            from distr.core.db import get_session, CustomVoice
            db_id = int(voice_id.split("_", 1)[1])
            with get_session() as session:
                cv = session.query(CustomVoice).filter(
                    CustomVoice.id == db_id,
                    CustomVoice.provider == "coqui",
                    CustomVoice.status == "ready",
                ).first()
                if cv and cv.audio_dir:
                    for fn in os.listdir(cv.audio_dir):
                        if fn.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm')):
                            return os.path.join(cv.audio_dir, fn)
        except Exception as e:
            logger.warning("Could not resolve Coqui reference audio for %s: %s", voice_id, e)
        return None

    @staticmethod
    def _resolve_custom_voice_name(voice_id: str) -> Optional[str]:
        """Resolve a custom voice id (custom_N) to its display name from DB."""
        try:
            from distr.core.db import get_session, CustomVoice
            db_id = int(voice_id.split('_', 1)[1])
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


# Module-level singleton for auto-discovery by the registry
DESCRIPTOR = CoquiDescriptor()
