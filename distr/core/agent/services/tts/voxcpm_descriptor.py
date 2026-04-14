"""
VoxCPMDescriptor — TTSProviderDescriptor for the VoxCPM (Offline) TTS provider.

Encapsulates ALL VoxCPM-specific logic previously scattered across constants.py,
service_factory.py, session.py, tts_handler.py, events.py, and voice_cloning.py.

NOTE: VoxCPM is currently DISABLED (enabled=False) — requires CUDA; MPS crashes, CPU too slow.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)

# --- VoxCPM defaults ---
DEFAULT_VOXCPM_VOICE = "default"
DEFAULT_VOXCPM_AGENT = "VoxCPM"


class VoxCPMDescriptor(TTSProviderDescriptor):
    """Provider descriptor for VoxCPM (Offline)."""

    # ------------------------------------------------------------------
    # Static configuration
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return "voxcpm"

    @property
    def name(self) -> str:
        return "VoxCPM (Offline)"

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        return False  # Requires CUDA — MPS crashes, CPU too slow

    @property
    def default_voice(self) -> str:
        return DEFAULT_VOXCPM_VOICE

    @property
    def settings_key(self) -> str:
        return "voxcpm_voice"

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
        """Create a VoxCPMTTSService instance.

        Replicates the VoxCPM branch from service_factory.create_tts_service()
        and session._create_services().
        """
        from distr.core.agent.services import VoxCPMTTSService

        if not VoxCPMTTSService:
            raise ImportError("VoxCPMTTSService is not available. Install with: pip install voxcpm")

        voice_name = tts_config.get('voice_name', DEFAULT_VOXCPM_VOICE)
        lo, hi = self.speed_bounds
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))

        # Resolve reference audio for voice cloning
        ref_audio = None
        ref_text = None
        if voice_name and voice_name.startswith('custom_'):
            try:
                from distr.core.db import get_session as _gs, CustomVoice as _CV
                _db_id = int(voice_name.split('_', 1)[1])
                _sess = _gs()
                try:
                    _cv = _sess.query(_CV).filter(
                        _CV.id == _db_id, _CV.provider == 'voxcpm', _CV.status == 'ready'
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

        service = VoxCPMTTSService(
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
        """Generate VoxCPM TTS audio and write WAV file.

        Uses VoxCPM-0.5B with reduced inference steps for fast previews on CPU.
        The full VoxCPM2 model is used only by the live pipeline (VoxCPMTTSService).
        Replicates _generate_voxcpm() from tts_handler.py.
        """
        import numpy as np
        import platform as _plat
        import soundfile as sf

        if _plat.system() == "Darwin":
            os.environ.setdefault("VOXCPM_DEVICE", "cpu")

        try:
            from voxcpm import VoxCPM  # noqa: F811
        except ImportError:
            raise ImportError("voxcpm is required. Install with: pip install voxcpm")

        # Resolve reference audio and text for custom voices
        ref_audio = None
        ref_text = None

        if voice and voice.startswith("custom_"):
            try:
                from distr.core.db import get_session, CustomVoice
                db_id = int(voice.split("_", 1)[1])
                session = get_session()
                try:
                    cv = session.query(CustomVoice).filter(
                        CustomVoice.id == db_id, CustomVoice.provider == "voxcpm", CustomVoice.status == "ready"
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
                logger.warning("Failed to resolve VoxCPM custom voice %s: %s", voice, e)
            if not ref_audio:
                raise FileNotFoundError(f"No reference audio found for custom voice {voice}")

        text = re.sub(r'\s+', ' ', text).strip()

        # Use the lighter 0.5B model for previews — cached after first load
        from distr.core.agent.services.tts.voxcpm import get_or_load_model
        model = get_or_load_model("openbmb/VoxCPM-0.5B")

        gen_kwargs: dict[str, Any] = {
            "text": text,
            "cfg_value": 2.0,
            "inference_timesteps": 3,
        }

        if ref_audio and os.path.isfile(ref_audio):
            import torch as _t
            if _t.cuda.is_available():
                # VoxCPM2: supports reference_wav_path
                if ref_text:
                    gen_kwargs["prompt_wav_path"] = ref_audio
                    gen_kwargs["prompt_text"] = ref_text
                    gen_kwargs["reference_wav_path"] = ref_audio
                else:
                    gen_kwargs["reference_wav_path"] = ref_audio
            else:
                # VoxCPM-0.5B: only prompt_wav_path + prompt_text
                gen_kwargs["prompt_wav_path"] = ref_audio
                gen_kwargs["prompt_text"] = ref_text or ""

        wav = model.generate(**gen_kwargs)

        if wav is None or len(wav) == 0:
            raise ValueError("VoxCPM returned no audio")

        audio = np.array(wav, dtype=np.float32)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)

        sr = model.tts_model.sample_rate if hasattr(model, 'tts_model') else 16000
        from distr.core.audio.tts_handler import _resample_audio
        audio, sr = _resample_audio(audio, sr, 48000)
        sf.write(out_file, audio, sr)
        logger.info("Wrote VoxCPM sample to %s", out_file)

    # ------------------------------------------------------------------
    # Voice / display-name resolution
    # ------------------------------------------------------------------

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        """Resolve a VoxCPM voice id to a human-readable display name.

        Replicates the VoxCPM branches from service_factory.resolve_voice_to_display_name()
        and tts_handler._resolve_display_name().
        """
        vm = (voice_id or '').strip()

        # If no voice_id provided, resolve from settings
        if not vm:
            v = (settings or {}).get('voxcpm_voice', DEFAULT_VOXCPM_VOICE)
            if v and v.startswith('custom_'):
                name = self._resolve_custom_voice_name(v)
                return name if name else DEFAULT_VOXCPM_AGENT
            return v.capitalize() if v and v != 'default' else DEFAULT_VOXCPM_AGENT

        # Custom voice — look up name from DB
        if vm.startswith('custom_'):
            name = self._resolve_custom_voice_name(vm)
            return name if name else "Custom Voice"

        return vm.capitalize() if vm and vm != 'default' else DEFAULT_VOXCPM_AGENT

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        """Normalize a raw voice string into a valid VoxCPM voice id.

        VoxCPM: custom voices pass through, default is "default".
        Replicates the VoxCPM branch from tts_handler._normalize_voice_for_provider().
        """
        raw = (raw_voice or "").strip()

        # Custom cloned voices pass through directly
        if raw.startswith("custom_"):
            return raw

        return raw if raw else DEFAULT_VOXCPM_VOICE

    def get_voices(self) -> list[dict]:
        """Return the list of available VoxCPM voices.

        VoxCPM uses reference-based voice cloning — there are no built-in
        voice presets. Returns a single "default" entry.
        """
        return [{"id": "default", "name": "Default"}]

    # ------------------------------------------------------------------
    # Hot-swap support
    # ------------------------------------------------------------------

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        """Return hot-swap configuration for VoxCPM.

        VoxCPM supports in-place reference voice swap (no service replacement
        needed when the existing service is already VoxCPM).
        Replicates the VoxCPM branch from session._hot_swap_tts_service().
        """
        resolved = voice_model or DEFAULT_VOXCPM_VOICE

        config: dict[str, Any] = {
            'engine': 'voxcpm',
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
                        _CV.id == _db_id, _CV.provider == 'voxcpm', _CV.status == 'ready'
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
            # Reset to no reference voice (default VoxCPM voice)
            config['reference_audio_path'] = None
            config['reference_text'] = None

        return config

    # ------------------------------------------------------------------
    # Voice settings entry
    # ------------------------------------------------------------------

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        """Return the _VOICE_SETTINGS tuple for VoxCPM.

        Replicates _VOICE_SETTINGS['voxcpm'] from session.py.
        """
        return ('voxcpm', 'voxcpm_voice', 'default', {})

    # ------------------------------------------------------------------
    # Telegram integration
    # ------------------------------------------------------------------

    def get_telegram_voice_id(self, settings: dict) -> str:
        """Resolve the VoxCPM voice id from settings for Telegram.

        Replicates the VoxCPM branch from events._telegram_resolve_voice_settings().
        """
        return settings.get('voxcpm_voice', DEFAULT_VOXCPM_VOICE)

    # ------------------------------------------------------------------
    # Provider name normalization
    # ------------------------------------------------------------------

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        """Check if *raw* matches VoxCPM and return 'voxcpm', or None.

        Replicates the VoxCPM branch from constants.normalize_voice_provider().
        """
        v = (raw or '').strip().lower()
        if 'voxcpm' in v or 'vox cpm' in v:
            return 'voxcpm'
        return None

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Register VoxCPM custom voice for voice cloning.

        VoxCPM uses a reference audio clip directly for zero-shot cloning — no training needed.
        Converts any non-WAV files to WAV using pydub (ffmpeg backend).
        VoxCPM accepts 16kHz reference audio and outputs 48kHz.
        Replicates _clone_voxcpm() from voice_cloning.py.
        """
        from pydub import AudioSegment

        _NATIVE_EXTS = {'.wav', '.flac', '.ogg'}

        for fpath in audio_files:
            ext = os.path.splitext(fpath)[1].lower()
            if ext not in _NATIVE_EXTS:
                wav_path = os.path.splitext(fpath)[0] + '.wav'
                logger.info("VoxCPM clone: converting %s -> %s", os.path.basename(fpath), os.path.basename(wav_path))
                audio_seg = AudioSegment.from_file(fpath)
                # VoxCPM accepts 16kHz reference audio
                audio_seg = audio_seg.set_channels(1).set_frame_rate(16000).set_sample_width(2)
                audio_seg.export(wav_path, format='wav')
                try:
                    os.remove(fpath)
                except OSError:
                    pass

        voice.provider_voice_id = f"custom_{voice.id}"
        voice.status = "ready"
        session.commit()
        logger.info("VoxCPM custom voice registered: %s -> %s", voice.name, voice.provider_voice_id)

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
DESCRIPTOR = VoxCPMDescriptor()
