from distr.core.harness.scheduled_actions import (
    compile_scheduled_action_workflow,
    normalize_scheduled_action,
    preview_scheduled_action,
)


def test_normalizes_one_time_keypress_preview():
    spec = normalize_scheduled_action(
        {
            "title": "Press Enter",
            "schedule": {"kind": "once", "run_at": "2026-06-02T13:05:00+02:00"},
            "action": {"type": "keypress", "key": "Enter"},
        }
    )

    assert spec["action"]["type"] == "keypress"
    assert spec["action"]["key"] == "enter"
    assert "Press enter" in preview_scheduled_action(spec)


def test_preview_includes_schedule_target_and_safety_details():
    preview = preview_scheduled_action(
        {
            "title": "Press Enter",
            "schedule": {
                "kind": "once",
                "run_at": "2026-06-02T13:05:00+02:00",
                "timezone": "Africa/Johannesburg",
            },
            "action": {"type": "keypress", "key": "Enter"},
            "target_context": {"app_name": "Chrome", "window_title_hint": "Inbox"},
            "safety": {"bring_app_to_front": True, "require_app_in_foreground": True},
        }
    )

    assert "2026-06-02T13:05:00+02:00" in preview
    assert "Africa/Johannesburg" in preview
    assert "Target app: Chrome" in preview
    assert "window: Inbox" in preview
    assert "bring app to front first" in preview
    assert "requires target app foreground" in preview


def test_normalizes_type_text_without_enter():
    spec = normalize_scheduled_action(
        {
            "title": "Type status",
            "schedule": {"kind": "daily", "time": "18:00", "timezone": "Africa/Johannesburg"},
            "action": {"type": "type_text", "text": "done for the day", "press_enter": False},
        }
    )

    assert spec["action"]["press_enter"] is False
    assert "daily" in preview_scheduled_action(spec).lower()


def test_rejects_unsafe_empty_text_action():
    try:
        normalize_scheduled_action(
            {
                "title": "Empty",
                "schedule": {"kind": "weekly", "weekday": "monday", "time": "08:30"},
                "action": {"type": "type_text", "text": ""},
            }
        )
    except ValueError as exc:
        assert "text" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError")


def test_compiles_daily_type_text_to_scheduled_workflow_payload():
    compiled = compile_scheduled_action_workflow(
        {
            "title": "Type status",
            "schedule": {"kind": "daily", "time": "18:00", "timezone": "Africa/Johannesburg"},
            "action": {"type": "type_text", "text": "done for the day", "press_enter": False},
        }
    )

    assert compiled["workflow"]["workflow_type"] == "scheduled"
    assert compiled["workflow"]["schedule_enabled"] is True
    assert compiled["workflow"]["schedule_preset"] == "daily"
    assert compiled["workflow"]["schedule_time"] == "18:00"
    assert compiled["workflow"]["schedule_timezone"] == "Africa/Johannesburg"
    step = compiled["steps"][0]
    assert step["action_type"] == "computer_use"
    assert "Type 'done for the day'" in step["instruction"]
    assert step["config"]["goal"] == step["instruction"]


def test_compiles_open_app_weekdays_to_workflow_payload():
    compiled = compile_scheduled_action_workflow(
        {
            "title": "Open dashboard",
            "schedule": {"kind": "weekdays", "time": "08:30"},
            "action": {"type": "open_app", "app_name": "Chrome"},
        }
    )

    assert compiled["workflow"]["schedule_preset"] == "weekly"
    assert compiled["workflow"]["schedule_days"] == "1,2,3,4,5"
    assert compiled["steps"][0]["action_type"] == "computer_use"
    assert "Open Chrome" in compiled["steps"][0]["instruction"]


def test_compiles_play_recording_to_play_recording_step():
    compiled = compile_scheduled_action_workflow(
        {
            "title": "Replay login",
            "schedule": {"kind": "weekly", "weekday": "monday", "time": "09:00"},
            "action": {"type": "play_recording", "recording_name": "login-flow"},
        }
    )

    step = compiled["steps"][0]
    assert step["action_type"] == "play_recording"
    assert step["recording_filename"] == "login-flow"
    assert step["config"]["recording_name"] == "login-flow"
