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
