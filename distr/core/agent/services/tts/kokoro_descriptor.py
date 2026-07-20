"""
KokoroDescriptor — TTSProviderDescriptor for the Kokoro (Offline) TTS provider.

Encapsulates ALL Kokoro-specific logic previously scattered across constants.py,
service_factory.py, session.py, tts_handler.py, events.py, and voice_cloning.py.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from typing import Any, Optional

from distr.core.agent.services.tts.provider_descriptor import TTSProviderDescriptor

logger = logging.getLogger(__name__)

# --- Kokoro voice map (moved from constants.py) ---
KOKORO_VOICES = {
    "af_heart": "Heart",
    "af_alloy": "Alloy",
    "af_aoede": "Aoede",
    "af_bella": "Bella",
    "af_jessica": "Jessica",
    "af_kore": "Kore",
    "af_nicole": "Nicole",
    "af_nova": "Nova",
    "af_river": "River",
    "af_sarah": "Sarah",
    "af_sky": "Sky",
    "am_adam": "Adam",
    "am_echo": "Echo",
    "am_eric": "Eric",
    "am_fenrir": "Fenrir",
    "am_liam": "Liam",
    "am_michael": "Michael",
    "am_onyx": "Onyx",
    "am_puck": "Puck",
    "am_santa": "Santa",
}

KOKORO_VOICE_BY_DISPLAY_NAME = {v: k for k, v in KOKORO_VOICES.items()}

DEFAULT_KOKORO_VOICE = "af_heart"
DEFAULT_KOKORO_AGENT = "Heart"


def _phonemizer_safe_text(text: str) -> str:
    """Collapse line/control characters that can desynchronise espeak output."""
    cleaned = []
    for char in str(text or ""):
        category = unicodedata.category(char)
        cleaned.append(" " if category in {"Cc", "Cf", "Zl", "Zp"} else char)
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()


def _split_text_for_kokoro(text: str, max_chars: int = 390) -> list[str]:
    """Split text into chunks that Kokoro can synthesize reliably."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return []

    sentence_parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]
    if not sentence_parts:
        sentence_parts = [clean]

    chunks: list[str] = []
    for sentence in sentence_parts:
        remaining = sentence
        while remaining:
            if len(remaining) <= max_chars:
                chunks.append(remaining.strip())
                break

            window = remaining[:max_chars]
            split_pos = max(window.rfind(","), window.rfind(";"), window.rfind(" - "), window.rfind(" "))
            if split_pos <= 0:
                split_pos = max_chars
            chunk = remaining[:split_pos].strip()
            if not chunk:
                chunk = remaining[:max_chars].strip()
            chunks.append(chunk)
            remaining = remaining[len(chunk):].strip(" ,;-")

    return [chunk for chunk in chunks if chunk]


