from distr.core.hotkeys import (
    DEFAULTS,
    chord_to_qt_sequence,
    format_ptt_combo_display,
    format_shortcut_display,
    parse_remote_hotkey,
)


def test_format_shortcut_display_with_key() -> None:
    label = format_shortcut_display("control_command", "down_arrow")
    assert "Control" in label
    assert "Command" in label
    assert "Down Arrow" in label


def test_format_shortcut_display_modifier_only() -> None:
    assert format_shortcut_display("option_command", "") == "Option (Alt) + Command (hold)"


def test_chord_to_qt_sequence() -> None:
    assert chord_to_qt_sequence("control_command", "s") == "Ctrl+Meta+S"
    assert chord_to_qt_sequence("option_command", "grave") == "Alt+Meta+`"


def test_format_ptt_combo_display() -> None:
    assert "Option" in format_ptt_combo_display("option_command")
    assert "(hold)" in format_ptt_combo_display("option_command")


def test_parse_remote_hotkey() -> None:
    assert parse_remote_hotkey("ctrl+shift+k") == ("control_shift", "k")
    assert parse_remote_hotkey("") is None


def test_web_launcher_default_hotkeys_use_control_shift() -> None:
    expected = {
        "chat": "c",
        "projects": "j",
        "actions": "a",
        "snippets": "n",
        "workflows": "w",
        "automations": "o",
        "ticket_board": "t",
        "preferences": "p",
    }
    for launcher, key in expected.items():
        assert DEFAULTS[f"web_hotkey_{launcher}_modifier"] == "control_shift"
        assert DEFAULTS[f"web_hotkey_{launcher}_key"] == key
