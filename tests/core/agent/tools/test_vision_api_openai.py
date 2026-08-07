from types import SimpleNamespace


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_openai_vision_uses_high_detail_and_json_for_summary(monkeypatch):
    from distr.core.agent.tools.vision import vision_api

    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _response('{"type":"summary","summary":"Codex is visible."}')

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr("openai.OpenAI", lambda **_kwargs: client)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"openai_key": "test-key"},
    )

    result = vision_api.call_openai_vision(
        ["image-bytes"],
        "Describe the screenshot as JSON.",
        "gpt-4o",
        False,
        image_mimes=["image/png"],
    )

    assert "Codex is visible" in result
    assert calls[0]["response_format"] == {"type": "json_object"}
    image_item = calls[0]["messages"][0]["content"][1]
    assert image_item["image_url"]["detail"] == "high"
    assert image_item["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_vision_retries_false_image_refusal(monkeypatch):
    from distr.core.agent.tools.vision import vision_api

    responses = [
        _response("I'm unable to analyze an image directly, but you can describe it."),
        _response('{"type":"summary","summary":"The Codex window shows completed validation."}'),
    ]
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return responses.pop(0)

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr("openai.OpenAI", lambda **_kwargs: client)
    monkeypatch.setattr(
        "distr.core.settings.load_settings_from_db",
        lambda: {"openai_key": "test-key"},
    )

    result = vision_api.call_openai_vision(
        ["image-bytes"],
        "Summarize the work shown.",
        "gpt-4o",
        False,
    )

    assert len(calls) == 2
    retry_prompt = calls[1]["messages"][0]["content"][0]["text"]
    assert "screenshot is attached" in retry_prompt
    assert "completed validation" in result


def test_false_image_refusal_detects_process_or_view_disclaimer():
    from distr.core.agent.tools.vision.vision_api import _is_false_image_refusal

    assert _is_false_image_refusal(
        "I'm unable to create or process screenshots or directly view images on your device."
    )


def test_false_image_refusal_does_not_reject_normal_visual_summary():
    from distr.core.agent.tools.vision.vision_api import _is_false_image_refusal

    assert not _is_false_image_refusal(
        "The screenshot shows a task that cannot proceed until the current test process completes."
    )
