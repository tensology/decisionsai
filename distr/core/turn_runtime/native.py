"""Decisions-owned provider-agnostic agent loop for Development turns."""

from __future__ import annotations

import json
import math
from typing import Any

from distr.core.services.context_window import context_window_for_model
from distr.core.turn_runtime.anthropic_adapter import is_fable_52

from distr.core.turn_runtime.contracts import (
    CancellationToken,
    ModelAdapter,
    ModelResponse,
    POTENTIALLY_MUTATING_PROJECT_TOOLS,
    SteeringChannel,
    ToolExecutor,
    ToolObservation,
    TurnEvent,
    TurnEventKind,
    TurnEventSink,
    TurnRequest,
    TurnResult,
    TurnStatus,
)


_ANSWER_STOPS = {"stop", "end_turn", "stop_sequence"}
_TOOL_STOPS = {"tool_use", "tool_calls", "function_call"}
_CONTEXT_STOPS = {"model_context_window_exceeded", "context_length_exceeded"}
_REFUSAL_STOPS = {"refusal", "content_filter"}
_STOP_ERRORS = {
    **dict.fromkeys(_REFUSAL_STOPS, "The provider declined the request."),
    **dict.fromkeys(_CONTEXT_STOPS, "The model reached its context limit before finishing."),
    "length": "The model reached its output limit before finishing.",
    "max_tokens": "The model reached its output limit before finishing.",
    "stream_incomplete": "The provider stream ended without a completion signal.",
}


