import pynput.keyboard
from distr.core.agent.tools.media import media_control


def test_media_control_returns_conversational_confirmation(monkeypatch):
    pressed = []

    class Keyboard:
        def press(self, key):
            pressed.append(("press", key))

        def release(self, key):
            pressed.append(("release", key))

    monkeypatch.setattr(media_control, "pyautogui", object())
    monkeypatch.setattr(pynput.keyboard, "Controller", Keyboard)

    result = media_control.MediaControlTool()._run(action="next_track")

    assert result == "Skipped to the next track."
    assert pressed == [
        ("press", pynput.keyboard.Key.media_next),
        ("release", pynput.keyboard.Key.media_next),
    ]


def test_named_browser_reload_focuses_target_then_restores_previous_app(monkeypatch):
    from distr.core.actions import desktop
    from distr.core.agent.tools.input.window_ops import FocusWindowTool

    calls = []

    class PyAutoGUI:
        @staticmethod
        def hotkey(*keys):
            calls.append(("hotkey", keys))

    def focus(self, process_name="", **kwargs):
        calls.append(("focus", process_name))
        return f"Focused {process_name}"

    monkeypatch.setattr(media_control, "pyautogui", PyAutoGUI())
    monkeypatch.setattr(media_control.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(desktop, "get_frontmost_app_name", lambda: "Python")
    monkeypatch.setattr(desktop, "get_remembered_external_frontmost_app", lambda: "Cursor")
    monkeypatch.setattr(FocusWindowTool, "_run", focus)

    result = media_control.MediaControlTool()._run(
        action="refresh",
        target_app="Brave",
        restore_focus=True,
    )

    assert result == "Reloaded Brave Browser and returned focus to Cursor."
    assert calls == [
        ("focus", "Brave Browser"),
        ("hotkey", ("command", "r")),
        ("focus", "Cursor"),
    ]


def test_media_control_schema_exposes_named_app_focus_options():
    from distr.core.agent.services.llm.tool_format import get_tool_parameters

    media_properties = get_tool_parameters("media_control")["properties"]
    oracle_properties = get_tool_parameters("oracle_control")["properties"]

    assert "target_app" in media_properties
    assert "restore_focus" in media_properties
    assert "target_app" not in oracle_properties