class KokoroDescriptor(TTSProviderDescriptor):
    """Provider descriptor for Kokoro (Offline) TTS."""

    # ------------------------------------------------------------------
    # Static configuration
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return "kokoro"

    @property
    def name(self) -> str:
        return "Kokoro (Offline)"

    @property
    def type(self) -> str:
        return "offline"

    @property
    def enabled(self) -> bool:
        return True

    @property
    def default_voice(self) -> str:
        return DEFAULT_KOKORO_VOICE

    @property
    def settings_key(self) -> str:
        return "kokoro_voice"

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
        """Create a KokoroTTSService instance.

        Replicates the Kokoro branch from service_factory.create_tts_service()
        and session._create_services().
        """
        from distr.core.agent.constants import KOKORO_MODEL_FILE, KOKORO_VOICES_FILE
        from distr.core.agent.services import KokoroTTSService

        kokoro_model = os.path.join(models_dir, KOKORO_MODEL_FILE)
        kokoro_voices = os.path.join(models_dir, KOKORO_VOICES_FILE)
        if not os.path.exists(kokoro_model):
            raise FileNotFoundError(f"Kokoro model not found at {kokoro_model}")

        lo, hi = self.speed_bounds
        playback_speed = max(lo, min(hi, settings.get('playback_speed', 1.0)))

        # Resolve custom voice reference clip for Kanade voice cloning
        _ref_path = None
        _voice_name = tts_config['voice_name']
        if _voice_name and _voice_name.startswith('custom_'):
            _ref_path = self._resolve_custom_voice_ref(_voice_name)
            _voice_name = 'af_heart'  # good base voice for cloning

        service = KokoroTTSService(
            model_path=kokoro_model,
            voices_path=kokoro_voices,
            voice_name=_voice_name,
            stt_service=stt_service,
            playback_speed=playback_speed,
            event_queue=settings.get('_event_queue'),
            speech_volume=100,
            reference_voice_path=_ref_path,
        )
        service.set_hands_free(is_hands_free)
        return service

    # ------------------------------------------------------------------
    # Audio generation
    # ------------------------------------------------------------------

    def generate_audio(self, text: str, voice: str, speed: float, out_file: str) -> None:
        """Generate Kokoro TTS audio. Applies Kanade voice conversion for custom voices.

        Replicates _generate_kokoro() from tts_handler.py.
        """
        import numpy as np
        from kokoro_onnx import Kokoro
        import soundfile as sf

        # Detect custom voice — resolve reference audio path from DB
        reference_path = None
        base_voice = voice
        if voice.startswith("custom_"):
            try:
                from distr.core.db import get_session, CustomVoice
                db_id = int(voice.split("_", 1)[1])
                session = get_session()
                try:
                    cv = session.query(CustomVoice).filter(CustomVoice.id == db_id).first()
                    if cv and cv.audio_dir and os.path.isdir(cv.audio_dir):
                        for fname in os.listdir(cv.audio_dir):
                            if fname.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm')):
                                reference_path = os.path.join(cv.audio_dir, fname)
                                break
                    gender = getattr(cv, 'gender', 'female') if cv else 'female'
                    base_voice = "am_puck" if gender == "male" else "af_heart"
                finally:
                    session.close()
            except Exception as e:
                logger.warning("Failed to resolve custom voice %s: %s", voice, e)
                base_voice = "af_heart"
            if not reference_path:
                raise FileNotFoundError(f"No reference audio found for custom voice {voice}")
            logger.info("Kokoro custom voice: base=%s, reference=%s", base_voice, os.path.basename(reference_path))

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../../..'))
        models_dir = os.path.join(base_dir, "distr", "core", "agent", "models")
        kokoro_model = os.path.join(models_dir, "kokoro-v1.0.onnx")
        kokoro_voices = os.path.join(models_dir, "voices-v1.0.bin")

        if not os.path.exists(kokoro_model) or not os.path.exists(kokoro_voices):
            raise FileNotFoundError(f"Kokoro model files not found at {models_dir}")

        kokoro = Kokoro(kokoro_model, kokoro_voices)
        clamped_speed = max(0.5, min(2.0, speed))

        # Sanitize text — phonemizer/espeak chokes on embedded newlines
        text = _phonemizer_safe_text(text)

        # Normalize smart quotes for correct pronunciation
        from distr.core.agent.services.tts.kokoro import _normalize_text_for_tts
        text = _phonemizer_safe_text(_normalize_text_for_tts(text))

        chunks = []
        sample_rate = None
        for chunk in _split_text_for_kokoro(text):
            try:
                audio, sr = kokoro.create(chunk, voice=base_voice, speed=clamped_speed)
                generated = [(audio, sr)]
            except RuntimeError as exc:
                if "number of lines in input and output must be equal" not in str(exc).lower():
                    raise
                # Some espeak builds emit spurious line breaks for a long or
                # punctuation-heavy notification. Retry smaller independent
                # clauses so one bad chunk does not drop the Telegram reply.
                retry_chunks = _split_text_for_kokoro(chunk, max_chars=140)
                if retry_chunks == [chunk] and len(chunk) > 40:
                    midpoint = len(chunk) // 2
                    split_at = chunk.rfind(" ", 0, midpoint + 1)
                    split_at = split_at if split_at > 0 else midpoint
                    retry_chunks = [chunk[:split_at].strip(), chunk[split_at:].strip()]
                generated = [
                    kokoro.create(part, voice=base_voice, speed=clamped_speed)
                    for part in retry_chunks
                    if part
                ]
            for audio, sr in generated:
                if audio is not None and len(audio) > 0:
                    chunks.append(audio)
                    sample_rate = sr

        if not chunks:
            raise ValueError("Kokoro returned no audio")

        audio = np.concatenate(chunks)

        # Apply Kanade voice conversion for custom voices
        if reference_path:
            logger.info("Applying Kanade voice conversion (%.1fs of audio)...", len(audio) / sample_rate)
            from distr.core.audio.voice_cloner import convert_voice, get_output_sample_rate
            audio = convert_voice(audio, sample_rate, reference_path)
            sample_rate = get_output_sample_rate()

        from distr.core.audio.tts_handler import _resample_audio
        audio, sample_rate = _resample_audio(audio, sample_rate, 48000)
        sf.write(out_file, audio, sample_rate)
        logger.info("Wrote Kokoro sample to %s", out_file)

    # ------------------------------------------------------------------
    # Voice / display-name resolution
    # ------------------------------------------------------------------

    def resolve_display_name(self, voice_id: str, settings: dict, voice_name: str | None = None) -> str:
        """Resolve a Kokoro voice id to a human-readable display name.

        Replicates the Kokoro branches from service_factory.resolve_voice_to_display_name()
        and tts_handler._resolve_display_name().
        """
        vm = (voice_id or '').strip()

        # If no voice_id provided, resolve from settings
        if not vm:
            v = (settings or {}).get('kokoro_voice', DEFAULT_KOKORO_VOICE)
            if v and v.startswith('custom_'):
                name = self._resolve_custom_voice_name(v)
                return name if name else DEFAULT_KOKORO_AGENT
            return KOKORO_VOICES.get(v, v) if v else DEFAULT_KOKORO_AGENT

        # Custom voice — look up name from DB
        if vm.startswith('custom_'):
            name = self._resolve_custom_voice_name(vm)
            return name if name else DEFAULT_KOKORO_AGENT

        # Display name passed as voice_id (e.g. "Heart" -> "af_heart")
        if vm in KOKORO_VOICE_BY_DISPLAY_NAME:
            vm = KOKORO_VOICE_BY_DISPLAY_NAME[vm]

        return KOKORO_VOICES.get(vm, vm)

    def normalize_voice(self, raw_voice: str, settings: dict) -> str:
        """Normalize a raw voice string into a valid Kokoro voice id.

        Replicates the Kokoro branch from tts_handler._normalize_voice_for_provider().
        """
        raw = (raw_voice or "").strip()

        # Custom cloned voices pass through directly
        if raw.startswith("custom_"):
            return raw

        valid_ids = set(KOKORO_VOICES.keys())
        if raw in valid_ids:
            return raw

        # Try normalization used in UI labels, then fallback
        candidate = raw.lower().replace(" ", "_")
        if candidate in valid_ids:
            return candidate

        configured = (settings.get("kokoro_voice") or "").strip()
        if configured.startswith("custom_"):
            return configured
        if configured in valid_ids:
            return configured

        return DEFAULT_KOKORO_VOICE

    def get_voices(self) -> list[dict]:
        """Return the list of available Kokoro voices.

        Replicates the Kokoro branch from voices._get_voices_for_provider().
        """
        return [{"id": vid, "name": name} for vid, name in KOKORO_VOICES.items()]

    # ------------------------------------------------------------------
    # Hot-swap support
    # ------------------------------------------------------------------

    def get_hot_swap_config(self, voice_model: str, settings: dict) -> dict:
        """Return hot-swap configuration for Kokoro.

        Kokoro supports in-place voice swap (no processor replacement needed).
        Replicates the Kokoro branch from session._hot_swap_tts_service().
        """
        resolved = (voice_model or '').strip()
        if resolved in KOKORO_VOICE_BY_DISPLAY_NAME:
            resolved = KOKORO_VOICE_BY_DISPLAY_NAME[resolved]
        elif resolved not in KOKORO_VOICES and not resolved.startswith('custom_'):
            resolved = resolved or DEFAULT_KOKORO_VOICE

        config: dict[str, Any] = {
            'engine': 'kokoro',
            'voice_name': resolved,
            'in_place': True,
        }

        if resolved.startswith('custom_'):
            ref_path = self._resolve_custom_voice_ref(resolved)
            config['reference_voice_path'] = ref_path
            config['base_voice'] = 'af_heart'

        return config

    # ------------------------------------------------------------------
    # Voice settings entry
    # ------------------------------------------------------------------

    def get_voice_settings_entry(self) -> tuple[str, str, str, dict]:
        """Return the _VOICE_SETTINGS tuple for Kokoro.

        Replicates _VOICE_SETTINGS['kokoro'] from session.py.
        """
        return ('kokoro', 'kokoro_voice', DEFAULT_KOKORO_VOICE, {})

    # ------------------------------------------------------------------
    # Telegram integration
    # ------------------------------------------------------------------

    def get_telegram_voice_id(self, settings: dict) -> str:
        """Resolve the Kokoro voice id from settings for Telegram.

        Replicates the Kokoro branch from events._telegram_resolve_voice_settings().
        """
        return settings.get('kokoro_voice', 'af_heart')

    # ------------------------------------------------------------------
    # Provider name normalization
    # ------------------------------------------------------------------

    def normalize_provider_name(self, raw: str) -> Optional[str]:
        """Check if *raw* matches Kokoro and return 'kokoro', or None.

        Replicates the Kokoro branch from constants.normalize_voice_provider().
        """
        v = (raw or '').strip().lower()
        if 'kokoro' in v:
            return 'kokoro'
        return None

    # ------------------------------------------------------------------
    # Voice cloning
    # ------------------------------------------------------------------

    def clone_voice(self, voice: Any, audio_files: list[str], session: Any) -> None:
        """Register Kokoro custom voice for Kanade voice cloning.

        Converts any non-WAV audio files to WAV using pydub (ffmpeg backend).
        Replicates _clone_kokoro() from voice_cloning.py.
        """
        from pydub import AudioSegment

        _NATIVE_EXTS = {'.wav', '.flac', '.ogg'}

        for fpath in audio_files:
            ext = os.path.splitext(fpath)[1].lower()
            if ext not in _NATIVE_EXTS:
                wav_path = os.path.splitext(fpath)[0] + '.wav'
                logger.info("Kokoro clone: converting %s -> %s", os.path.basename(fpath), os.path.basename(wav_path))
                audio_seg = AudioSegment.from_file(fpath)
                audio_seg = audio_seg.set_channels(1).set_frame_rate(24000).set_sample_width(2)
                audio_seg.export(wav_path, format='wav')
                try:
                    os.remove(fpath)
                except OSError:
                    pass

        voice.provider_voice_id = f"custom_{voice.id}"
        voice.status = "ready"
        session.commit()
        logger.info("Kokoro custom voice registered: %s -> %s (Kanade voice cloning)", voice.name, voice.provider_voice_id)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_custom_voice_ref(voice_id: str) -> Optional[str]:
        """Resolve a custom voice id (custom_N) to its reference audio path."""
        try:
            from distr.core.db import get_session as _gs, CustomVoice as _CV
            _db_id = int(voice_id.split('_', 1)[1])
            _sess = _gs()
            try:
                _cv = _sess.query(_CV).filter(
                    _CV.id == _db_id, _CV.provider == 'kokoro', _CV.status == 'ready'
                ).first()
                if _cv and _cv.audio_dir:
                    for _fn in os.listdir(_cv.audio_dir):
                        if _fn.lower().endswith(('.wav', '.mp3', '.m4a', '.ogg', '.flac', '.webm')):
                            return os.path.join(_cv.audio_dir, _fn)
            finally:
                _sess.close()
        except Exception:
            pass
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
DESCRIPTOR = KokoroDescriptor()
