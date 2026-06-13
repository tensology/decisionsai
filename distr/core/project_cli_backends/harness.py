"""Unified harness adapter over project CLI backends."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from distr.core.project_cli_backends.base import BackendTaskResult


class HarnessStatus(str, Enum):
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    DONE = "done"
    FAILED = "failed"


@dataclass
class HarnessContext:
    project: Any
    instruction: str
    backend_id: str
    model: str = ""
    ticket_id: int | None = None
    board_id: int | None = None
    run_id: int | None = None
    workflow_id: int | None = None
    step_id: int | None = None
    ticket_complexity: str = "medium"
    codex_reasoning_effort: str = ""
    codex_service_tier: str = ""
    origin: str = "workflow"
    on_event: Optional[Callable[[dict[str, Any]], None]] = None


@dataclass
class HarnessHandle:
    backend_id: str
    execution_session_id: int | None = None
    result: BackendTaskResult | None = None
    status: HarnessStatus = HarnessStatus.RUNNING
    evidence: dict[str, Any] = field(default_factory=dict)


async def dispatch_harness(context: HarnessContext) -> HarnessHandle:
    """Run a project task through the selected backend and normalize the handle."""
    from distr.core.project_handoff import dispatch_project_handoff

    result = await dispatch_project_handoff(context)
    engine = (getattr(result, "engine", "") or "").strip()
    success = bool(getattr(result, "success", False))
    waits_for_human = bool(getattr(result, "waits_for_human", False))
    if waits_for_human and success:
        status = HarnessStatus.WAITING_HUMAN
    elif success:
        status = HarnessStatus.DONE
    else:
        status = HarnessStatus.FAILED
    session_id = getattr(result, "execution_session_id", None)
    handle = HarnessHandle(
        backend_id=context.backend_id,
        execution_session_id=int(session_id) if session_id else None,
        result=result,
        status=status,
        evidence={
            "output": (getattr(result, "output", "") or "")[:8000],
            "error": (getattr(result, "error", "") or "")[:2000],
            "engine": engine,
        },
    )
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source=context.backend_id,
            event_type="execution_dispatched",
            status=status.value,
            workflow_id=context.workflow_id,
            run_id=context.run_id,
            step_id=context.step_id,
            ticket_id=context.ticket_id,
            board_id=context.board_id,
            project_id=getattr(context.project, "id", None),
            execution_session_id=handle.execution_session_id,
            summary=f"Harness {context.backend_id} dispatch → {status.value}",
            payload={"backend_id": context.backend_id, "engine": engine},
            evidence=handle.evidence,
        )
    except Exception:
        pass
    return handle


def poll_harness(handle: HarnessHandle) -> HarnessStatus:
    """Return current handle status (one-shot backends finish in dispatch)."""
    return handle.status


def collect_harness_evidence(handle: HarnessHandle) -> dict[str, Any]:
    """Collect normalized evidence from a completed or waiting handle."""
    evidence = dict(handle.evidence or {})
    if handle.result is not None:
        evidence.setdefault("output", (getattr(handle.result, "output", "") or "")[:8000])
        evidence.setdefault("error", (getattr(handle.result, "error", "") or "")[:2000])
        evidence.setdefault("success", bool(getattr(handle.result, "success", False)))
    return evidence


STEERABLE_BACKENDS = frozenset(
    {"pi", "codex", "codex_ide", "cursor", "cursor_ide", "claude_code", "claude", "hermes_agent"}
)


def is_steerable_backend(backend_id: str) -> bool:
    from distr.core.project_cli_backends import normalize_backend_id

    return normalize_backend_id(backend_id or "") in STEERABLE_BACKENDS


def steer_harness(
    *,
    message: str,
    backend_id: str,
    project_id: int | None = None,
    project_folder: str | None = None,
) -> dict[str, Any]:
    """
    Steer an in-flight harness without restarting the workflow step.

    Pi: sends RPC steer when a live session exists.
    Other CLIs: returns queued=True for persistence on the run record.
    """
    from distr.core.project_cli_backends import normalize_backend_id

    instruction = str(message or "").strip()
    if not instruction:
        return {"success": False, "error": "Steer message is required", "delivered": False}

    bid = normalize_backend_id(backend_id or "pi")
    if bid not in STEERABLE_BACKENDS:
        return {
            "success": False,
            "error": f"Backend {bid} does not support mid-flight steering",
            "delivered": False,
        }

    if bid == "pi" and project_id:
        from distr.core.pi_rpc import get_rpc_session

        rpc = get_rpc_session(int(project_id))
        if rpc and rpc.is_alive:
            delivered = bool(rpc.steer(instruction))
            return {
                "success": delivered,
                "delivered": delivered,
                "method": "pi_rpc",
                "backend_id": bid,
                "error": "" if delivered else "Pi RPC steer was not accepted",
            }
        return {
            "success": True,
            "delivered": False,
            "method": "queued",
            "backend_id": bid,
            "error": "No live Pi session — steer queued for when the harness reconnects",
        }

    return {
        "success": True,
        "delivered": False,
        "method": "queued",
        "backend_id": bid,
    }


def run_harness_sync(context: HarnessContext) -> HarnessHandle:
    """Sync wrapper for workflow step executor (handles nested event loops)."""
    import concurrent.futures

    try:
        return asyncio.run(dispatch_harness(context))
    except RuntimeError:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(dispatch_harness(context))).result()
