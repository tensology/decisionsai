"""Contracts for selectable OpenAI TTS models and STT batch model optionality."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from distr.core.agent.constants import (
    DEFAULT_OPENAI_LIVE_TRANSCRIPTION_MODEL,
    DEFAULT_OPENAI_WHISPER_MODEL,
    VALID_OPENAI_TRANSCRIPTION_MODELS,
)
from distr.core.agent.config_loader import resolve_stt_config
from distr.core.agent.services.stt.openai import OpenAIWhisperSTTService
from distr.core.agent.services.tts.openai_tts_config import (
    DEFAULT_OPENAI_TTS_MODEL,
    openai_tts_supports_instructions,
    resolve_openai_tts_model,
    voices_for_openai_tts_model,
)
from distr.core.agent.services.tts.elevenlabs_config import (
    DEFAULT_ELEVENLABS_TTS_MODEL,
    resolve_elevenlabs_tts_model,
)


def test_openai_tts_default_remains_tts_1():
    assert DEFAULT_OPENAI_TTS_MODEL == "tts-1"
    assert resolve_openai_tts_model(None) == "tts-1"
    assert resolve_openai_tts_model("bogus") == "tts-1"
    assert resolve_openai_tts_model("gpt-4o-mini-tts") == "gpt-4o-mini-tts"
    assert resolve_openai_tts_model("tts-1-hd") == "tts-1-hd"


def test_openai_tts_instructions_only_on_gpt4o_mini():
    assert openai_tts_supports_instructions("gpt-4o-mini-tts")
    assert not openai_tts_supports_instructions("tts-1")
    assert not openai_tts_supports_instructions("tts-1-hd")


def test_openai_tts_voice_sets_gate_by_model():
    legacy = voices_for_openai_tts_model("tts-1")
    steerable = voices_for_openai_tts_model("gpt-4o-mini-tts")
    assert "alloy" in legacy
    assert "marin" not in legacy
    assert "marin" in steerable
    assert "cedar" in steerable


def test_openai_tts_service_passes_selected_model_and_instructions():
    pytest.importorskip("openai")
    from distr.core.agent.services.tts.openai import OpenAITTSService

    service = OpenAITTSService.__new__(OpenAITTSService)
    service.model = resolve_openai_tts_model("gpt-4o-mini-tts")
    service.voice_id = "coral"
    service.instructions = "Speak warmly and calmly."

    kwargs = OpenAITTSService._speech_create_kwargs(service, "Hello there.")
    assert kwargs["model"] == "gpt-4o-mini-tts"
    assert kwargs["voice"] == "coral"
    assert kwargs["input"] == "Hello there."
    assert kwargs["instructions"] == "Speak warmly and calmly."
    assert kwargs["speed"] == 1.0

    service.model = "tts-1"
    kwargs_legacy = OpenAITTSService._speech_create_kwargs(service, "Hello there.")
    assert kwargs_legacy["model"] == "tts-1"
    assert "instructions" not in kwargs_legacy


def test_openai_stt_batch_models_selectable_via_resolve_stt_config():
    for model in VALID_OPENAI_TRANSCRIPTION_MODELS:
        cfg = resolve_stt_config(f"OpenAI ({model} + gpt-live-transcribe)")
        assert cfg["engine"] == "openai_whisper"
        assert cfg["model"] == model

    default_cfg = resolve_stt_config("OpenAI (gpt-transcribe + gpt-live-transcribe)")
    assert default_cfg["model"] == DEFAULT_OPENAI_WHISPER_MODEL


def test_openai_realtime_session_includes_optional_prompt_and_noise_reduction():
    service = OpenAIWhisperSTTService.__new__(OpenAIWhisperSTTService)
    service._realtime_model = DEFAULT_OPENAI_LIVE_TRANSCRIPTION_MODEL
    service._transcription_prompt = "DecisionsAI, Tensology, Cape Town"
    service._noise_reduction = "near_field"

    config = service._realtime_session_config()
    session_input = config["session"]["audio"]["input"]
    assert session_input["transcription"]["model"] == "gpt-live-transcribe"
    assert session_input["transcription"]["prompt"] == "DecisionsAI, Tensology, Cape Town"
    assert session_input["noise_reduction"] == {"type": "near_field"}
    assert session_input["turn_detection"] is None


def test_settings_api_exposes_openai_batch_stt_choices(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "openai_enabled": True,
            "openai_key": "internal-openai-key",
            "assemblyai_enabled": True,
            "assemblyai_key": "internal-assemblyai-key",
            "transcription_model": "Whisper.cpp (Local & Offline)",
        },
    )

    app = FastAPI()
    templates_dir = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app.include_router(create_routes(templates_dir), prefix="/api")
    response = TestClient(app).get("/api/llms")

    assert response.status_code == 200
    options = {item["id"]: item["name"] for item in response.json()["stt_options"]}
    assert options["openai_gpt-transcribe"] == "OpenAI (gpt-transcribe + gpt-live-transcribe)"
    assert options["openai_gpt-4o-transcribe"] == "OpenAI (gpt-4o-transcribe + gpt-live-transcribe)"
    assert options["openai_gpt-4o-mini-transcribe"] == "OpenAI (gpt-4o-mini-transcribe + gpt-live-transcribe)"
    assert options["openai_whisper-1"] == "OpenAI (whisper-1 + gpt-live-transcribe)"
    assert "openai_whisper" not in options  # legacy alias hidden from dropdown
    assert options["assemblyai"] == "AssemblyAI (universal-3-5-pro)"


def test_elevenlabs_model_resolve_prefers_settings_then_env(monkeypatch):
    monkeypatch.delenv("DECISIONS_ELEVENLABS_TTS_MODEL_ID", raising=False)
    assert resolve_elevenlabs_tts_model(None) == DEFAULT_ELEVENLABS_TTS_MODEL
    assert resolve_elevenlabs_tts_model("eleven_turbo_v2_5") == "eleven_turbo_v2_5"
    monkeypatch.setenv("DECISIONS_ELEVENLABS_TTS_MODEL_ID", "eleven_multilingual_v2")
    assert resolve_elevenlabs_tts_model(None) == "eleven_multilingual_v2"
    # Explicit settings win over env.
    assert resolve_elevenlabs_tts_model("eleven_v3") == "eleven_v3"


def test_openai_batch_transcription_includes_prompt_when_set(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text="transcribed")

    service = OpenAIWhisperSTTService.__new__(OpenAIWhisperSTTService)
    service.model = "gpt-4o-transcribe"
    service._transcription_prompt = "DecisionsAI Tensology"
    service.client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
    )

    assert service.transcribe_file(str(audio_path)) == "transcribed"
    assert captured["model"] == "gpt-4o-transcribe"
    assert captured["prompt"] == "DecisionsAI Tensology"


def test_agent_openai_stt_wires_prompt_and_noise_from_settings(monkeypatch):
    from distr.core.agent import session as session_module

    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def set_hands_free(self, enabled):
            pass

        def set_vad_threshold(self, threshold):
            pass

    monkeypatch.setattr(session_module, "OPENAI_STT_AVAILABLE", True)
    monkeypatch.setattr(session_module, "OpenAIWhisperSTTService", FakeService)

    agent = SimpleNamespace(
        config={"stt": {"engine": "openai_whisper", "model": "gpt-4o-mini-transcribe"}},
        settings={
            "openai_key": "internal-settings-key",
            "vad_threshold": 50,
            "openai_stt_prompt": "keyword hints",
            "openai_stt_noise_reduction": "near_field",
        },
        stt_service=None,
        event_queue=None,
        is_hands_free=False,
        ptt_active=False,
        is_dictating=False,
        logger=Mock(),
        _emit_stt_ready=lambda reason: None,
    )

    session_module.AgentSession._create_stt_service(agent)

    assert captured["api_key"] == "internal-settings-key"
    assert captured["model"] == "gpt-4o-mini-transcribe"
    assert captured["prompt"] == "keyword hints"
    assert captured["noise_reduction"] == "near_field"


def test_openai_descriptor_create_service_passes_model_and_instructions(monkeypatch):
    from distr.core.agent.services.tts.openai_descriptor import OpenAIDescriptor
    import distr.core.agent.services as services_mod

    captured = {}

    class FakeTTS:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def set_hands_free(self, enabled):
            captured["hands_free"] = enabled

    monkeypatch.setattr(services_mod, "OpenAITTSService", FakeTTS)

    desc = OpenAIDescriptor()
    svc = desc.create_service(
        {"api_key": "k", "voice_id": "marin"},
        settings={
            "playback_speed": 1.0,
            "openai_tts_model": "gpt-4o-mini-tts",
            "openai_tts_instructions": "Warm and clear",
            "_event_queue": None,
        },
        stt_service=None,
        is_hands_free=True,
        models_dir="/tmp",
    )
    assert svc is not None
    assert captured["model"] == "gpt-4o-mini-tts"
    assert captured["instructions"] == "Warm and clear"
    assert captured["voice_id"] == "marin"
    assert captured["hands_free"] is True


def test_elevenlabs_descriptor_create_service_uses_settings_model(monkeypatch):
    from distr.core.agent.services.tts.elevenlabs_descriptor import ElevenLabsDescriptor
    import distr.core.agent.services as services_mod

    captured = {}

    class FakeTTS:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self._resolved_voice_name = kwargs.get("voice_name")

        def set_hands_free(self, enabled):
            captured["hands_free"] = enabled

    monkeypatch.setattr(services_mod, "ElevenLabsTTSService", FakeTTS)
    monkeypatch.setattr(
        "distr.core.agent.service_factory.resolve_elevenlabs_voice",
        lambda api_key, voice: ("voice123", "Rachel"),
    )

    desc = ElevenLabsDescriptor()
    svc = desc.create_service(
        {"api_key": "k", "voice_id": "Rachel"},
        settings={
            "playback_speed": 1.0,
            "elevenlabs_tts_model": "eleven_turbo_v2_5",
            "elevenlabs_stability": 0.5,
            "elevenlabs_similarity_boost": 0.6,
            "elevenlabs_style": 0.25,
            "elevenlabs_use_speaker_boost": True,
            "_event_queue": None,
        },
        stt_service=None,
        is_hands_free=False,
        models_dir="/tmp",
    )
    assert svc is not None
    assert captured["model_id"] == "eleven_turbo_v2_5"


def test_openai_chat_model_sort_prefers_gpt5_family():
    from distr.gui.utils import get_ollama_models as gom

    # Replicate the OpenAI sort key used by get_openai_models.
    models = ["gpt-4o", "o3-mini", "gpt-5.6-luna", "gpt-3.5-turbo", "gpt-5.6-sol"]

    def sort_key(m):
        ml = m.lower()
        if ml.startswith("gpt-5.6") or ml.startswith("gpt-5"):
            return (0, m)
        if m.startswith("o4"):
            return (1, m)
        if m.startswith("o3"):
            return (2, m)
        if m.startswith("o1"):
            return (3, m)
        if "gpt-4o" in m:
            return (4, m)
        if "gpt-4" in m:
            return (5, m)
        if "gpt-3.5" in m:
            return (6, m)
        return (7, m)

    models.sort(key=sort_key)
    assert models[0].startswith("gpt-5")
    assert models[1].startswith("gpt-5")
    assert "GPT-5.6 Luna" == gom._format_openai_model_name("gpt-5.6-luna")
    assert "GPT-5.6 Sol" == gom._format_openai_model_name("gpt-5.6-sol")


def test_assemblyai_default_still_current():
    from distr.core.agent.constants import DEFAULT_ASSEMBLYAI_MODEL, VALID_ASSEMBLYAI_MODELS

    assert DEFAULT_ASSEMBLYAI_MODEL == "universal-3-5-pro"
    assert "universal-3-5-pro" in VALID_ASSEMBLYAI_MODELS
