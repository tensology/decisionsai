"""Window ops: list / focus / launch / set_bounds via sidecar."""

from __future__ import annotations

from unittest.mock import patch

from distr.core.agent.tools.input.window_ops import (
    FocusWindowTool,
    LaunchAppTool,
    ListWindowsTool,
    SetWindowBoundsTool,
    format_window_list,
    resolve_window_pid,
)

_WINDOWS = [
    {
        "title": "Finder",
        "pid": 111,
        "process_name": "Finder",
        "left": 0,
        "top": 0,
        "right": 1440,
        "bottom": 900,
        "is_foreground": True,
    },
    {
        "title": "bash — 80x24",
        "pid": 222,
        "process_name": "Terminal",
        "left": 100,
        "top": 100,
        "right": 900,
        "bottom": 700,
        "is_foreground": False,
    },
]


def test_format_window_list_includes_pid_and_title():
    text = format_window_list(_WINDOWS)
    assert "pid=222" in text
    assert "Terminal" in text
    assert "bash" in text


def test_resolve_pid_from_process_name():
    with patch(
        "distr.core.agent.tools.input.window_ops._call_sidecar",
        return_value={"windows": _WINDOWS},
    ):
        pid, window = resolve_window_pid(process_name="Terminal")
    assert pid == 222
    assert window["process_name"] == "Terminal"


def test_resolve_pid_passthrough_skips_list():
    calls = []

    def fake(tool, params, timeout=20):
        calls.append(tool)
        return {}

    with patch("distr.core.agent.tools.input.window_ops._call_sidecar", side_effect=fake):
        pid, window = resolve_window_pid(pid=999)
    assert pid == 999
    assert window == {}
    assert calls == []


def test_list_windows_tool():
    with patch(
        "distr.core.agent.tools.input.window_ops._call_sidecar",
        return_value={"windows": _WINDOWS},
    ):
        output = ListWindowsTool()._run()
    assert "Terminal" in output
    assert "222" in output


def test_focus_window_by_name():
    calls = []

    def fake(tool, params, timeout=20):
        calls.append((tool, params))
        if tool == "list_windows":
            return {"windows": _WINDOWS}
        return {"success": True, "pid": params.get("pid")}

    with patch("distr.core.agent.tools.input.window_ops._call_sidecar", side_effect=fake):
        output = FocusWindowTool()._run(process_name="Terminal")
    assert "222" in output
    assert any(t == "focus_window" and p.get("pid") == 222 for t, p in calls)


def test_launch_app_uses_executable():
    with patch(
        "distr.core.agent.tools.input.window_ops._call_sidecar",
        return_value={"success": True, "app": "TextEdit"},
    ) as mocked:
        output = LaunchAppTool()._run(executable="TextEdit")
    assert "TextEdit" in output
    mocked.assert_called_once()
    assert mocked.call_args[0][0] == "launch_app"
    assert mocked.call_args[0][1]["executable"] == "TextEdit"


def test_set_window_bounds_snap_left():
    """Focus+snap must not screenshot; it is pid + snap via sidecar."""
    calls = []

    def fake(tool, params, timeout=20):
        calls.append((tool, params))
        if tool == "list_windows":
            return {"windows": _WINDOWS}
        return {
            "success": True,
            "pid": 222,
            "x": 0,
            "y": 0,
            "w": 720,
            "h": 900,
            "snap": "left",
        }

    with patch("distr.core.agent.tools.input.window_ops._call_sidecar", side_effect=fake):
        output = SetWindowBoundsTool()._run(process_name="Terminal", snap="left")
    assert "222" in output
    assert "720" in output
    bounds = next(p for t, p in calls if t == "set_window_bounds")
    assert bounds["pid"] == 222
    assert bounds["snap"] == "left"
    assert not any(t == "capture_screen" for t, _ in calls)
