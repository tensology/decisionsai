from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

from distr.core.turn_runtime import (
    CancellationToken,
    ModelResponse,
    ModelRoute,
    ModelToolCall,
    NativeTurnRuntime,
    SteeringChannel,
    ToolObservation,
    TurnCancelled,
    TurnEvent,
    TurnEventKind,
    TurnRequest,
    TurnScope,
    TurnStatus,
)
from distr.core.turn_runtime.cli_harness import CliHarnessTurnRuntime
from distr.core.turn_runtime.context import build_development_context
from distr.core.turn_runtime.project_tools import _bounded_output
from distr.core.turn_runtime import service as turn_service


def _request() -> TurnRequest:
    project = SimpleNamespace(id=7, folder_location="/tmp/project")
    return TurnRequest(
        instruction="Implement the current Development thread.",
        project=project,
        route=ModelRoute(provider="kilocode", model="free-model", backend_id="pi"),
        scope=TurnScope(chat_id=11, project_id=7, board_id=13, ticket_id=17, turn_id=19),
    )


def test_command_output_bounding_preserves_head_tail_and_limit():
    source = "A" * 900 + "middle" + "Z" * 900

    output, truncated = _bounded_output(source, 300)

    assert truncated is True
    assert len(output) == 300
    assert output.startswith("A")
    assert output.endswith("Z")
    assert "characters omitted" in output


def test_command_output_bounding_leaves_small_output_unchanged():
    assert _bounded_output("small", 300) == ("small", False)


def test_cli_harness_runtime_preserves_direct_turn_identity(monkeypatch):
    captured = {}
    events = []

    async def fake_dispatch(context):
        captured["context"] = context
        context.on_event(
            {
                "type": "message_update",
                "summary": "Inspecting the project.",
                "execution_session_id": 23,
            }
        )
        return SimpleNamespace(
            execution_session_id=23,
            evidence={"files": ["app.py"]},
            result=SimpleNamespace(
                success=True,
                output="Implemented.",
                error="",
                waits_for_human=False,
            ),
        )

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", fake_dispatch)
    result = asyncio.run(CliHarnessTurnRuntime().execute(_request(), on_event=events.append))

    context = captured["context"]
    assert context.origin == "development"
    assert context.workflow_id is None
    assert context.run_id is None
    assert context.step_id is None
    assert context.backend_id == "pi"
    assert context.adapter_options["model_provider"] == "kilocode"
    assert result.runtime_id == "cli_harness"
    assert result.output == "Implemented."
    assert result.execution_session_id == 23
    assert events[0].kind == TurnEventKind.STATUS
    assert events[0].status == TurnStatus.WORKING
    assert events[0].summary == "Inspecting the project."


def test_cli_harness_runtime_honours_cancellation_before_dispatch(monkeypatch):
    async def should_not_dispatch(_context):
        raise AssertionError("cancelled turn reached the CLI harness")

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", should_not_dispatch)
    cancellation = CancellationToken()
    cancellation.cancel()

    try:
        asyncio.run(CliHarnessTurnRuntime().execute(_request(), cancellation=cancellation))
    except TurnCancelled:
        pass
    else:
        raise AssertionError("cancelled turn did not stop")


def test_cli_harness_projects_codex_items_into_typed_turn_events(monkeypatch):
    events = []

    async def fake_dispatch(context):
        context.on_event({"type": "agent_start", "backend": "codex"})
        context.on_event(
            {
                "type": "item.started",
                "item": {"id": "cmd-1", "type": "command_execution", "command": "pytest -q"},
            }
        )
        context.on_event(
            {
                "type": "item.completed",
                "item": {
                    "id": "edit-1",
                    "type": "file_change",
                    "status": "completed",
                    "changes": [{"path": "src/app.js", "diff": "--- a/src/app.js\n+++ b/src/app.js\n-old\n+new\n+extra"}],
                },
            }
        )
        context.on_event(
            {
                "type": "item.completed",
                "item": {
                    "id": "cmd-1",
                    "type": "command_execution",
                    "command": "pytest -q",
                    "status": "completed",
                    "aggregated_output": "4 passed",
                },
            }
        )
        context.on_event({"type": "agent_end", "backend": "codex"})
        return SimpleNamespace(
            execution_session_id=23,
            evidence={},
            result=SimpleNamespace(success=True, output="Implemented.", error="", waits_for_human=False),
        )

    monkeypatch.setattr("distr.core.project_cli_backends.harness.dispatch_harness", fake_dispatch)
    asyncio.run(CliHarnessTurnRuntime().execute(_request(), on_event=events.append))

    assert [event.kind for event in events] == [
        TurnEventKind.TOOL_STARTED,
        TurnEventKind.TOOL_STARTED,
        TurnEventKind.TOOL_COMPLETED,
        TurnEventKind.TOOL_COMPLETED,
        TurnEventKind.TOOL_COMPLETED,
    ]
    assert events[1].tool_name == "run_command"
    assert events[1].details["command"] == "pytest -q"
    assert events[2].tool_name == "apply_patch"
    assert events[2].details["files"] == [{"path": "src/app.js", "additions": 2, "deletions": 1}]
    assert events[3].summary == "4 passed"


