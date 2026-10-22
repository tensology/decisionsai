import base64
import json
from pathlib import Path

import pytest

from distr.core import pixazo_frames
from distr.core.workflow import dev_agent_tools as t

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DECISIONS_DB_DIR", str(tmp_path / "db"))
    monkeypatch.setattr(t, "_settings", lambda: {"video_frames_model": ""})


def _img(tmp_path, name="a.png"):
    p = tmp_path / name
    p.write_bytes(PNG)
    return p


def test_schemas_cover_tools():
    assert set(t.TOOL_SCHEMAS) == {"image_generate", "vision_analyze", "video_generate", "computer_use", "calendar",
                                "files", "browser", "approval_wait", "select_thread", "open_thread"}
    for spec in t.TOOL_SCHEMAS.values():
        assert spec["parameters"]["required"]


def test_image_generate_writes_artifact(tmp_path):
    class Fake:
        def _run(self, prompt, output_path, style=None):
            Path(output_path).write_bytes(PNG)
            return f"saved {output_path}"
    r = t.image_generate("a red cube", project_folder=str(tmp_path), _tool_factory=Fake)
    assert r["ok"] and r["artifacts"][0]["kind"] == "image"
    assert ".decisions/dev-artifacts" in r["artifacts"][0]["path"]


def test_image_generate_reports_error(tmp_path):
    class Fake:
        def _run(self, **kw):
            return "Error: Image LLM not configured."
    r = t.image_generate("x", project_folder=str(tmp_path), _tool_factory=Fake)
    assert not r["ok"] and "not configured" in r["summary"]


def test_vision_analyze_relative_path_and_async(tmp_path):
    _img(tmp_path)
    async def fake(path, q):
        return f"seen {Path(path).name}: {q}"
    r = t.vision_analyze("a.png", "what?", project_folder=str(tmp_path), _analyze=fake)
    assert r["ok"] and r["summary"] == "seen a.png: what?"


def test_vision_analyze_rejects_missing_and_non_image(tmp_path):
    assert not t.vision_analyze("nope.png", project_folder=str(tmp_path), _analyze=lambda *a: "x")["ok"]
    (tmp_path / "f.txt").write_text("hi")
    assert not t.vision_analyze("f.txt", project_folder=str(tmp_path), _analyze=lambda *a: "x")["ok"]


def test_vision_analyze_unconfigured(tmp_path):
    _img(tmp_path)
    assert not t.vision_analyze("a.png", project_folder=str(tmp_path), _analyze=lambda *a: "")["ok"]


def test_video_text_mode(tmp_path):
    class Fake:
        def _run(self, prompt, output_path, duration_sec=None):
            Path(output_path).write_bytes(b"mp4")
            return "Video saved"
    r = t.video_generate("waves", duration=5, project_folder=str(tmp_path), _text_tool_factory=Fake)
    assert r["ok"] and r["artifacts"][0]["path"].endswith(".mp4")


def test_video_frames_requires_both(tmp_path):
    assert not t.video_generate("x", start_image="a.png", project_folder=str(tmp_path))["ok"]


def test_video_frames_mode(tmp_path, monkeypatch):
    _img(tmp_path, "s.png"); _img(tmp_path, "e.png")
    import distr.core.third_party_keys as k
    monkeypatch.setattr(k, "pixazo_api_key", lambda: "k")
    seen = {}
    def frames(key, model, prompt, s, e, d):
        seen.update(model=model, s=s, e=e, d=d)
        return ["https://cdn/x.mp4"]
    r = t.video_generate("morph", start_image="s.png", end_image="e.png", duration=4, project_folder=str(tmp_path),
                         _frames_fn=frames, _download=lambda url, timeout: b"mp4")
    assert r["ok"] and seen["model"] == "seedance-2.5-frames" and seen["s"].endswith("s.png") and seen["d"] == 4


def test_seedance_body_uses_data_uri_and_roles(tmp_path):
    s = _img(tmp_path, "s.png"); e = _img(tmp_path, "e.png")
    url, body = pixazo_frames.build_frames_body("seedance-2.5-frames", "go", str(s), str(e), 3)
    assert url.endswith("/seedance-2-5/v1/first-last-frame-to-video")
    imgs = [c for c in body["content"] if c["type"] == "image_url"]
    assert [c["role"] for c in imgs] == ["first_frame", "last_frame"]
    assert imgs[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["duration"] == 4 and body["ratio"] == "adaptive"  # clamped to 4-30


def test_veo_and_wan_need_https(tmp_path):
    s = _img(tmp_path)
    with pytest.raises(ValueError):
        pixazo_frames.build_frames_body("veo-3.1-fast-frames", "go", str(s), str(s), 5)
    url, body = pixazo_frames.build_frames_body("veo-3.1-fast-frames", "go", "https://a/1.png", "https://a/2.png", 5)
    assert body["image"] and body["last_frame"] and body["duration"] in (4, 6)
    _, wan = pixazo_frames.build_frames_body("wan-3.0-frames", "go", "https://a/1.png", "https://a/2.png", 5)
    assert [m["type"] for m in wan["media"]] == ["first_frame", "last_frame"]


def test_computer_use_always_disabled():
    r = t.call_tool("computer_use", {"goal": "click", "approval_id": "anything"})
    assert not r["ok"] and r["disabled"] is True


def test_call_tool_filters_unknown_args(tmp_path):
    r = t.call_tool("nope", {})
    assert not r["ok"]


def test_no_speech_injection_in_module():
    src = Path(t.__file__).read_text() + Path(pixazo_frames.__file__).read_text()
    assert "speech/inject" not in src and "speak_text_directly(" not in src


def test_status_never_returns_keys(monkeypatch):
    import distr.core.third_party_keys as k
    monkeypatch.setattr(k, "pixazo_api_key", lambda: "SECRET123")
    s = t.tools_status({"video_llm_model": "veo-3.1", "image_llm_model": "m"})
    assert "SECRET123" not in json.dumps(s) and s["video_generate"]["pixazo_key_present"] is True
    assert s["computer_use"]["enabled"] is False


def _task(**kw):
    from distr.core.project_cli_backends.base import ProjectTask
    return ProjectTask(project_id=1, project_name="P", folder="/tmp/p", instruction="do", **kw)


def test_pi_development_turn_loads_media_extension():
    from distr.core.project_cli_backends.registry import _pi_print_command
    cmd = _pi_print_command("pi", _task(origin="development", chat_id=1))
    ext = [cmd[i + 1] for i, c in enumerate(cmd) if c == "--extension"]
    assert any(e.endswith("dev_media_tools.ts") and Path(e).is_file() for e in ext)


def test_pi_read_only_and_opt_out_skip_media_extension():
    from distr.core.project_cli_backends.registry import _pi_print_command
    for opts in ({"read_only_expected": True}, {"disable_dev_media_tools": True}, {"disable_tools": True}):
        cmd = _pi_print_command("pi", _task(origin="development", chat_id=1, adapter_options=opts))
        assert not any(str(c).endswith("dev_media_tools.ts") for c in cmd)
    cmd = _pi_print_command("pi", _task(origin="workflow"))
    assert not any(str(c).endswith("dev_media_tools.ts") for c in cmd)
