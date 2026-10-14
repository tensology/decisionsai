from __future__ import annotations

from datetime import datetime

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


def test_connection_status_reports_verified_google_authentication(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    fake_connector = type(
        "Conn",
        (),
        {
            "is_connected": lambda self: True,
            "connection_health": lambda self: (True, ""),
        },
    )()
    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", fake_connector)

    result = tool._run(action="connection_status")

    assert "connected and authentication is healthy" in result


def test_find_and_read_email_uses_targeted_query_and_returns_body(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    captured = {}

    class Connector:
        last_error = None

        def is_connected(self):
            return True

        def check_inbox(self, max_results=10, query="in:inbox"):
            captured.update(max_results=max_results, query=query)
            return [
                {
                    "id": "wedding-message",
                    "from": "Kaneil",
                    "to": "Paul",
                    "subject": "Wedding itinerary",
                    "date": "2026-10-04",
                    "body": "Padel from noon to two.",
                    "attachments": [],
                }
            ]

    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", Connector())

    result = tool._run(
        action="find_and_read_email",
        params={"query": "Neil wedding"},
    )

    assert captured == {"max_results": 5, "query": "in:inbox Neil wedding"}
    assert "Subject: Wedding itinerary" in result
    assert "Body:\nPadel from noon to two." in result


def test_calendar_lookup_includes_event_id_for_safe_replacement(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)

    class Connector:
        last_error = None

        def is_connected(self):
            return True

        def get_calendar_events(self, *_args):
            return [
                {
                    "id": "existing-event-id",
                    "summary": "Wedding placeholder",
                    "start": {"dateTime": "2026-11-26T12:00:00+02:00"},
                    "end": {"dateTime": "2026-11-27T00:00:00+02:00"},
                }
            ]

    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", Connector())

    result = tool._run(
        action="get_calendar_events",
        params={
            "time_min": "2026-11-26T00:00:00+02:00",
            "time_max": "2026-11-29T00:00:00+02:00",
        },
    )

    assert "Event ID: existing-event-id" in result


def test_list_drafts_clamps_zero_max_results(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    captured = {}

    class Connector:
        last_error = None

        def is_connected(self):
            return True

        def list_drafts(self, max_results=10):
            captured["max_results"] = max_results
            return []

    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", Connector())

    result = tool._run(action="list_drafts", params={"max_results": 0})

    assert result == "No drafts found."
    assert captured["max_results"] == 10


def test_list_drafts_surfaces_connector_error(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)

    class Connector:
        last_error = "Gmail rejected the request"

        def is_connected(self):
            return True

        def list_drafts(self, max_results=10):
            return []

    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", Connector())

    result = tool._run(action="list_drafts")

    assert result == "Error: Gmail rejected the request"


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

    assert result == (
        "Event created successfully (ID: evt-1; "
        "start=2026-06-20T13:00:00; end=2026-06-20T14:00:00)"
    )
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


def test_calendar_event_can_be_updated_by_returned_id(monkeypatch) -> None:
    tool = GoogleWorkspaceTool.__new__(GoogleWorkspaceTool)
    captured = {}

    def update_calendar_event(event_id, **kwargs):
        captured.update(event_id=event_id, **kwargs)
        return True

    fake_connector = type(
        "Conn",
        (),
        {
            "is_connected": lambda self: True,
            "update_calendar_event": staticmethod(update_calendar_event),
        },
    )()
    monkeypatch.setattr(tool, "_ensure_initialized", lambda: None)
    object.__setattr__(tool, "connector", fake_connector)

    result = tool._run(
        action="update_calendar_event",
        params={
            "event_id": "calendar-event-123",
            "start_time": "2026-10-07T12:30:00",
            "end_time": "2026-10-07T14:30:00",
            "time_zone": "Africa/Johannesburg",
        },
    )

    assert result == "Calendar event updated successfully (ID: calendar-event-123)"
    assert captured["event_id"] == "calendar-event-123"
    assert captured["start_time"] == datetime(2026, 10, 7, 12, 30)
