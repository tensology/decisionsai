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


def test_calendar_create_surfaces_google_authentication_failure(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    fake_connector = type(
        "Conn",
        (),
        {
            "is_connected": lambda self: True,
            "last_error": (
                "Google authentication failed while refreshing access. "
                "Reconnect the Google account in Settings."
            ),
            "create_calendar_event": lambda *args, **kwargs: None,
        },
    )()
    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", fake_connector)

    result = tool._run(
        action="create_calendar_event",
        params={
            "summary": "Wine tasting",
            "start_time": "2026-08-08T18:00:00",
            "end_time": "2026-08-08T19:00:00",
        },
    )

    assert result.startswith("Error: Google authentication failed")
    assert "Reconnect the Google account" in result


def test_calendar_event_can_be_deleted_by_returned_id(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    captured = {}

    def delete_calendar_event(event_id):
        captured["event_id"] = event_id
        return True

    fake_connector = type(
        "Conn",
        (),
        {
            "is_connected": lambda self: True,
            "delete_calendar_event": staticmethod(delete_calendar_event),
        },
    )()
    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", fake_connector)

    result = tool._run(
        action="delete_calendar_event",
        params={"event_id": "calendar-event-123"},
    )

    assert result == "Calendar event deleted successfully (ID: calendar-event-123)"
    assert captured["event_id"] == "calendar-event-123"
