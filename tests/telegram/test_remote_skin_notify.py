"""Tests for remote skin change notifications."""

from unittest.mock import MagicMock, patch

import pytest

from distr.core.integrations.telegram import remote_skin
from distr.core.integrations.telegram.remote_skin import (
    notify_remote_avatar_state,
    notify_remote_skin_changed,
)


@pytest.fixture(autouse=True)
def reset_avatar_state_throttle():
    remote_skin._reset_avatar_state_throttle()
    yield
    remote_skin._reset_avatar_state_throttle()


def test_notify_remote_skin_changed_sends_payload():
    manager = MagicMock()
    manager.is_connected.return_value = True
    manager._send_websocket_message.return_value = True

    with patch(
        "distr.core.integrations.telegram.remote_skin._resolve_telegram_manager",
        return_value=manager,
    ):
        sent = notify_remote_skin_changed(
            folder_name="clippy",
            skin_name="Clippy",
            skin_type="avatar",
            idle_animation="idle.webm",
        )

    assert sent is True
    manager._send_websocket_message.assert_called_once_with(
        {
            "type": "remote_skin_changed",
            "data": {
                "selected_skin": "clippy",
                "name": "Clippy",
                "type": "avatar",
                "idle_animation": "idle.webm",
            },
        }
    )


def test_notify_remote_skin_changed_no_manager():
    with patch(
        "distr.core.integrations.telegram.remote_skin._resolve_telegram_manager",
        return_value=None,
    ):
        assert notify_remote_skin_changed(folder_name="oracle") is False


def test_notify_remote_avatar_state_sends_active_animation():
    manager = MagicMock()
    manager.is_connected.return_value = True
    manager._send_websocket_message.return_value = True

    with patch(
        "distr.core.integrations.telegram.remote_skin._resolve_telegram_manager",
        return_value=manager,
    ):
        sent = notify_remote_avatar_state(
            folder_name="hayley",
            hook="thinking",
            animation="thinking.webm",
        )

    assert sent is True
    manager._send_websocket_message.assert_called_once_with(
        {
            "type": "remote_avatar_state",
            "data": {
                "selected_skin": "hayley",
                "hook": "thinking",
                "animation": "thinking.webm",
                "playback": "loop",
            },
        }
    )


def test_notify_remote_avatar_state_dedupes_identical_states():
    manager = MagicMock()
    manager._send_websocket_message.return_value = True

    with patch.object(remote_skin, "_resolve_telegram_manager", return_value=manager):
        assert notify_remote_avatar_state(folder_name="hayley", hook="idle", animation="idle.webm")
        assert notify_remote_avatar_state(folder_name="hayley", hook="idle", animation="idle.webm")

    manager._send_websocket_message.assert_called_once()


def test_notify_remote_avatar_state_flushes_only_the_latest_pending_state():
    manager = MagicMock()
    manager._send_websocket_message.return_value = True

    with (
        patch.object(remote_skin, "_resolve_telegram_manager", return_value=manager),
        patch.object(remote_skin, "time") as clock,
        patch.object(remote_skin.threading, "Timer") as timer,
    ):
        clock.monotonic.return_value = 10.0
        assert notify_remote_avatar_state(folder_name="hayley", hook="idle", animation="idle.webm")
        clock.monotonic.return_value = 10.25
        assert notify_remote_avatar_state(
            folder_name="hayley", hook="thinking", animation="thinking.webm"
        )
        assert notify_remote_avatar_state(
            folder_name="hayley", hook="talking", animation="talking.webm"
        )

        manager._send_websocket_message.assert_called_once()
        timer.assert_called_once_with(0.75, remote_skin._flush_pending_avatar_state)
        assert timer.return_value.daemon is True
        timer.return_value.start.assert_called_once()

        clock.monotonic.return_value = 11.0
        remote_skin._flush_pending_avatar_state()

    sent_hooks = [
        call.args[0]["data"]["hook"] for call in manager._send_websocket_message.call_args_list
    ]
    assert sent_hooks == ["idle", "talking"]
