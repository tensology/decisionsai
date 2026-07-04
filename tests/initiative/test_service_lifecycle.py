import pytest
from unittest.mock import MagicMock, patch


class TestInitiativeServiceLifecycle:
    def _make_service(self):
        from distr.core.initiative.service import InitiativeService
        mock_telegram = MagicMock()
        mock_telegram.telegram_user_id = None
        mock_chat = MagicMock()

        with patch("distr.core.initiative.service.QTimer") as MockQTimer, \
             patch("distr.core.initiative.service.DraftQueue"), \
             patch("distr.core.initiative.service.ContextAssembler"), \
             patch("distr.core.utils.load_settings_from_db", return_value={"initiative_level": "assist"}):
            mock_timer_instance = MagicMock()
            MockQTimer.return_value = mock_timer_instance
            MockQTimer.singleShot = MagicMock()
            service = InitiativeService(
                telegram_manager=mock_telegram,
                chat_manager=mock_chat,
            )
        return service

    def test_start_is_idempotent(self):
        service = self._make_service()
        with patch("distr.core.signals.signal_manager") as mock_sm:
            service.start()
            service.start()  # second call should be no-op
            # connect should only be called once per signal
            assert mock_sm.chat_stream_finished.connect.call_count == 1

    def test_stop_is_idempotent(self):
        service = self._make_service()
        with patch("distr.core.signals.signal_manager"):
            service.start()
            service.stop()
            service.stop()  # second call should be no-op
            assert service._stopped is True
            assert service._started is False

    def test_stop_sets_stopped_flag(self):
        service = self._make_service()
        with patch("distr.core.signals.signal_manager"):
            service.start()
            assert service._stopped is False
            service.stop()
            assert service._stopped is True

    def test_reset_idle_timer_calls_start(self):
        service = self._make_service()
        mock_timer = MagicMock()
        service._idle_timer = mock_timer
        bridge = MagicMock()
        bridge.reset_idle_timer_requested.emit.side_effect = (
            lambda *a, **k: service._reset_idle_timer_on_qt()
        )
        service._qt_bridge = bridge
        service._reset_idle_timer(chat_id=42)
        mock_timer.start.assert_called_once_with(service.IDLE_TIMEOUT_MS)

    def test_cycle_running_flag_prevents_reentry(self):
        service = self._make_service()
        service._cycle_running = True
        dispatched = []

        original_dispatch = service._dispatch_cycle

        def fake_dispatch(trigger):
            dispatched.append(trigger)

        service._dispatch_cycle = fake_dispatch

        with patch("distr.core.utils.load_settings_from_db", return_value={"initiative_level": "operate"}), \
             patch("distr.core.initiative.service.QTimer") as MockQTimer:
            MockQTimer.singleShot = MagicMock()
            service._on_idle_timer_expired()
            MockQTimer.singleShot.assert_not_called()

    def test_dispatch_cycle_skips_during_quiet_window(self):
        service = self._make_service()
        service._last_cycle_at = 1_000.0
        ran = []
        service._run_initiative_cycle = lambda trigger: ran.append(trigger)

        with patch("distr.core.initiative.service.time.time", return_value=1_020.0):
            service._dispatch_cycle("schedule_tick")

        assert ran == []
        assert service._cycle_running is False