def test_tool_observation_contract_is_deterministic():
    observation = ToolObservation(
        status="success",
        summary="Changed one file.",
        next_actions=("run tests",),
        artifacts=("app.py",),
        output={"changed": 1},
    )

    assert observation.status == "success"
    assert observation.summary == "Changed one file."
    assert observation.next_actions == ("run tests",)
    assert observation.artifacts == ("app.py",)


def test_native_runtime_owns_model_tool_loop_and_emits_activity():
    class FakeModel:
        adapter_id = "fake"

        def __init__(self):
            self.calls = 0
            self.messages = []

        async def complete(self, messages, tools, *, on_delta=None):
            self.calls += 1
            self.messages.append(list(messages))
            assert tools[0]["function"]["name"] == "read_file"
            if self.calls == 1:
                return ModelResponse(
                    tool_calls=(
                        ModelToolCall(call_id="call-1", name="read_file", arguments={"path": "app.py"}),
                    )
                )
            if on_delta:
                on_delta("Implemented")
                on_delta(" and verified.")
            return ModelResponse(text="Implemented and verified.", usage={"input_tokens": 10})

    class FakeTools:
        def definitions(self):
            return [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]

        async def execute(self, name, arguments):
            assert name == "read_file"
            assert arguments == {"path": "app.py"}
            return ToolObservation(
                status="success",
                summary="Read app.py.",
                artifacts=("app.py",),
                output="print('hello')",
            )

    events = []
    model = FakeModel()
    result = asyncio.run(NativeTurnRuntime(model=model, tools=FakeTools()).execute(_request(), on_event=events.append))

    assert result.success is True
    assert result.runtime_id == "native"
    assert result.output == "Implemented and verified."
    assert model.calls == 2
    assert model.messages[1][-1]["role"] == "tool"
    assert '"artifacts": ["app.py"]' in model.messages[1][-1]["content"]
    assert [event.kind for event in events] == [
        TurnEventKind.STATUS,
        TurnEventKind.TOOL_STARTED,
        TurnEventKind.TOOL_COMPLETED,
        TurnEventKind.OUTPUT_DELTA,
        TurnEventKind.OUTPUT_DELTA,
        TurnEventKind.COMPLETED,
    ]
    assert events[0].status == TurnStatus.THINKING
    assert events[1].summary == "Running read_file."
    assert events[2].summary == "Read app.py."


def test_native_runtime_includes_command_in_tool_activity():
    class CommandModel:
        adapter_id = "command"

        def __init__(self):
            self.calls = 0

        async def complete(self, _messages, _tools, *, on_delta=None):
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    tool_calls=(ModelToolCall(call_id="cmd-1", name="run_command", arguments={"command": "python -m pytest -q"}),)
                )
            return ModelResponse(text="Tests passed.")

    class CommandTools:
        def definitions(self):
            return [{"type": "function", "function": {"name": "run_command", "parameters": {"type": "object"}}}]

        async def execute(self, name, arguments):
            assert name == "run_command"
            assert arguments["command"] == "python -m pytest -q"
            return ToolObservation(status="success", summary="Command completed.")

    events = []
    result = asyncio.run(NativeTurnRuntime(model=CommandModel(), tools=CommandTools()).execute(_request(), on_event=events.append))

    assert result.success is True
    started = next(event for event in events if event.kind == TurnEventKind.TOOL_STARTED)
    completed = next(event for event in events if event.kind == TurnEventKind.TOOL_COMPLETED)
    assert started.details["command"] == "python -m pytest -q"
    assert completed.details["command"] == "python -m pytest -q"


