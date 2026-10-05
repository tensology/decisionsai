from __future__ import annotations

import asyncio

from distr.core.agent.services.llm import openai_compat as openai_compat_module
from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService


class _TextFrame:
    def __init__(self, text: str = "") -> None:
        self.text = text


class _FakeService:
    _pipeline_direction = object()
    _speaker_enabled = True
    _is_telegram_request = False
    SERVICE_NAME = "TestOpenAICompat"

    def __init__(self) -> None:
        self._messages = []
        self._telegram_fallback_text = None
        self.chat_manager = None
        self.pushed: list[tuple[str, object | None]] = []
        self.event_queue = None

    async def push_frame(self, frame, direction=None):
        self.pushed.append((type(frame).__name__, direction))

    async def _push_pipeline_frame(self, frame):
        await self.push_frame(frame, self._pipeline_direction)


class _FakeEventQueue:
    def __init__(self) -> None:
        self.items = []

    def put(self, item, block=False):
        self.items.append(item)


class _Delta:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None


class _Choice:
    def __init__(self, content: str) -> None:
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


def test_handle_follow_up_content_routes_tts_frames_with_pipeline_direction():
    service = _FakeService()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    original_text_frame = openai_compat_module.TextFrame
    openai_compat_module.TextFrame = _TextFrame

    try:
        OpenAICompatibleLLMService._handle_follow_up_content(
            service,
            "Here is the follow-up summary.",
        )
        # The method uses ensure_future, so let the scheduled tasks run.
        loop.run_until_complete(asyncio.sleep(0))
    finally:
        openai_compat_module.TextFrame = original_text_frame
        loop.close()
        asyncio.set_event_loop(None)

    assert service.pushed == [
        ("LLMFullResponseStartFrame", service._pipeline_direction),
        ("_TextFrame", service._pipeline_direction),
        ("LLMFullResponseEndFrame", service._pipeline_direction),
    ]


def test_send_done_after_tools_routes_tts_frames_with_pipeline_direction():
    service = _FakeService()
    service._messages = [
        {"role": "tool", "name": "execute_code", "content": "Task finished successfully."}
    ]
    original_text_frame = openai_compat_module.TextFrame
    openai_compat_module.TextFrame = _TextFrame

    try:
        result = asyncio.run(OpenAICompatibleLLMService._send_done_after_tools(service))
    finally:
        openai_compat_module.TextFrame = original_text_frame

    assert result is True
    assert service.pushed == [
        ("LLMFullResponseStartFrame", service._pipeline_direction),
        ("_TextFrame", service._pipeline_direction),
        ("LLMFullResponseEndFrame", service._pipeline_direction),
    ]


def test_send_done_after_speak_on_desktop_does_not_speak_done():
    service = _FakeService()
    service._messages = [
        {"role": "tool", "name": "speak_on_desktop", "content": "Done"}
    ]
    original_text_frame = openai_compat_module.TextFrame
    openai_compat_module.TextFrame = _TextFrame

    try:
        result = asyncio.run(OpenAICompatibleLLMService._send_done_after_tools(service))
    finally:
        openai_compat_module.TextFrame = original_text_frame

    assert result is True
    assert service.pushed == [
        ("LLMFullResponseStartFrame", service._pipeline_direction),
        ("LLMFullResponseEndFrame", service._pipeline_direction),
    ]
    assert service._messages == [
        {"role": "tool", "name": "speak_on_desktop", "content": "Done"}
    ]


def test_process_follow_up_suppresses_done_after_speak_on_desktop():
    async def stream():
        yield _Chunk("Done")

    async def call_stream(*args, **kwargs):
        return stream()

    service = _FakeService()
    service._cancelled = False
    service.event_queue = _FakeEventQueue()
    service._messages = [
        {"role": "tool", "name": "speak_on_desktop", "content": "Done"}
    ]
    service._prepare_api_messages = lambda: service._messages
    service._call_stream = call_stream
    service._sanitize_tool_calls = lambda tool_calls: tool_calls

    content, tool_calls = asyncio.run(
        OpenAICompatibleLLMService._process_follow_up(service, tools_list=[])
    )

    assert content == ""
    assert tool_calls == []
    assert service.event_queue.items == []


def test_stream_retries_transient_overload_before_first_chunk(monkeypatch):
    calls = 0

    async def call_stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            async def failed():
                raise RuntimeError("Service temporarily overloaded")
                yield None
            return failed()

        async def healthy():
            yield _Chunk("ready")
        return healthy()

    async def no_wait(delay):
        return None

    service = _FakeService()
    service._call_stream = call_stream
    service._is_transient_provider_error = OpenAICompatibleLLMService._is_transient_provider_error
    monkeypatch.setattr(openai_compat_module.asyncio, "sleep", no_wait)

    async def collect():
        stream = OpenAICompatibleLLMService._iter_stream_with_retry(
            service, [], max_retries=2
        )
        return [chunk async for chunk in stream]

    chunks = asyncio.run(collect())
    assert calls == 2
    assert chunks[0].choices[0].delta.content == "ready"
