"""Desktop chat switches must notify the agent subprocess."""

from unittest.mock import MagicMock, patch

from distr.app.signals import SignalBridgeMixin


class _AppStub(SignalBridgeMixin):
    def __init__(self):
        self._suppress_current_chat_relay = False
        self.chat_manager = MagicMock()
        self._commands = []

    def _send_command_to_agent(self, command, params):
        self._commands.append((command, params))

    def _on_interrupt_tts(self):
        pass


def test_chat_manager_current_chat_changed_wired_to_signal_manager():
    app = _AppStub()
    agent_relay = None

    with patch("distr.app.signals.signal_manager") as mock_sm:
        mock_sm.current_chat_changed.disconnect = MagicMock()

        def capture_agent_relay(slot):
            nonlocal agent_relay
            agent_relay = slot

        mock_sm.current_chat_changed.connect.side_effect = capture_agent_relay
        mock_sm.current_chat_changed.emit = MagicMock()

        with patch(
            "distr.app.signals.load_settings_from_db", return_value={}
        ), patch("distr.app.signals.save_settings_to_db"):
            app._bridge_signals_to_agent()

        app.chat_manager.current_chat_changed.connect.assert_called_once_with(
            mock_sm.current_chat_changed.emit
        )

        agent_relay(72)

    assert app._commands == [("current_chat_changed", {"chat_id": 72})]
