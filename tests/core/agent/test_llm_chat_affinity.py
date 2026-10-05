import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.core.agent.services.llm.core_mixin import LLMSharedMixin


def test_async_turn_reasserts_requested_chat_after_another_input_switched_it():
    chat_manager = MagicMock()
    chat_manager.get_current_chat.return_value = 99
    service = SimpleNamespace(
        chat_manager=chat_manager,
        on_chat_changed=MagicMock(),
    )

    LLMSharedMixin._activate_requested_chat_for_turn(service, 42)

    chat_manager.set_current_chat.assert_called_once_with(42)
    service.on_chat_changed.assert_called_once_with(42)


def test_telegram_provider_rejection_stops_typing_and_returns_error(monkeypatch):
    class Session:
        def get(self, _model, _chat_id):
            return SimpleNamespace(provider="OpenAI")

        def close(self):
            pass

    event_queue = MagicMock()
    service = SimpleNamespace(
        chat_manager=SimpleNamespace(get_current_chat=lambda: 381),
        _get_provider_name=lambda: "OpenRouter",
        _activate_requested_chat_for_turn=lambda _chat_id: None,
        _emit_telegram_response=MagicMock(),
        _cleanup_telegram_flags=MagicMock(),
        event_queue=event_queue,
    )
    monkeypatch.setattr("distr.core.db.get_session", lambda: Session())

    try:
        asyncio.run(
            LLMSharedMixin.process_chat_input(
                service,
                "open this link",
                is_telegram=True,
            )
        )
    finally:
        import threading

        threading.current_thread().telegram_request = False

    assert "chat uses OpenAI" in service._telegram_fallback_text
    service._emit_telegram_response.assert_called_once_with("", "")
    event_queue.put.assert_called_once_with(
        ('typing_indicator_changed', {'show': False}),
        block=False,
    )
    service._cleanup_telegram_flags.assert_called_once_with()
