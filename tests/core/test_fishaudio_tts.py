"""Fish Audio TTS wiring: registry, request body, key validation."""

from distr.core.agent.services.tts.fishaudio_config import (
    DEFAULT_FISHAUDIO_TTS_MODEL,
    DEFAULT_FISHAUDIO_VOICE,
    resolve_fishaudio_tts_model,
)


def test_resolve_fishaudio_tts_model():
    assert resolve_fishaudio_tts_model(None) == DEFAULT_FISHAUDIO_TTS_MODEL
    assert resolve_fishaudio_tts_model("bogus") == DEFAULT_FISHAUDIO_TTS_MODEL
    assert resolve_fishaudio_tts_model("s2.1-pro") == "s2.1-pro"


def test_fishaudio_descriptor_registered():
    from distr.core.agent.services.tts.registry import tts_registry

    desc = tts_registry.get("fishaudio")
    assert desc.id == "fishaudio"
    assert desc.default_voice == DEFAULT_FISHAUDIO_VOICE
    assert desc.settings_key == "fishaudio_voice"
    assert desc.normalize_provider_name("Fish Audio (Online)") == "fishaudio"


def test_validate_fishaudio_registered():
    from distr.core import api_validation

    assert callable(api_validation.validate_fishaudio)


def test_synthesize_sends_model_header_and_reference_id(monkeypatch):
    from distr.core.agent.services.tts import fishaudio_client

    captured = {}

    class _Resp:
        is_success = True
        status_code = 200
        content = b"RIFF...."
        text = ""

    class _Client:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(fishaudio_client.httpx, "Client", _Client)
    audio = fishaudio_client.synthesize_audio(
        "sk-test",
        "Hello",
        reference_id=DEFAULT_FISHAUDIO_VOICE,
        model="s2.1-pro-free",
        speed=1.1,
    )
    assert audio == b"RIFF...."
    assert captured["url"] == "https://api.fish.audio/v1/tts"
    assert captured["headers"]["model"] == "s2.1-pro-free"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"]["text"] == "Hello"
    assert captured["json"]["reference_id"] == DEFAULT_FISHAUDIO_VOICE
    assert captured["json"]["prosody"]["speed"] == 1.1


def test_list_voices_merges_owned_and_library(monkeypatch):
    from distr.core.agent.services.tts import fishaudio_client

    def fake_list(_key, params):
        if params.get("self") == "true":
            return [{"id": "mine-1", "name": "My Clone", "owned": True}]
        if params.get("licensed") == "true":
            return [
                {"id": "mine-1", "name": "dup", "owned": False},
                {"id": "lib-1", "name": "Licensed Voice", "owned": False, "languages": ["ja"]},
            ]
        if params.get("language") == "en":
            return [{"id": "en-1", "name": "Sarah", "owned": False, "languages": ["en"]}]
        return [{"id": "pop-1", "name": "Smash", "owned": False, "languages": ["en"]}]

    monkeypatch.setattr(fishaudio_client, "_list_models", fake_list)
    voices = fishaudio_client.list_voices("sk-test")
    ids = [v["id"] for v in voices]
    assert ids.count("mine-1") == 1
    owned = next(v for v in voices if v["id"] == "mine-1")
    assert owned["custom"] is True
    assert any(v["id"] == "lib-1" for v in voices)
    assert any(v["id"] == "en-1" and v["name"] == "Sarah" for v in voices)
    assert any("ja" in v["name"] for v in voices if v["id"] == "lib-1")
    default = next(v for v in voices if v["id"] == DEFAULT_FISHAUDIO_VOICE)
    assert default["name"] == "Fish Audio"


def test_thirdparty_js_lists_fishaudio():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    thirdparty_js = (root / "distr/gui/web/static/settings/js/thirdparty.js").read_text(
        encoding="utf-8"
    )
    general_html = (
        root / "distr/gui/web/templates/settings/sections/general.html"
    ).read_text(encoding="utf-8")
    assert "id: 'fishaudio'" in thirdparty_js
    assert "fishaudio_voice_options" in general_html
    assert "s2.1-pro-free" in general_html
    assert 'data-provider-id="' in thirdparty_js
    settings_html = (
        root / "distr/gui/web/templates/settings/settings.html"
    ).read_text(encoding="utf-8")
    assert "thirdparty.js?v=20260921-icons" in settings_html
    assert "iconPath: '/assets/img/providers/fishaudio.png'" in thirdparty_js
    assert "iconPath: '/assets/img/providers/tensology.png'" in thirdparty_js
    assert "general.js?v=20260921-editvoice" in settings_html
    chat_js = (root / "distr/gui/web/static/chat/js/chat.js").read_text(encoding="utf-8")
    assert "'fishaudio'" in chat_js
    assert "fishaudio-voices" in chat_js


