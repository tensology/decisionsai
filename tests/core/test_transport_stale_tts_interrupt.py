"""PTT interrupt must not silence the TTS response it just captured."""

import asyncio
import time
from types import SimpleNamespace

from distr.core.agent.command_handler import _cmd_interrupt_tts
from distr.core.agent.libs import InterruptionFrame
from distr.core.agent.transport import AudioPlaybackState, HotSwappableLocalAudioOutputTransport


def _transport(**overrides):
    transport = HotSwappableLocalAudioOutputTransport.__new__(
        HotSwappableLocalAudioOutputTransport
    )
    transport._pipeline_cut = False
    transport._force_silence = True
    transport._state = AudioPlaybackState.IDLE
    transport._tts_response_started_at = 0.0
    transport._STALE_INTERRUPT_GRACE_SEC = 2.0
    for key, value in overrides.items():
        setattr(transport, key, value)
    return transport


def test_begin_tts_response_unmutes_and_opens_stale_window():
    transport = _transport()
    transport._begin_tts_response()
    assert transport._force_silence is False
    assert transport._pipeline_cut is False
    assert transport._is_stale_tts_interrupt() is True


def test_stale_window_expires():
    transport = _transport(
        _tts_response_started_at=time.monotonic() - 3.0,
        _STALE_INTERRUPT_GRACE_SEC=2.0,
    )
    assert transport._is_stale_tts_interrupt() is False


def test_interruption_while_idle_does_not_keep_output_muted():
    transport = _transport()

    asyncio.run(transport.process_frame(InterruptionFrame(), None))

    assert transport._force_silence is False
    assert transport._pipeline_cut is False
    assert transport._state is AudioPlaybackState.IDLE


def test_stale_interrupt_after_response_start_is_ignored():
    transport = _transport(_state=AudioPlaybackState.PLAYING)
    transport._begin_tts_response()

    asyncio.run(transport.process_frame(InterruptionFrame(), None))

    assert transport._force_silence is False
    assert transport._state is AudioPlaybackState.PLAYING


def _interrupt_session(state_name: str, force_silence: bool = False):
    transport_out = SimpleNamespace(
        _state=SimpleNamespace(name=state_name),
        _force_silence=force_silence,
        _out_stream=None,
    )
    return transport_out, SimpleNamespace(
        logger=SimpleNamespace(info=lambda *_a, **_k: None, debug=lambda *_a, **_k: None),
        llm_service=None,
        tts_service=None,
        _welcome_task=None,
        transport=SimpleNamespace(_output=transport_out),
        event_queue=None,
        runner=None,
        _main_loop=None,
        stt_service=None,
    )


def test_interrupt_tts_when_idle_does_not_force_silence():
    transport_out, session = _interrupt_session("IDLE", force_silence=True)
    _cmd_interrupt_tts(session, {})
    assert transport_out._force_silence is False


def test_interrupt_tts_while_playing_still_mutes():
    transport_out, session = _interrupt_session("PLAYING", force_silence=False)
    _cmd_interrupt_tts(session, {})
    assert transport_out._force_silence is True
