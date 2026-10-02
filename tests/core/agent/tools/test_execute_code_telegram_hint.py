"""execute_code should only hint send_file_to_telegram for Telegram / explicit ask."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from distr.core.agent.tools.system.execute_code import (
    ExecuteCodeTool,
    _should_append_telegram_send_hint,
    _user_explicitly_asked_telegram,
)


@pytest.fixture
def tool():
    return ExecuteCodeTool()


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    path = tmp_path / "desktop_artifact.zip"
    path.write_bytes(b"pk\x03\x04fake-zip")
    return path


def _code_returning_path(path: Path) -> str:
    return f"result = {str(path)!r}"


def test_helper_desktop_message_does_not_hint():
    assert _should_append_telegram_send_hint(last_user_message="zip my Desktop notes into notes.zip") is False
    assert _user_explicitly_asked_telegram("create a png of the chart") is False


def test_helper_explicit_ask_hints():
    assert _should_append_telegram_send_hint(
        last_user_message="zip Desktop and send it to telegram"
    )
    assert _user_explicitly_asked_telegram("please send that to my telegram")


def test_helper_telegram_sourced_kwargs_hints():
    assert _should_append_telegram_send_hint(
        is_telegram_request=True,
        last_user_message="zip my Desktop notes",
    )


def test_helper_telegram_thread_flag_hints():
    thread = threading.current_thread()
    previous = getattr(thread, "telegram_request", None)
    thread.telegram_request = True
    try:
        assert _should_append_telegram_send_hint(last_user_message="make a markdown summary")
    finally:
        if previous is None:
            delattr(thread, "telegram_request")
        else:
            thread.telegram_request = previous


def test_desktop_file_result_has_no_action_required(tool, sample_file):
    out = tool._run(
        code=_code_returning_path(sample_file),
        last_user_message="compress this folder into a zip on my Desktop",
    )
    assert f"Result: {sample_file}" in out
    assert "ACTION REQUIRED" not in out
    assert "send_file_to_telegram" not in out


def test_explicit_ask_file_result_has_action_required(tool, sample_file):
    out = tool._run(
        code=_code_returning_path(sample_file),
        last_user_message="zip Desktop notes and send it to telegram",
    )
    assert f"Result: {sample_file}" in out
    assert "[ACTION REQUIRED: Call send_file_to_telegram" in out
    assert str(sample_file) in out


def test_telegram_sourced_file_result_has_action_required(tool, sample_file):
    out = tool._run(
        code=_code_returning_path(sample_file),
        last_user_message="create a markdown summary file",
        is_telegram_request=True,
    )
    assert "[ACTION REQUIRED: Call send_file_to_telegram" in out