class NativeTurnRuntime:
    """Run the model and tool loop inside Decisions, without a workflow or agent CLI."""

    runtime_id = "native"

    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolExecutor,
        max_iterations: int = 25,
        max_failed_tool_calls: int = 6,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least one")
        if max_failed_tool_calls < 1:
            raise ValueError("max_failed_tool_calls must be at least one")
        self._model = model
        self._tools = tools
        self._max_iterations = max_iterations
        self._max_failed_tool_calls = max_failed_tool_calls

    def cancel_active(self) -> None:
        cancel = getattr(self._tools, "cancel_active", None)
        if callable(cancel):
            cancel()

    @staticmethod
    def _emit(
        sink: TurnEventSink | None,
        *,
        kind: TurnEventKind,
        status: TurnStatus,
        summary: str,
        tool_name: str = "",
        output_delta: str = "",
        details: dict | None = None,
    ) -> None:
        if sink:
            sink(
                TurnEvent(
                    kind=kind,
                    status=status,
                    summary=summary,
                    runtime_id=NativeTurnRuntime.runtime_id,
                    tool_name=tool_name,
                    output_delta=output_delta,
                    details=dict(details or {}),
                )
            )

    @staticmethod
    def _observation_message(observation: ToolObservation) -> str:
        return json.dumps(
            {
                "status": observation.status,
                "summary": observation.summary,
                "next_actions": list(observation.next_actions),
                "artifacts": list(observation.artifacts),
                "output": observation.output,
                "error": observation.error,
            },
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _estimated_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> int:
        payload = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False, default=str)
        return max(1, math.ceil(len(payload) / 3))

    @staticmethod
    def _context_limit(request: TurnRequest) -> int:
        configured = request.route.adapter_options.get("context_window")
        try:
            if int(configured or 0) >= 4_096:
                return int(configured)
        except (TypeError, ValueError):
            pass
        return context_window_for_model(request.route.provider, request.route.model)

    @staticmethod
    def _is_context_overflow(exc: Exception) -> bool:
        message = str(exc or "").lower()
        return any(
            marker in message
            for marker in (
                "maximum context length",
                "context_length_exceeded",
                "context window exceeded",
                "input length",
                "prompt is too long",
                "too many tokens",
            )
        )

    @staticmethod
    def _checkpoint_line(message: dict[str, Any]) -> str:
        role = str(message.get("role") or "message")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message.get("tool_calls") or []:
                arguments = json.dumps(call.get("arguments") or {}, ensure_ascii=False, default=str)
                calls.append(f"{call.get('name') or 'tool'}({arguments[:240]})")
            return "Assistant requested: " + ", ".join(calls)
        content = message.get("content")
        if role == "tool":
            try:
                parsed = json.loads(str(content or ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = {}
            if isinstance(parsed, dict):
                facts = {
                    key: parsed.get(key)
                    for key in ("status", "summary", "next_actions", "artifacts", "error")
                    if parsed.get(key)
                }
                if facts:
                    return "Tool result: " + json.dumps(facts, ensure_ascii=False, default=str)[:700]
        if isinstance(content, list):
            content = " ".join(
                str(item.get("text") or "[image]") for item in content if isinstance(item, dict)
            )
        clean = " ".join(str(content or "").split())
        return f"{role.title()}: {clean[:500]}" if clean else ""

    @classmethod
    def _compact_messages(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        protected: list[dict[str, Any]],
        target_tokens: int,
        force: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        if not messages:
            return messages, 0
        primary_system = next((message for message in messages if message.get("role") == "system"), None)
        protected_ids = {id(message) for message in protected}
        required = [message for message in messages if id(message) in protected_ids]
        if primary_system is not None and primary_system not in required:
            required.insert(0, primary_system)

        required_ids = {id(message) for message in required}
        candidates = [message for message in messages if id(message) not in required_ids and message.get("role") != "system"]
        units: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for message in candidates:
            role = message.get("role")
            starts_unit = role == "user" or (role == "assistant" and message.get("tool_calls"))
            if starts_unit and current:
                units.append(current)
                current = []
            if role == "tool" and not current:
                units.append([message])
                continue
            current.append(message)
        if current:
            units.append(current)

        kept_units: list[list[dict[str, Any]]] = []
        available = max(1, int(target_tokens))
        maximum_units = max(0, len(units) - 1) if force else len(units)
        for unit in reversed(units):
            if len(kept_units) >= maximum_units:
                break
            trial = [*required, *unit, *(message for kept in reversed(kept_units) for message in kept)]
            if cls._estimated_tokens(trial, tools) > available:
                break
            kept_units.append(unit)
        kept_recent = [message for unit in reversed(kept_units) for message in unit]

        kept_ids = {id(message) for message in (*required, *kept_recent)}
        omitted = [message for message in messages if id(message) not in kept_ids]
        notes = [line for message in omitted if (line := cls._checkpoint_line(message))]
        checkpoint: dict[str, Any] | None = None
        if notes:
            checkpoint = {
                "role": "system",
                "content": (
                    "Development context checkpoint. Earlier detail was compacted automatically. "
                    "Treat these facts as prior tool and conversation evidence:\n- "
                    + "\n- ".join(notes[-24:])
                )[:8_000],
            }

        ordered: list[dict[str, Any]] = []
        if primary_system is not None:
            ordered.append(primary_system)
        if checkpoint is not None:
            ordered.append(checkpoint)
        for message in messages:
            if message is primary_system or id(message) not in kept_ids:
                continue
            ordered.append(message)
        return ordered, len(omitted)

    async def execute(
        self,
        request: TurnRequest,
        *,
        on_event: TurnEventSink | None = None,
        cancellation: CancellationToken | None = None,
        steering: SteeringChannel | None = None,
    ) -> TurnResult:
        token = cancellation or CancellationToken()
        token.raise_if_cancelled()
        journal = request.metadata.get("recovery_journal")
        messages: list[dict] = [dict(message) for message in request.context_messages] or [
            {"role": "user", "content": request.instruction}
        ]
        completed_writes = list(journal.completed.values()) if journal else []
        recovery_message = None
        if completed_writes:
            recovery_message = {
                "role": "user",
                "content": (
                    "Saved tool receipts from the interrupted attempt. Use these as evidence only. "
                    "Inspect current files, continue the unfinished work, and verify the result.\n"
                    + json.dumps(completed_writes, ensure_ascii=False)
                ),
            }
            messages.insert(len(messages) - 1, recovery_message)
        tool_definitions = self._tools.definitions()
        context_window = self._context_limit(request)
        reserve_tokens = max(4_096, min(16_384, context_window // 8))
        compact_at = min(context_window - reserve_tokens, int(context_window * 0.75))
        compact_target = max(4_096, int(context_window * 0.55))
        context_compactions = 0
        overflow_retries = 0
        protected_messages = [messages[-1]] if messages and messages[-1].get("role") == "user" else []
        if recovery_message:
            protected_messages.append(recovery_message)
        change_expected = bool(request.metadata.get("change_expected"))
        mutation_completed = bool(completed_writes)
        mutation_started = bool(completed_writes)
        completion_corrections = 0

        self._emit(
            on_event,
            kind=TurnEventKind.STATUS,
            status=TurnStatus.THINKING,
            summary="Thinking through the request.",
        )

        def apply_steering() -> bool:
            if steering is None:
                return False
            instructions = steering.drain()
            if not instructions:
                return False
            guidance = "\n\n".join(item.content for item in instructions)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "The user added guidance while this turn was running. Apply it from this "
                        "safe boundary without repeating completed work:\n\n" + guidance
                    ),
                }
            )
            protected_messages.append(messages[-1])
            self._emit(
                on_event,
                kind=TurnEventKind.STEERED,
                status=TurnStatus.UPDATING,
                summary="Applying your latest guidance.",
                details={"event_ids": [item.event_id for item in instructions if item.event_id]},
            )
            return True

        failed_tool_calls_without_progress = 0
        for iteration in range(1, self._max_iterations + 1):
            token.raise_if_cancelled()
            apply_steering()

            estimated_tokens = self._estimated_tokens(messages, tool_definitions)
            # A tool continuation with signed Anthropic blocks requires an
            # unchanged prefix. Trim before opening that tool turn.
            bound_prefix = any(message.get("anthropic_content") for message in messages)
            if estimated_tokens >= compact_at and not bound_prefix:
                messages, omitted = self._compact_messages(
                    messages,
                    tool_definitions,
                    protected=protected_messages,
                    target_tokens=compact_target,
                )
                if omitted:
                    context_compactions += 1
                    self._emit(
                        on_event,
                        kind=TurnEventKind.CONTEXT_COMPACTED,
                        status=TurnStatus.UPDATING,
                        summary="Compacted earlier Development context to keep the turn running.",
                        details={
                            "iteration": iteration,
                            "omitted_messages": omitted,
                            "estimated_tokens_before": estimated_tokens,
                            "estimated_tokens_after": self._estimated_tokens(messages, tool_definitions),
                            "context_window": context_window,
                        },
                    )

            streamed = False

            def on_delta(delta: str) -> None:
                nonlocal streamed
                token.raise_if_cancelled()
                if delta:
                    streamed = True
                    self._emit(
                        on_event,
                        kind=TurnEventKind.OUTPUT_DELTA,
                        status=TurnStatus.UPDATING,
                        summary="Updating the response.",
                        output_delta=delta,
                    )

            def compact_for_retry() -> bool:
                nonlocal messages, overflow_retries, context_compactions
                if overflow_retries or streamed or mutation_started or bound_prefix:
                    return False
                before = self._estimated_tokens(messages, tool_definitions)
                compacted, omitted = self._compact_messages(
                    messages,
                    tool_definitions,
                    protected=protected_messages,
                    target_tokens=max(4_096, int(context_window * 0.25)),
                    force=True,
                )
                if not omitted:
                    return False
                messages = compacted
                overflow_retries += 1
                context_compactions += 1
                self._emit(
                    on_event,
                    kind=TurnEventKind.CONTEXT_COMPACTED,
                    status=TurnStatus.UPDATING,
                    summary="The model reached its context limit. Compacted earlier context and retried.",
                    details={
                        "iteration": iteration,
                        "omitted_messages": omitted,
                        "estimated_tokens_before": before,
                        "estimated_tokens_after": self._estimated_tokens(messages, tool_definitions),
                        "context_window": context_window,
                        "provider_retry": True,
                    },
                )
                return True

            while True:
                token.raise_if_cancelled()
                try:
                    response: ModelResponse = await self._model.complete(
                        messages, tool_definitions, on_delta=on_delta,
                    )
                except Exception as exc:
                    if self._is_context_overflow(exc) and compact_for_retry():
                        continue
                    raise
                token.raise_if_cancelled()
                finish_reason = str(response.finish_reason or "").strip().lower()
                if finish_reason in _CONTEXT_STOPS and not response.text and compact_for_retry():
                    continue
                break

            calls = tuple(response.tool_calls or ())
            output = str(response.text or "").strip()
            error = _STOP_ERRORS.get(finish_reason)
            if not error:
                if finish_reason in _TOOL_STOPS and not calls:
                    error = "The provider stopped for a tool call without returning one."
                elif finish_reason not in _ANSWER_STOPS and finish_reason not in _TOOL_STOPS:
                    error = "The provider stopped without a supported completion signal."
                elif not calls and not output:
                    error = "The model stopped without returning a response."
            if error:
                details = {
                    "iteration": iteration,
                    "finish_reason": finish_reason,
                    "usage": dict(response.usage),
                    "context_compactions": context_compactions,
                }
                self._emit(
                    on_event,
                    kind=TurnEventKind.FAILED,
                    status=TurnStatus.FAILED,
                    summary=error,
                    details=details,
                )
                return TurnResult(
                    success=False,
                    runtime_id=self.runtime_id,
                    backend_id="native",
                    model=request.route.model,
                    output="" if finish_reason in _REFUSAL_STOPS else output,
                    error=error,
                    evidence=details,
                )

            if not calls:
                if apply_steering():
                    if response.text:
                        messages.insert(-1, {"role": "assistant", "content": response.text})
                    continue
                if change_expected and not mutation_completed and completion_corrections < 2:
                    completion_corrections += 1
                    if output:
                        messages.append({"role": "assistant", "content": output})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The user requested a project change and already authorized it, but no project file has been changed. "
                                "Do not finish with an offer or an avoidable clarification. Use the project evidence you inspected, make the smallest conventional implementation now, and verify it. "
                                "Only stop without editing if a concrete external blocker makes the change impossible."
                            ),
                        }
                    )
                    self._emit(
                        on_event,
                        kind=TurnEventKind.STATUS,
                        status=TurnStatus.WORKING,
                        summary="Continuing until the requested change is implemented.",
                        details={"iteration": iteration, "completion_correction": completion_corrections},
                    )
                    continue
                if change_expected and not mutation_completed:
                    message = "The model stopped without making the requested project change."
                    self._emit(
                        on_event,
                        kind=TurnEventKind.FAILED,
                        status=TurnStatus.FAILED,
                        summary=message,
                        details={"iteration": iteration, "usage": dict(response.usage)},
                    )
                    return TurnResult(
                        success=False,
                        runtime_id=self.runtime_id,
                        backend_id="native",
                        model=request.route.model,
                        output=output,
                        error=message,
                        evidence={"iterations": iteration, "usage": dict(response.usage), "changed_files": False, "context_compactions": context_compactions},
                    )
                self._emit(
                    on_event,
                    kind=TurnEventKind.COMPLETED,
                    status=TurnStatus.COMPLETED,
                    summary=output[:1000] or "Turn completed.",
                    details={"iteration": iteration, "usage": dict(response.usage)},
                )
                return TurnResult(
                    success=True,
                    runtime_id=self.runtime_id,
                    backend_id="native",
                    model=request.route.model,
                    output=output or "Turn completed.",
                    evidence={"iterations": iteration, "usage": dict(response.usage), "context_compactions": context_compactions},
                )

            if is_fable_52(request.route.model):
                summary = " ".join(output.split())[:360] if output else (
                    "Running the next project tool." if len(calls) == 1 else f"Running the next {len(calls)} project tools."
                )
                self._emit(
                    on_event, kind=TurnEventKind.PROGRESS, status=TurnStatus.UPDATING,
                    summary=summary, output_delta="\n\n" if streamed and output else summary + "\n\n",
                    details={"iteration": iteration, "tool_count": len(calls)},
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": response.text or "",
                    **({"anthropic_content": response.anthropic_content} if response.anthropic_content else {}),
                    "tool_calls": [
                        {
                            "id": call.call_id,
                            "name": call.name,
                            "arguments": dict(call.arguments),
                        }
                        for call in calls
                    ],
                }
            )
            for call in calls:
                token.raise_if_cancelled()
                if call.name in POTENTIALLY_MUTATING_PROJECT_TOOLS:
                    mutation_started = True
                command_preview = ""
                if call.name == "run_command":
                    from distr.core.chat_turns import redact_text

                    command_preview = redact_text(
                        call.arguments.get("command"), limit=500, preserve_paths=True
                    )
                tool_details = {"iteration": iteration, "call_id": call.call_id}
                if command_preview:
                    tool_details["command"] = command_preview
                self._emit(
                    on_event,
                    kind=TurnEventKind.TOOL_STARTED,
                    status=TurnStatus.WORKING,
                    summary=f"Running {call.name}.",
                    tool_name=call.name,
                    details=tool_details,
                )
                arguments = dict(call.arguments)
                key = journal.key(call.name, arguments) if journal else None
                if key and key in journal.completed:
                    observation = ToolObservation(
                        status="success",
                        summary=f"Skipped a completed write. {journal.completed[key]}",
                        next_actions=("Inspect the current file and continue the remaining work.",),
                    )
                else:
                    if key:
                        journal.start(call.name, key)
                    observation = await self._tools.execute(call.name, arguments)
                    if key:
                        journal.finish(key, observation)
                if call.name in {"replace_text", "write_file"} and observation.status == "success":
                    mutation_completed = True
                token.raise_if_cancelled()
                self._emit(
                    on_event,
                    kind=TurnEventKind.TOOL_COMPLETED,
                    status=TurnStatus.UPDATING,
                    summary=observation.summary,
                    tool_name=call.name,
                    details={
                        "iteration": iteration,
                        "call_id": call.call_id,
                        "status": observation.status,
                        **({'exit_code': observation.exit_code} if observation.exit_code is not None else {}),
                        **({'pipefail': True} if call.name == 'run_command' and observation.exit_code is not None else {}),
                        "next_actions": list(observation.next_actions),
                        "artifacts": list(observation.artifacts),
                        **({"command": command_preview} if command_preview else {}),
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "name": call.name,
                        "content": self._observation_message(observation),
                    }
                )
                if observation.status in {"error", "failed"}:
                    failed_tool_calls_without_progress += 1
                else:
                    failed_tool_calls_without_progress = 0
                if failed_tool_calls_without_progress >= self._max_failed_tool_calls and not mutation_completed:
                    message = (
                        "The native Development turn stopped after repeated tool failures "
                        "without making progress. Review the latest failure and retry with a different approach."
                    )
                    self._emit(
                        on_event,
                        kind=TurnEventKind.FAILED,
                        status=TurnStatus.FAILED,
                        summary=message,
                        details={
                            "iteration": iteration,
                            "failed_tool_calls": failed_tool_calls_without_progress,
                            "changed_files": False,
                        },
                    )
                    return TurnResult(
                        success=False,
                        runtime_id=self.runtime_id,
                        backend_id="native",
                        model=request.route.model,
                        error=message,
                        evidence={
                            "iterations": iteration,
                            "failed_tool_calls": failed_tool_calls_without_progress,
                            "changed_files": False,
                            "context_compactions": context_compactions,
                        },
                    )
            apply_steering()

        message = f"The native Development turn reached its {self._max_iterations}-iteration safety limit."
        self._emit(
            on_event,
            kind=TurnEventKind.FAILED,
            status=TurnStatus.FAILED,
            summary=message,
        )
        return TurnResult(
            success=False,
            runtime_id=self.runtime_id,
            backend_id="native",
            model=request.route.model,
            error=message,
            evidence={"iterations": self._max_iterations, "context_compactions": context_compactions},
        )
