"""Playback must end once per reply, not once per sentence.

Regression for the continuous-mode jank (23 Nov 2026): the new full-duplex
transports treated every per-sentence TTSStoppedFrame as the end of playback.
When the next sentence was still synthesising the buffer drained, the desktop
got playback_finished (player hidden, avatar idle) and then a fresh tts_started
with a new playback id for the next sentence. A stale helper STATS report could
also make the VPIO output finish within milliseconds of starting.
"""

from __future__ import annotations

import asyncio
import queue
import struct
import time
from types import SimpleNamespace

import numpy as np

from distr.core.agent import macos_vpio_transport as vpio_mod
from distr.core.agent import mediadevices_transport as md_mod
from distr.core.agent import sd_apm_transport as sd_mod
from distr.core.agent.libs import (
    AudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from distr.core.agent.transport import AudioPlaybackState
from distr.core.audio.echo_canceller import ReferenceBuffer

GAP_SECS = 1.2
# 100 ms of 16 kHz mono int16
CHUNK_16K = (np.ones(1600, dtype=np.int16) * 3000).tobytes()


def _events(evq):
    out = []
    while True:
        try:
            out.append(evq.get_nowait())
        except queue.Empty:
            return out


def _fast(monkeypatch, mod):
    monkeypatch.setattr(mod, "_FINISH_GRACE_SECS", 0.05, raising=True)


def _make_vpio(evq, ref_buf=None):
    t = vpio_mod.MacOsVpioAudioTransport(
        pipeline_sample_rate=16000,
        output_sample_rate=16000,
        vad_enabled=False,
        event_queue=evq,
        aec_reference_buffer=ref_buf,
        helper_binary="/usr/bin/true",
    )

    class _FakeStdin:
        def write(self, data):
            struct.unpack("<BI", data[:5])

        async def drain(self):
            pass

    t._proc = SimpleNamespace(stdin=_FakeStdin(), stdout=None, returncode=None, pid=1)
    out = t.output()
    out._started = True

    async def noop(frame, direction=None):
        pass

    out.push_frame = noop
    return t, out


def _make_sdapm(evq, ref_buf=None):
    t = sd_mod.SdApmAudioTransport(
        input_sample_rate=16000,
        output_sample_rate=16000,
        vad_enabled=False,
        event_queue=evq,
        aec_reference_buffer=ref_buf,
    )
    out = t.output()
    out._started = True
    t._actual_output_rate = 16000

    async def noop(frame, direction=None):
        pass

    out.push_frame = noop
    return t, out


def _make_mediadevices(evq):
    async def capture_frame(_frame):
        return None

    holder = SimpleNamespace(
        _output_sample_rate=16000,
        _tts_source=SimpleNamespace(capture_frame=capture_frame),
        _rtc_audio_frame=lambda pcm, sr, ch, n: pcm,
        _ref_buf=None,
        _event_queue=evq,
        _playback_id=None,
        _player=SimpleNamespace(_buffer=bytearray()),
    )

    def new_playback_id():
        import uuid

        holder._playback_id = str(uuid.uuid4())
        return holder._playback_id

    holder.new_playback_id = new_playback_id
    out = md_mod.MediaDevicesOutputTransport(holder)
    out._started = True

    async def noop(frame, direction=None):
        pass

    out.push_frame = noop
    return holder, out


async def _two_sentence_reply(out, after_write=None):
    await out.process_frame(LLMFullResponseStartFrame(), None)
    for _sentence in range(2):
        await out.process_frame(TTSStartedFrame(), None)
        await out.process_frame(AudioRawFrame(audio=CHUNK_16K, sample_rate=16000, num_channels=1), None)
        if after_write:
            after_write()
        await out.process_frame(TTSStoppedFrame(), None)
        # Next sentence still synthesising: the speaker buffer runs dry.
        await asyncio.sleep(GAP_SECS)
    await out.process_frame(LLMFullResponseEndFrame(), None)
    await asyncio.sleep(0.6)


def _assert_one_start_one_finish(evq):
    evs = _events(evq)
    starts = [d for e, d in evs if e == "tts_started"]
    finishes = [d for e, d in evs if e == "playback_finished"]
    assert len(starts) == 1, evs
    assert len(finishes) == 1, evs
    assert starts[0]["playback_id"] == finishes[0]["playback_id"]
    assert starts[0]["playback_id"]


def test_vpio_two_sentences_with_gap_is_one_playback(monkeypatch):
    _fast(monkeypatch, vpio_mod)
    evq = queue.Queue()
    ref_buf = ReferenceBuffer(max_duration_secs=1.0, sample_rate=16000)
    _t, out = _make_vpio(evq, ref_buf)

    async def scenario():
        await out.process_frame(LLMFullResponseStartFrame(), None)
        await out.process_frame(TTSStartedFrame(), None)
        await out.process_frame(AudioRawFrame(audio=CHUNK_16K, sample_rate=16000, num_channels=1), None)
        await out.process_frame(TTSStoppedFrame(), None)
        await asyncio.sleep(GAP_SECS)
        # Mid-reply gap: still "speaking", AEC reference still active.
        assert out._state != AudioPlaybackState.IDLE
        assert ref_buf.is_active is True
        await out.process_frame(TTSStartedFrame(), None)
        await out.process_frame(AudioRawFrame(audio=CHUNK_16K, sample_rate=16000, num_channels=1), None)
        await out.process_frame(TTSStoppedFrame(), None)
        await out.process_frame(LLMFullResponseEndFrame(), None)
        await asyncio.sleep(0.6)
        assert out._state == AudioPlaybackState.IDLE
        assert ref_buf.is_active is False

    asyncio.run(scenario())
    _assert_one_start_one_finish(evq)


def test_sdapm_two_sentences_with_gap_is_one_playback(monkeypatch):
    _fast(monkeypatch, sd_mod)
    evq = queue.Queue()
    t, out = _make_sdapm(evq)
    # No real output stream: simulate the device consuming the buffer.
    asyncio.run(_two_sentence_reply(out, after_write=t._out_buf.clear))
    _assert_one_start_one_finish(evq)
    assert out._state == AudioPlaybackState.IDLE


def test_mediadevices_two_sentences_with_gap_is_one_playback(monkeypatch):
    _fast(monkeypatch, md_mod)
    evq = queue.Queue()
    _holder, out = _make_mediadevices(evq)
    asyncio.run(_two_sentence_reply(out))
    _assert_one_start_one_finish(evq)
    assert out._active is False


def test_standalone_utterance_without_reply_wrapper_still_finishes(monkeypatch):
    """Direct speech with no LLMFullResponse frames must not stay 'speaking'."""
    _fast(monkeypatch, vpio_mod)
    evq = queue.Queue()
    _t, out = _make_vpio(evq)

    async def scenario():
        await out.process_frame(TTSStartedFrame(), None)
        await out.process_frame(AudioRawFrame(audio=CHUNK_16K, sample_rate=16000, num_channels=1), None)
        await out.process_frame(TTSStoppedFrame(), None)
        await asyncio.sleep(0.5)

    asyncio.run(scenario())
    _assert_one_start_one_finish(evq)
    assert out._state == AudioPlaybackState.IDLE


def test_empty_reply_does_not_leave_speaking_state(monkeypatch):
    _fast(monkeypatch, vpio_mod)
    evq = queue.Queue()
    _t, out = _make_vpio(evq)

    async def scenario():
        await out.process_frame(LLMFullResponseStartFrame(), None)
        await out.process_frame(LLMFullResponseEndFrame(), None)
        await asyncio.sleep(0.3)

    asyncio.run(scenario())
    assert out._state == AudioPlaybackState.IDLE
    assert not [e for e, _d in _events(evq) if e == "tts_started"]


def test_reply_without_end_frame_falls_back_to_idle(monkeypatch):
    _fast(monkeypatch, vpio_mod)
    monkeypatch.setattr(vpio_mod, "_REPLY_IDLE_FALLBACK_SECS", 0.3)
    evq = queue.Queue()
    _t, out = _make_vpio(evq)

    async def scenario():
        await out.process_frame(LLMFullResponseStartFrame(), None)
        await out.process_frame(TTSStartedFrame(), None)
        await out.process_frame(AudioRawFrame(audio=CHUNK_16K, sample_rate=16000, num_channels=1), None)
        await out.process_frame(TTSStoppedFrame(), None)
        await asyncio.sleep(0.9)

    asyncio.run(scenario())
    _assert_one_start_one_finish(evq)
    assert out._state == AudioPlaybackState.IDLE


def test_vpio_abort_emits_playback_finished_when_active(monkeypatch):
    _fast(monkeypatch, vpio_mod)
    evq = queue.Queue()
    _t, out = _make_vpio(evq)

    async def scenario():
        await out.process_frame(LLMFullResponseStartFrame(), None)
        await out.process_frame(AudioRawFrame(audio=CHUNK_16K, sample_rate=16000, num_channels=1), None)
        await out._abort_output_stream_async()
        # A second abort while idle must not emit again.
        await out._abort_output_stream_async()
        await out.process_frame(InterruptionFrame(), None)

    asyncio.run(scenario())
    _assert_one_start_one_finish(evq)
    assert out._state == AudioPlaybackState.IDLE


def test_vpio_stale_helper_report_does_not_finish_early(monkeypatch):
    """A STATS report that predates the last write must not read as 'empty'."""
    _fast(monkeypatch, vpio_mod)
    evq = queue.Queue()
    t, out = _make_vpio(evq)
    one_second = (np.ones(16000, dtype=np.int16) * 3000).tobytes()

    async def scenario():
        await out.process_frame(LLMFullResponseStartFrame(), None)
        await out.process_frame(AudioRawFrame(audio=one_second, sample_rate=16000, num_channels=1), None)
        # Helper reported an empty ring just before it ingested the write.
        t._buffered_ms = 0.0
        t._last_stats_at = time.monotonic()
        assert out._remaining_playback_secs() > 0.8
        await out.process_frame(LLMFullResponseEndFrame(), None)
        await asyncio.sleep(0.3)
        assert not [e for e, _d in _events(evq) if e == "playback_finished"]
        assert out._state == AudioPlaybackState.DRAINING

        # A fresh report received well after the write is trusted when larger.
        t._wall_at_last_audio = time.monotonic() - 1.0
        t._buffered_ms = 5000.0
        t._last_stats_at = time.monotonic()
        assert out._remaining_playback_secs() >= 4.9
        out._flush_now()

    asyncio.run(scenario())
