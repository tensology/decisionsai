"""
Simulate the live-mic / PTT path without sounddevice: push InputAudioRawFrame chunks
through VibeVoiceAsrSTTService while PTT is held, release PTT, assert transcription.

The real ASR weights are not loaded — ``transcribe_audio_file`` is mocked. This still
exercises the same code path DecisionsAI uses for mic capture (frame normalization,
PTT buffer, batch infer via temp WAV).
"""

from __future__ import annotations

import pytest

pytest.importorskip("pipecat.frames.frames")

from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from distr.core.agent.libs import PIPECAT_AVAILABLE

pytestmark = pytest.mark.skipif(not PIPECAT_AVAILABLE, reason="pipecat not installed")


@pytest.mark.asyncio
async def test_vibevoice_ptt_mic_frames_yield_transcription(monkeypatch):
    calls: list[str] = []

    def fake_transcribe(path: str, max_new_tokens: int = 8192) -> str:
        calls.append(path)
        return "hello from synthetic mic buffer"

    monkeypatch.setattr(
        "distr.core.agent.services.tts.vibevoice_asr_inference.transcribe_audio_file",
        fake_transcribe,
    )

    from distr.core.agent.services.stt.vibevoice_asr import VibeVoiceAsrSTTService

    stt = VibeVoiceAsrSTTService(event_queue=None, is_hands_free=False)
    # Pipecat normally sets this after StartFrame; set directly so push_frame runs.
    stt._FrameProcessor__started = True
    pushed: list = []

    async def capture_push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    stt.push_frame = capture_push

    direction = FrameDirection.DOWNSTREAM
    # Prime pipeline context (event loop + direction) like a real transport frame.
    await stt.process_frame(InputAudioRawFrame(audio=b"\x00" * 640, sample_rate=16000, num_channels=1), direction)

    stt.set_ptt_active(True)
    chunk = (b"\x00\x01" * 320)  # 640 bytes ≈ 20 ms @ 16 kHz mono s16le
    for _ in range(80):
        await stt.process_frame(InputAudioRawFrame(audio=chunk, sample_rate=16000, num_channels=1), direction)
    stt.set_ptt_active(False)
    await stt.process_frame(InputAudioRawFrame(audio=chunk, sample_rate=16000, num_channels=1), direction)

    assert calls, "transcribe_audio_file should run once for the PTT buffer"
    assert any(c.endswith(".wav") for c in calls)
    texts = [f.text for f in pushed if isinstance(f, TranscriptionFrame)]
    assert "hello from synthetic mic buffer" in texts
