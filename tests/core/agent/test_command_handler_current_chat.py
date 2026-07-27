from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.core.agent import command_handler


def test_explicit_hands_free_disable_clears_worker_dictation_restore():
    llm_service = MagicMock()
    llm_service._hands_free_before_dictation = True
    session = SimpleNamespace(
        is_hands_free=True,
        stt_service=MagicMock(),
        llm_service=llm_service,
        tts_service=MagicMock(),
        logger=MagicMock(),
    )

    command_handler._cmd_set_hands_free(
        session,
        {"enabled": False, "clear_pending_restore": True},
    )

    assert session.is_hands_free is False
    assert llm_service._hands_free_before_dictation is False
    session.stt_service.set_hands_free.assert_called_once_with(False)
    llm_service.set_hands_free.assert_called_once_with(False)
    session.tts_service.set_hands_free.assert_called_once_with(False)


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
    assert session._pending_text_inputs[0] == {
        "text": "[Workflow Report]\nDone",
        "chat_id": 77,
        "speak": False,
    }


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


def test_process_text_input_preserves_full_request_until_loop_is_ready(monkeypatch):
    monkeypatch.setattr(command_handler, "_chat_id_exists_for_input", lambda _chat_id: True)
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
    params = {
        "text": "Research this without speaking.",
        "chat_id": 77,
        "speak": False,
        "skip_user_persist": True,
        "work_intake_uid": "intake-77",
    }

    command_handler._cmd_process_text_input(session, params)

    assert session._pending_text_inputs == [params]
