import sys
import types
from pathlib import Path
from types import SimpleNamespace

from distr.core.agent.tools.vision import screen_capture


def test_macos_capture_is_silent(monkeypatch, tmp_path):
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"png")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(screen_capture.platform, "system", lambda: "Darwin")
    monkeypatch.setattr("subprocess.run", run)

    assert screen_capture.capture_screenshot(str(tmp_path / "screen.png"))
    assert commands == [["screencapture", "-x", str(tmp_path / "screen.png")]]


def test_qt_all_screens_does_not_recapture_primary(monkeypatch, tmp_path):
    commands = []

    class FakePixmap:
        def save(self, path, _format):
            Path(path).write_bytes(b"png")
            return True

    class FakeScreen:
        def geometry(self):
            return SimpleNamespace(left=lambda: 0, top=lambda: 0, width=lambda: 100, height=lambda: 100)

        def name(self):
            return "display"

        def grabWindow(self, _window):
            return FakePixmap()

    class FakeQScreen:
        @staticmethod
        def availableScreens():
            raise AttributeError("use QApplication")

    class FakeApplication:
        @staticmethod
        def instance():
            return SimpleNamespace(screens=lambda: [FakeScreen()])

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="Resolution:", stderr="")

    qt_widgets = types.ModuleType("PyQt6.QtWidgets")
    qt_widgets.QApplication = FakeApplication
    qt_gui = types.ModuleType("PyQt6.QtGui")
    qt_gui.QScreen = FakeQScreen
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", qt_widgets)
    monkeypatch.setitem(sys.modules, "PyQt6.QtGui", qt_gui)
    monkeypatch.setattr(screen_capture.platform, "system", lambda: "Darwin")
    monkeypatch.setattr("subprocess.run", run)

    paths = screen_capture.capture_all_screens(str(tmp_path))

    assert paths == [str(tmp_path / "screen_1.png")]
    assert not any(command[0] == "screencapture" for command in commands)
