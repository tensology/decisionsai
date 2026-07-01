"""Pixazo integration and workflow NVIDIA mapping tests."""

import json
from pathlib import Path
from types import SimpleNamespace

from distr.core.pixazo_client import pixazo_model_spec, pixazo_models_for_modality
from distr.core.workflow.planning import _litellm_model


def test_planning_litellm_nvidia():
    assert _litellm_model("nvidia", "meta/llama-3.3-70b-instruct", {}) == (
        "nvidia_nim/meta/llama-3.3-70b-instruct"
    )


def test_pixazo_catalog_has_image_and_video():
    images = pixazo_models_for_modality("image")
    videos = pixazo_models_for_modality("video")
    assert any(m["id"] == "flux-pro" for m in images)
    assert any(m["id"] == "seedance-2" for m in videos)


def test_pixazo_model_spec_lookup():
    spec = pixazo_model_spec("sdxl-turbo")
    assert spec is not None
    assert "submit_url" in spec


def test_validate_pixazo_registered():
    from distr.core import api_validation

    assert callable(api_validation.validate_pixazo)


def test_thirdparty_and_llms_wiring_present():
    root = Path(__file__).resolve().parents[2]
    thirdparty_js = (root / "distr/gui/web/static/settings/js/thirdparty.js").read_text(encoding="utf-8")
    llms_py = (root / "distr/gui/web/routes/settings/llms.py").read_text(encoding="utf-8")
    mcp_py = (root / "distr/core/mcp_harness.py").read_text(encoding="utf-8")
    assert "pixazo" in thirdparty_js
    assert "available-media-providers" in llms_py
    assert "get_pixazo_models" in llms_py
    assert "pixazo_media" in mcp_py


def test_mcp_harness_pixazo_catalog_entry():
    from distr.core import mcp_harness

    catalog = mcp_harness.collect_mcp_catalog()
    entry = catalog.get("pixazo_media") or {}
    assert entry.get("skill") == "pixazo-media"
    assert entry.get("mcp", {}).get("url") == "https://gateway.pixazo.ai/pixazo/mcp"
    assert entry.get("api_key_settings_field") == "pixazo_key"
    assert entry.get("api_key_header") == "Ocp-Apim-Subscription-Key"


def test_pixazo_tts_descriptor_registered():
    from distr.core.agent.services.tts.registry import tts_registry

    desc = tts_registry.get("pixazo")
    assert desc.id == "pixazo"
    assert desc.default_voice == "voxcpm"
    assert desc.supports_custom_voices is True


def test_voxcpm_client_urls():
    from distr.core.pixazo_client import VOXCPM_CLONE_URL, VOXCPM_TTS_URL, pixazo_model_spec

    spec = pixazo_model_spec("voxcpm")
    assert spec is not None
    assert spec["submit_url"] == VOXCPM_TTS_URL
    assert "voice-cloning" in VOXCPM_CLONE_URL


