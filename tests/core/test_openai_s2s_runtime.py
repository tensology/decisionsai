"""Contract tests for OpenAI Realtime S2S runtime bridge."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


class _EventQueue:
    def __init__(self):
        self.items = []

    def put(self, item, block=False):
        self.items.append(item)


class _ChatManager:
    def __init__(self):
        self.user = []
        self.assistant = []

    def get_current_chat(self):
        return 7

    def add_user_message(self, chat_id, text):
        self.user.append((chat_id, text))

    def add_assistant_message(self, chat_id, text):
        self.assistant.append((chat_id, text))


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


def test_bridge_hands_free_streams_audio_before_local_vad_speaking_started():
    """Realtime semantic VAD must receive the full filtered hands-free stream."""
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import InputAudioRawFrame, PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._is_hands_free = True
    bridge._connected = True
    sent = []

    class _WS:
        async def send(self, payload):
            sent.append(__import__("json").loads(payload))

    bridge._ws = _WS()
    pushed = []

    async def _push(frame, direction=None):
        pushed.append(frame)

    bridge.push_frame = _push  # type: ignore[method-assign]

    async def _run():
        frame = InputAudioRawFrame(audio=b"\x00\x01" * 160, sample_rate=16000, num_channels=1)
        await bridge.process_frame(frame, None)

    asyncio.run(_run())

    assert pushed == []
    assert sent[0]["type"] == "input_audio_buffer.append"


def test_server_turn_markers_update_speaking_hint_without_local_capture_gate():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._is_hands_free = True

    async def _run():
        await bridge._handle_realtime_event({"type": "input_audio_buffer.speech_started"})
        assert bridge._user_speaking is True
        await bridge._handle_realtime_event({"type": "input_audio_buffer.speech_stopped"})
        assert bridge._user_speaking is False

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
    bridge._buffered_audio_bytes = 4800

    async def _run():
        # Release schedules commit on running loop
        bridge.set_ptt_active(False)
        await asyncio.sleep(0.05)
        assert any("input_audio_buffer.commit" in s for s in sent)
        assert not any("response.create" in s for s in sent)
        await bridge._handle_realtime_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "turn-1",
                "transcript": "Hello",
            }
        )
        assert any("response.create" in s for s in sent)
        assert bridge._response_active is True

    asyncio.run(_run())


def test_bridge_ptt_release_does_not_commit_empty_audio():
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
        bridge.set_ptt_active(False)
        await asyncio.sleep(0.05)
        assert sent == []

    asyncio.run(_run())


def test_session_update_declares_output_pcm_rate():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    output_format = bridge._session_update_event()["session"]["audio"]["output"]["format"]

    assert output_format == {"type": "audio/pcm", "rate": 24000}


def test_session_update_enables_input_transcription_for_web_history():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        transcription_model="gpt-4o-mini-transcribe",
    )
    input_audio = bridge._session_update_event()["session"]["audio"]["input"]

    assert input_audio["transcription"] == {"model": "gpt-4o-mini-transcribe"}


def test_session_update_exposes_normal_agent_tools_to_realtime():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    class _LLM:
        def _tools_list_for_message(self, _text):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                }
            ]

    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        llm_service=_LLM(),
    )
    session = bridge._session_update_event()["session"]
    tools = bridge._realtime_tool_schemas("search today")

    assert session["tool_choice"] == "none"
    assert session["tools"] == []
    assert tools == [
        {
            "type": "function",
            "name": "web_search",
            "description": "Search the web",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    ]


def test_realtime_tool_gate_rejects_casual_speech_without_querying_catalog():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    class _LLM:
        def _tools_list_for_message(self, _text):
            raise AssertionError("casual speech must not query or expose tools")

    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        llm_service=_LLM(),
    )

    assert bridge._realtime_tool_schemas("Hello, I made a bowl.") == []
    assert bridge._realtime_tool_schemas("Tell me a story about Spot.") == []


def test_realtime_tool_gate_filters_catalog_to_explicit_spoken_intent():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    class _LLM:
        def _tools_list_for_message(self, _text):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
                for name in ("web_search", "computer_use", "create_ticket")
            ]

    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        llm_service=_LLM(),
    )
    tools = bridge._realtime_tool_schemas(
        "Could you search the web for today's weather?"
    )

    assert [tool["name"] for tool in tools] == ["web_search"]


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Hello, I made a bowl.", set()),
        ("Tell me a story about Spot.", set()),
        ("Search the web for today's weather.", {"web_search"}),
        ("Can you move the mouse to the center of my screen?", {"mouse_movement"}),
        ("Create a ticket for the broken login.", {"create_ticket"}),
        ("Open the Downloads folder.", {"smart_open"}),
        ("What is the capital of France?", set()),
    ],
)
def test_spoken_tool_intent_matrix_is_strict_and_deterministic(spoken, expected):
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge

    assert OpenAIRealtimeS2SBridge._realtime_tool_names_for_message(spoken) == expected


def test_final_transcript_selects_tools_before_starting_exactly_one_response():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    class _LLM:
        _messages = []

        def _tools_list_for_message(self, text):
            assert text == "Search the web for today's weather"
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "computer_use",
                        "description": "Control desktop",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ]

    sent = []

    class _WS:
        async def send(self, payload):
            sent.append(__import__("json").loads(payload))

    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        llm_service=_LLM(),
    )
    bridge._connected = True
    bridge._ws = _WS()
    bridge._response_active = True
    bridge._response_waiting_for_transcript = True

    event = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "spoken-search-1",
        "transcript": "Search the web for today's weather",
    }

    async def _run():
        await bridge._handle_realtime_event(event)
        await bridge._handle_realtime_event(event)

    asyncio.run(_run())

    assert [payload["type"] for payload in sent] == ["session.update", "response.create"]
    assert sent[0]["session"]["tool_choice"] == "required"
    assert [tool["name"] for tool in sent[0]["session"]["tools"]] == ["web_search"]


def test_transcription_timeout_starts_one_tool_free_response():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    sent = []

    class _WS:
        async def send(self, payload):
            sent.append(__import__("json").loads(payload))

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._connected = True
    bridge._ws = _WS()
    bridge._response_active = True
    bridge._response_waiting_for_transcript = True
    bridge._TRANSCRIPTION_WAIT_SECONDS = 0.01

    async def _run():
        bridge._schedule_response_timeout()
        await asyncio.sleep(0.03)
        await bridge._begin_response_for_transcript("")

    asyncio.run(_run())

    assert [payload["type"] for payload in sent] == ["session.update", "response.create"]
    assert sent[0]["session"]["tools"] == []
    assert sent[0]["session"]["tool_choice"] == "none"


def test_unsupported_input_transcription_model_uses_realtime_safe_default():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        transcription_model="local-whisper-model",
    )

    assert bridge.transcription_model == "gpt-4o-mini-transcribe"


def test_response_done_stops_player_without_waiting_for_another_pipeline_frame():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import (
        LLMFullResponseEndFrame,
        PIPECAT_AVAILABLE,
        TTSStoppedFrame,
    )

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._tts_started = True
    bridge._response_active = True
    emitted = []

    async def _emit(frame):
        emitted.append(frame)

    bridge._emit_to_output = _emit

    async def _run():
        await bridge._handle_realtime_event({"type": "response.done"})

    asyncio.run(_run())
    assert bridge._tts_started is False
    assert bridge._response_active is False
    assert len(emitted) == 2
    assert isinstance(emitted[0], TTSStoppedFrame)
    assert isinstance(emitted[1], LLMFullResponseEndFrame)


def test_first_realtime_audio_opens_one_full_response_boundary():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import (
        LLMFullResponseStartFrame,
        OutputAudioRawFrame,
        PIPECAT_AVAILABLE,
        TTSStartedFrame,
    )

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    emitted = []

    async def _emit(frame):
        emitted.append(frame)

    bridge._emit_to_output = _emit

    asyncio.run(
        bridge._handle_realtime_event(
            {
                "type": "response.output_audio.delta",
                "delta": "AAE=",
            }
        )
    )

    assert len(emitted) == 3
    assert isinstance(emitted[0], LLMFullResponseStartFrame)
    assert isinstance(emitted[1], TTSStartedFrame)
    assert isinstance(emitted[2], OutputAudioRawFrame)


def test_audio_done_then_response_done_emits_one_terminal_boundary():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import (
        LLMFullResponseEndFrame,
        PIPECAT_AVAILABLE,
        TTSStoppedFrame,
    )

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._tts_started = True
    emitted = []

    async def _emit(frame):
        emitted.append(frame)

    bridge._emit_to_output = _emit

    async def _run():
        await bridge._handle_realtime_event({"type": "response.output_audio.done"})
        await bridge._handle_realtime_event({"type": "response.done", "response": {}})

    asyncio.run(_run())

    assert sum(isinstance(frame, TTSStoppedFrame) for frame in emitted) == 1
    assert sum(isinstance(frame, LLMFullResponseEndFrame) for frame in emitted) == 1


def test_interruption_closes_active_audio_once_and_clears_input():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import LLMFullResponseEndFrame, PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._connected = True
    bridge._response_active = True
    bridge._tts_started = True
    sent = []
    emitted = []

    class _WS:
        async def send(self, payload):
            sent.append(payload)

    bridge._ws = _WS()

    async def _emit(frame):
        emitted.append(frame)

    bridge._emit_to_output = _emit
    asyncio.run(bridge._cancel_active_response())

    assert any("response.cancel" in payload for payload in sent)
    assert any("input_audio_buffer.clear" in payload for payload in sent)
    assert sum(isinstance(frame, LLMFullResponseEndFrame) for frame in emitted) == 1
    assert bridge._response_active is False
    assert bridge._tts_started is False


def test_interruption_orders_cancel_and_clear_before_first_new_audio():
    """A new PTT turn cannot be cleared after its first microphone frames arrive."""
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import InputAudioRawFrame, PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._connected = True
    bridge._response_active = True
    bridge._tts_started = True
    sent = []

    class _WS:
        async def send(self, payload):
            sent.append(__import__("json").loads(payload)["type"])

    bridge._ws = _WS()

    async def _emit(_frame):
        return None

    bridge._emit_to_output = _emit

    async def _run():
        bridge.set_ptt_active(True)
        frame = InputAudioRawFrame(
            audio=b"\x00\x01" * 160,
            sample_rate=16000,
            num_channels=1,
        )
        await bridge.process_frame(frame, None)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert sent[:3] == [
        "response.cancel",
        "input_audio_buffer.clear",
        "input_audio_buffer.append",
    ]
    assert bridge._needs_input_reset is False
    assert bridge._buffered_audio_bytes > 0


def test_realtime_transcripts_persist_and_emit_one_chat_turn():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    queue = _EventQueue()
    chats = _ChatManager()
    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        event_queue=queue,
        chat_manager=chats,
    )

    async def _run():
        await bridge._handle_realtime_event({"type": "response.created"})
        await bridge._handle_realtime_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "user-item-1",
                "transcript": "Hello there",
            }
        )
        await bridge._handle_realtime_event(
            {"type": "response.output_audio_transcript.delta", "delta": "Hi "}
        )
        await bridge._handle_realtime_event(
            {"type": "response.output_audio_transcript.delta", "delta": "Paul"}
        )
        await bridge._handle_realtime_event(
            {
                "type": "response.output_audio_transcript.done",
                "transcript": "Hi Paul",
            }
        )
        await bridge._handle_realtime_event({"type": "response.done", "response": {}})

    asyncio.run(_run())

    assert chats.user == [(7, "Hello there")]
    assert chats.assistant == [(7, "Hi Paul")]
    event_names = [name for name, _data in queue.items]
    assert event_names.count("transcription_progress") == 1
    assert event_names.count("chat_message_added") == 1
    assert event_names.count("chat_stream_started") == 1
    assert event_names.count("chat_stream_token") == 2
    assert event_names.count("chat_stream_finished") == 1
    progress_index = event_names.index("transcription_progress")
    message_index = event_names.index("chat_message_added")
    assert progress_index < message_index
    progress = queue.items[progress_index][1]
    assert progress["status_text"] == "Hello there"
    assert progress["done"] is True


def test_ptt_preview_is_closed_when_capture_is_too_short():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    queue = _EventQueue()
    chats = _ChatManager()
    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        event_queue=queue,
        chat_manager=chats,
    )
    bridge._connected = True
    bridge._ws = MagicMock()
    bridge._ptt_active = True

    async def _run():
        bridge.set_ptt_active(False)
        await asyncio.sleep(0)

    asyncio.run(_run())

    event, payload = queue.items[-1]
    assert event == "transcription_progress"
    assert payload["done"] is True
    assert payload["discard_live_preview"] is True


def test_failed_input_transcription_closes_live_preview():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    queue = _EventQueue()
    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        event_queue=queue,
        chat_manager=_ChatManager(),
    )

    asyncio.run(
        bridge._handle_realtime_event(
            {
                "type": "conversation.item.input_audio_transcription.failed",
                "error": {"message": "bad audio"},
            }
        )
    )

    event, payload = queue.items[-1]
    assert event == "transcription_progress"
    assert payload["discard_live_preview"] is True


def test_duplicate_input_transcript_item_is_persisted_once():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    queue = _EventQueue()
    chats = _ChatManager()
    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        event_queue=queue,
        chat_manager=chats,
    )
    event = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": "same-item",
        "transcript": "Only once",
    }

    async def _run():
        await bridge._handle_realtime_event(event)
        await bridge._handle_realtime_event(event)

    asyncio.run(_run())
    assert chats.user == [(7, "Only once")]


def test_response_done_fallback_transcript_persists_without_delta_events():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    queue = _EventQueue()
    chats = _ChatManager()
    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        event_queue=queue,
        chat_manager=chats,
    )

    async def _run():
        await bridge._handle_realtime_event({"type": "response.created"})
        await bridge._handle_realtime_event(
            {
                "type": "response.done",
                "response": {
                    "output": [
                        {"content": [{"type": "audio", "transcript": "Fallback text"}]}
                    ]
                },
            }
        )

    asyncio.run(_run())
    assert chats.assistant == [(7, "Fallback text")]
    assert [name for name, _data in queue.items] == [
        "chat_stream_started",
        "chat_stream_finished",
    ]


def test_realtime_function_call_uses_existing_executor_and_continues_response():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    executed = []
    sent = []

    class _LLM:
        def __init__(self):
            self._messages = []

        async def _execute_tool_calls(self, calls):
            executed.extend(calls)
            return [
                {
                    "role": "tool",
                    "tool_call_id": calls[0]["id"],
                    "name": calls[0]["function"]["name"],
                    "content": "Search result: current answer",
                }
            ]

    class _WS:
        async def send(self, payload):
            sent.append(__import__("json").loads(payload))

    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        llm_service=_LLM(),
    )
    bridge._connected = True
    bridge._ws = _WS()

    response = {
        "type": "response.done",
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "web_search",
                    "arguments": '{"query":"today"}',
                }
            ]
        },
    }

    asyncio.run(bridge._handle_realtime_event(response))

    assert executed[0]["function"]["name"] == "web_search"
    assert sent[0] == {
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "Search result: current answer",
        },
    }
    assert sent[1] == {"type": "response.create"}
    assert bridge._response_active is True


def test_duplicate_realtime_function_call_id_executes_once():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    executions = []
    sent = []

    class _LLM:
        _messages = [{"role": "user", "content": "Search the web for weather"}]

        async def _execute_tool_calls(self, calls):
            executions.extend(calls)
            return [{"tool_call_id": calls[0]["id"], "content": "Sunny"}]

    class _WS:
        async def send(self, payload):
            sent.append(__import__("json").loads(payload))

    bridge = OpenAIRealtimeS2SBridge(
        enabled=True,
        api_key="sk-test",
        llm_service=_LLM(),
    )
    bridge._connected = True
    bridge._ws = _WS()
    event = {
        "type": "response.done",
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "same-call",
                    "name": "web_search",
                    "arguments": '{"query":"weather"}',
                }
            ]
        },
    }

    async def _run():
        await bridge._handle_realtime_event(event)
        await bridge._handle_realtime_event(event)

    asyncio.run(_run())

    assert len(executions) == 1
    assert [payload["type"] for payload in sent] == [
        "conversation.item.create",
        "response.create",
    ]


def test_connected_voice_change_reconnects_live_session():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test", voice="marin")
    bridge._connected = True
    reconnects = []

    async def _reconnect():
        reconnects.append(bridge.voice)

    bridge._reconnect = _reconnect

    async def _run():
        bridge.set_s2s_enabled(True, voice="cedar")
        await asyncio.sleep(0)

    asyncio.run(_run())
    assert bridge.voice == "cedar"
    assert reconnects == ["cedar"]


def test_unexpected_realtime_disconnect_reconnects_and_closes_player():
    from distr.core.agent.services.s2s.openai_realtime import OpenAIRealtimeS2SBridge
    from distr.core.agent.libs import PIPECAT_AVAILABLE

    if not PIPECAT_AVAILABLE:
        pytest.skip("Pipecat not available")

    bridge = OpenAIRealtimeS2SBridge(enabled=True, api_key="sk-test")
    bridge._connected = True
    bridge._response_active = True
    bridge._tts_started = True
    reconnects = []
    emitted = []

    class _BrokenWS:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise ConnectionError("socket vanished")

        async def close(self):
            return None

    async def _connect():
        reconnects.append(True)
        return True

    async def _emit(frame):
        emitted.append(frame)

    bridge._ws = _BrokenWS()
    bridge.connect = _connect
    bridge._emit_to_output = _emit

    asyncio.run(bridge._listen())

    assert reconnects == [True]
    assert bridge._connected is False
    assert bridge._response_active is False
    assert bridge._tts_started is False
    assert len(emitted) == 2


def test_hands_free_change_updates_connected_server_vad():
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

    async def _run():
        bridge.set_hands_free(True)
        await asyncio.sleep(0)

    asyncio.run(_run())
    payload = __import__("json").loads(sent[-1])
    assert payload["session"]["audio"]["input"]["turn_detection"] == {
        "type": "semantic_vad",
        "create_response": False,
        "interrupt_response": True,
    }


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
            self.chat_manager = None
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
