"""The vision capability probe must not guess from the model name."""

import base64
import io

import pytest

from distr.core.agent.tools.vision import vision_probe


def test_probe_image_is_a_real_png():
    from PIL import Image

    raw = base64.b64decode(vision_probe._solid_colour_png((220, 40, 40), size=8))
    img = Image.open(io.BytesIO(raw))
    assert img.size == (8, 8)
    assert img.convert("RGB").getpixel((4, 4)) == (220, 40, 40)


def test_probe_reports_missing_model_without_calling():
    result = vision_probe.probe_vision_capability("openai", "")
    assert result["ok"] is False
    assert "No vision model configured" in result["reason"]


@pytest.mark.parametrize(
    "reply,ok",
    [
        ('{"colour":"red"}', True),
        ('{"colour": "red"}', True),
        ('{"colour":"NO_IMAGE"}', False),
        ("I cannot see an image.", False),
        ("Error: Connection issue with OpenAI API.", False),
    ],
)
def test_probe_classifies_the_reply(monkeypatch, reply, ok):
    # Pin the colour so the assertion is deterministic.
    monkeypatch.setattr(vision_probe.random, "choice", lambda pairs: pairs[0])
    from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool

    monkeypatch.setattr(
        ScreenshotAnalyzerTool,
        "_call_vision_api",
        staticmethod(lambda *a, **k: reply),
    )
    result = vision_probe.probe_vision_capability("openai", "some-model")
    assert result["ok"] is ok, result


def test_probe_never_raises_on_a_failing_call(monkeypatch):
    from distr.core.agent.tools.vision.screenshot_analyzer import ScreenshotAnalyzerTool

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(ScreenshotAnalyzerTool, "_call_vision_api", staticmethod(boom))
    result = vision_probe.probe_vision_capability("openai", "some-model")
    assert result["ok"] is False
    assert "Probe call failed" in result["reason"]
