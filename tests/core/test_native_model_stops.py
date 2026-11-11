"""Regression cases for the native model stop and retry boundary."""

import asyncio
from types import SimpleNamespace

import pytest

from distr.core.turn_runtime import (
    ModelResponse,
    ModelRoute,
    ModelToolCall,
    NativeTurnRuntime,
    OpenAICompatibleModelAdapter,
    RetryingModelAdapter,
    ToolObservation,
    TurnEventKind,
    TurnRequest,
    TurnScope,
)
from distr.core.turn_runtime import service


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    async def complete(self, messages, tools, *, on_delta=None):
        self.requests.append(list(messages))
        response = next(self.responses)
        if callable(response):
            return response(on_delta)
        return response


class RecordingTools:
    def __init__(self):
        self.calls = []

    def definitions(self):
        return []

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return ToolObservation(status="success", summary="Tool ran.")


def request_for(*, change_expected=False):
    return TurnRequest(
        instruction="Current request",
        project=SimpleNamespace(id=7),
        route=ModelRoute(provider="anthropic", model="test-model", adapter_options={
            "fallback_backend": "pi",
            "fallback_model": "fallback-model",
            "fallback_model_provider": "ollama",
        }),
        scope=TurnScope(chat_id=11, project_id=7),
        context_messages=(
            {"role": "system", "content": "Work within the current request."},
            {"role": "user", "content": "Earlier request"},
            {"role": "assistant", "content": "Earlier response"},
            {"role": "user", "content": "Current request"},
        ),
        metadata={"change_expected": change_expected},
    )


def execute(model, tools, *, change_expected=False):
    events = []
    result = asyncio.run(NativeTurnRuntime(model=model, tools=tools).execute(
        request_for(change_expected=change_expected), on_event=events.append,
    ))
    return result, events


def test_normal_stop_completes():
    model = ScriptedModel(ModelResponse(text="Done.", finish_reason="end_turn"))
    result, events = execute(model, RecordingTools())
    assert result.success
    assert result.output == "Done."
    assert events[-1].kind == TurnEventKind.COMPLETED


@pytest.mark.parametrize("reason", ["length", "max_tokens", "stream_incomplete"])
@pytest.mark.parametrize("with_tool", [False, True])
def test_incomplete_response_cannot_finish_or_execute_partial_tool(reason, with_tool):
    calls = (ModelToolCall("edit", "write_file", {"path": "app.py"}),) if with_tool else ()
    tools = RecordingTools()
    model = ScriptedModel(
        ModelResponse(text="Partial answer", finish_reason=reason, tool_calls=calls),
        ModelResponse(text="Done."),
    )
    result, events = execute(model, tools)
    assert (result.success, len(model.requests), tools.calls) == (False, 1, [])
    assert events[-1].kind == TurnEventKind.FAILED


def test_empty_stop_cannot_finish():
    result, _ = execute(ScriptedModel(ModelResponse()), RecordingTools())
    assert not result.success


@pytest.mark.parametrize("reason", ["refusal", "content_filter"])
def test_refusal_never_reprompts_or_falls_back(monkeypatch, reason):
    model = ScriptedModel(
        ModelResponse(text="Partial output: rate limit", finish_reason=reason),
        ModelResponse(text="Can I edit?"),
        ModelResponse(text="Can I edit?"),
    )
    tools = RecordingTools()
    runtime = NativeTurnRuntime(model=model, tools=tools)
    runtime_selections = []

    def select_runtime(runtime_id, *, request):
        runtime_selections.append(runtime_id)
        return runtime

    monkeypatch.setattr(service, "select_turn_runtime_id", lambda *args, **kwargs: "native")
    monkeypatch.setattr(service, "get_turn_runtime", select_runtime)
    monkeypatch.setattr(service, "_create_native_execution_session", lambda request: None)
    result = asyncio.run(service.execute_turn(request_for(change_expected=True)))
    assert (result.success, result.output, len(model.requests), runtime_selections, tools.calls) == (
        False, "", 1, ["native"], [],
    )


def test_context_stop_compacts_and_retries_once():
    model = ScriptedModel(
        ModelResponse(finish_reason="model_context_window_exceeded"),
        ModelResponse(text="Recovered."),
    )
    result, events = execute(model, RecordingTools())
    assert (result.success, result.output, len(model.requests)) == (True, "Recovered.", 2)
    assert model.requests[1][-1] == model.requests[0][-1]
    assert any(event.kind == TurnEventKind.CONTEXT_COMPACTED for event in events)


def test_context_stop_after_write_does_not_restart_inference_with_trimmed_history():
    write = ModelResponse(tool_calls=(ModelToolCall("edit", "write_file", {"path": "app.py"}),))
    tools = RecordingTools()
    model = ScriptedModel(write, ModelResponse(finish_reason="model_context_window_exceeded"), write)
    result, events = execute(model, tools, change_expected=True)
    assert (result.success, len(model.requests), len(tools.calls)) == (False, 2, 1)
    assert events[-1].kind == TurnEventKind.FAILED


def test_transient_provider_retry_after_write_keeps_tool_result_and_does_not_replay_write():
    def timeout(_on_delta):
        raise TimeoutError("Provider timeout")

    tools = RecordingTools()
    adapter = ScriptedModel(
        ModelResponse(tool_calls=(ModelToolCall("edit", "write_file", {"path": "app.py"}),)),
        timeout,
        ModelResponse(text="Done."),
    )
    result, _ = execute(RetryingModelAdapter(adapter), tools, change_expected=True)
    assert result.success
    assert len(tools.calls) == 1
    assert len(adapter.requests) == 3
    assert adapter.requests[1] == adapter.requests[2]
    assert adapter.requests[2][-1]["tool_call_id"] == "edit"


@pytest.mark.parametrize("error", ["Provider timeout", "Maximum context length exceeded"])
def test_stream_failure_after_output_does_not_restart_via_recovery_or_fallback(monkeypatch, error):
    def interrupted(on_delta):
        on_delta("Partial answer")
        raise RuntimeError(error)

    model = ScriptedModel(interrupted, ModelResponse(text="Duplicate answer."))
    runtime = NativeTurnRuntime(model=model, tools=RecordingTools())
    runtime_selections = []

    def select_runtime(runtime_id, *, request):
        runtime_selections.append(runtime_id)
        return runtime

    monkeypatch.setattr(service, "select_turn_runtime_id", lambda *args, **kwargs: "native")
    monkeypatch.setattr(service, "get_turn_runtime", select_runtime)
    monkeypatch.setattr(service, "_create_native_execution_session", lambda request: None)
    with pytest.raises(RuntimeError, match=error):
        asyncio.run(service.execute_turn(request_for()))
    assert (len(model.requests), runtime_selections) == (1, ["native"])


@pytest.mark.parametrize(("delta", "finish_reason", "expected"), [
    (SimpleNamespace(content="Partial"), None, "stream_incomplete"),
    (SimpleNamespace(refusal="Declined"), "stop", "refusal"),
])
def test_openai_compatible_adapter_reports_abnormal_stream_stops(delta, finish_reason, expected):
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    chunk = SimpleNamespace(choices=[choice])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: iter([chunk]),
    )))
    adapter = OpenAICompatibleModelAdapter(
        provider="ollama", model="test-model", settings={}, client=client,
    )
    result = asyncio.run(adapter.complete([], []))
    assert result.finish_reason == expected
