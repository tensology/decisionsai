"""Cursor CLI stream-json maps into live Development turn events."""

from distr.core.project_cli_backends.registry import CursorBackend


def test_cursor_backend_uses_account_stream_json():
    backend = CursorBackend()
    assert backend.structured_jsonl is True
    assert "--output-format" in backend.command_args
    assert "stream-json" in backend.command_args


def test_cursor_stream_json_maps_assistant_and_thinking():
    backend = CursorBackend()
    sample = "\n".join(
        [
            '{"type":"system","subtype":"init","apiKeySource":"login"}',
            '{"type":"thinking","subtype":"delta","text":"Inspecting repo"}',
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Merrypak packaging"}]}}',
            '{"type":"result","subtype":"success","result":"Merrypak packaging","is_error":false}',
        ]
    ) + "\n"
    events, remainder = backend._protocol_events(sample, "")
    assert remainder == ""
    deltas = [
        event.get("assistantMessageEvent", {}).get("delta")
        for event in events
        if event.get("type") == "message_update"
    ]
    assert "Merrypak packaging" in deltas
    assert any(event.get("type") == "status" for event in events)
    assert backend._result_output(sample) == "Merrypak packaging"
