"""Contract tests for OpenAI Realtime S2S runtime bridge."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


def test_resample_16k_to_24k_length():
    from distr.core.agent.services.s2s.openai_realtime import _resample_pcm16_16k_to_24k
    import numpy as np

    # 100ms @ 16kHz mono int16 → 100ms @ 24kHz
    pcm16 = (np.zeros(1600, dtype=np.int16)).tobytes()
    out = _resample_pcm16_16k_to_24k(pcm16)
    assert len(out) == 4800  # 2400 samples * 2 bytes
    assert len(out) % 2 == 0


def test_bridge_disabled_forwards_audio():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import InputAudioRawFrame, PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=False, api_key="")
    pushed = []

    async def _push(frame, direction=None):
        pushed.append(frame)

    bridge.push_frame = _push  # type: ignore[method-assign]

    async def _run():
        frame = InputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=16000, num_channels=1)
        await bridge.process_frame(frame, None)
        assert len(pushed) == 1
        assert pushed[0] is frame

    asyncio.run(_run())


def test_bridge_agent_ptt_swallows_audio_when_enabled():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import InputAudioRawFrame, PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._connected = True
    bridge._ws = MagicMock()
    # Make send async-compatible
    async def _send(_payload):
        return None

    bridge._ws.send = _send
    pushed = []

    async def _push(frame, direction=None):
        pushed.append(frame)

    bridge.push_frame = _push  # type: ignore[method-assign]
    bridge.set_ptt_active(True)

    async def _run():
        frame = InputAudioRawFrame(audio=b"\x00\x01" * 160, sample_rate=16000, num_channels=1)
        await bridge.process_frame(frame, None)
        # Agent S2S must not forward mic to STT
        assert pushed == []

    asyncio.run(_run())


def test_bridge_dictation_forwards_even_when_enabled():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import InputAudioRawFrame, PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge.set_dictating(True)
    bridge.set_ptt_active(True)
    pushed = []

    async def _push(frame, direction=None):
        pushed.append(frame)

    bridge.push_frame = _push  # type: ignore[method-assign]

    async def _run():
        frame = InputAudioRawFrame(audio=b"\x00\x01" * 80, sample_rate=16000, num_channels=1)
        await bridge.process_frame(frame, None)
        assert len(pushed) == 1

    asyncio.run(_run())


def test_bridge_ptt_release_commits():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._connected = True
    sent = []

    class _WS:
        async def send(self, payload):
            sent.append(payload)

    bridge._ws = _WS()
    bridge._ptt_active = True

    async def _run():
        # Release schedules commit on running loop
        bridge.set_ptt_active(False)
        await asyncio.sleep(0.05)
        assert any("input_audio_buffer.commit" in s for s in sent)
        assert any("response.create" in s for s in sent)

    asyncio.run(_run())


def test_session_create_s2s_service_enable_flag():
    """_create_s2s_service respects config llm.s2s_active without full session boot."""
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    class _Stub:
        def __init__(self):
            self.config = {
                "llm": {"s2s_active": True, "s2s_model": "gpt-realtime-2.1"},
                "tts": {"voice_name": "marin", "voice_id": "marin"},
            }
            self.settings = {"openai_key": "sk-test"}
            self.event_queue = None
            self.s2s_service = None
            self.is_hands_free = False
            self.is_dictating = False
            self.ptt_active = False
            self.logger = MagicMock()

        def _load_agent_role(self):
            return "You are a test agent."

        def _create_s2s_service(self):
            # Copy of session method body (import path must stay valid)
            from distr.core.agent.session import AgentSession

            AgentSession._create_s2s_service(self)

    stub = _Stub()
    stub._create_s2s_service()
    assert isinstance(stub.s2s_service, OpenAIRealtimeS2SBridge)
    assert stub.s2s_service._enabled is True
    assert stub.s2s_service.voice == "marin"

    stub.config["llm"]["s2s_active"] = False
    stub._create_s2s_service()
    assert stub.s2s_service._enabled is False
