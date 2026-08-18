"""Desktop ops run in the Decisions process when the sidecar cannot."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from distr.core.agent.tools.input.desktop_local import run_local_desktop_tool
from distr.core.macos_permissions import (
    permissions_setup_needed,
    user_facing_permission_items,
)


def test_list_windows_falls_back_to_decisions_process(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.tools.input.sidecar_http.requests.post",
        lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError()),
    )
    monkeypatch.setattr(
        "distr.core.agent.tools.input.desktop_local.run_local_desktop_tool",
        lambda tool, params: {"windows": [{"pid": 222, "process_name": "Terminal"}]},
    )
    from distr.core.agent.tools.input.sidecar_http import call_sidecar_tool

    result = call_sidecar_tool("list_windows", {})
    assert result["windows"][0]["process_name"] == "Terminal"


def test_run_python_does_not_fallback_when_sidecar_down(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.tools.input.sidecar_http.requests.post",
        lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError()),
    )
    from distr.core.agent.tools.input.sidecar_http import call_sidecar_tool
    import pytest

    with pytest.raises(RuntimeError, match="Sidecar not running"):
        call_sidecar_tool("run_python", {"code": "print(1)"})


def test_focus_window_local_uses_appkit(monkeypatch):
    import sys

    fake_app = MagicMock()
    fake_app.activateWithOptions_.return_value = True
    fake_ns = MagicMock()
    fake_ns.runningApplicationWithProcessIdentifier_.return_value = fake_app
    fake_appkit = MagicMock()
    fake_appkit.NSRunningApplication = fake_ns
    fake_appkit.NSApplicationActivateIgnoringOtherApps = 1
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    result = run_local_desktop_tool("focus_window", {"pid": 222})
    assert result["success"] is True
    assert result["via"] == "decisions"
    assert result["pid"] == 222


def test_set_window_bounds_local_snaps_left(monkeypatch):
    monkeypatch.setattr(
        "distr.core.agent.tools.input.desktop_local._primary_visible_rect",
        lambda: (0, 25, 1440, 875),
    )
    captured = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=5):
        captured["script"] = cmd[-1]
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        m.stdout = ""
        return m

    monkeypatch.setattr("distr.core.agent.tools.input.desktop_local.subprocess.run", fake_run)
    result = run_local_desktop_tool("set_window_bounds", {"pid": 222, "snap": "left"})
    assert result["success"] is True
    assert result["w"] == 720
    assert "unix id is 222" in captured["script"]


def test_permissions_ok_without_sidecar(monkeypatch):
    monkeypatch.setattr(
        "distr.core.macos_permissions.is_permissions_setup_dismissed",
        lambda: False,
    )
    report = {
        "supported": True,
        "items": [
            {"id": "sidecar_running", "ok": False, "detail": "down"},
            {"id": "sidecar_screen_recording", "ok": False, "detail": "down"},
            {"id": "sidecar_accessibility", "ok": False, "detail": "down"},
            {"id": "sidecar_automation", "ok": False, "detail": "down"},
            {"id": "python_screen_recording", "ok": True, "detail": "ok"},
            {"id": "python_accessibility", "ok": True, "detail": "ok"},
            {"id": "python_automation", "ok": True, "detail": "ok"},
            {"id": "python_microphone", "ok": True, "detail": "ok"},
        ],
    }
    assert permissions_setup_needed(report) is False
    rows = user_facing_permission_items(report)
    desktop = next(r for r in rows if r["id"] == "desktop_control")
    assert desktop["ok"] is True
    assert desktop["prompt_target"] == "desktop"


def test_permissions_prompt_targets_decisions_not_sidecar():
    report = {
        "supported": True,
        "items": [
            {"id": "python_screen_recording", "ok": False, "detail": "not granted"},
            {"id": "python_accessibility", "ok": False, "detail": "not trusted"},
            {"id": "python_automation", "ok": False, "detail": "not allowed"},
            {"id": "python_microphone", "ok": True, "detail": "ok"},
        ],
    }
    desktop = user_facing_permission_items(report)[0]
    assert desktop["ok"] is False
    assert desktop["can_prompt"] is True
    assert "sidecar" not in desktop["enable_in_settings"].lower()
    assert "Decisions" in desktop["enable_in_settings"]
