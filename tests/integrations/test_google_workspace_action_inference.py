from __future__ import annotations

from distr.core.agent.tools.integrations.google_workspace_tool import (
    GoogleWorkspaceTool,
    _resolve_google_workspace_action,
)


def test_resolve_action_from_flattened_calendar_fields() -> None:
    action = _resolve_google_workspace_action(
        None,
        {},
        {
            "summary": "Visit Louis",
            "start_time": "2026-06-20T13:00:00",
            "end_time": "2026-06-20T14:00:00",
        },
    )
    assert action == "create_calendar_event"


def test_resolve_action_from_events_batch() -> None:
    events = [
        {
            "summary": "Breakfast",
            "start_time": "2026-05-05T08:00:00",
            "end_time": "2026-05-05T08:45:00",
        }
    ]
    action = _resolve_google_workspace_action(None, {}, {}, events=events)
    assert action == "create_calendar_events_batch"


def test_run_without_action_but_with_calendar_params(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    captured: dict = {}

    def fake_create_calendar_event(*args, **kwargs):
        captured["summary"] = args[1] if len(args) > 1 else kwargs.get("summary")
        return "evt-1"

    fake_connector = type(
        "Conn",
        (),
        {
            "is_connected": lambda self: True,
            "create_calendar_event": fake_create_calendar_event,
        },
    )()
    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", fake_connector)

    result = tool._run(
        summary="Visit Louis",
        start_time="2026-06-20T13:00:00",
        end_time="2026-06-20T14:00:00",
    )

    assert result == "Event created successfully (ID: evt-1)"
    assert captured.get("summary") == "Visit Louis"
