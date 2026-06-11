import sys
import types

import distr.core.signals as signals_mod


class _QueueStub:
    def __init__(self):
        self.events = []

    def put(self, item, block=False):
        self.events.append(item)


def test_speak_text_directly_event_queue_deduplicates_burst(monkeypatch):
    q = _QueueStub()
    monkeypatch.setattr(signals_mod, "_agent_event_queue", q)
    monkeypatch.setattr(signals_mod, "_last_speak_text", "")
    monkeypatch.setattr(signals_mod, "_last_speak_ts", 0.0)

    now = {"value": 1000.0}

    def _fake_time():
        return now["value"]

    monkeypatch.setattr(signals_mod.time, "time", _fake_time)

    signals_mod.speak_text_directly_event_queue("Done.")
    signals_mod.speak_text_directly_event_queue("Done.")
    now["value"] += 1.1
    signals_mod.speak_text_directly_event_queue("Done.")

    assert len(q.events) == 2
    assert q.events[0][1]["text"] == "Done."
    assert q.events[1][1]["text"] == "Done."


def test_speak_text_directly_event_queue_uses_queue_even_with_qapplication(monkeypatch):
    """Web/API threads must not emit Qt signals directly when a queue is registered."""
    q = _QueueStub()
    monkeypatch.setattr(signals_mod, "_agent_event_queue", q)
    monkeypatch.setattr(signals_mod, "_last_speak_text", "")
    monkeypatch.setattr(signals_mod, "_last_speak_ts", 0.0)

    direct_emit_calls = []
    monkeypatch.setattr(
        signals_mod,
        "signal_manager",
        types.SimpleNamespace(
            speak_text_directly=types.SimpleNamespace(
                emit=lambda text: direct_emit_calls.append(text)
            )
        ),
    )

    class _FakeApp:
        agent_event_queue = q

    qt_widgets = types.ModuleType("PyQt6.QtWidgets")

    class _QApplication:
        @staticmethod
        def instance():
            return _FakeApp()

    qt_widgets.QApplication = _QApplication
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", qt_widgets)

    signals_mod.speak_text_directly_event_queue("Synced 3 new messages from WhatsApp.")

    assert len(q.events) == 1
    assert q.events[0] == (
        "speak_text_directly",
        {"text": "Synced 3 new messages from WhatsApp."},
    )
    assert direct_emit_calls == []
