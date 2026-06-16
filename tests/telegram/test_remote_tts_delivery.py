"""Tests for remote TTS delivery helpers."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from distr.core.integrations.telegram.remote_tts_delivery import (
    build_synthetic_remote_context,
    deliver_remote_tts,
    is_remote_delivery_available,
    resolve_remote_delivery_context,
)
from distr.core.notification_routing import record_surface_activity, reset_notification_activity


class DummyManager:
    def __init__(self, *, connected=True):
        self.connected = connected
        self.sent = []
        self._pending_remote_agent_response = None
        self._cancelled_remote_audio_requests = set()

    def is_connected(self):
        return self.connected

    def _send_websocket_message(self, message):
        self.sent.append(message)
        return True


def test_is_remote_delivery_available_when_recent_remote_activity():
    reset_notification_activity()
    manager = DummyManager()
    record_surface_activity("remote", at=1000.0)
    assert is_remote_delivery_available(manager, now=1100.0, window_s=300.0)


def test_resolve_remote_delivery_context_uses_synthetic_for_proactive():
    reset_notification_activity()
    manager = DummyManager()
    now = time.time()
    manager._pending_remote_agent_response = {
        "request_id": "agent-1",
        "created_at": now,
        "source_command": "instruction",
        "mode": "command",
    }
    record_surface_activity("remote", at=now)

    ctx = resolve_remote_delivery_context(
        manager,
        {"mode": "proactive", "engagement_source": "workflow"},
        consume_pending=False,
    )

    assert ctx is not None
    assert ctx.get("synthetic") is True
    assert ctx.get("request_id") != "agent-1"
    assert manager._pending_remote_agent_response["request_id"] == "agent-1"


def test_deliver_remote_tts_sends_text_before_audio(tmp_path):
    manager = DummyManager()
    ogg_path = tmp_path / "voice.ogg"
    ogg_path.write_bytes(b"OggS" + b"\x00" * 20)

    def generate_tts(_text):
        return ogg_path

    def convert_wav_to_ogg(path):
        return path

    cleanup = MagicMock()

    ok = deliver_remote_tts(
        manager,
        "Workflow paused.",
        build_synthetic_remote_context({"mode": "proactive"}),
        generate_tts=generate_tts,
        convert_wav_to_ogg=convert_wav_to_ogg,
        cleanup_files=cleanup,
        send_text_first=True,
    )

    assert ok is True
    response_messages = [msg for msg in manager.sent if msg.get("type") == "remote_agent_response"]
    assert response_messages
    first = response_messages[0]["data"]
    assert first["text"] == "Workflow paused."
    assert first["audio_pending"] is True
    assert first["audio_streamed"] is True
    assert any(msg.get("type") == "remote_agent_audio_start" for msg in manager.sent)
    cleanup.assert_called()
