"""Cost instrumentation: CLI token usage must reach the cost ledger, not be guessed.

The workflow power budget and cost ledger previously received ``tokens_in=None``
because ``BackendTaskResult`` dropped the usage that backends already parsed.
These tests pin the plumbing so "how much did this ticket cost" stays answerable.
"""

from __future__ import annotations

import json

from distr.core.project_cli_backends.base import BackendTaskResult


def _cursor_stream(*, input_tokens: int = 1500, output_tokens: int = 320) -> str:
    events = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}},
        {"type": "tool_call", "name": "read_file", "subtype": "started"},
        {
            "type": "result",
            "subtype": "success",
            "result": "done",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": 100,
                "cache_write_tokens": 20,
            },
        },
    ]
    return "\n".join(json.dumps(event) for event in events)


def test_cursor_backend_reports_provider_usage():
    from distr.core.project_cli_backends.registry import CursorBackend

    usage = CursorBackend()._usage_from_output(_cursor_stream())

    assert usage["input"] == 1500
    assert usage["output"] == 320
    assert usage["totalTokens"] == 1820


def test_cursor_backend_returns_no_usage_rather_than_zero():
    from distr.core.project_cli_backends.registry import CursorBackend

    # No result/usage event means "unknown", not "free".
    assert CursorBackend()._usage_from_output("just some raw text") == {}


def test_usage_totals_split_into_in_and_out():
    totals = BackendTaskResult.usage_token_totals({"input": 1500, "output": 320, "totalTokens": 1820})
    assert totals == (1500, 320)


def test_usage_totals_are_none_without_provider_data():
    assert BackendTaskResult.usage_token_totals({}) == (None, None)
    assert BackendTaskResult.usage_token_totals(None) == (None, None)


def test_backend_task_result_carries_usage_by_default_empty():
    result = BackendTaskResult(True, "cursor", "cursor", output="ok")

    assert result.tokens_in is None
    assert result.tokens_out is None
    assert result.usage == {}
    # And the result survives serialization for the execution transcript.
    assert result.to_dict()["tokens_in"] is None