def test_relay_media_multipart_and_refresh(tmp_path, monkeypatch):
    from distr.core.integrations import relay_media

    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"RIFFxxxx")
    audio_dir = tmp_path / "voice"
    audio_dir.mkdir()

    captured: dict = {}

    class _Resp:
        def read(self):
            return json.dumps(
                {
                    "download_url": "https://www.decisionsai.net/api/telegram/media/download/abc/",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                }
            ).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake_urlopen(req, timeout=60):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return _Resp()

    monkeypatch.setattr(relay_media.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(relay_media, "relay_auth_headers", lambda force_refresh=False: {
        "X-Relay-Internal-Token": "relay-test-token"
    })

    record = relay_media.upload_pixazo_voice_reference(str(wav), label="custom_1")
    assert "download_url" in record
    assert captured["url"].endswith("/api/media/voice-reference/upload/")
    assert captured["headers"].get("X-relay-internal-token") or captured["headers"].get(
        "X-Relay-Internal-Token"
    )

    relay_media.write_relay_reference_meta(str(audio_dir), record)
    url = relay_media.ensure_pixazo_reference_url(str(wav), str(audio_dir), label="custom_1")
    assert url == record["download_url"]
    assert captured["url"].endswith("/api/media/voice-reference/upload/")


def test_ensure_pixazo_reference_url_force_refreshes_cached_reference(tmp_path, monkeypatch):
    from distr.core.integrations import relay_media

    audio_dir = tmp_path / "voice"
    audio_dir.mkdir()
    wav = audio_dir / "reference.wav"
    wav.write_bytes(b"RIFFxxxx")
    relay_media.write_relay_reference_meta(
        str(audio_dir),
        {
            "download_url": "https://www.decisionsai.net/api/telegram/media/download/old/",
            "expires_at": "2099-01-01T00:00:00+00:00",
        },
    )

    uploads = []

    def _fake_upload(local_path, *, label):
        uploads.append((local_path, label))
        return {
            "download_url": "https://www.decisionsai.net/api/telegram/media/download/new/",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(relay_media, "upload_pixazo_voice_reference", _fake_upload)

    url = relay_media.ensure_pixazo_reference_url(
        str(wav),
        str(audio_dir),
        label="custom_1",
        force_refresh=True,
    )

    assert url == "https://www.decisionsai.net/api/telegram/media/download/new/"
    assert uploads == [(str(wav), "custom_1")]


def test_pixazo_clone_voice_defers_relay_staging_failure(tmp_path, monkeypatch):
    from distr.core.agent.services.tts.pixazo_descriptor import PixazoDescriptor
    from distr.core.integrations import relay_media

    audio_dir = tmp_path / "voice"
    audio_dir.mkdir()
    wav = audio_dir / "clip1.wav"
    wav.write_bytes(b"RIFFxxxx")

    class _Voice:
        def __init__(self):
            self.id = 7
            self.name = "Hayley"
            self.audio_dir = str(audio_dir)
            self.provider_voice_id = ""
            self.status = "processing"
            self.error_message = ""

    class _Session:
        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1

    def _fail_upload(*args, **kwargs):
        raise RuntimeError("Relay upload failed (404): missing")

    monkeypatch.setitem(__import__("sys").modules, "pydub", SimpleNamespace(AudioSegment=object))
    monkeypatch.setattr(relay_media, "upload_pixazo_voice_reference", _fail_upload)

    voice = _Voice()
    session = _Session()

    PixazoDescriptor().clone_voice(voice, [str(wav)], session)

    assert voice.status == "ready"
    assert voice.provider_voice_id == "custom_7"
    assert voice.error_message == ""
    assert session.commits >= 1


def test_pixazo_dit_steps_from_settings_defaults():
    from distr.core.pixazo_client import VOXCPM_DIT_STEPS_DEFAULT, pixazo_dit_steps_from_settings

    assert pixazo_dit_steps_from_settings({}) == VOXCPM_DIT_STEPS_DEFAULT
    assert pixazo_dit_steps_from_settings({"pixazo_dit_steps": 4}) == 4
    assert pixazo_dit_steps_from_settings({"pixazo_dit_steps": 99}) == 30
    assert pixazo_dit_steps_from_settings({"pixazo_dit_steps": 1}) == 4


def test_download_url_to_bytes_sets_user_agent(monkeypatch):
    from distr.core import pixazo_client
    from distr.gui.web import security

    captured: dict = {}

    class _Resp:
        def read(self):
            return b"wav"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fake_urlopen(req, timeout=120):
        captured["ua"] = req.headers.get("User-agent") or req.headers.get("User-Agent")
        return _Resp()

    monkeypatch.setattr(pixazo_client.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(security, "validate_safe_outbound_url", lambda url: url)

    data = pixazo_client.download_url_to_bytes("https://pub-example.r2.dev/voxcpm/test.wav")
    assert data == b"wav"
    assert captured["ua"] and "DecisionsAI" in captured["ua"]


def test_mcp_merge_pixazo_with_settings_key(tmp_path, monkeypatch):
    from distr.core.mcp_harness import recalibrate_mcp_harness

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir(parents=True)
    (cursor_dir / "mcp.json").write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")

    monkeypatch.setenv("PIXAZO_API_KEY", "test-pixazo-key")
    result = recalibrate_mcp_harness(home=tmp_path, run_full=False)
    assert "pixazo" in result["cursor_merged"]
    servers = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["pixazo"]["headers"]["Ocp-Apim-Subscription-Key"] == "test-pixazo-key"
