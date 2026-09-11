"""Stable contracts for Decisions-owned Development turn execution."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from threading import Event, Lock
from typing import Any, Awaitable, Callable, Protocol


class TurnStatus(str, Enum):
    INITIALIZING = "initializing"
    QUEUED = "queued"
    THINKING = "thinking"
    WORKING = "working"
    UPDATING = "updating"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TurnEventKind(str, Enum):
    STATUS = "status"
    MODEL = "model"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    OUTPUT_DELTA = "output_delta"
    STEERED = "steered"
    CONTEXT_COMPACTED = "context_compacted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ModelRoute:
    """The inference selection, separate from the runtime that executes the turn."""

    provider: str = ""
    model: str = "auto"
    backend_id: str = "pi"
    adapter_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnScope:
    chat_id: int
    project_id: int
    board_id: int | None = None
    ticket_id: int | None = None
    turn_id: int | None = None
    project_folder: str = ""


@dataclass(frozen=True)
class TurnRequest:
    instruction: str
    project: Any
    route: ModelRoute
    scope: TurnScope
    autonomy_level: str = "full"
    complexity: str = "medium"
    required_capabilities: tuple[str, ...] = ("tools", "files")
    context_messages: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnEvent:
    kind: TurnEventKind
    status: TurnStatus
    summary: str
    runtime_id: str
    execution_session_id: int | None = None
    tool_name: str = ""
    output_delta: str = ""
    artifacts: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.kind.value,
            "status": self.status.value,
            "summary": self.summary,
            "runtime_id": self.runtime_id,
            "execution_session_id": self.execution_session_id,
            "tool_name": self.tool_name,
            "output_delta": self.output_delta,
            "artifacts": list(self.artifacts),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ToolObservation:
    status: str
    summary: str
    next_actions: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    output: Any = None
    error: str = ""


@dataclass(frozen=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    text: str = ""
    tool_calls: tuple[ModelToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResult:
    success: bool
    runtime_id: str
    backend_id: str
    model: str
    output: str = ""
    error: str = ""
    waits_for_human: bool = False
    execution_session_id: int | None = None
    artifacts: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


class CancellationToken:
    """Thread-safe cooperative cancellation shared by runtimes and tools."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TurnCancelled("Turn cancelled by the user.")


class TurnCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class SteeringInstruction:
    """User guidance accepted while an existing turn is still running."""

    content: str
    event_id: str = ""


class SteeringChannel:
    """Thread-safe guidance queue drained at safe model boundaries."""

    def __init__(self, *, on_applied: Callable[[tuple[str, ...]], None] | None = None) -> None:
        self._items: deque[SteeringInstruction] = deque()
        self._lock = Lock()
        self._on_applied = on_applied

    def put(self, content: str, *, event_id: str = "") -> None:
        clean = str(content or "").strip()
        if not clean:
            raise ValueError("Steering guidance is required.")
        with self._lock:
            self._items.append(SteeringInstruction(content=clean, event_id=str(event_id or "")))

    def drain(self) -> tuple[SteeringInstruction, ...]:
        with self._lock:
            items = tuple(self._items)
            self._items.clear()
        if items and self._on_applied:
            event_ids = tuple(item.event_id for item in items if item.event_id)
            if event_ids:
                self._on_applied(event_ids)
        return items


TurnEventSink = Callable[[TurnEvent], None]


class TurnRuntime(Protocol):
    runtime_id: str

    async def execute(
        self,
        request: TurnRequest,
        *,
        on_event: TurnEventSink | None = None,
        cancellation: CancellationToken | None = None,
        steering: SteeringChannel | None = None,
    ) -> TurnResult: ...


class ModelAdapter(Protocol):
    adapter_id: str

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        on_delta: Callable[[str], None] | None = None,
    ) -> ModelResponse: ...


class ToolExecutor(Protocol):
    def definitions(self) -> list[dict[str, Any]]: ...

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolObservation: ...


AsyncTurnExecutor = Callable[
    [TurnRequest, TurnEventSink | None, CancellationToken | None],
    Awaitable[TurnResult],
]