def test_native_runtime_does_not_accept_an_offer_as_a_completed_code_change():
    class InitiallyPassiveModel:
        adapter_id = "passive"

        def __init__(self):
            self.calls = 0
            self.messages = []

        async def complete(self, messages, _tools, *, on_delta=None):
            self.calls += 1
            self.messages.append(list(messages))
            if self.calls == 1:
                return ModelResponse(text="I can add a footer. Which footer would you like?")
            if self.calls == 2:
                return ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            call_id="edit-footer",
                            name="replace_text",
                            arguments={"path": "src/App.jsx", "old_text": "</main>", "new_text": "</main><footer>Menu Project</footer>"},
                        ),
                    )
                )
            return ModelResponse(text="Added and verified the footer.")

    class EditingTools:
        def definitions(self):
            return [{"type": "function", "function": {"name": "replace_text", "parameters": {"type": "object"}}}]

        async def execute(self, name, arguments):
            assert name == "replace_text"
            assert arguments["path"] == "src/App.jsx"
            return ToolObservation(status="success", summary="Updated src/App.jsx.", artifacts=("src/App.jsx",))

    model = InitiallyPassiveModel()
    request = replace(_request(), metadata={"change_expected": True})
    result = asyncio.run(NativeTurnRuntime(model=model, tools=EditingTools()).execute(request))

    assert result.success is True
    assert result.output == "Added and verified the footer."
    assert model.calls == 3
    assert "already authorized" in model.messages[1][-1]["content"]


def test_native_runtime_fails_when_change_model_repeatedly_refuses_to_edit():
    class RefusingModel:
        adapter_id = "refusing"

        async def complete(self, _messages, _tools, *, on_delta=None):
            return ModelResponse(text="I can do that if you confirm.")

    class NoTools:
        def definitions(self):
            return []

        async def execute(self, _name, _arguments):
            raise AssertionError("refusing model unexpectedly used a tool")

    request = replace(_request(), metadata={"change_expected": True})
    result = asyncio.run(NativeTurnRuntime(model=RefusingModel(), tools=NoTools()).execute(request))

    assert result.success is False
    assert result.error == "The model stopped without making the requested project change."
    assert result.evidence["changed_files"] is False


def test_native_runtime_stops_at_iteration_limit():
    class LoopingModel:
        adapter_id = "loop"

        async def complete(self, _messages, _tools, *, on_delta=None):
            return ModelResponse(tool_calls=(ModelToolCall(call_id="again", name="noop"),))

    class NoopTools:
        def definitions(self):
            return []

        async def execute(self, _name, _arguments):
            return ToolObservation(status="success", summary="No operation.")

    result = asyncio.run(
        NativeTurnRuntime(model=LoopingModel(), tools=NoopTools(), max_iterations=2).execute(_request())
    )

    assert result.success is False
    assert result.runtime_id == "native"
    assert "2-iteration safety limit" in result.error


def test_native_runtime_stops_repeated_failed_tool_calls_before_iteration_limit():
    class FailingInspectionModel:
        adapter_id = "failing-inspection"

        def __init__(self):
            self.calls = 0

        async def complete(self, _messages, _tools, *, on_delta=None):
            self.calls += 1
            return ModelResponse(
                tool_calls=(ModelToolCall(call_id=f"inspect-{self.calls}", name="read_file", arguments={"path": f"missing-{self.calls}.py"}),)
            )

    class FailingInspectionTools:
        def definitions(self):
            return [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]

        async def execute(self, _name, _arguments):
            return ToolObservation(status="error", summary="read_file failed.", error="missing file")

    model = FailingInspectionModel()
    result = asyncio.run(NativeTurnRuntime(model=model, tools=FailingInspectionTools()).execute(_request()))

    assert result.success is False
    assert "repeated tool failures" in result.error
    assert result.evidence["failed_tool_calls"] == 6
    assert model.calls == 6


