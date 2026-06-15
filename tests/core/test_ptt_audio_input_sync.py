"""Regression tests for PTT / dictation mic resume after idle pause."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from distr.core.agent import command_handler


def _session_with_input_transport(input_transport):
    session = SimpleNamespace(
        transport=SimpleNamespace(input=lambda: input_transport),
        runner=SimpleNamespace(_loop=None),
        _main_loop=None,
        logger=MagicMock(),
    )
    return session


def test_set_audio_input_active_skips_resume_when_healthy():
    input_transport = MagicMock()
    input_transport.get_input_health.return_value = {
        "enabled": True,
        "stream_active": True,
        "audio_task_alive": True,
        "stream_callbacks_stale": False,
    }

    command_handler._set_audio_input_active(
        _session_with_input_transport(input_transport),
        True,
        "test",
    )

    input_transport.resume_input.assert_not_called()


def test_set_audio_input_active_resumes_when_callbacks_stale():
    input_transport = MagicMock()
    input_transport.get_input_health.return_value = {
        "enabled": True,
        "stream_active": True,
        "audio_task_alive": True,
        "stream_callbacks_stale": True,
    }

    command_handler._set_audio_input_active(
        _session_with_input_transport(input_transport),
        True,
        "test",
    )

    input_transport.resume_input.assert_called_once()


def test_set_audio_input_active_force_resumes_even_when_healthy():
    input_transport = MagicMock()
    input_transport.get_input_health.return_value = {
        "enabled": True,
        "stream_active": True,
        "audio_task_alive": True,
        "stream_callbacks_stale": False,
    }

    command_handler._set_audio_input_active(
        _session_with_input_transport(input_transport),
        True,
        "push_to_talk_start",
        force=True,
    )

    input_transport.resume_input.assert_called_once()


def test_ptt_start_forces_mic_resume_before_stt_arms():
    input_transport = MagicMock()
    input_transport.get_input_health.return_value = {
        "enabled": True,
        "stream_active": True,
        "audio_task_alive": True,
        "stream_callbacks_stale": False,
    }
    stt_service = MagicMock()
    stt_service._FrameProcessor__started = True
    calls = []

    def _set_ptt(active, *, queue_interruption=False):
        calls.append(("set_ptt", active, queue_interruption))

    stt_service.set_ptt_active.side_effect = _set_ptt

    session = SimpleNamespace(
        transport=SimpleNamespace(input=lambda: input_transport),
        runner=SimpleNamespace(_loop=None),
        _main_loop=None,
        logger=MagicMock(),
        ptt_active=False,
        tts_service=None,
        stt_service=stt_service,
        llm_service=None,
    )

    command_handler._cmd_push_to_talk_start(session, {})

    input_transport.resume_input.assert_called_once()
    assert calls == [("set_ptt", True, False)]
    assert session.ptt_active is True
