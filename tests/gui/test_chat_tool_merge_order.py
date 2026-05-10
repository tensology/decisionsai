"""Chat API merges tool_events with the DB row (turn) they belong to."""

import json
from types import SimpleNamespace

from distr.gui.web.routes.chat import _merge_thread_rows_with_tool_and_workflow_events


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
