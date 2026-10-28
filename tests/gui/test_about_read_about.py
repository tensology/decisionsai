"""Read about must replace the current read instead of stacking another one.

The button re-enables on player_stop, which can fire before the queued sentences
have drained. A second click then enqueued the whole About copy again behind audio
that was still playing, and the player lost the order of the stream.
"""

from __future__ import annotations

from distr.gui.dialogs.about import AboutWindow


class _Signal:
    def __init__(self, name: str, log: list) -> None:
        self._name = name
        self._log = log

    def emit(self, *args, **kwargs) -> None:
        self._log.append(self._name)


class _SignalManager:
    def __init__(self, log: list) -> None:
        self.interrupt_tts = _Signal("stop", log)
        self.speak_text_directly = _Signal("speak", log)


class _Button:
    def __init__(self) -> None:
        self.enabled = True
        self.text = "Read about"

    def isEnabled(self) -> bool:
        return self.enabled

    def setEnabled(self, value) -> None:
        self.enabled = bool(value)

    def setText(self, value) -> None:
        self.text = value


class _Window:
    # Reuse the real methods so the stub matches the dialog's surface.
    _stop_current_read = AboutWindow.__dict__["_stop_current_read"]
    _enable_read_button = AboutWindow._enable_read_button
    _read_about_aloud = AboutWindow._read_about_aloud

    def __init__(self) -> None:
        self._read_button = _Button()
        self._about_text = "The About copy."


def test_a_second_click_replaces_the_read_instead_of_stacking(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr("distr.core.signals.signal_manager", _SignalManager(log))

    window = _Window()
    AboutWindow._on_read_about_clicked(window)
    AboutWindow._on_read_about_clicked(window)

    # Every click stops what is playing, then starts one fresh read.
    assert log == ["stop", "speak", "stop", "speak"]


def test_the_button_greys_out_while_reading(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr("distr.core.signals.signal_manager", _SignalManager(log))

    window = _Window()
    AboutWindow._on_read_about_clicked(window)

    assert window._read_button.enabled is False
    assert window._read_button.text == "Reading…"
