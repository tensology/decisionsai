"""Contracts for the current OpenAI and AssemblyAI transcription models."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from distr.core.agent.constants import (
    DEFAULT_ASSEMBLYAI_MODEL,
    DEFAULT_OPENAI_LIVE_TRANSCRIPTION_MODEL,
    DEFAULT_OPENAI_WHISPER_MODEL,
)
from distr.core.agent import session as session_module
from distr.core.agent.services.stt import assemblyai as assemblyai_stt
from distr.core.agent.services.stt.openai import OpenAIWhisperSTTService


def test_latest_remote_stt_defaults():
    assert DEFAULT_OPENAI_WHISPER_MODEL == "gpt-transcribe"
    assert DEFAULT_OPENAI_LIVE_TRANSCRIPTION_MODEL == "gpt-live-transcribe"
    assert DEFAULT_ASSEMBLYAI_MODEL == "universal-3-5-pro"


def test_settings_api_exposes_latest_models_through_existing_stt_choices(monkeypatch):
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
    assert options["assemblyai"] == "AssemblyAI (universal-3-5-pro)"


def test_openai_realtime_session_uses_live_model_and_24khz_pcm():
    service = OpenAIWhisperSTTService.__new__(OpenAIWhisperSTTService)
    service._realtime_model = DEFAULT_OPENAI_LIVE_TRANSCRIPTION_MODEL

    config = service._realtime_session_config()
    session_input = config["session"]["audio"]["input"]

    assert config["session"]["type"] == "transcription"
    assert session_input["format"] == {"type": "audio/pcm", "rate": 24000}
    assert session_input["transcription"]["model"] == "gpt-live-transcribe"
    assert session_input["turn_detection"] is None


def test_openai_realtime_resampler_converts_16khz_pcm_to_24khz():
    samples = np.arange(320, dtype=np.int16)
    converted = OpenAIWhisperSTTService._resample_pcm16_16khz_to_24khz(samples.tobytes())

    assert len(converted) == 480 * 2


def test_openai_file_transcription_uses_selected_model_without_shell_fallback(tmp_path):
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text="transcribed")

    service = OpenAIWhisperSTTService.__new__(OpenAIWhisperSTTService)
    service.model = "gpt-transcribe"
    service.client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
    )

    assert service.transcribe_file(str(audio_path)) == "transcribed"
    assert captured["model"] == "gpt-transcribe"
    assert "language" not in captured


def test_assemblyai_configs_use_universal_3_5_pro(monkeypatch):
    captured = {}

    def streaming_parameters(**kwargs):
        captured["streaming"] = kwargs
        return kwargs

    def transcription_config(**kwargs):
        captured["batch"] = kwargs
        return kwargs

    monkeypatch.setattr(assemblyai_stt, "StreamingParameters", streaming_parameters)
    monkeypatch.setattr(assemblyai_stt.aai, "TranscriptionConfig", transcription_config)

    service = assemblyai_stt.AssemblyAISTTService.__new__(assemblyai_stt.AssemblyAISTTService)
    service.speech_model = "universal-3-5-pro"

    service._streaming_parameters()
    service._batch_config()

    assert captured["streaming"] == {
        "sample_rate": 16000,
        "speech_model": "universal-3-5-pro",
        "language_detection": True,
    }
    assert captured["batch"] == {
        "speech_models": ["universal-3-5-pro"],
        "language_detection": True,
    }


def test_assemblyai_streams_audio_with_current_sdk_method():
    captured = []
    service = assemblyai_stt.AssemblyAISTTService.__new__(assemblyai_stt.AssemblyAISTTService)
    service._streaming_connected = True
    service._streaming_client = SimpleNamespace(stream=captured.append)

    service._stream_audio(b"pcm")

    assert captured == [b"pcm"]


def test_agent_openai_stt_uses_application_settings_key_not_shell(monkeypatch):
    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def set_hands_free(self, enabled):
            pass

        def set_vad_threshold(self, threshold):
            pass

    monkeypatch.setenv("OPENAI_API_KEY", "rejected-shell-key")
    monkeypatch.setattr(session_module, "OPENAI_STT_AVAILABLE", True)
    monkeypatch.setattr(session_module, "OpenAIWhisperSTTService", FakeService)

    agent = SimpleNamespace(
        config={"stt": {"engine": "openai_whisper", "model": "gpt-transcribe"}},
        settings={"openai_key": "internal-settings-key", "vad_threshold": 50},
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
    assert captured["model"] == "gpt-transcribe"


def test_agent_assemblyai_stt_uses_application_settings_key(monkeypatch):
    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def set_hands_free(self, enabled):
            pass

        def set_vad_threshold(self, threshold):
            pass

    monkeypatch.setattr(session_module, "ASSEMBLYAI_STT_AVAILABLE", True)
    monkeypatch.setattr(session_module, "AssemblyAISTTService", FakeService)

    agent = SimpleNamespace(
        config={"stt": {"engine": "assemblyai", "model": "universal-3-5-pro"}},
        settings={"assemblyai_key": "internal-assemblyai-key", "vad_threshold": 50},
        stt_service=None,
        event_queue=None,
        is_hands_free=False,
        ptt_active=False,
        is_dictating=False,
        logger=Mock(),
        _emit_stt_ready=lambda reason: None,
    )

    session_module.AgentSession._create_stt_service(agent)

    assert captured["api_key"] == "internal-assemblyai-key"
    assert captured["model"] == "universal-3-5-pro"
