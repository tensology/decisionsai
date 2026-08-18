from __future__ import annotations

from types import SimpleNamespace

import pytest

from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService


@pytest.mark.asyncio
async def test_chained_tool_is_attached_to_active_turn(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "distr.core.agent.tool_audit.record_tool_start",
        lambda chat_id, tool_name, **kwargs: calls.append(("start", chat_id, tool_name)),
    )
    monkeypatch.setattr(
        "distr.core.agent.tool_audit.record_tool_execution",
        lambda chat_id, tool_name, result, status, **kwargs: calls.append(
            ("finish", chat_id, tool_name, result, status)
        ),
    )

    class Harness:
        def __init__(self):
            self._messages = [{"role": "user", "content": "Create the calendar event"}]
            self._tools_dict = {"google_workspace": SimpleNamespace(name="google_workspace")}
            self.chat_manager = SimpleNamespace(get_current_chat=lambda: 41)
            self.event_queue = None
            self._is_telegram_request = False

        def _sanitize_tool_calls(self, tool_calls):
            return tool_calls

        async def _run_tool_with_timeout(self, tool, args, tool_name):
            return "Event created successfully (ID: evt-123)"

    harness = Harness()
    await OpenAICompatibleLLMService._execute_chained_tools(
        harness,
        [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "google_workspace",
                    "arguments": '{"action":"create_calendar_event"}',
                },
            }
        ],
        "",
    )

    assert ("start", 41, "google_workspace") in calls
    assert (
        "finish",
        41,
        "google_workspace",
        "Event created successfully (ID: evt-123)",
        "completed",
    ) in calls
