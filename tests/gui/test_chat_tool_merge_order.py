"""Chat API merges tool_events with the DB row (turn) they belong to."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from distr.gui.web.routes.chat import (
    _api_timestamp,
    _append_row_messages,
    _chat_tool_event_messages,
    _merge_thread_rows_with_tool_and_workflow_events,
)


def test_api_timestamp_serializes_naive_utc_with_z_suffix():
    ts = datetime(2026, 6, 14, 17, 22, 26, 988981)
    assert _api_timestamp(ts) == "2026-06-14T17:22:26.988981Z"


def test_append_row_messages_use_api_timestamps():
    row = SimpleNamespace(
        id=3,
        input="Tell me your story.",
        response="Once upon a time...",
        created_date=datetime(2026, 6, 14, 17, 22, 26, tzinfo=timezone.utc),
        modified_date=datetime(2026, 6, 14, 17, 22, 28, tzinfo=timezone.utc),
        is_hidden=False,
        response_marker=None,
    )
    messages = []
    _append_row_messages(messages, row)
    assert messages[0]["timestamp"] == "2026-06-14T17:22:26Z"
    assert messages[1]["timestamp"] == "2026-06-14T17:22:28Z"


def test_tools_follow_assistant_on_same_chat_row():
    rows = [
        SimpleNamespace(
            id=1,
            input=None,
            response=None,
            created_date=None,
            modified_date=None,
        ),
        SimpleNamespace(
            id=2,
            input="Open downloads",
            response="Done.",
            created_date=None,
            modified_date=None,
        ),
    ]
    root = SimpleNamespace()
    root.params = json.dumps(
        {
            "tool_events": [
                {
                    "tool_name": "smart_open",
                    "title": "Opened",
                    "result_summary": "Opened ~/Downloads",
                    "status": "completed",
                    "timestamp": "2026-05-10T17:12:00.000000+00:00",
                    "turn_chat_id": 2,
                    "chat_visible": True,
                    "chat_compact": False,
                }
            ]
        }
    )

    messages = _merge_thread_rows_with_tool_and_workflow_events(rows, root)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "tool"]
    assert messages[0]["chat_row_id"] == 2
    assert messages[1]["chat_row_id"] == 2
    assert messages[2]["role"] == "tool"
    assert messages[2]["turn_chat_id"] == 2


def test_orphan_tools_merge_in_chronological_order():
    from datetime import datetime

    user_ts = datetime(2026, 5, 10, 16, 43)
    assistant_ts = datetime(2026, 5, 10, 16, 45)
    rows = [
        SimpleNamespace(
            id=1,
            input="What did he say?",
            response="Who do you mean...",
            created_date=user_ts,
            modified_date=assistant_ts,
        ),
    ]
    root = SimpleNamespace()
    root.params = json.dumps(
        {
            "tool_events": [
                {
                    "tool_name": "chat_settings",
                    "title": "Voice: old -> new",
                    "result_summary": "Voice: old -> new",
                    "status": "completed",
                    "timestamp": "2026-05-10T16:44:00.000000+00:00",
                    "chat_visible": True,
                    "chat_compact": False,
                },
                {
                    "tool_name": "chat_settings",
                    "title": "Voice: newer -> newest",
                    "result_summary": "Voice: newer -> newest",
                    "status": "completed",
                    "timestamp": "2026-05-10T16:46:00.000000+00:00",
                    "chat_visible": True,
                    "chat_compact": False,
                },
            ]
        }
    )

    messages = _merge_thread_rows_with_tool_and_workflow_events(rows, root)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "tool", "assistant", "tool"]
    assert messages[1]["timestamp"] == "2026-05-10T16:44:00.000000+00:00"
    assert messages[3]["timestamp"] == "2026-05-10T16:46:00.000000+00:00"


def test_orphan_tools_merge_with_naive_db_timestamps():
    """DB rows use naive UTC; tool_events use timezone-aware ISO strings."""
    from datetime import datetime

    user_ts = datetime(2026, 5, 10, 16, 43)
    assistant_ts = datetime(2026, 5, 10, 16, 45)
    rows = [
        SimpleNamespace(
            id=1,
            input="Hello",
            response="Hi there",
            created_date=user_ts,
            modified_date=assistant_ts,
        ),
    ]
    root = SimpleNamespace()
    root.params = json.dumps(
        {
            "tool_events": [
                {
                    "tool_name": "chat_settings",
                    "title": "Voice change",
                    "result_summary": "Voice: old -> new",
                    "status": "completed",
                    "timestamp": "2026-05-10T16:44:00.000000+00:00",
                    "chat_visible": True,
                    "chat_compact": False,
                }
            ]
        }
    )

    messages = _merge_thread_rows_with_tool_and_workflow_events(rows, root)
    assert [m["role"] for m in messages] == ["user", "tool", "assistant"]


def test_chat_tool_event_messages_preserve_activity_style():
    chat = SimpleNamespace(
        params=json.dumps(
            {
                "tool_events": [
                    {
                        "tool_name": "clipboard_action",
                        "title": "Ingested clipboard into context",
                        "result_summary": "CLIPBOARD CONTENT: hello",
                        "status": "completed",
                        "timestamp": "2026-05-10T16:44:00.000000+00:00",
                        "chat_visible": True,
                        "chat_compact": True,
                        "activity_style": "passive",
                    }
                ]
            }
        )
    )

    messages = _chat_tool_event_messages(chat)
    assert len(messages) == 1
    assert messages[0]["tool_event"]["activity_style"] == "passive"
    assert messages[0]["tool_event"]["compact"] is True