def test_native_runtime_compacts_before_the_provider_limit(monkeypatch):
    monkeypatch.setattr("distr.core.turn_runtime.native.context_window_for_model", lambda *_args: 4_096)

    class RecordingModel:
        adapter_id = "recording"

        def __init__(self):
            self.messages = []

        async def complete(self, messages, _tools, *, on_delta=None):
            self.messages = list(messages)
            return ModelResponse(text="Completed after compaction.")

    class NoTools:
        def definitions(self):
            return []

        async def execute(self, _name, _arguments):
            raise AssertionError("No tool should be called")

    current = {"role": "user", "content": "Keep the active request."}
    request = replace(
        _request(),
        context_messages=(
            {"role": "system", "content": "Work in the project."},
            {"role": "user", "content": "old request " * 2_000},
            {"role": "assistant", "content": "old result " * 2_000},
            current,
        ),
    )
    events = []
    model = RecordingModel()

    result = asyncio.run(NativeTurnRuntime(model=model, tools=NoTools()).execute(request, on_event=events.append))

    assert result.success is True
    assert any(event.kind == TurnEventKind.CONTEXT_COMPACTED for event in events)
    assert any("Keep the active request" in str(message.get("content")) for message in model.messages)
    assert any("Development context checkpoint" in str(message.get("content")) for message in model.messages)
    assert result.evidence["context_compactions"] == 1


def test_native_runtime_compacts_and_retries_one_provider_context_overflow():
    class OverflowOnceModel:
        adapter_id = "overflow-once"

        def __init__(self):
            self.calls = 0
            self.messages = []

        async def complete(self, messages, _tools, *, on_delta=None):
            self.calls += 1
            self.messages.append(list(messages))
            if self.calls == 1:
                raise RuntimeError("input length exceeds the model's maximum context length")
            return ModelResponse(text="Recovered after compaction.")

    class NoTools:
        def definitions(self):
            return []

        async def execute(self, _name, _arguments):
            raise AssertionError("No tool should be called")

    request = replace(
        _request(),
        context_messages=(
            {"role": "system", "content": "Work in the project."},
            {"role": "user", "content": "Earlier request"},
            {"role": "assistant", "content": "Earlier response"},
            {"role": "user", "content": "Current request"},
        ),
    )
    events = []
    model = OverflowOnceModel()

    result = asyncio.run(NativeTurnRuntime(model=model, tools=NoTools()).execute(request, on_event=events.append))

    assert result.success is True
    assert model.calls == 2
    assert len(model.messages[1]) < len(model.messages[0])
    compacted = [event for event in events if event.kind == TurnEventKind.CONTEXT_COMPACTED]
    assert len(compacted) == 1
    assert compacted[0].details["provider_retry"] is True


def test_native_context_compaction_keeps_tool_call_and_result_together():
    assistant_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "name": "read_file", "arguments": {"path": "app.py"}}],
    }
    tool_result = {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "read_file",
        "content": '{"status":"success","summary":"Read app.py","output":"large"}',
    }
    current = {"role": "user", "content": "Current request"}
    compacted, _ = NativeTurnRuntime._compact_messages(
        [
            {"role": "system", "content": "Work in the project."},
            {"role": "user", "content": "Old request"},
            {"role": "assistant", "content": "Old response"},
            current,
            assistant_call,
            tool_result,
        ],
        [],
        protected=[current],
        target_tokens=4_096,
        force=True,
    )

    assert (assistant_call in compacted) is (tool_result in compacted)


def test_native_runtime_compacts_tool_output_before_the_next_model_call(monkeypatch):
    monkeypatch.setattr("distr.core.turn_runtime.native.context_window_for_model", lambda *_args: 4_096)

    class ToolLoopModel:
        adapter_id = "tool-loop"

        def __init__(self):
            self.calls = 0
            self.messages = []

        async def complete(self, messages, _tools, *, on_delta=None):
            self.calls += 1
            self.messages.append(list(messages))
            if self.calls == 1:
                return ModelResponse(
                    tool_calls=(ModelToolCall(call_id="large-read", name="read_file", arguments={"path": "large.txt"}),)
                )
            return ModelResponse(text="Finished with the compacted evidence.")

    class LargeReadTools:
        def definitions(self):
            return [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]

        async def execute(self, _name, _arguments):
            return ToolObservation(status="success", summary="Read large.txt.", output="x" * 30_000)

    events = []
    model = ToolLoopModel()
    result = asyncio.run(NativeTurnRuntime(model=model, tools=LargeReadTools()).execute(_request(), on_event=events.append))

    assert result.success is True
    assert model.calls == 2
    assert any(event.kind == TurnEventKind.CONTEXT_COMPACTED for event in events)
    assert "x" * 1_000 not in str(model.messages[1])
    assert "Read large.txt" in str(model.messages[1])


