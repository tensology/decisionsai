"""Tests for cross-platform full-duplex audio transports and turn-taking.

Covers:
- Automatic OS selection (macOS -> vpio, Windows/Linux -> sdapm, fallback -> portaudio)
- macOS VoiceProcessingIO transport protocol, instant FLUSH on barge-in, post-cut silence guard
- Cross-platform SdApmAudioTransport exact 10 ms reverse-stream framing at 44.1/48 kHz,
  lagged correlation metrics, and instant interruption flush
- LiveKitAECFilter idle cursor drift fix + lagged correlation
- BaseSTTService turn-taking with hardware VPIO AEC metrics
"""

from __future__ import annotations

import asyncio
import queue
import struct
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from distr.core.agent.libs import (
    AudioRawFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    StartFrame,
)
from distr.core.agent.macos_vpio_transport import (
    MacOsVpioAudioTransport,
    VpioAecMetrics,
    ensure_vpio_helper_binary,
)
from distr.core.agent.sd_apm_transport import (
    SdApmAecMetrics,
    SdApmAudioTransport,
)
from distr.core.agent.session import AgentSession
from distr.core.agent.transport import AudioPlaybackState
from distr.core.audio.echo_canceller import ReferenceBuffer
from distr.core.audio.livekit_aec import LiveKitAECFilter


def test_resolve_audio_backend_cross_platform(monkeypatch):
    monkeypatch.delenv("DECISIONS_AUDIO_BACKEND", raising=False)
    sess = object.__new__(AgentSession)

    # macOS auto + legacy 'mediadevices' -> vpio
    monkeypatch.setattr(sys, "platform", "darwin")
    sess.settings = {"audio_backend": "auto"}
    assert sess._resolve_audio_backend() == "vpio"
    sess.settings = {"audio_backend": "mediadevices"}
    assert sess._resolve_audio_backend() == "vpio"

    # Windows auto + legacy 'mediadevices' -> sdapm
    monkeypatch.setattr(sys, "platform", "win32")
    sess.settings = {"audio_backend": "auto"}
    assert sess._resolve_audio_backend() == "sdapm"
    sess.settings = {"audio_backend": "mediadevices"}
    assert sess._resolve_audio_backend() == "sdapm"

    # Linux / Unix -> sdapm
    monkeypatch.setattr(sys, "platform", "linux")
    sess.settings = {"audio_backend": "auto"}
    assert sess._resolve_audio_backend() == "sdapm"

    # Explicit env override wins everywhere
    monkeypatch.setenv("DECISIONS_AUDIO_BACKEND", "portaudio")
    assert sess._resolve_audio_backend() == "portaudio"


def test_sdapm_reverse_stream_feeds_exact_10ms_16k_frames_from_44100hz():
    """Regression: 44.1 kHz output must downsample BEFORE slicing 160-sample APM frames."""
    ref_buf = ReferenceBuffer(max_duration_secs=1.0, sample_rate=44100)
    transport = SdApmAudioTransport(
        input_sample_rate=16000,
        output_sample_rate=44100,
        vad_enabled=False,
        aec_reference_buffer=ref_buf,
    )
    recorded_frames = []

    class SpyApm:
        def process_reverse_stream(self, frame):
            recorded_frames.append((frame.sample_rate, frame.samples_per_channel, frame.data.nbytes))

        def process_stream(self, frame):
            pass

        def set_stream_delay_ms(self, ms):
            pass

    transport._apm = SpyApm()
    transport._actual_output_rate = 44100
    transport._reverse_down.configure(44100, 16000)

    # Feed 50 ms of 44.1 kHz audio (2205 samples -> ~800 samples @ 16 kHz = 5 x 10 ms frames)
    t = np.arange(2205, dtype=np.float32) / 44100.0
    sine = (np.sin(2 * np.pi * 440.0 * t) * 10000.0).astype(np.int16)
    transport._feed_reverse_16k(sine)

    assert len(recorded_frames) >= 4
    for sr, spc, byte_len in recorded_frames:
        assert sr == 16000
        assert spc == 160
        assert byte_len == 320


def test_sdapm_metrics_lagged_correlation_detects_delayed_echo():
    """Echo delayed by 80 ms must still show strong correlation for smart barge-in."""
    metrics = SdApmAecMetrics()
    rng = np.random.default_rng(42)
    # Build 200 ms of speech-like broadband signal in 10 ms frames @ 16 kHz
    frames = [(rng.normal(0, 0.2, size=160) * 32767).astype(np.int16) for _ in range(20)]
    for f in frames:
        metrics.record_reference(f)

    # Near-end mic picks up frames[10:12] (80 ms behind the newest frame!)
    delayed_echo = np.concatenate(frames[10:12])
    cleaned = np.zeros_like(delayed_echo)
    metrics.record_near(delayed_echo, cleaned)

    last = metrics.last_metrics
    assert last["reference_correlation"] > 0.80
    assert last["erle_db"] > 20.0


