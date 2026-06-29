import sys
import threading
import types

from distr.core.audio import voice_cloning


def test_loaded_agent_stt_wait_is_short_by_default(monkeypatch, tmp_path):
    waits = []

    class _NoResultEvent:
        def wait(self, timeout=None):
            waits.append(timeout)
            return False

    class _QueueNoResult:
        def put(self, *_args, **_kwargs):
            return None

    class _App:
        agent_command_queue = _QueueNoResult()

    qt_mod = types.ModuleType("PyQt6")
    widgets_mod = types.ModuleType("PyQt6.QtWidgets")

    class _QApplication:
        @staticmethod
        def instance():
            return _App()

    widgets_mod.QApplication = _QApplication
    monkeypatch.setitem(sys.modules, "PyQt6", qt_mod)
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", widgets_mod)
    monkeypatch.setattr(threading, "Event", _NoResultEvent)

    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"not real audio")

    result = voice_cloning._transcribe_via_loaded_agent_stt(str(audio_path))

    assert result is None
    assert waits
    assert waits[-1] <= 3.0