def test_native_runtime_applies_mid_turn_guidance_at_a_safe_model_boundary():
    steering = SteeringChannel()
    steering.put("Keep the public API unchanged.", event_id="cte-steer")

    class GuidedModel:
        adapter_id = "guided"

        def __init__(self):
            self.messages = []

        async def complete(self, messages, _tools, *, on_delta=None):
            self.messages = list(messages)
            return ModelResponse(text="Updated without changing the public API.")

    class NoTools:
        def definitions(self):
            return []

        async def execute(self, _name, _arguments):
            raise AssertionError("No tool should be called")

    events = []
    model = GuidedModel()
    result = asyncio.run(
        NativeTurnRuntime(model=model, tools=NoTools()).execute(
            _request(),
            on_event=events.append,
            steering=steering,
        )
    )

    assert result.success is True
    assert model.messages[-1]["role"] == "user"
    assert "Keep the public API unchanged." in model.messages[-1]["content"]
    assert any(event.kind == TurnEventKind.STEERED for event in events)


def test_native_construction_failure_falls_back_to_direct_cli_runtime(monkeypatch):
    calls = []

    class FakeCliRuntime:
        runtime_id = "cli_harness"

        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            calls.append((request.scope.chat_id, steering is not None))
            return turn_service.TurnResult(
                success=True,
                runtime_id="cli_harness",
                backend_id=request.route.backend_id,
                model=request.route.model,
                output="CLI fallback completed.",
            )

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "native")

    def fake_runtime(runtime_id, *, request=None):
        if runtime_id == "native":
            raise ValueError("provider key is unavailable")
        return FakeCliRuntime()

    monkeypatch.setattr(turn_service, "get_turn_runtime", fake_runtime)

    result = asyncio.run(turn_service.execute_turn(_request()))

    assert result.runtime_id == "cli_harness"
    assert result.output == "CLI fallback completed."
    assert calls == [(11, True)]
    assert turn_service.active_turn_registered(11) is False


def test_service_delivers_guidance_to_registered_native_turn(monkeypatch):
    captured = {}
    applied = []

    class FakeNativeRuntime:
        runtime_id = "native"

        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            captured["delivery"] = turn_service.steer_active_turn(
                request.scope.chat_id,
                "Keep the existing architecture.",
            )
            captured["guidance"] = steering.drain()[0].content
            return turn_service.TurnResult(
                success=True,
                runtime_id="native",
                backend_id="native",
                model=request.route.model,
                output="Guidance applied.",
            )

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "native")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: FakeNativeRuntime())
    monkeypatch.setattr(
        "distr.core.chat_turns.steer_turn",
        lambda chat_id, turn_id, guidance: {"event_id": "cte-native-steer"},
    )
    monkeypatch.setattr(
        "distr.core.chat_turns.mark_steering_applied",
        lambda event_ids: applied.extend(event_ids),
    )

    result = asyncio.run(turn_service.execute_turn(_request()))

    assert result.success is True
    assert captured["delivery"]["method"] == "native_turn_steering"
    assert captured["guidance"] == "Keep the existing architecture."
    assert applied == ["cte-native-steer"]


