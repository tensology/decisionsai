"""Provider-neutral execution contracts shared by workflow backends."""

from __future__ import annotations

import re
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
    next_actions: dict[str, Any] = field(default_factory=dict)
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
    resolved_backend = str(value("backend_id", "") or backend_id)
    engine = str(value("engine", "") or backend_id)

    report_labels = (
        "Status",
        "Summary",
        "Files changed",
        "Tests",
        "Evidence",
        "Blockers",
        "Next step",
    )

    def report_field(label: str) -> str:
        table_match = re.search(
            rf"(?im)^\s*\|\s*(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*\|\s*(.+?)\s*\|",
            output,
        )
        if table_match:
            return table_match.group(1).strip().strip("`*").strip()
        labels = "|".join(re.escape(item) for item in report_labels)
        match = re.search(
            rf"(?is)(?:^|[;\r\n])\s*(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*:\s*(?:\*\*)?\s*"
            rf"(.+?)(?=\s*;\s*(?:\*\*)?(?:{labels})(?:\*\*)?\s*:|\r?\n|$)",
            output,
        )
        return match.group(1).strip() if match else ""

    artifacts = list(value("artifacts", []) or [])
    files_changed = report_field("Files changed")
    if not artifacts and files_changed and files_changed.lower() != "none":
        artifacts = [{"type": "changed_files", "value": files_changed}]

    memory_delta = dict(value("memory_delta", {}) or {})
    if not memory_delta:
        evidence = [item for item in (report_field("Tests"), report_field("Evidence")) if item]
        memory_delta = {
            "summary": report_field("Summary") or (output or error)[:2000],
            "changed_files": [files_changed] if files_changed and files_changed.lower() != "none" else [],
            "evidence": evidence,
            "blockers": [report_field("Blockers")] if report_field("Blockers") else [],
        }

    diagnostics = dict(value("diagnostics", {}) or {})
    diagnostics.setdefault("backend_id", resolved_backend)
    diagnostics.setdefault("engine", engine)
    if error:
        diagnostics.setdefault("error", error)

    raw_next_actions = value("next_actions", {}) or {}
    if isinstance(raw_next_actions, dict):
        next_actions = dict(raw_next_actions)
    elif isinstance(raw_next_actions, (list, tuple)):
        next_actions = {"recommended": list(raw_next_actions)}
    else:
        next_actions = {"recommended": [str(raw_next_actions)]} if str(raw_next_actions).strip() else {}
    next_step = report_field("Next step")
    if next_step and not next_actions.get("recommended"):
        next_actions["recommended"] = [next_step]
    if not next_actions.get("recommended"):
        next_actions["recommended"] = [
            "Provide the requested input to continue."
            if status == "waiting"
            else (
                "Validate the worker result before closing the ticket."
                if status == "completed"
                else "Resolve the reported error, then retry the worker."
            )
        ]
    if error and not memory_delta.get("blockers"):
        memory_delta["blockers"] = [error]

    return ExecutionResult(
        status=status,
        success=success,
        summary=str(value("summary", "") or output or error)[:2000],
        output=output,
        error=error,
        backend_id=resolved_backend,
        engine=engine,
        attempt_id=attempt_id,
        execution_session_id=value("execution_session_id"),
        continuation_token=str(value("continuation_token", "") or ""),
        artifacts=artifacts,
        evidence=dict(value("evidence", {}) or {}),
        memory_delta=memory_delta,
        diagnostics=diagnostics,
        next_actions=next_actions,
        waits_for_human=waits,
    )
