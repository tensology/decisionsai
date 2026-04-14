"""
F5TTSDescriptor — TTSProviderDescriptor for the F5-TTS (Offline) provider.

Encapsulates ALL F5-TTS-specific logic previously scattered across constants.py,
service_factory.py, session.py, tts_handler.py, and voice_cloning.py.

NOTE: F5-TTS is currently DISABLED (enabled=False) — preserved for future use.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)

# --- F5-TTS defaults ---
DEFAULT_F5TTS_VOICE = "default"
DEFAULT_F5TTS_AGENT = "F5-TTS"


class F5TTSDescriptor(TTSProviderDescriptor):
    """Provider descriptor for F5-TTS (Offline)."""

    # ------------------------------------------------------------------
    # Static configuration
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return "f5tts"

    @property
    def name(self) -> str:
        return "F5-TTS (Offline)"

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        return False

    @property
    def default_voice(self) -> str:
        return DEFAULT_F5TTS_VOICE

    @property
    def settings_key(self) -> str:
        return "f5tts_voice"

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
        """Create an F5TTSTTSService instance.

        Replicates the intended F5-TTS service creation logic.
        Resolves custom voice reference audio from the DB.
        """
        from distr.core.agent.services.tts.f5tts import F5TTSTTSService

        voice_name = tts_config.get('voice_name', DEFAULT_F5TTS_VOICE)
        lo, hi = self.speed_bounds
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))

        # Resolve custom voice reference audio
        ref_audio = None
        ref_text = None
        if voice_name and voice_name.startswith('custom_'):
            try:
                from distr.core.db import get_session as _gs, CustomVoice as _CV
                _db_id = int(voice_name.split('_', 1)[1])
                _sess = _gs()
                try:
                    _cv = _sess.query(_CV).filter(
                        _CV.id == _db_id, _CV.provider == 'f5tts', _CV.status == 'ready'
                    ).first()
                    if _cv and _cv.audio_dir:
                        for _fn in os.listdir(_cv.audio_dir):
                            if _fn.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac')):
                                ref_audio = os.path.join(_cv.audio_dir, _fn)
                                break
                        ref_text = getattr(_cv, 'system_prompt', None) or None
                finally:
                    _sess.close()
            except Exception:
                pass

        service = F5TTSTTSService(
            voice_name=voice_name,
            reference_audio_path=ref_audio,
            reference_text=ref_text,
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
        """Generate F5-TTS audio and write WAV file.

        Supports voice cloning via reference audio from custom voices.
        Falls back to the default reference audio bundled with f5-tts.
        Replicates _generate_f5tts() from tts_handler.py.
        """
        import numpy as np
        import soundfile as sf

        # Uninstall torchcodec if it somehow got reinstalled — it's broken on macOS with torch 2.x
        try:
            import torchcodec  # noqa: F401
            logger.warning("F5-TTS: torchcodec is installed but broken on this system — ignoring")
        except Exception:
            pass

        try:
            from f5_tts.api import F5TTS
        except ImportError:
            raise ImportError("f5-tts is required. Install with: pip install f5-tts")
        except OSError as e:
            if "torchcodec" in str(e) or "libtorchcodec" in str(e):
                raise RuntimeError(
                    "torchcodec is causing a conflict. Run: pip uninstall torchcodec -y"
                ) from e
            raise

        # Resolve reference audio and text
        ref_audio = None
        ref_text = None

        if voice and voice.startswith("custom_"):
            try:
                from distr.core.db import get_session, CustomVoice
                db_id = int(voice.split("_", 1)[1])
                session = get_session()
                try:
                    cv = session.query(CustomVoice).filter(
                        CustomVoice.id == db_id, CustomVoice.provider == "f5tts", CustomVoice.status == "ready"
                    ).first()
                    if cv and cv.audio_dir and os.path.isdir(cv.audio_dir):
                        for fname in os.listdir(cv.audio_dir):
                            if fname.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac')):
                                ref_audio = os.path.join(cv.audio_dir, fname)
                                break
                        ref_text = getattr(cv, 'system_prompt', None) or None
                finally:
                    session.close()
            except Exception as e:
                logger.warning("Failed to resolve F5-TTS custom voice %s: %s", voice, e)
            if not ref_audio:
                raise FileNotFoundError(f"No reference audio found for custom voice {voice}")

        # Fall back to default reference audio bundled with f5-tts
        if not ref_audio:
            try:
                import f5_tts
                pkg_dir = os.path.dirname(f5_tts.__file__)
                candidate = os.path.join(pkg_dir, "infer", "examples", "basic", "basic_ref_en.wav")
                if os.path.isfile(candidate):
                    ref_audio = candidate
                    ref_text = "Some call me nature, others call me mother nature."
            except Exception:
                pass

        if not ref_audio:
            raise FileNotFoundError(
                "F5-TTS requires a reference audio file. "
                "Upload a custom voice or ensure f5-tts is installed with its example files."
            )

        clamped_speed = max(0.5, min(2.0, speed))
        text = re.sub(r'\s+', ' ', text).strip()

        model = F5TTS()
        wav, sr, _ = model.infer(
            ref_file=ref_audio,
            ref_text=ref_text or "",
            gen_text=text,
            speed=clamped_speed,
        )

        audio = np.array(wav, dtype=np.float32)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)

        from distr.core.audio.tts_handler import _resample_audio
        audio, sr = _resample_audio(audio, sr, 48000)
        sf.write(out_file, audio, sr)
        logger.info("Wrote F5-TTS sample to %s", out_file)

    # ------------------------------------------------------------------
    # Voice / display-name resolution
    # ------------------------------------------------------------------

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        """Resolve an F5-TTS voice id to a human-readable display name.

        Replicates the F5-TTS branches from service_factory.resolve_voice_to_display_name()
        and tts_handler._resolve_display_name().
        """
        vm = (voice_id or '').strip()

        # If no voice_id provided, resolve from settings
        if not vm:
            v = (settings or {}).get('f5tts_voice', DEFAULT_F5TTS_VOICE)
            if v and v.startswith('custom_'):
                name = self._resolve_custom_voice_name(v)
                return name if name else DEFAULT_F5TTS_AGENT
            return v.capitalize() if v and v != 'default' else DEFAULT_F5TTS_AGENT

        # Custom voice — look up name from DB
        if vm.startswith('custom_'):
            name = self._resolve_custom_voice_name(vm)
            return name if name else "Custom Voice"

        return vm.capitalize() if vm and vm != 'default' else DEFAULT_F5TTS_AGENT

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        """Normalize a raw voice string into a valid F5-TTS voice id.

        F5-TTS: custom voices pass through, default is "default".
        Replicates the F5-TTS branch from tts_handler._normalize_voice_for_provider().
        """
        raw = (raw_voice or "").strip()

        # Custom cloned voices pass through directly
        if raw.startswith("custom_"):
            return raw

        return raw if raw else DEFAULT_F5TTS_VOICE

    def get_voices(self) -> list[dict]:
        """Return the list of available F5-TTS voices.

        F5-TTS uses reference-based voice cloning — there are no built-in
        voice presets. Returns a single "default" entry.
        """
        return [{"id": "default", "name": "Default"}]

    # ------------------------------------------------------------------
    # Hot-swap support
    # ------------------------------------------------------------------

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        """Return hot-swap configuration for F5-TTS.

        F5-TTS supports in-place reference voice swap (no service replacement
        needed when the existing service is already F5-TTS).
        Replicates the F5-TTS branch from session._hot_swap_tts_service().
        """
        resolved = voice_model or DEFAULT_F5TTS_VOICE

        config: dict[str, Any] = {
            'engine': 'f5tts',
            'voice_name': resolved,
            'in_place': True,
        }

        if resolved.startswith('custom_'):
            ref_path = None
            ref_text = None
            try:
                from distr.core.db import get_session as _gs, CustomVoice as _CV
                _db_id = int(resolved.split('_', 1)[1])
                _sess = _gs()
                try:
                    _cv = _sess.query(_CV).filter(
                        _CV.id == _db_id, _CV.provider == 'f5tts', _CV.status == 'ready'
                    ).first()
                    if _cv and _cv.audio_dir:
                        for _fn in os.listdir(_cv.audio_dir):
                            if _fn.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac')):
                                ref_path = os.path.join(_cv.audio_dir, _fn)
                                break
                        ref_text = getattr(_cv, 'system_prompt', None) or None
                finally:
                    _sess.close()
            except Exception:
                pass
            config['reference_audio_path'] = ref_path
            config['reference_text'] = ref_text
        else:
            # Reset to default reference audio
            try:
                from distr.core.agent.services.tts.f5tts import _get_default_ref_audio, _DEFAULT_REF_TEXT
                config['reference_audio_path'] = _get_default_ref_audio() or ''
                config['reference_text'] = _DEFAULT_REF_TEXT
            except Exception:
                config['reference_audio_path'] = ''
                config['reference_text'] = ''

        return config

    # ------------------------------------------------------------------
    # Voice settings entry
    # ------------------------------------------------------------------

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        """Return the _VOICE_SETTINGS tuple for F5-TTS.

        Replicates _VOICE_SETTINGS['f5tts'] from session.py.
        """
        return ('f5tts', 'f5tts_voice', 'default', {})

    # ------------------------------------------------------------------
    # Telegram integration
    # ------------------------------------------------------------------

    def get_telegram_voice_id(self, settings: dict) -> str:
        """Resolve the F5-TTS voice id from settings for Telegram.

        F5-TTS was not previously wired into events._telegram_resolve_voice_settings(),
        so this provides the correct implementation.
        """
        return settings.get('f5tts_voice', DEFAULT_F5TTS_VOICE)

    # ------------------------------------------------------------------
    # Provider name normalization
    # ------------------------------------------------------------------

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        """Check if *raw* matches F5-TTS and return 'f5tts', or None.

        Replicates the F5-TTS branch from constants.normalize_voice_provider().
        """
        v = (raw or '').strip().lower()
        if 'f5tts' in v or 'f5-tts' in v or 'f5 tts' in v:
            return 'f5tts'
        return None

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Register F5-TTS custom voice for voice cloning.

        F5-TTS uses a reference audio clip directly — no training needed.
        Converts any non-WAV files to WAV using pydub (ffmpeg backend).
        Replicates _clone_f5tts() from voice_cloning.py.
        """
        from pydub import AudioSegment

        _NATIVE_EXTS = {'.wav', '.flac', '.ogg'}

        for fpath in audio_files:
            ext = os.path.splitext(fpath)[1].lower()
            if ext not in _NATIVE_EXTS:
                wav_path = os.path.splitext(fpath)[0] + '.wav'
                logger.info("F5-TTS clone: converting %s -> %s", os.path.basename(fpath), os.path.basename(wav_path))
                audio_seg = AudioSegment.from_file(fpath)
                # F5-TTS works best with 24kHz mono 16-bit WAV
                audio_seg = audio_seg.set_channels(1).set_frame_rate(24000).set_sample_width(2)
                audio_seg.export(wav_path, format='wav')
                try:
                    os.remove(fpath)
                except OSError:
                    pass

        voice.provider_voice_id = f"custom_{voice.id}"
        voice.status = "ready"
        session.commit()
        logger.info("F5-TTS custom voice registered: %s -> %s", voice.name, voice.provider_voice_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
DESCRIPTOR = F5TTSDescriptor()
