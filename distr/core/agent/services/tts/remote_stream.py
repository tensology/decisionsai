"""Streaming PCM synthesis for the remote web app.

``open_pcm_stream`` returns mono s16le PCM chunks *as the provider produces
them*, so the first audio leaves the desktop after the first chunk is ready
instead of after the whole reply is synthesised, converted and uploaded.

Per provider:

* Fish Audio  - native: live TTS WebSocket (``iter_pcm_audio``), PCM frames.
* OpenAI      - native: Speech API ``response_format="pcm"`` streamed response.
* ElevenLabs  - native: ``text_to_speech.stream(output_format="pcm_24000")``.
* Kokoro      - sentence level: a cached Kokoro model synthesises one sentence at a time.
* Everything else (Supertonic, Coqui, Pixazo, ...) - sentence level through the
  provider's ``generate_audio`` for one sentence at a time.

Sentence splitting uses ``extract_complete_sentences`` so hostnames such as
``merrypak.co.za`` are never cut at their dots.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

NATIVE = "native"
SENTENCE = "sentence"

_MIN_SENTENCE_CHARS = 24
_MAX_CHUNK_CHARS = 300
_STREAM_TARGET_RATE = 24000


@dataclass
class PcmStream:
    """Mono signed 16-bit little-endian PCM, produced lazily."""

    provider: str
    sample_rate: int
    chunks: Iterator[bytes]
    mode: str = NATIVE
    _close: Callable[[], None] | None = field(default=None, repr=False)

    def close(self) -> None:
        close, self._close = self._close, None
        if close:
            try:
                close()
            except Exception:
                logger.debug("[REMOTE TTS] stream close failed", exc_info=True)
        closer = getattr(self.chunks, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                logger.debug("[REMOTE TTS] chunk iterator close failed", exc_info=True)


# ---------------------------------------------------------------------------
# Text splitting
# ---------------------------------------------------------------------------


def _split_long(piece: str, max_chars: int) -> list[str]:
    out: list[str] = []
    rest = piece.strip()
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max(window.rfind(", "), window.rfind("; "), window.rfind(": "), window.rfind(" "))
        if cut <= 0:
            cut = max_chars
        out.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        out.append(rest)
    return out


def split_for_streaming(text: str, *, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split a reply into sentence-sized synthesis units, in order.

    Very short sentences are merged with the next one so the voice does not
    sound chopped, and very long ones are cut at a clause or word boundary.
    """
    from distr.core.agent.services.tts.sentence_split import extract_complete_sentences

    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return []
    sentences, tail = extract_complete_sentences(clean)
    if tail.strip():
        sentences = list(sentences) + [tail.strip()]
    units: list[str] = []
    carry = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{carry} {sentence}".strip() if carry else sentence
        if len(candidate) < _MIN_SENTENCE_CHARS:
            carry = candidate
            continue
        carry = ""
        units.extend(_split_long(candidate, max_chars))
    if carry:
        if units and len(units[-1]) + 1 + len(carry) <= max_chars:
            units[-1] = f"{units[-1]} {carry}"
        else:
            units.append(carry)
    return units or [clean]


# ---------------------------------------------------------------------------
# PCM helpers
# ---------------------------------------------------------------------------


def _align_pcm(carry: bytearray, data: bytes) -> bytes:
    """Return whole 16-bit samples only; keep a dangling odd byte for later."""
    carry.extend(data)
    usable = len(carry) - (len(carry) % 2)
    if not usable:
        return b""
    out = bytes(carry[:usable])
    del carry[:usable]
    return out


