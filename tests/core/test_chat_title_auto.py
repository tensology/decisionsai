"""Tests for lightweight automatic chat title refresh."""

from distr.core.chat_title_auto import (
    TITLE_CONTEXT_MESSAGE_COUNT,
    TITLE_REFRESH_MESSAGE_INTERVAL,
    _fallback_chat_title,
    maybe_refresh_chat_title,
    suggest_chat_title,
)


def test_interval_constants():
    assert TITLE_REFRESH_MESSAGE_INTERVAL == 5
    assert TITLE_CONTEXT_MESSAGE_COUNT == 5


def test_fallback_title_uses_latest_user_message():
    messages = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "SSH access for prod"},
    ]
    assert _fallback_chat_title(messages) == "SSH access for prod"


def test_suggest_chat_title_uses_fallback_without_models(monkeypatch):
    monkeypatch.setattr(
        "distr.core.chat_title_auto._lightweight_title_models",
        lambda settings: [],
    )
    messages = [
        {"role": "user", "content": "Need dev server credentials"},
        {"role": "assistant", "content": "Sure, checking notes."},
    ]
    title = suggest_chat_title(messages, settings={})
    assert title == "Need dev server credentials"


def test_maybe_refresh_chat_title_skips_until_interval():
    class FakeChat:
        title = "Old title"
        additional_context = "{}"
        modified_date = None

    chat = FakeChat()
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]

    class FakeSession:
        def commit(self):
            return None

    result = maybe_refresh_chat_title(FakeSession(), chat, messages, settings={}, force=False)
    assert result is None
    assert chat.title == "Old title"


def test_maybe_refresh_chat_title_updates_on_interval(monkeypatch):
    class FakeChat:
        title = "Old title"
        additional_context = "{}"
        modified_date = None

    chat = FakeChat()
    messages = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
        {"role": "user", "content": "five"},
    ]

    monkeypatch.setattr(
        "distr.core.chat_title_auto.suggest_chat_title",
        lambda msgs, settings: "Planning server access",
    )

    class FakeSession:
        committed = False

        def commit(self):
            self.committed = True

    session = FakeSession()
    result = maybe_refresh_chat_title(session, chat, messages, settings={}, force=False)
    assert result == "Planning server access"
    assert chat.title == "Planning server access"
    assert session.committed is True
