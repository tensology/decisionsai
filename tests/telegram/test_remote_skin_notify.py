"""Tests for remote skin change notifications."""

from unittest.mock import MagicMock, patch

from distr.core.integrations.telegram.remote_skin import notify_remote_skin_changed


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
