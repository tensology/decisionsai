"""Foreground Telegram auto-send must match BackgroundChain's Telegram-or-explicit gate."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from distr.core.agent.services.llm.mixins.telegram import TelegramMixin


class _FakeTool:
    def __init__(self):
        self.calls = []

    def _run(self, **kwargs):
        self.calls.append(kwargs)
        return f"sent:{kwargs.get('file_path')}"


class _Harness(TelegramMixin):
    def __init__(self, *, is_telegram: bool, messages: list, tool: _FakeTool):
        self._is_telegram_request = is_telegram
        self._messages = messages
        self._tools_dict = {"send_file_to_telegram": tool}
        self._tts_service = None


def _run(coro):
    return asyncio.run(coro)


def test_auto_send_refuses_desktop_without_explicit_ask(tmp_path):
    artifact = tmp_path / "notes.zip"
    artifact.write_bytes(b"zip")
    tool = _FakeTool()
    harness = _Harness(
        is_telegram=False,
        messages=[
            {"role": "user", "content": "zip my Desktop notes"},
            {
                "role": "tool",
                "content": (
                    f'Result: {artifact}\n\n'
                    f'[ACTION REQUIRED: Call send_file_to_telegram with file_path="{artifact}" to send this file]'
                ),
            },
        ],
        tool=tool,
    )
    thread = threading.current_thread()
    thread.suppress_tts_for_tool_chain = True
    previous = getattr(thread, "telegram_request", None)
    thread.telegram_request = False
    try:
        sent = _run(harness._auto_send_file_to_telegram())
    finally:
        thread.suppress_tts_for_tool_chain = False
        if previous is None and hasattr(thread, "telegram_request"):
            delattr(thread, "telegram_request")
        elif previous is not None:
            thread.telegram_request = previous

    assert sent is False
    assert tool.calls == []


def test_auto_send_allows_explicit_ask(tmp_path):
    artifact = tmp_path / "notes.zip"
    artifact.write_bytes(b"zip")
    tool = _FakeTool()
    harness = _Harness(
        is_telegram=False,
        messages=[
            {"role": "user", "content": "zip Desktop and send it to telegram"},
            {
                "role": "tool",
                "content": (
                    f'Result: {artifact}\n\n'
                    f'[ACTION REQUIRED: Call send_file_to_telegram with file_path="{artifact}" to send this file]'
                ),
            },
        ],
        tool=tool,
    )
    thread = threading.current_thread()
    thread.suppress_tts_for_tool_chain = True
    previous = getattr(thread, "telegram_request", None)
    thread.telegram_request = False
    try:
        sent = _run(harness._auto_send_file_to_telegram())
    finally:
        thread.suppress_tts_for_tool_chain = False
        if previous is None and hasattr(thread, "telegram_request"):
            delattr(thread, "telegram_request")
        elif previous is not None:
            thread.telegram_request = previous

    assert sent is True
    assert len(tool.calls) == 1
    assert tool.calls[0]["file_path"] == str(artifact)
    assert tool.calls[0]["auto_chained"] is True
    assert "telegram" in tool.calls[0]["last_user_message"].lower()


def test_auto_send_allows_telegram_sourced(tmp_path):
    artifact = tmp_path / "summary.md"
    artifact.write_text("# hi")
    tool = _FakeTool()
    harness = _Harness(
        is_telegram=True,
        messages=[
            {"role": "user", "content": "create a markdown summary"},
            {
                "role": "tool",
                "content": (
                    f'Result: {artifact}\n\n'
                    f'[ACTION REQUIRED: Call send_file_to_telegram with file_path="{artifact}" to send this file]'
                ),
            },
        ],
        tool=tool,
    )
    thread = threading.current_thread()
    thread.suppress_tts_for_tool_chain = True
    try:
        sent = _run(harness._auto_send_file_to_telegram())
    finally:
        thread.suppress_tts_for_tool_chain = False

    assert sent is True
    assert tool.calls[0]["is_telegram_request"] is True
    assert tool.calls[0]["auto_chained"] is True