def test_hot_swap_and_voice_settings_entry():
    from distr.core.agent.services.tts.fishaudio_descriptor import FishAudioDescriptor

    desc = FishAudioDescriptor()
    cfg = desc.get_hot_swap_config(
        "voice-1",
        {"fishaudio_key": "sk-live", "fishaudio_tts_model": "s2.1-pro"},
    )
    assert cfg["engine"] == "fishaudio"
    assert cfg["voice_id"] == "voice-1"
    assert cfg["api_key"] == "sk-live"
    assert cfg["model"] == "s2.1-pro"
    engine, key, default, extra = desc.get_voice_settings_entry()
    assert engine == "fishaudio"
    assert key == "fishaudio_voice"
    assert extra == {"api_key": "fishaudio_key"}
    assert default == DEFAULT_FISHAUDIO_VOICE


def test_online_provider_gate_and_tts_providers_api(monkeypatch):
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from distr.gui.web.routes.settings import create_routes
    from distr.gui.web.routes.settings import voices as voices_mod

    settings = {
        "fishaudio_enabled": True,
        "fishaudio_key": "sk-test",
        "openai_enabled": False,
        "openai_key": "",
        "elevenlabs_enabled": False,
        "elevenlabs_key": "",
        "pixazo_enabled": False,
        "pixazo_key": "",
    }
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: settings,
    )
    monkeypatch.setattr(
        "distr.core.agent.services.tts.fishaudio_descriptor.FishAudioDescriptor.get_voices",
        lambda self: [{"id": DEFAULT_FISHAUDIO_VOICE, "name": "Demo"}],
    )

    templates = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app = FastAPI()
    app.include_router(create_routes(templates), prefix="/api")
    client = TestClient(app)

    assert voices_mod._tts_online_provider_verified(settings, "fishaudio") is True
    gated = dict(settings)
    gated["fishaudio_enabled"] = False
    assert voices_mod._tts_online_provider_verified(gated, "fishaudio") is False

    resp = client.get("/api/tts/providers")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()]
    assert "fishaudio" in ids
    fish = next(p for p in resp.json() if p["id"] == "fishaudio")
    assert fish["settings_key"] == "fishaudio_voice"
    assert fish["voices"][0]["id"] == DEFAULT_FISHAUDIO_VOICE
    assert fish["supports_custom_voices"] is True


def test_validate_and_thirdparty_endpoints(monkeypatch):
    from pathlib import Path

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from distr.gui.web.routes.settings import create_routes

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "fishaudio_enabled": False,
            "fishaudio_key": "",
            "ollama_url": "http://localhost:11434/",
        },
    )
    monkeypatch.setattr(
        "distr.core.api_validation.validate_fishaudio",
        lambda key: (key == "sk-good", "" if key == "sk-good" else "Invalid API key"),
    )

    templates = Path(__file__).resolve().parents[2] / "distr/gui/web/templates"
    app = FastAPI()
    app.include_router(create_routes(templates), prefix="/api")
    client = TestClient(app)

    third = client.get("/api/thirdparty")
    assert third.status_code == 200
    body = third.json()
    assert "fishaudio_enabled" in body
    assert body["fishaudio_key_set"] is False

    bad = client.post("/api/validate", json={"provider": "fishaudio", "key": "nope"})
    assert bad.status_code == 200
    assert bad.json()["valid"] is False

    good = client.post("/api/validate", json={"provider": "fishaudio", "key": "sk-good"})
    assert good.status_code == 200
    assert good.json()["valid"] is True


def test_generate_audio_writes_wav(monkeypatch, tmp_path):
    from distr.core.agent.services.tts.fishaudio_descriptor import FishAudioDescriptor

    written = {}

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {
            "fishaudio_key": "sk-test",
            "fishaudio_voice": DEFAULT_FISHAUDIO_VOICE,
            "fishaudio_tts_model": "s2.1-pro-free",
        },
    )
    monkeypatch.setattr(
        "distr.core.agent.services.tts.fishaudio_client.synthesize_audio",
        lambda *args, **kwargs: b"wav-bytes",
    )
    monkeypatch.setattr(
        "soundfile.read",
        lambda *_a, **_k: (__import__("numpy").zeros(16, dtype="float32"), 44100),
    )
    monkeypatch.setattr(
        "distr.core.audio.tts_handler._resample_audio",
        lambda audio, sample_rate, _target: (audio, sample_rate),
    )

    def fake_write(path, audio, rate):
        written["path"] = path
        written["rate"] = rate

    monkeypatch.setattr("soundfile.write", fake_write)
    out = tmp_path / "sample.wav"
    FishAudioDescriptor().generate_audio("Hello", DEFAULT_FISHAUDIO_VOICE, 1.0, str(out))
    assert written["path"] == str(out)
    assert written["rate"] == 44100


