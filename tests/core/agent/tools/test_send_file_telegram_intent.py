"""send_file_to_telegram must not stamp artifact intent for unsolicited desktop auto-chains."""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest

from distr.core.agent.tools.integrations.send_file_to_telegram import SendFileToTelegramTool


@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    path = tmp_path / "chart.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return path


def test_desktop_file_path_without_ask_is_refused(sample_file):
    events = queue.Queue()
    tool = SendFileToTelegramTool(event_queue=events)
    thread = threading.current_thread()
    previous = getattr(thread, "telegram_request", None)
    thread.telegram_request = False
    try:
        out = tool._run(file_path=str(sample_file), last_user_message="make a png of the chart")
    finally:
        if previous is None and hasattr(thread, "telegram_request"):
            delattr(thread, "telegram_request")
        elif previous is not None:
            thread.telegram_request = previous

    assert "Skipped" in out
    assert events.empty()


def test_explicit_ask_stamps_artifact_intent(sample_file):
    events = queue.Queue()
    tool = SendFileToTelegramTool(event_queue=events)
    out = tool._run(
        file_path=str(sample_file),
        last_user_message="send this png to telegram",
        text="send this png to telegram",
    )
    assert "sending" in out.lower()
    kind, payload = events.get_nowait()
    assert kind == "send_file_to_telegram"
    assert payload["explicit_artifact_intent"] is True
    assert payload["file_path"] == str(sample_file)


def test_telegram_sourced_auto_chain_stamps_intent(sample_file):
    events = queue.Queue()
    tool = SendFileToTelegramTool(event_queue=events)
    out = tool._run(
        file_path=str(sample_file),
        is_telegram_request=True,
        auto_chained=True,
        last_user_message="create a png summary",
    )
    assert "sending" in out.lower()
    kind, payload = events.get_nowait()
    assert kind == "send_file_to_telegram"
    assert payload["explicit_artifact_intent"] is True


def test_auto_chained_desktop_without_ask_refused(sample_file):
    events = queue.Queue()
    tool = SendFileToTelegramTool(event_queue=events)
    out = tool._run(
        file_path=str(sample_file),
        auto_chained=True,
        last_user_message="compress Desktop into archive.zip",
    )
    assert "Skipped" in out
    assert events.empty()
