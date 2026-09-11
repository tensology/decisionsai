from distr.core.agent.tools.input import window_management, window_ops


WINDOWS = {
    "windows": [
        {
            "window_id": 41,
            "pid": 1001,
            "process_name": "Terminal",
            "title": "Terminal",
            "is_foreground": False,
        },
        {
            "window_id": 42,
            "pid": 1002,
            "process_name": "Codex",
            "title": "Decisions audit",
            "is_foreground": True,
        },
    ]
}

SCREENS = {
    "screens": [
        {"index": 0, "name": "Dell", "x_offset": 0, "y_offset": 0, "logical_width": 1920, "logical_height": 1080, "is_primary": True},
        {"index": 1, "name": "Built-in Retina Display", "x_offset": 1920, "y_offset": 0, "logical_width": 1512, "logical_height": 982},
        {"index": 2, "name": "LG", "x_offset": -1920, "y_offset": 0, "logical_width": 1920, "logical_height": 1080},
    ]
}


def test_numbered_displays_follow_live_physical_left_to_right_order(monkeypatch):
    monkeypatch.setattr(window_management, "_call_sidecar", lambda tool, params: SCREENS)

    assert window_management._screen_index(1, "", "") == 2
    assert window_management._screen_index(2, "", "") == 0
    assert window_management._screen_index(3, "", "") == 1


def test_middle_display_uses_physical_order_not_raw_screen_index(monkeypatch):
    monkeypatch.setattr(window_management, "_call_sidecar", lambda tool, params: SCREENS)

    assert window_management._screen_index(None, "middle", "") == 0


def test_resolve_window_without_name_uses_live_foreground(monkeypatch):
    monkeypatch.setattr(window_ops, "_call_sidecar", lambda tool, params: WINDOWS)

    pid, window = window_ops.resolve_window_pid()

    assert pid == 1002
    assert window["process_name"] == "Codex"


def test_named_window_action_targets_exact_pid(monkeypatch):
    calls = []

    def fake_call(tool, params, timeout=20):
        calls.append((tool, params))
        if tool == "list_windows":
            return WINDOWS
        return {"success": True, "pid": params["pid"], "action": params["action"]}

    monkeypatch.setattr(window_ops, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "record_action", lambda *args, **kwargs: None)

    result = window_management.WindowManagementTool()._run(
        action="minimize", process_name="Terminal"
    )

    assert "succeeded" in result.lower()
    assert ("window_action", {"pid": 1001, "action": "minimize"}) in calls


def test_named_app_is_extracted_from_original_request(monkeypatch):
    calls = []

    def fake_call(tool, params, timeout=20):
        calls.append((tool, params))
        if tool == "list_windows":
            return WINDOWS
        return {"success": True, "pid": params["pid"], "action": params["action"]}

    monkeypatch.setattr(window_ops, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "record_action", lambda *args, **kwargs: None)

    result = window_management.WindowManagementTool()._run(text="Can you maximize Codex?")

    assert "succeeded" in result.lower()
    assert ("window_action", {"pid": 1002, "action": "maximize"}) in calls


def test_move_to_named_screen_uses_real_display_offsets(monkeypatch):
    calls = []

    def fake_call(tool, params, timeout=20):
        calls.append((tool, params))
        if tool == "list_windows":
            return WINDOWS
        if tool == "get_screen_info":
            return SCREENS
        return {"success": True, "pid": params["pid"], "x": -1536, "y": 120, "w": 1536, "h": 840}

    monkeypatch.setattr(window_ops, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "record_action", lambda *args, **kwargs: None)

    result = window_management.WindowManagementTool()._run(
        text="Move the terminal to the left screen", process_name="Terminal"
    )

    assert "verified" in result.lower()
    assert (
        "set_window_bounds",
        {"pid": 1001, "screen": 2, "snap": "center"},
    ) in calls


def test_move_to_screen_does_not_claim_success_when_coordinates_miss_target(monkeypatch):
    def fake_call(tool, params, timeout=20):
        if tool == "list_windows":
            return WINDOWS
        if tool == "get_screen_info":
            return SCREENS
        return {"success": True, "pid": params["pid"], "x": 200, "y": 120, "w": 900, "h": 700}

    monkeypatch.setattr(window_ops, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "record_action", lambda *args, **kwargs: None)

    result = window_management.WindowManagementTool()._run(
        text="Move the terminal to the left screen", process_name="Terminal"
    )

    assert result.startswith("Error:")
    assert "verification" in result.lower()


def test_second_desktop_is_space_not_second_display(monkeypatch):
    calls = []

    def fake_call(tool, params, timeout=20):
        calls.append((tool, params))
        if tool == "list_windows":
            return WINDOWS
        return {"success": True, "pid": params.get("pid")}

    monkeypatch.setattr(window_ops, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "_call_sidecar", fake_call)
    monkeypatch.setattr(window_management, "record_action", lambda *args, **kwargs: None)

    result = window_management.WindowManagementTool()._run(
        text="Move the terminal to my second desktop", process_name="Terminal"
    )

    assert "succeeded" in result.lower()
    assert ("move_window_to_space", {"space": 2, "display": 0, "pid": 1001}) in calls