def test_sdapm_output_instant_flush_on_interruption():
    evq = queue.Queue()
    ref_buf = ReferenceBuffer(max_duration_secs=1.0, sample_rate=44100)
    transport = SdApmAudioTransport(
        input_sample_rate=16000,
        output_sample_rate=44100,
        vad_enabled=False,
        event_queue=evq,
        aec_reference_buffer=ref_buf,
    )
    out = transport.output()
    out._started = True  # skip opening real PortAudio output stream in unit test

    async def scenario():
        pushed = []

        async def capture_push(frame, direction=None):
            pushed.append(frame)

        out.push_frame = capture_push

        await out.process_frame(LLMFullResponseStartFrame(), None)
        assert out._state == AudioPlaybackState.SYNTHESIZING

        chunk = (np.ones(4410, dtype=np.int16) * 5000).tobytes()
        await out.process_frame(AudioRawFrame(audio=chunk, sample_rate=44100, num_channels=1), None)
        assert out._state == AudioPlaybackState.PLAYING
        assert ref_buf.is_active is True
        assert len(transport._out_buf) > 0

        # User interrupts!
        await out.process_frame(InterruptionFrame(), None)
        assert out._state == AudioPlaybackState.IDLE
        assert out._force_silence is True
        assert ref_buf.is_active is False
        assert len(transport._out_buf) == 0

        # Trailing TTS chunks from the cancelled stream must be dropped.
        await out.process_frame(AudioRawFrame(audio=chunk, sample_rate=44100, num_channels=1), None)
        assert len(transport._out_buf) == 0

        # Next LLM response clears _force_silence and plays cleanly.
        await out.process_frame(LLMFullResponseStartFrame(), None)
        assert out._force_silence is False
        await out.process_frame(AudioRawFrame(audio=chunk, sample_rate=44100, num_channels=1), None)
        assert len(transport._out_buf) > 0

    asyncio.run(scenario())


def test_macos_vpio_output_protocol_and_instant_flush(tmp_path):
    fake_bin = tmp_path / "fake_vpio"
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)

    evq = queue.Queue()
    ref_buf = ReferenceBuffer(max_duration_secs=1.0, sample_rate=44100)
    transport = MacOsVpioAudioTransport(
        pipeline_sample_rate=16000,
        output_sample_rate=44100,
        vad_enabled=False,
        event_queue=evq,
        aec_reference_buffer=ref_buf,
        helper_binary=str(fake_bin),
    )

    sent_packets = []

    class FakeStdin:
        def write(self, data: bytes):
            mtype, mlen = struct.unpack("<BI", data[:5])
            sent_packets.append((mtype, data[5 : 5 + mlen]))

        async def drain(self):
            pass

    transport._proc = SimpleNamespace(stdin=FakeStdin(), stdout=None, returncode=None, pid=9999)
    out = transport.output()
    out._started = True

    async def scenario():
        async def noop_push(frame, direction=None):
            pass

        out.push_frame = noop_push

        await out.process_frame(LLMFullResponseStartFrame(), None)
        # Send 100 ms of 44.1 kHz TTS audio -> should resample to 16 kHz and send SPK (0x01)
        chunk = (np.ones(4410, dtype=np.int16) * 4000).tobytes()
        await out.process_frame(AudioRawFrame(audio=chunk, sample_rate=44100, num_channels=1), None)
        assert out._state == AudioPlaybackState.PLAYING
        assert ref_buf.is_active is True
        assert any(t == 0x01 and len(p) > 0 for t, p in sent_packets)

        # Barge-in InterruptionFrame must send FLUSH (0x02) immediately!
        await out.process_frame(InterruptionFrame(), None)
        assert out._state == AudioPlaybackState.IDLE
        assert out._force_silence is True
        assert ref_buf.is_active is False
        assert any(t == 0x02 and p == b"" for t, p in sent_packets)

        # Trailing TTS audio after barge-in is silently dropped (no new SPK packets).
        before_count = len(sent_packets)
        await out.process_frame(AudioRawFrame(audio=chunk, sample_rate=44100, num_channels=1), None)
        assert len(sent_packets) == before_count

    asyncio.run(scenario())


def test_livekit_aec_cursor_does_not_drift_during_silence():
    ref_buf = ReferenceBuffer(max_duration_secs=1.0, sample_rate=44100)
    aec = LiveKitAECFilter(input_sample_rate=16000, output_sample_rate=44100, reference_buffer=ref_buf)

    # While TTS is inactive, _pull_far must return None and keep _far_read reset.
    ref_buf.set_active(False)
    for _ in range(50):
        assert aec._pull_far(160) is None
    assert aec._far_read is None

    # When TTS starts, _pull_far locks onto write_pos cleanly.
    ref_buf.set_active(True)
    ref_buf.push(np.ones(4410, dtype=np.float32) * 0.25)
    far = aec._pull_far(160)
    assert far is not None
    assert far.size == 160
    assert float(np.mean(np.abs(far))) > 0.2

    # When TTS stops again, cursor resets so it never drifts ahead.
    ref_buf.set_active(False)
    assert aec._pull_far(160) is None
    assert aec._far_read is None


def test_stt_smart_bargein_with_hardware_vpio_aec():
    from distr.core.agent.services.stt.base import BaseSTTService

    class DummySTT(BaseSTTService):
        async def run_stt(self, audio: bytes):
            if False:
                yield audio

    stt = DummySTT(is_hands_free=True)
    vpio_metrics = VpioAecMetrics()
    stt._aec_filter = vpio_metrics
    stt._bargein_mode = "smart"

    # Quiet post-VPIO noise floor while TTS speaks -> hold (never self-cut!)
    vpio_metrics.update(in_rms=0.001, out_rms=0.15)
    assert stt._should_hold_bargein() is True

    # Real user voice clearing VPIO -> allow immediate barge-in!
    vpio_metrics.update(in_rms=0.045, out_rms=0.15)
    assert stt._should_hold_bargein() is False

    # Explicit bargein_mode='off' -> always hold.
    stt._bargein_mode = "off"
    assert stt._should_hold_bargein() is True


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS VoiceProcessingIO helper binary build")
def test_macos_vpio_helper_builds_cleanly():
    binary = ensure_vpio_helper_binary()
    assert binary
