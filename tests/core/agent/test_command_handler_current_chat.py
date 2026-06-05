from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.core.agent import command_handler


def test_process_text_input_honors_requested_chat_before_appending(monkeypatch):
    monkeypatch.setattr(command_handler, "_chat_id_exists_for_input", lambda chat_id: True)
    chat_manager = MagicMock()
    llm_service = MagicMock()
    llm_service._speaker_enabled = True
    session = SimpleNamespace(
        _welcome_task=None,
        tts_service=None,
        llm_service=llm_service,
        chat_manager=chat_manager,
        runner=None,
        _main_loop=None,
        logger=MagicMock(),
    )

    command_handler._cmd_process_text_input(session, {
        "text": "[Workflow Report]\nDone",
        "chat_id": 77,
        "speak": False,
    })

    assert session._agent_current_chat_id_from_signal == 77
    chat_manager.set_current_chat.assert_called_once_with(77)
    llm_service.on_chat_changed.assert_called_once_with(77)
    assert session._pending_text_inputs[0][0] == "[Workflow Report]\nDone"


def test_process_text_input_ignores_stale_requested_chat(monkeypatch):
    monkeypatch.setattr(command_handler, "_chat_id_exists_for_input", lambda chat_id: False)
    chat_manager = MagicMock()
    llm_service = MagicMock()
    llm_service._speaker_enabled = True
    session = SimpleNamespace(
        _welcome_task=None,
        tts_service=None,
        llm_service=llm_service,
        chat_manager=chat_manager,
        runner=None,
        _main_loop=None,
        logger=MagicMock(),
    )

    command_handler._cmd_process_text_input(session, {
        "text": "[Workflow Report]\nDone",
        "chat_id": 404,
        "speak": False,
    })

    assert not hasattr(session, "_agent_current_chat_id_from_signal")
    chat_manager.set_current_chat.assert_not_called()
    llm_service.on_chat_changed.assert_not_called()
