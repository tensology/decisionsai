from unittest.mock import MagicMock

from distr.app.events import EventHandlerMixin


class _Timer:
    def isActive(self):
        return False

    def stop(self):
        pass

    def start(self, *_args, **_kwargs):
        pass


class _Player:
    def isVisible(self):
        return False


class _Signal:
    def __init__(self):
        self.emit = MagicMock()


class _SignalManager:
    def __init__(self):
        self.show_player_window = _Signal()
        self.player_play = _Signal()
        self.player_stop = _Signal()

    def emit_hide_player_window(self):
        pass


class _App(EventHandlerMixin):
    pass


def test_delayed_player_callbacks_are_dropped_after_playback_finished(monkeypatch):
    from distr.app import events

    app = _App()
    app._event_dedup_cache = {}
    app._tts_active_sessions = 1
    app._tts_pending_non_interrupt_closes = 0
    app._tts_player_generation = 7
    app._tts_non_interrupt_fallback_timer = _Timer()
    app.player_window = _Player()

    signals = _SignalManager()
    monkeypatch.setattr(events, "signal_manager", signals)

    app._evt_tts_player("playback_finished", {})

    assert app._tts_active_sessions == 0
    assert app._tts_player_generation == 8

    app._emit_player_signal_if_tts_active(7, "show")
    app._emit_player_signal_if_tts_active(7, "play")

    signals.show_player_window.emit.assert_not_called()
    signals.player_play.emit.assert_not_called()
