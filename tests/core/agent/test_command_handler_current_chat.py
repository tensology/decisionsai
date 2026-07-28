from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.core.agent import command_handler


def test_delayed_ptt_resume_is_ignored_after_key_release(monkeypatch):
    scheduled = []
    loop = MagicMock()
    loop.is_running.return_value = True
    loop.call_later.side_effect = lambda _delay, callback: scheduled.append(callback)
    set_audio = MagicMock()
    monkeypatch.setattr(command_handler, "_set_audio_input_active", set_audio)
    session = SimpleNamespace(
        is_listening=True,
        is_hands_free=False,
        is_dictating=False,
        ptt_active=False,
        stt_service=None,
        llm_service=None,
        tts_service=None,
        runner=SimpleNamespace(_loop=loop),
        _main_loop=None,
        logger=MagicMock(),
    )

    command_handler._cmd_push_to_talk_start(session, {})
    assert len(scheduled) == 1
    set_audio.reset_mock()

    session.ptt_active = False
    scheduled[0]()

    set_audio.assert_not_called()


def test_duplicate_ptt_stop_does_not_authorize_a_transcript(monkeypatch):
    monkeypatch.setattr(command_handler, "_schedule_audio_input_idle_pause", MagicMock())
    llm = MagicMock()
    session = SimpleNamespace(
        is_dictating=False,
        ptt_active=False,
        stt_service=None,
        llm_service=llm,
        tts_service=None,
        transport=None,
        logger=MagicMock(),
    )

    command_handler._cmd_push_to_talk_stop(session, {})

    llm.set_ptt_active.assert_called_once_with(False, expect_transcript=False)


def test_agent_ptt_authorization_does_not_depend_on_stale_dictation_state(monkeypatch):
    """A completed dictation must not make the next agent PTT transcript invalid."""
    monkeypatch.setattr(command_handler, "_set_audio_input_active", MagicMock())
    monkeypatch.setattr(command_handler, "_schedule_audio_input_idle_pause", MagicMock())
    llm = MagicMock()
    session = SimpleNamespace(
        is_listening=True,
        is_hands_free=False,
        is_dictating=True,
        ptt_active=False,
        stt_service=None,
        llm_service=llm,
        tts_service=None,
        transport=None,
        runner=SimpleNamespace(_loop=None),
        _main_loop=None,
        logger=MagicMock(),
    )

    command_handler._cmd_push_to_talk_start(session, {})
    command_handler._cmd_push_to_talk_stop(session, {})

    assert session._ptt_for_dictation is False
    assert llm.set_ptt_active.call_args_list[-1].kwargs == {"expect_transcript": True}


def test_dictation_ptt_release_does_not_authorize_agent_transcript(monkeypatch):
    monkeypatch.setattr(command_handler, "_set_audio_input_active", MagicMock())
    monkeypatch.setattr(command_handler, "_schedule_audio_input_idle_pause", MagicMock())
    llm = MagicMock()
    session = SimpleNamespace(
        is_listening=True,
        is_hands_free=False,
        is_dictating=True,
        ptt_active=False,
        stt_service=None,
        llm_service=llm,
        tts_service=None,
        transport=None,
        runner=SimpleNamespace(_loop=None),
        _main_loop=None,
        logger=MagicMock(),
    )

    command_handler._cmd_push_to_talk_start(session, {"for_dictation": True})
    command_handler._cmd_push_to_talk_stop(session, {})

    assert llm.set_ptt_active.call_args_list[-1].kwargs == {"expect_transcript": False}


def test_dictation_hotkey_release_stops_worker_capture_without_waiting_for_ui(monkeypatch):
    monkeypatch.setattr(command_handler, "_schedule_audio_input_idle_pause", MagicMock())
    llm = MagicMock()
    stt = MagicMock()
    session = SimpleNamespace(
        is_dictating=True,
        ptt_active=True,
        stt_service=stt,
        llm_service=llm,
        tts_service=None,
        transport=None,
        logger=MagicMock(),
    )

    command_handler._cmd_dictation_hotkey_released(session, {})

    assert session.is_dictating is False
    stt.set_ptt_active.assert_called_once_with(False)
    stt.set_dictating.assert_called_once_with(False)
    llm._finish_dictation_after_pending_transcript.assert_called_once_with()


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
