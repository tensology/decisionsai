"""Decisions-owned provider-agnostic agent loop for Development turns."""

from __future__ import annotations

import json
import math
from typing import Any

from distr.core.services.context_window import context_window_for_model

from distr.core.turn_runtime.contracts import (
    CancellationToken,
    ModelAdapter,
    ModelResponse,
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
        messages: list[dict] = [dict(message) for message in request.context_messages] or [
            {"role": "user", "content": request.instruction}
        ]
        tool_definitions = self._tools.definitions()
        context_window = self._context_limit(request)
        reserve_tokens = max(4_096, min(16_384, context_window // 8))
        compact_at = min(context_window - reserve_tokens, int(context_window * 0.75))
        compact_target = max(4_096, int(context_window * 0.55))
        context_compactions = 0
        overflow_retries = 0
        protected_messages = [messages[-1]] if messages and messages[-1].get("role") == "user" else []
        change_expected = bool(request.metadata.get("change_expected"))
        mutation_completed = False
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
            if estimated_tokens >= compact_at:
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

            def on_delta(delta: str) -> None:
                token.raise_if_cancelled()
                if delta:
                    self._emit(
                        on_event,
                        kind=TurnEventKind.OUTPUT_DELTA,
                        status=TurnStatus.UPDATING,
                        summary="Updating the response.",
                        output_delta=delta,
                    )

            try:
                response: ModelResponse = await self._model.complete(
                    messages,
                    tool_definitions,
                    on_delta=on_delta,
                )
            except Exception as exc:
                if overflow_retries >= 1 or not self._is_context_overflow(exc):
                    raise
                overflow_retries += 1
                before = self._estimated_tokens(messages, tool_definitions)
                messages, omitted = self._compact_messages(
                    messages,
                    tool_definitions,
                    protected=protected_messages,
                    target_tokens=max(4_096, int(context_window * 0.25)),
                    force=True,
                )
                if not omitted:
                    raise
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
                response = await self._model.complete(messages, tool_definitions, on_delta=on_delta)
            token.raise_if_cancelled()
            calls = tuple(response.tool_calls or ())
            if not calls:
                if apply_steering():
                    if response.text:
                        messages.insert(-1, {"role": "assistant", "content": response.text})
                    continue
                output = str(response.text or "").strip()
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

            messages.append(
                {
                    "role": "assistant",
                    "content": response.text or "",
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
                observation = await self._tools.execute(call.name, dict(call.arguments))
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
