"""The visual tier is opt-in, rate-limited, and does not keep the pixels."""

import os

import pytest

from distr.core.initiative import visual_context as vc


@pytest.fixture()
def capture_dir(monkeypatch, tmp_path):
    d = tmp_path / "visual"
    monkeypatch.setattr(vc, "capture_dir", lambda: str(d))
    monkeypatch.setattr(vc, "_stamp_path", lambda: str(d / ".last_capture"))
    return d


# ------------------------------------------------------------------ gating

def test_disabled_unless_depth_is_visual():
    assert vc.is_enabled({"initiative_awareness_depth": "visual"}) is True
    assert vc.is_enabled({"initiative_awareness_depth": "VISUAL"}) is True
    assert vc.is_enabled({"initiative_awareness_depth": "tier_ab"}) is False
    assert vc.is_enabled({"initiative_awareness_depth": "tier_a"}) is False
    assert vc.is_enabled({}) is False
    assert vc.is_enabled(None) is False


def test_capture_interval_has_a_floor_and_a_fallback():
    assert vc.capture_interval_s({"initiative_visual_capture_seconds": 900}) == 900
    assert vc.capture_interval_s({"initiative_visual_capture_seconds": 5}) == 60
    assert vc.capture_interval_s({"initiative_visual_capture_seconds": "junk"}) == vc.DEFAULT_INTERVAL_S
    assert vc.capture_interval_s({}) == vc.DEFAULT_INTERVAL_S


def test_nothing_is_captured_when_disabled(monkeypatch, capture_dir):
    called = []
    import distr.core.agent.tools.vision.screen_capture as sc

    monkeypatch.setattr(sc, "capture_all_screens", lambda d: called.append(d) or [])
    assert vc.capture_and_describe({"initiative_awareness_depth": "tier_ab"}) == ""
    assert called == []


# ------------------------------------------------------------------ rate limit

def test_due_when_there_has_never_been_a_capture(capture_dir):
    assert vc.due({"initiative_visual_capture_seconds": 900}, now=1000.0) is True


def test_not_due_inside_the_interval(capture_dir):
    vc._mark_captured(now=1000.0)
    assert vc.due({"initiative_visual_capture_seconds": 900}, now=1100.0) is False
    assert vc.due({"initiative_visual_capture_seconds": 900}, now=1000.0 + 901) is True


# ------------------------------------------------------------------ purge

def test_purge_removes_only_old_captures(capture_dir):
    capture_dir.mkdir(parents=True, exist_ok=True)
    old = capture_dir / "screen_1.png"
    new = capture_dir / "screen_2.png"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (1000.0, 1000.0))
    os.utime(new, (5000.0, 5000.0))
    removed = vc.purge_captures(keep_s=1000.0, now=5000.0)
    assert removed == 1
    assert not old.exists()
    assert new.exists()


# ------------------------------------------------------------------ capture+describe

def test_capture_summarises_then_deletes_the_images(monkeypatch, capture_dir):
    capture_dir.mkdir(parents=True, exist_ok=True)
    img = capture_dir / "screen_1.png"
    img.write_bytes(b"png")

    import distr.core.agent.tools.vision.screen_capture as sc
    monkeypatch.setattr(sc, "capture_all_screens", lambda d: [str(img)])
    monkeypatch.setattr(sc, "image_to_base64", lambda p, **k: ("BASE64", "image/png"))

    from distr.core.agent.tools.vision import vision_api
    monkeypatch.setattr(vision_api, "resolve_vision_llm_config", lambda s: ("openai", "gpt-4o"))

    from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool
    monkeypatch.setattr(
        ScreenshotAnalyzerTool, "_call_vision_api",
        staticmethod(lambda *a, **k: "Working in Warp on the initiative module."),
    )

    out = vc.capture_and_describe({"initiative_awareness_depth": "visual"})
    assert out == "Working in Warp on the initiative module."
    # the pixels must not be kept
    assert not img.exists()


def test_capture_is_empty_when_the_vision_model_refuses(monkeypatch, capture_dir):
    capture_dir.mkdir(parents=True, exist_ok=True)
    img = capture_dir / "screen_1.png"
    img.write_bytes(b"png")

    import distr.core.agent.tools.vision.screen_capture as sc
    monkeypatch.setattr(sc, "capture_all_screens", lambda d: [str(img)])
    monkeypatch.setattr(sc, "image_to_base64", lambda p, **k: ("BASE64", "image/png"))

    from distr.core.agent.tools.vision import vision_api
    monkeypatch.setattr(vision_api, "resolve_vision_llm_config", lambda s: ("openai", "gpt-4o"))

    from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool
    monkeypatch.setattr(ScreenshotAnalyzerTool, "_call_vision_api", staticmethod(lambda *a, **k: ""))

    assert vc.capture_and_describe({"initiative_awareness_depth": "visual"}) == ""
    assert not img.exists()


def test_capture_is_empty_without_a_vision_model(monkeypatch, capture_dir):
    capture_dir.mkdir(parents=True, exist_ok=True)
    img = capture_dir / "screen_1.png"
    img.write_bytes(b"png")

    import distr.core.agent.tools.vision.screen_capture as sc
    monkeypatch.setattr(sc, "capture_all_screens", lambda d: [str(img)])

    from distr.core.agent.tools.vision import vision_api
    monkeypatch.setattr(vision_api, "resolve_vision_llm_config", lambda s: ("", ""))

    assert vc.capture_and_describe({"initiative_awareness_depth": "visual"}) == ""


def test_capture_never_raises_when_capture_explodes(monkeypatch, capture_dir):
    import distr.core.agent.tools.vision.screen_capture as sc

    def boom(d):
        raise RuntimeError("no display")

    monkeypatch.setattr(sc, "capture_all_screens", boom)
    assert vc.capture_and_describe({"initiative_awareness_depth": "visual"}) == ""


def test_digest_line_prefixes_the_description(monkeypatch, capture_dir):
    monkeypatch.setattr(vc, "capture_and_describe", lambda s: "Warp and Brave")
    assert vc.visual_digest_line({}) == "on screen: Warp and Brave"
    monkeypatch.setattr(vc, "capture_and_describe", lambda s: "")
    assert vc.visual_digest_line({}) == ""