def test_live_fishaudio_tts_roundtrip():
    """Hit Fish Audio when FISH_AUDIO_API_KEY or FISH_API_KEY is set."""
    import os

    import pytest

    key = (os.environ.get("FISH_AUDIO_API_KEY") or os.environ.get("FISH_API_KEY") or "").strip()
    if not key:
        pytest.skip("Set FISH_AUDIO_API_KEY to run live Fish Audio TTS")

    from distr.core.agent.services.tts.fishaudio_client import list_voices, probe_api_key, synthesize_audio

    ok, err = probe_api_key(key)
    assert ok, err
    voices = list_voices(key)
    assert voices
    audio = synthesize_audio(
        key,
        "Decisions AI Fish Audio check.",
        reference_id=voices[0]["id"],
        model="s2.1-pro-free",
    )
    assert len(audio) > 100


def test_clone_voice_posts_studio_model(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from distr.core.agent.services.tts.fishaudio_descriptor import FishAudioDescriptor

    sample = tmp_path / "clip.wav"
    sample.write_bytes(b"RIFF")
    captured = {}

    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"fishaudio_key": "sk-clone"},
    )

    def fake_create(api_key, title, audio_paths, **kwargs):
        captured["api_key"] = api_key
        captured["title"] = title
        captured["paths"] = list(audio_paths)
        captured["visibility"] = kwargs.get("visibility")
        return {"_id": "cloned-model-1"}

    monkeypatch.setattr(
        "distr.core.agent.services.tts.fishaudio_client.create_voice_model",
        fake_create,
    )
    voice = SimpleNamespace(
        name="Studio Clone",
        personality="calm",
        status="processing",
        error_message="",
        provider_voice_id="",
    )
    commits = []
    session = SimpleNamespace(commit=lambda: commits.append(True))
    FishAudioDescriptor().clone_voice(voice, [str(sample)], session)
    assert voice.status == "ready"
    assert voice.provider_voice_id == "cloned-model-1"
    assert captured["api_key"] == "sk-clone"
    assert captured["title"] == "Studio Clone"
    assert captured["visibility"] == "private"
    assert commits


def test_create_voice_model_sends_multipart(monkeypatch, tmp_path):
    from distr.core.agent.services.tts import fishaudio_client

    clip = tmp_path / "a.wav"
    clip.write_bytes(b"wav")
    captured = {}

    class FakeResp:
        is_success = True
        status_code = 200
        content = b'{"_id":"m1","state":"trained"}'

        def json(self):
            return {"_id": "m1", "state": "trained"}

    class FakeClient:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers=None, data=None, files=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            captured["files"] = files
            return FakeResp()

    monkeypatch.setattr(fishaudio_client.httpx, "Client", FakeClient)
    payload = fishaudio_client.create_voice_model("sk-test", "My Voice", [str(clip)])
    assert payload["_id"] == "m1"
    assert captured["url"].endswith("/model")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert "Content-Type" not in captured["headers"]
    assert captured["data"]["type"] == "tts"
    assert captured["data"]["train_mode"] == "fast"
    assert captured["files"][0][0] == "voices"


def test_fishaudio_run_tts_yields_transport_audio_frames(monkeypatch):
    import asyncio
    import io
    import wave

    from distr.core.agent.libs import AudioRawFrame, OutputAudioRawFrame, TTSStartedFrame
    from distr.core.agent.services.tts.fishaudio import FishAudioTTSService

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b"\x00\x10" * 4410)

    monkeypatch.setattr(
        "distr.core.agent.services.tts.fishaudio_client.synthesize_audio",
        lambda *args, **kwargs: buf.getvalue(),
    )
    service = FishAudioTTSService(api_key="sk-test", voice_id="voice-1", voice_name="Demo")

    async def collect():
        out = []
        async for frame in service.run_tts("Hello from the transport."):
            out.append(frame)
        return out

    frames = asyncio.run(collect())
    assert any(isinstance(f, TTSStartedFrame) for f in frames)
    audio_frames = [
        f for f in frames
        if isinstance(f, (OutputAudioRawFrame, AudioRawFrame)) and getattr(f, "audio", b"")
    ]
    assert audio_frames
    assert sum(len(f.audio) for f in audio_frames) > 100