def test_native_service_persists_direct_execution_identity_and_events(monkeypatch):
    persisted_events = []
    completions = []
    relayed = []

    class PersistentNativeRuntime:
        runtime_id = "native"

        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            on_event(
                TurnEvent(
                    kind=TurnEventKind.STATUS,
                    status=TurnStatus.WORKING,
                    summary="Inspecting the project.",
                    runtime_id="native",
                )
            )
            return turn_service.TurnResult(
                success=True,
                runtime_id="native",
                backend_id="native",
                model=request.route.model,
                output="Implemented.",
                evidence={"verified": True},
            )

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "native")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: PersistentNativeRuntime())
    monkeypatch.setattr(turn_service, "_create_native_execution_session", lambda request: 55)
    monkeypatch.setattr(turn_service, "_record_native_certification", lambda request, result, session_id: None)
    monkeypatch.setattr(
        "distr.core.kanban.project_execution.append_execution_event",
        lambda session_id, event_type, **kwargs: persisted_events.append((session_id, event_type, kwargs)),
    )
    monkeypatch.setattr(
        "distr.core.kanban.project_execution.complete_execution_session",
        lambda session_id, **kwargs: completions.append((session_id, kwargs)),
    )
    request = replace(_request(), metadata={"source": "development"})

    result = asyncio.run(turn_service.execute_turn(request, on_event=relayed.append))

    assert result.execution_session_id == 55
    assert relayed[0].execution_session_id == 55
    assert persisted_events[0][0:2] == (55, "status")
    assert persisted_events[0][2]["update_session_status"] is False
    assert completions[0][0] == 55
    assert completions[0][1]["success"] is True
    assert completions[0][1]["output_packet"]["output"] == "Implemented."


def test_native_provider_failure_before_mutation_uses_cli_fallback(monkeypatch):
    events = []

    class ProviderFailure(RuntimeError):
        pass

    class FailedNative:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            raise ProviderFailure("HTTP 429 rate limit")

    class CliFallback:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            return turn_service.TurnResult(
                success=True,
                runtime_id="cli_harness",
                backend_id=request.route.backend_id,
                model=request.route.model,
                output="Fallback completed.",
            )

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "native")
    monkeypatch.setattr(
        turn_service,
        "get_turn_runtime",
        lambda runtime_id, request=None: FailedNative() if runtime_id == "native" else CliFallback(),
    )

    result = asyncio.run(turn_service.execute_turn(_request(), on_event=events.append))

    assert result.runtime_id == "cli_harness"
    assert result.output == "Fallback completed."
    assert events[-1].runtime_id == "cli_harness"
    assert events[-1].execution_session_id == 0
    assert "CLI fallback" in events[-1].summary


def test_native_provider_failure_after_mutation_does_not_repeat_work_in_cli(monkeypatch):
    fallback_calls = []

    class FailedAfterMutation:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            on_event(
                TurnEvent(
                    kind=TurnEventKind.TOOL_STARTED,
                    status=TurnStatus.WORKING,
                    summary="Running write_file.",
                    runtime_id="native",
                    tool_name="write_file",
                )
            )
            raise RuntimeError("HTTP 429 rate limit")

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "native")

    def runtime(runtime_id, request=None):
        if runtime_id == "native":
            return FailedAfterMutation()
        fallback_calls.append(runtime_id)
        raise AssertionError("mutated turn reached CLI fallback")

    monkeypatch.setattr(turn_service, "get_turn_runtime", runtime)

    try:
        asyncio.run(turn_service.execute_turn(_request()))
    except RuntimeError as exc:
        assert "429" in str(exc)
    else:
        raise AssertionError("provider failure was hidden")

    assert fallback_calls == []


def test_auto_route_uses_configured_provider_fallback_on_credit_failure(monkeypatch):
    calls = []
    events = []

    class RoutedRuntime:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            calls.append((request.route.backend_id, request.route.provider, request.route.model))
            if request.route.backend_id == "claude_code":
                return turn_service.TurnResult(
                    success=False,
                    runtime_id="cli_harness",
                    backend_id="claude_code",
                    model="opus",
                    error="Credit balance is too low",
                )
            return turn_service.TurnResult(
                success=True,
                runtime_id="cli_harness",
                backend_id=request.route.backend_id,
                model=request.route.model,
                output="Local fallback completed.",
            )

    request = replace(
        _request(),
        route=ModelRoute(
            provider="anthropic",
            model="opus",
            backend_id="claude_code",
            adapter_options={
                "fallback_backend": "pi",
                "fallback_model": "muse-glimmer:30b-mlx",
                "fallback_model_provider": "ollama",
            },
        ),
    )
    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "cli_harness")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: RoutedRuntime())

    result = asyncio.run(turn_service.execute_turn(request, on_event=events.append))

    assert result.success is True
    assert result.output == "Local fallback completed."
    assert result.evidence["auto_fallback"]["from"]["provider"] == "anthropic"
    assert result.evidence["auto_fallback"]["to"] == {
        "backend": "pi",
        "provider": "ollama",
        "model": "muse-glimmer:30b-mlx",
    }
    assert calls == [
        ("claude_code", "anthropic", "opus"),
        ("pi", "ollama", "muse-glimmer:30b-mlx"),
    ]
    assert "Auto is continuing with ollama" in events[-1].summary