def float_audio_to_pcm16(audio: Any, src_rate: int, target_rate: int = _STREAM_TARGET_RATE) -> tuple[bytes, int]:
    """float32 mono (any rate) -> s16le bytes, downsampled to at most ``target_rate``."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    rate = int(src_rate)
    if rate > target_rate:
        from distr.core.audio.tts_handler import _resample_audio

        arr, rate = _resample_audio(arr, rate, target_rate)
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes(), int(rate)


def _read_wav_pcm16(path: str | Path) -> tuple[bytes, int]:
    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32")
    return float_audio_to_pcm16(audio, int(rate))


def _settings() -> dict:
    from distr.core.settings import load_settings_from_db

    return load_settings_from_db()


# ---------------------------------------------------------------------------
# Native providers
# ---------------------------------------------------------------------------


def _fish_stream(text: str, voice: str, speed: float) -> PcmStream:
    from distr.core.agent.services.tts.fishaudio_client import iter_pcm_audio
    from distr.core.agent.services.tts.fishaudio_config import resolve_fishaudio_tts_model
    from distr.core.agent.services.tts.registry import tts_registry

    settings = _settings()
    api_key = (settings.get("fishaudio_key") or "").strip()
    if not api_key:
        raise ValueError("Fish Audio API key not configured")
    descriptor = tts_registry.get("fishaudio")
    reference_id = descriptor.resolve_reference_id(voice or settings.get("fishaudio_voice"), settings)
    model = resolve_fishaudio_tts_model(settings.get("fishaudio_tts_model"))
    rate = _STREAM_TARGET_RATE

    cancel = threading.Event()
    out: queue.Queue = queue.Queue(maxsize=64)
    done = object()

    def pump() -> None:
        import asyncio

        async def run() -> None:
            agen = iter_pcm_audio(
                api_key, text, reference_id=reference_id, model=model,
                speed=speed, sample_rate=rate, latency="low",
            )
            try:
                async for chunk in agen:
                    if cancel.is_set():
                        break
                    while not cancel.is_set():
                        try:
                            out.put(chunk, timeout=0.2)
                            break
                        except queue.Full:
                            continue
            finally:
                await agen.aclose()

        try:
            asyncio.run(run())
        except Exception as exc:  # noqa: BLE001 - surfaced to the consumer
            out.put(exc)
        finally:
            out.put(done)

    thread = threading.Thread(target=pump, name="fish-tts-stream", daemon=True)
    thread.start()

    def chunks() -> Iterator[bytes]:
        carry = bytearray()
        try:
            while True:
                item = out.get()
                if item is done:
                    return
                if isinstance(item, Exception):
                    raise item
                pcm = _align_pcm(carry, item)
                if pcm:
                    yield pcm
        finally:
            cancel.set()

    return PcmStream("fishaudio", rate, chunks(), NATIVE, cancel.set)


def _openai_stream(text: str, voice: str, speed: float) -> PcmStream:
    from openai import OpenAI

    from distr.core.agent.services.tts.openai_descriptor import (
        OPENAI_TTS_FALLBACK_VOICE,
        _is_voice_compatibility_error,
    )
    from distr.core.agent.services.tts.openai_tts_config import (
        openai_tts_supports_instructions,
        resolve_openai_tts_model_for_voice,
    )

    settings = _settings()
    api_key = settings.get("openai_key", "")
    if not api_key:
        raise ValueError("OpenAI API key not configured")
    client = OpenAI(api_key=api_key)
    model = resolve_openai_tts_model_for_voice(settings.get("openai_tts_model"), voice)

    def open_response(selected_voice: str, unit_text: str):
        kwargs = {
            "model": model, "voice": selected_voice, "input": unit_text,
            "speed": speed, "response_format": "pcm",
        }
        instructions = (settings.get("openai_tts_instructions") or "").strip()
        if instructions and openai_tts_supports_instructions(model):
            kwargs["instructions"] = instructions
        cm = client.audio.speech.with_streaming_response.create(**kwargs)
        return cm, cm.__enter__()

    # OpenAI only starts sending a response once a good part of it is rendered, so
    # one request for the whole reply delays the first audio. Ask for the first
    # sentence on its own (fast), and request the remaining text in parallel
    # batches while the first plays.
    units = _openai_units(text)
    active_voice = voice
    try:
        first_cm, first_response = open_response(active_voice, units[0])
    except Exception as exc:  # noqa: BLE001
        if (voice or "").lower() == OPENAI_TTS_FALLBACK_VOICE or not _is_voice_compatibility_error(exc):
            raise
        logger.warning("OpenAI rejected TTS voice %s; streaming with %s", voice, OPENAI_TTS_FALLBACK_VOICE)
        active_voice = OPENAI_TTS_FALLBACK_VOICE
        first_cm, first_response = open_response(active_voice, units[0])

    cancel = threading.Event()
    later: list[queue.Queue] = []

    def read_unit(response: Any, sink: Callable[[Any], None]) -> None:
        carry = bytearray()
        for piece in response.iter_bytes(chunk_size=4800):
            if cancel.is_set():
                return
            pcm = _align_pcm(carry, piece)
            if pcm:
                sink(pcm)

    def worker(unit_text: str, sink_queue: queue.Queue) -> None:
        cm = None
        try:
            cm, response = open_response(active_voice, unit_text)
            read_unit(response, sink_queue.put)
        except Exception as exc:  # noqa: BLE001 - surfaced to the consumer
            sink_queue.put(exc)
        finally:
            sink_queue.put(None)
            if cm is not None:
                try:
                    cm.__exit__(None, None, None)
                except Exception:
                    pass

    for unit_text in units[1:]:
        q: queue.Queue = queue.Queue()
        later.append(q)
        threading.Thread(target=worker, args=(unit_text, q), name="openai-tts-unit", daemon=True).start()

    def chunks() -> Iterator[bytes]:
        try:
            carry = bytearray()
            try:
                for piece in first_response.iter_bytes(chunk_size=4800):
                    pcm = _align_pcm(carry, piece)
                    if pcm:
                        yield pcm
            finally:
                try:
                    first_cm.__exit__(None, None, None)
                except Exception:
                    pass
            for q in later:
                while True:
                    item = q.get()
                    if item is None:
                        break
                    if isinstance(item, Exception):
                        raise item
                    yield item
        finally:
            cancel.set()

    return PcmStream("openai", 24000, chunks(), NATIVE, cancel.set)


def _openai_units(text: str, *, batch_chars: int = 600) -> list[str]:
    """First sentence alone, then the rest grouped into a few larger requests."""
    units = split_for_streaming(text)
    if len(units) <= 1:
        return units or [text]
    batches: list[str] = []
    current = ""
    for unit in units[1:]:
        if current and len(current) + 1 + len(unit) > batch_chars:
            batches.append(current)
            current = unit
        else:
            current = f"{current} {unit}".strip()
    if current:
        batches.append(current)
    return [units[0], *batches]


def _elevenlabs_stream(text: str, voice: str, speed: float) -> PcmStream | None:
    from distr.core.agent.services.tts.elevenlabs_config import (
        ELEVENLABS_DIALOGUE_MODELS,
        resolve_elevenlabs_tts_model,
    )

    settings = _settings()
    model_id = resolve_elevenlabs_tts_model(settings.get("elevenlabs_tts_model"))
    if model_id in ELEVENLABS_DIALOGUE_MODELS:
        return None  # v3 dialogue models: use sentence-level streaming
    api_key = settings.get("elevenlabs_key", "")
    if not api_key:
        raise ValueError("ElevenLabs API key not configured")
    from elevenlabs import ElevenLabs

    client = ElevenLabs(api_key=api_key)
    voice_id = (voice or "").strip()
    if not voice_id or voice_id.lower() == "default":
        voice_id = (settings.get("elevenlabs_voice") or "").strip()
    if not voice_id or " " in voice_id:  # display names need the voice list
        voices = client.voices.get_all().voices or []
        by_name = {(v.name or "").strip().lower(): v.voice_id for v in voices if getattr(v, "voice_id", None)}
        voice_id = by_name.get(voice_id.lower()) or (voices[0].voice_id if voices else voice_id)
    audio_stream = client.text_to_speech.stream(
        text=text,
        voice_id=voice_id,
        model_id=model_id,
        output_format="pcm_24000",
        voice_settings={
            "stability": float(settings.get("elevenlabs_stability", 0.5)),
            "similarity_boost": float(settings.get("elevenlabs_similarity_boost", 0.6)),
            "style": float(settings.get("elevenlabs_style", 0.25)),
            "use_speaker_boost": bool(settings.get("elevenlabs_use_speaker_boost", True)),
            "speed": max(0.7, min(1.2, float(speed))),
        },
    )

    def chunks() -> Iterator[bytes]:
        carry = bytearray()
        for piece in audio_stream:
            if isinstance(piece, (bytes, bytearray)) and piece:
                pcm = _align_pcm(carry, bytes(piece))
                if pcm:
                    yield pcm

    return PcmStream("elevenlabs", 24000, chunks(), NATIVE)


# ---------------------------------------------------------------------------
# Sentence-level providers
# ---------------------------------------------------------------------------

_kokoro_lock = threading.Lock()
_kokoro_model: Any = None


def _kokoro_instance() -> Any:
    global _kokoro_model
    with _kokoro_lock:
        if _kokoro_model is None:
            from kokoro_onnx import Kokoro

            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.."))
            models_dir = os.path.join(base_dir, "distr", "core", "agent", "models")
            model = os.path.join(models_dir, "kokoro-v1.0.onnx")
            voices = os.path.join(models_dir, "voices-v1.0.bin")
            if not (os.path.exists(model) and os.path.exists(voices)):
                from distr.core.paths import user_data_root

                installed = user_data_root() / "models"
                model, voices = str(installed / "kokoro-v1.0.onnx"), str(installed / "voices-v1.0.bin")
            if not (os.path.exists(model) and os.path.exists(voices)):
                raise FileNotFoundError("Kokoro model files not found")
            _kokoro_model = Kokoro(model, voices)
        return _kokoro_model


def _kokoro_stream(text: str, voice: str, speed: float) -> PcmStream | None:
    if (voice or "").startswith("custom_"):
        return None  # custom voices need Kanade conversion; use the generic path
    from distr.core.agent.services.tts.kokoro import _normalize_text_for_tts
    from distr.core.agent.services.tts.kokoro_descriptor import (
        _phonemizer_safe_text,
        _split_text_for_kokoro,
    )

    kokoro = _kokoro_instance()
    clamped = max(0.5, min(2.0, float(speed or 1.0)))
    base_voice = voice or "af_heart"
    units = split_for_streaming(_phonemizer_safe_text(_normalize_text_for_tts(text)))

    def chunks() -> Iterator[bytes]:
        for unit in units:
            for part in _split_text_for_kokoro(unit):
                try:
                    pieces = [kokoro.create(part, voice=base_voice, speed=clamped)]
                except RuntimeError as exc:
                    if "number of lines in input and output must be equal" not in str(exc).lower():
                        raise
                    half = max(1, len(part) // 2)
                    cut = part.rfind(" ", 0, half + 1)
                    cut = cut if cut > 0 else half
                    pieces = [
                        kokoro.create(p, voice=base_voice, speed=clamped)
                        for p in (part[:cut].strip(), part[cut:].strip()) if p
                    ]
                for audio, rate in pieces:
                    if audio is not None and len(audio) > 0:
                        pcm, _ = float_audio_to_pcm16(audio, rate, max(rate, _STREAM_TARGET_RATE))
                        yield pcm

    return PcmStream("kokoro", 24000, chunks(), SENTENCE)


def _sentence_stream(provider_id: str, descriptor: Any, text: str, voice: str, speed: float) -> PcmStream:
    """Generic sentence-level streaming through ``descriptor.generate_audio``."""
    units = split_for_streaming(text)

    def synth(unit: str) -> tuple[bytes, int]:
        tmp_dir = Path(tempfile.mkdtemp(prefix="decisions_remote_tts_"))
        out_file = tmp_dir / "unit.wav"
        try:
            descriptor.generate_audio(unit, voice or descriptor.default_voice, speed, str(out_file))
            return _read_wav_pcm16(out_file)
        finally:
            for leftover in tmp_dir.glob("*"):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

    # Synthesise the first unit now so the stream's sample rate is known and a
    # provider that cannot synthesise at all fails before audio_start is sent.
    first_pcm, first_rate = synth(units[0])

    def chunks() -> Iterator[bytes]:
        if first_pcm:
            yield first_pcm
        for unit in units[1:]:
            pcm, rate = synth(unit)
            if rate != first_rate:
                import numpy as np

                arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                from distr.core.audio.tts_handler import _resample_audio

                arr, _ = _resample_audio(arr, rate, first_rate)
                pcm = (np.clip(arr, -1, 1) * 32767.0).astype("<i2").tobytes()
            if pcm:
                yield pcm

    return PcmStream(provider_id, first_rate, chunks(), SENTENCE)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

_NATIVE_OPENERS: dict[str, Callable[[str, str, float], PcmStream | None]] = {
    "fishaudio": _fish_stream,
    "openai": _openai_stream,
    "elevenlabs": _elevenlabs_stream,
    "kokoro": _kokoro_stream,
}


def open_pcm_stream(provider_id: str, text: str, voice: str, speed: float = 1.0) -> PcmStream:
    """Open a streaming PCM synthesis for ``provider_id``.

    Raises when the provider cannot synthesise at all (missing key, model...),
    before any audio was produced, so the caller can fall back to the file path.
    """
    from distr.core.agent.services.tts.registry import tts_registry

    spoken = re.sub(r"\s+", " ", str(text or "")).strip()
    if not spoken:
        raise ValueError("No text to speak")
    opener = _NATIVE_OPENERS.get(provider_id)
    if opener is not None:
        try:
            stream = opener(spoken, voice, speed)
        except (ValueError, FileNotFoundError, ImportError):
            raise
        if stream is not None:
            return stream
    descriptor = tts_registry.get(provider_id)
    return _sentence_stream(provider_id, descriptor, spoken, voice, speed)


def remote_streaming_enabled(settings: dict | None = None) -> bool:
    """Streaming is on unless ``remote_tts_streaming`` is switched off."""
    if os.getenv("DECISIONS_REMOTE_TTS_STREAM", "").strip().lower() in {"0", "false", "off", "no"}:
        return False
    try:
        value = (settings if settings is not None else _settings()).get("remote_tts_streaming", True)
    except Exception:
        return True
    return str(value).strip().lower() not in {"0", "false", "off", "no"}


def open_current_voice_pcm_stream(text: str) -> PcmStream | None:
    """Open a stream for the active outbound voice, or ``None`` when disabled."""
    from distr.core.agent.constants import normalize_voice_provider
    from distr.core.agent.services.tts.outbound_voice import resolve_outbound_voice_settings

    settings = _settings()
    if not remote_streaming_enabled(settings):
        return None
    _provider, voice_id, provider_label = resolve_outbound_voice_settings(settings)
    provider_id = normalize_voice_provider(provider_label)
    return open_pcm_stream(provider_id, text, voice_id, 1.0)
