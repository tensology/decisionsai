import copy

import pytest

from distr.core.llm_factory import _provider_image_messages


@pytest.mark.parametrize("provider", ["ollama", "anthropic"])
def test_inline_images_translate_without_mutating_messages(provider):
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "Review this screen"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}}]}]
    original = copy.deepcopy(messages)
    result = _provider_image_messages(messages, provider)
    assert messages == original
    if provider == "ollama":
        assert result[0]["content"] == "Review this screen"
        assert result[0]["images"] == [b"abc"]
    else:
        assert result[0]["content"][1] == {"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": "YWJj"}}


@pytest.mark.parametrize("url", ["https://example.com/private.png", "file:///etc/passwd", "data:image/svg+xml;base64,YWJj", "data:image/png;base64,A"])
def test_invalid_image_sources_are_rejected_without_fetching(url):
    with pytest.raises(ValueError):
        _provider_image_messages([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}], "ollama")


def test_text_and_native_blocks_are_unchanged():
    messages = [{"role": "system", "content": "System"}, {"role": "user", "content": "Text"},
                {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "YWJj"}}]}]
    assert _provider_image_messages(messages, "anthropic") == messages
