"""Tests for lightweight automatic chat title refresh."""

from distr.core.chat_title_auto import (
    TITLE_CONTEXT_MESSAGE_COUNT,
    TITLE_REFRESH_MESSAGE_INTERVAL,
    _fallback_chat_title,
    maybe_refresh_chat_title,
    suggest_chat_title,
)


def test_interval_constants():
    assert TITLE_REFRESH_MESSAGE_INTERVAL == 2
    assert TITLE_CONTEXT_MESSAGE_COUNT == 5


def test_fallback_title_uses_latest_user_message():
    messages = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "SSH access for prod"},
    ]
    assert _fallback_chat_title(messages) == "SSH access for prod"


def test_lightweight_title_models_prefers_openai_and_skips_missing_ollama(monkeypatch):
    from distr.core.chat_title_auto import _lightweight_title_models

    monkeypatch.setattr(
        "distr.core.chat_title_auto._ollama_installed_models",
        lambda settings: set(),
    )
    models = _lightweight_title_models(
        {"openai_enabled": True, "openai_key": "sk-test", "ollama_url": "http://localhost:11434/"}
    )
    assert models[0] == ("openai", "gpt-4o-mini")
    assert all(provider != "ollama" for provider, _ in models)


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
    ]

    monkeypatch.setattr(
        "distr.core.chat_title_auto.suggest_chat_title",
        lambda msgs, settings: "Planning server access",
    )

    class FakeSession:
        committed = False

        def flush(self):
            pass

        def refresh(self, chat, attribute_names):
            pass

        def commit(self):
            self.committed = True

    session = FakeSession()
    result = maybe_refresh_chat_title(session, chat, messages, settings={}, force=False)
    assert result == "Planning server access"
    assert chat.title == "Planning server access"
    assert session.committed is True


def test_latest_intent_titles_preserve_manual_names_and_skin_metadata(monkeypatch):
    import json
    from distr.core.db import Chat, get_session

    monkeypatch.setattr('distr.core.chat_title_auto.suggest_chat_title', lambda msgs, settings: msgs[-2]['content'])
    with get_session() as session:
        chat = Chat(title='Old topic', additional_context=json.dumps({'skin_name': 'character'}))
        session.add(chat); session.commit()
        messages = [{'role': 'user', 'content': 'Fix sidebar'}, {'role': 'assistant', 'content': 'Done'}]
        assert maybe_refresh_chat_title(session, chat, messages, settings={'ollama_url': ''}) == 'Fix sidebar'
        assert json.loads(chat.additional_context)['skin_name'] == 'character'
        messages += [{'role': 'user', 'content': 'Plan deployment'}, {'role': 'assistant', 'content': 'Plan ready'}]
        assert maybe_refresh_chat_title(session, chat, messages, settings={'ollama_url': ''}) == 'Plan deployment'
        context = json.loads(chat.additional_context)
        context['title_auto']['manual'] = True
        chat.title = 'My chosen name'; chat.additional_context = json.dumps(context); session.commit()
        messages += [{'role': 'user', 'content': 'Another topic'}, {'role': 'assistant', 'content': 'Reply'}]
        assert maybe_refresh_chat_title(session, chat, messages, settings={'ollama_url': ''}) is None
        assert chat.title == 'My chosen name'


def test_recent_intent_uses_insertion_order_even_with_future_dated_history():
    from datetime import datetime
    from distr.core.db import Chat, get_session
    from distr.core.chat_title_auto import _load_chat_messages

    with get_session() as session:
        chat = Chat(title='Future history')
        session.add(chat); session.flush()
        session.add(Chat(parent_id=chat.id, input='Old topic', response='Old answer', created_date=datetime(2099, 1, 1)))
        session.flush()
        session.add(Chat(parent_id=chat.id, input='Latest intent', response='Current answer'))
        session.flush()
        assert _load_chat_messages(session, chat.id)[-2]['content'] == 'Latest intent'
