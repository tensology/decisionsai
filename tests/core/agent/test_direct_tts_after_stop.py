"""Stop, then speak again: the second direct utterance must be synthesised audibly.
No real TTS: fake TTS service and fake transport output."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace


class LLMFullResponseStartFrame:
    pass


class LLMFullResponseEndFrame:
    pass


class TextFrame:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeOut:
    def __init__(self) -> None:
        self._state = SimpleNamespace(name="PLAYING")
        self._force_silence = False
        self._pipeline_cut = False
        self._out_stream = None


class _FakeTTS:
    """Mimics the real gate: TextFrames are dropped while _cancelled; audio written
    while transport._force_silence is True is silence. Deliberately does NOT clear
    flags on LLMFullResponseStartFrame (models the late/missing-frame case)."""

    def __init__(self, out: _FakeOut) -> None:
        self._cancelled = False
        self._cancelled_since = 0.0
        self._text_buffer = ""
        self._force_desktop_tts = False
        self.out = out
        self.audible: list[str] = []
        self.silent_or_dropped: list[str] = []

    async def process_frame(self, frame, direction) -> None:
        if isinstance(frame, TextFrame):
            if self._cancelled or self.out._force_silence or self.out._pipeline_cut:
                self.silent_or_dropped.append(frame.text)
            else:
                self.audible.append(frame.text)


def _session(monkeypatch):
    from distr.core.agent import command_handler, libs

    monkeypatch.setattr(libs, "LLMFullResponseStartFrame", LLMFullResponseStartFrame)
    monkeypatch.setattr(libs, "TextFrame", TextFrame)
    monkeypatch.setattr(libs, "LLMFullResponseEndFrame", LLMFullResponseEndFrame)
    loop = asyncio.new_event_loop()
    out = _FakeOut()
    tts = _FakeTTS(out)
    session = SimpleNamespace(
        logger=logging.getLogger("test.direct_tts_stop"),
        llm_service=SimpleNamespace(_cancelled=False, _pipeline_direction=object()),
        tts_service=tts,
        runner=SimpleNamespace(_loop=loop),
        transport=SimpleNamespace(_output=out),
        _welcome_task=None,
        stt_service=None,
        event_queue=None,
    )

    def run_now(coro, _loop):
        loop.run_until_complete(coro)
        return SimpleNamespace(result=lambda timeout=None: None)

    monkeypatch.setattr(command_handler.asyncio, "run_coroutine_threadsafe", run_now)
    return command_handler, session, tts, out, loop


def test_stop_then_speak_again_is_synthesised(monkeypatch):
    ch, session, tts, out, loop = _session(monkeypatch)
    text = "The main feature is the Oracle."
    try:
        ch._cmd_speak_text_directly(session, {"text": text})
        assert tts.audible, "first utterance should be audible"
        ch._cmd_interrupt_tts(session, {})  # Stop while PLAYING
        assert tts._cancelled is True and out._force_silence is True
        first = len(tts.audible)
        ch._cmd_speak_text_directly(session, {"text": text})  # same text, right after Stop
    finally:
        loop.close()
    assert len(tts.audible) > first, "second utterance after Stop must be synthesised audibly"
    assert not tts.silent_or_dropped
    assert out._force_silence is False and tts._cancelled is False


def test_stop_still_applies_to_current_utterance(monkeypatch):
    ch, session, tts, out, loop = _session(monkeypatch)
    loop.close()
    ch._cmd_interrupt_tts(session, {})
    # Stop leaves the current utterance cancelled/silenced until a NEW utterance starts.
    assert tts._cancelled is True and out._force_silence is True
    assert session._last_direct_tts_text == ""


def test_reset_helper_reports_previous_state(monkeypatch):
    ch, session, tts, out, loop = _session(monkeypatch)
    loop.close()
    tts._cancelled = True
    out._force_silence = True
    out._pipeline_cut = True
    before = ch._reset_stop_state_for_direct_tts(session, "t1")
    assert before == {"tts_cancelled": True, "transport_state": "PLAYING", "force_silence": True, "pipeline_cut": True}
    assert (tts._cancelled, out._force_silence, out._pipeline_cut) == (False, False, False)