def test_manual_route_without_fallback_returns_provider_credit_failure(monkeypatch):
    class FailedRuntime:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            return turn_service.TurnResult(
                success=False,
                runtime_id="cli_harness",
                backend_id=request.route.backend_id,
                model=request.route.model,
                error="Credit balance is too low",
            )

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "cli_harness")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: FailedRuntime())

    result = asyncio.run(turn_service.execute_turn(_request()))

    assert result.success is False
    assert result.error == "Credit balance is too low"


def test_service_cancellation_reaches_native_tool_runtime(monkeypatch):
    state = {"runtime_cancelled": False}

    class CancellableNative:
        def cancel_active(self):
            state["runtime_cancelled"] = True

        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            assert turn_service.cancel_active_turn(request.scope.chat_id) is True
            cancellation.raise_if_cancelled()
            raise AssertionError("cancelled native turn continued")

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "native")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: CancellableNative())

    try:
        asyncio.run(turn_service.execute_turn(_request()))
    except TurnCancelled:
        pass
    else:
        raise AssertionError("native cancellation was hidden")

    assert state["runtime_cancelled"] is True


def test_native_context_is_structured_checkpoint_aware_and_not_a_workflow(monkeypatch):
    monkeypatch.setattr(
        "distr.core.turn_runtime.context.ChatService.get_chat_history",
        lambda _chat_id: [
            {"role": "system", "content": "Chat compact checkpoint. Prior implementation is verified."},
            {"role": "user", "content": "Earlier request"},
            {"role": "assistant", "content": "Earlier result"},
            {
                "role": "assistant",
                "content": "Error code: 400 - input length exceeds the model's maximum context length",
            },
            {"role": "user", "content": "Current request"},
        ],
    )
    messages = build_development_context(
        7,
        "Current request",
        {
            "board_key": "local:3",
            "ticket_id": 9,
            "turn_skill_ids": ["humanizer"],
            "active_runtime_identity": {
                "agent": "Decisions Development agent",
                "provider": "kilocode",
                "model": "kilo-auto/free",
                "turn_runtime": "native",
                "routing_backend": "pi",
                "route_mode": "manual",
            },
        },
        project_folder="/tmp/project",
        autonomy_level="full",
    )

    assert messages[0]["role"] == "system"
    assert "native Decisions Development agent" in messages[0]["content"]
    assert "Do not create another ticket" in messages[0]["content"]
    assert "workflow run" in messages[0]["content"]
    assert "list_harness_skills" in messages[0]["content"]
    assert "humanizer" in messages[0]["content"]
    assert "Do not search or list the skill catalog first" in messages[0]["content"]
    assert "Provider: kilocode" in messages[0]["content"]
    assert "Model: kilo-auto/free" in messages[0]["content"]
    assert "Turn runtime: native" in messages[0]["content"]
    assert "Configured routing backend: pi" in messages[0]["content"]
    assert "configured routing backend are not the model provider" in messages[0]["content"]
    assert messages[1]["role"] == "system"
    assert "compact checkpoint" in messages[1]["content"]
    assert messages[-1] == {"role": "user", "content": "Current request"}
    assert sum(1 for message in messages if message == messages[-1]) == 1
    assert not any("Error code: 400" in str(message.get("content")) for message in messages)


def test_native_context_sends_attached_images_as_model_image_input(tmp_path, monkeypatch):
    image = tmp_path / "reference.png"
    image.write_bytes(b"fake-image")
    monkeypatch.setattr("distr.core.turn_runtime.context.ChatService.get_chat_history", lambda _chat_id: [])

    messages = build_development_context(
        7,
        "Describe the reference image",
        {},
        project_folder=str(tmp_path),
        autonomy_level="full",
        attachments=[{"name": "reference.png", "path": str(image), "mime_type": "image/png", "size": 10}],
    )

    content = messages[-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "Attachments available for this turn" in content[0]["text"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
