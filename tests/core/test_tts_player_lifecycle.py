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


def test_direct_desktop_tts_started_does_not_open_player(monkeypatch):
    from distr.app import events

    class _FakeTimeout:
        def connect(self, *_args, **_kwargs):
            pass

    class _FakeQTimer:
        def __init__(self, *_args, **_kwargs):
            self.timeout = _FakeTimeout()

        def setSingleShot(self, *_args, **_kwargs):
            pass

        def isActive(self):
            return False

        def stop(self):
            pass

        def start(self, *_args, **_kwargs):
            pass

    app = _App()
    app._event_dedup_cache = {}
    app._tts_active_sessions = 0
    app._tts_pending_non_interrupt_closes = 0
    app._tts_player_generation = 0
    app.player_window = _Player()

    signals = _SignalManager()
    monkeypatch.setattr(events, "signal_manager", signals)
    monkeypatch.setattr(events, "QTimer", _FakeQTimer)

    app._evt_tts_player("tts_started", {"source": "direct_desktop"})

    assert app._tts_active_sessions == 0
    signals.show_player_window.emit.assert_not_called()
    signals.player_play.emit.assert_not_called()


def test_zero_duration_stop_with_no_visible_player_does_not_reset_player(monkeypatch):
    from distr.app import events

    class _FakeTimeout:
        def connect(self, *_args, **_kwargs):
            pass

    class _FakeQTimer:
        def __init__(self, *_args, **_kwargs):
            self.timeout = _FakeTimeout()

        def setSingleShot(self, *_args, **_kwargs):
            pass

        def isActive(self):
            return False

        def stop(self):
            pass

        def start(self, *_args, **_kwargs):
            pass

    app = _App()
    app._event_dedup_cache = {}
    app._tts_active_sessions = 0
    app._tts_pending_non_interrupt_closes = 0
    app._tts_player_generation = 0
    app.player_window = _Player()

    signals = _SignalManager()
    monkeypatch.setattr(events, "signal_manager", signals)
    monkeypatch.setattr(events, "QTimer", _FakeQTimer)

    app._evt_tts_player("tts_stopped", {"duration": 0.0})

    signals.player_stop.emit.assert_not_called()


def test_set_dictating_deduplicates_repeated_state():
    app = _App()
    app._last_set_dictating_enabled = False
    app.sent = []

    def send(command, payload):
        app.sent.append((command, payload))

    app._send_command_to_agent = send

    app._dispatch_agent_event("set_dictating", {"enabled": False})
    app._dispatch_agent_event("set_dictating", {"enabled": True})
    app._dispatch_agent_event("set_dictating", {"enabled": True})

    assert app.sent == [("set_dictating", {"enabled": True})]
