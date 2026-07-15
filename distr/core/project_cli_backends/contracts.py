"""Provider-neutral execution contracts shared by workflow backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BackendCapabilities:
    persistent_session: bool = False
    steering: bool = False
    resume: bool = False
    tools: bool = True
    files: bool = True
    images: bool = False
    structured_output: bool = False
    local_execution: bool = False

    def supports(self, required: set[str] | list[str] | tuple[str, ...]) -> bool:
        return all(bool(getattr(self, capability, False)) for capability in required)

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass
class ExecutionRequest:
    instruction: str
    working_directory: str
    run_id: int | None = None
    step_id: int | None = None
    attempt_id: int | None = None
    workflow_id: int | None = None
    project_id: int | None = None
    ticket_id: int | None = None
    board_id: int | None = None
    context_packet: str = ""
    required_capabilities: list[str] = field(default_factory=list)
    adapter_options: dict[str, Any] = field(default_factory=dict)
    continuation_token: str = ""


@dataclass
class ExecutionResult:
    status: str
    success: bool
    summary: str = ""
    output: str = ""
    error: str = ""
    backend_id: str = ""
    engine: str = ""
    attempt_id: int | None = None
    execution_session_id: int | None = None
    continuation_token: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    memory_delta: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    waits_for_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_execution_result(raw: Any, *, backend_id: str = "", attempt_id: int | None = None) -> ExecutionResult:
    """Normalize legacy adapter results without teaching workflows each format."""
    def value(name: str, default: Any = None) -> Any:
        if isinstance(raw, dict):
            return raw.get(name, default)
        return getattr(raw, name, default)

    success = bool(value("success", False))
    waits = bool(value("waits_for_human", False))
    error = str(value("error", "") or "")
    output = str(value("output", "") or "")
    status = str(value("status", "") or "").strip().lower()
    if not status:
        status = "waiting" if waits and success else ("completed" if success else "failed")
    return ExecutionResult(
        status=status,
        success=success,
        summary=str(value("summary", "") or output or error)[:2000],
        output=output,
        error=error,
        backend_id=str(value("backend_id", "") or backend_id),
        engine=str(value("engine", "") or backend_id),
        attempt_id=attempt_id,
        execution_session_id=value("execution_session_id"),
        continuation_token=str(value("continuation_token", "") or ""),
        artifacts=list(value("artifacts", []) or []),
        evidence=dict(value("evidence", {}) or {}),
        memory_delta=dict(value("memory_delta", {}) or {}),
        diagnostics=dict(value("diagnostics", {}) or {}),
        waits_for_human=waits,
    )
