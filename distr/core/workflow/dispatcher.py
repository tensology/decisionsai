"""
StepDispatcher — step execution engine.

Validates config → assembles context → executes action → records result → pushes websocket.
Extracted from _dispatch_step() in service.py and _execute_step_directly() in workflow.py.

**Validates: Requirements 1, 2, 5, 8, 9**
"""
import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import uuid4

from distr.core.db import get_session
from distr.core.db.time import utc_now_naive
from distr.core.workflow_engine.context_assembly import WorkflowRunContext
from distr.core.db.workflow import (
    AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun,
    AutoWorkflowStepResult,
)
from distr.core.workflow.context_limits import truncate_step_summary
from distr.core.kanban.result_packet import (
    create_initial_result_packet_for_run,
)
from distr.core.kanban.ticket_audit import append_ticket_audit_entry
from distr.core.workflow.risk_and_audit import (
    infer_risk_profile,
    build_audit_gates,
    validation_rules_for_risk,
    enforce_validation_requirements,
)
from distr.core.workflow.chat_trace import record_workflow_chat_event
try:
    from distr.gui.web.workflow_events import increment_workflow_updated
except Exception:  # pragma: no cover - tests may stub distr.gui.web
    def increment_workflow_updated(*args, **kwargs):
        return None

try:
    from distr.gui.web.kanban_events import increment_kanban_updated
except Exception:  # pragma: no cover - tests may stub distr.gui.web
    def increment_kanban_updated(*args, **kwargs):
        return None

logger = logging.getLogger(__name__)

DEFAULT_RUN_SETTINGS = {
    "execution_mode": "sequential",
    "concurrency_scope": "project",
    "max_parallel_tickets": 3,
    "branch_per_ticket": True,
}


def _workflow_run_settings(wf: AutoWorkflow) -> Dict[str, Any]:
    try:
        raw = json.loads(getattr(wf, "run_settings", None) or "{}")
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    settings = dict(DEFAULT_RUN_SETTINGS)
    settings.update({k: v for k, v in raw.items() if k in settings})
    settings["execution_mode"] = "parallel" if settings.get("execution_mode") == "parallel" else "sequential"
    settings["concurrency_scope"] = "workflow" if settings.get("concurrency_scope") == "workflow" else "project"
    try:
        settings["max_parallel_tickets"] = max(1, min(12, int(settings.get("max_parallel_tickets") or 3)))
    except Exception:
        settings["max_parallel_tickets"] = 3
    settings["branch_per_ticket"] = bool(settings.get("branch_per_ticket"))
    return settings


def _scope_developer_context_to_run(
    context: Dict[str, Any],
    run_metadata: Dict[str, Any],
    *,
    board_id: Optional[int],
    ticket_id: Optional[int],
) -> Dict[str, Any]:
    """Remove ambient project state that can misroute a ticket-scoped worker."""
    scoped = dict(context or {})
    metadata = dict(run_metadata or {})
    project_id = metadata.get("project_id")
    project_name = str(metadata.get("project_name") or "").strip()
    project_folder = str(metadata.get("project_folder") or "").strip()

    runtime = dict(scoped.get("runtime") or {})
    if project_folder:
        runtime["cwd"] = project_folder
    scoped["runtime"] = runtime
    if project_id not in (None, "") or project_name or project_folder:
        scoped["active_project"] = {
            "id": int(project_id) if str(project_id or "").isdigit() else project_id,
            "name": project_name,
            "folder_location": project_folder,
            "description": str(metadata.get("project_description") or ""),
        }
    if board_id is not None or metadata.get("board_name"):
        scoped["active_board"] = {
            "id": int(board_id) if board_id is not None else metadata.get("board_id"),
            "name": str(metadata.get("board_name") or ""),
            "source": str(metadata.get("board_source") or "database"),
            "lanes": [],
            "default_project_id": project_id,
            "default_workflow_id": metadata.get("workflow_id"),
            "send_to_cli": False,
        }
    if ticket_id is not None:
        scoped["active_tickets"] = [{
            "id": int(ticket_id),
            "title": str(metadata.get("ticket_title") or ""),
            "lane": str(metadata.get("lane_name") or ""),
            "priority": str(metadata.get("priority") or ""),
            "workflow_status": "running",
            "linked_project_id": (
                int(project_id) if str(project_id or "").isdigit() else project_id
            ),
            "linked_workflow_id": metadata.get("workflow_id"),
            "send_to_cli": False,
        }]

    # Ambient runs and editor threads belong to whatever the desktop currently
    # has selected. They are useful in chat, but unsafe in an explicitly scoped
    # project worker packet.
    scoped["active_workflows"] = []
    scoped["active_executions"] = []
    if not metadata.get("include_external_agent_context"):
        scoped["external_agent_context"] = {}
    if not metadata.get("include_ambient_memory_context"):
        # These values are assembled from whichever project/board the desktop
        # currently has selected.  A ticket run already carries its explicit
        # project, board and ticket; leaking an ambient workspace handoff or
        # global board notes into it can send the worker toward another repo.
        # Project-neutral memory remains available to chat, while workflow
        # workers receive durable facts through their ticket/run contract.
        scoped["user_memory_context"] = ""
        scoped["workspace"] = {}
        scoped["board_notes"] = []
        scoped["ecosystem"] = {}
    return scoped


def _run_project_id(run: AutoWorkflowRun) -> Optional[str]:
    try:
        data = json.loads(run.run_data or "{}")
    except Exception:
        data = {}
    value = data.get("project_id") if isinstance(data, dict) else None
    return str(value) if value not in (None, "") else None

from distr.core.workflow.step_validator import build_step_config, validate_before_dispatch as _validate_step  # noqa: E402
from distr.core.workflow.step_executor import StepExecutorMixin
from distr.core.workflow.post_execution import PostExecutionMixin
from distr.core.workflow.runtime_contract import (
    build_step_preflight,
    emit_step_activity,
    should_pause_after_step,
)


# ── Run-context infrastructure ──────────────────────────────────────
# Moved from service.py — tracks per-run WorkflowAgent lifecycle.


@dataclass
class _RunContext:
    """Per-run state for the WorkflowAgent lifecycle."""
    run_id: int
    workflow_agent: "WorkflowAgent"  # noqa: F821
    event_loop: asyncio.AbstractEventLoop
    thread: threading.Thread
    context_prefix: str = ""
    run_ctx: Optional["WorkflowRunContext"] = None


_active_runs: Dict[int, _RunContext] = {}
_initializing_runs: set[int] = set()
_runs_lock = threading.Lock()


def workflow_run_context_is_current(run_id: int, expected_ctx: _RunContext) -> bool:
    """Return whether *expected_ctx* is still the live context for *run_id*."""
    with _runs_lock:
        return _active_runs.get(run_id) is expected_ctx


def build_workflow_run_receipt(
    *,
    run_id: int,
    workflow_id: int,
    status: str,
    steps_summary: List[dict],
    board_id: Optional[int] = None,
    ticket_id: Optional[int] = None,
    project_id: Optional[int] = None,
    validation_records: Optional[List[dict]] = None,
    result_packet: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a compact, stable terminal summary for a workflow run."""
    normalized_status = (status or "completed").strip().lower()
    normalized_steps = [dict(item or {}) for item in (steps_summary or [])]
    completed_count = sum(
        1
        for item in normalized_steps
        if str(item.get("status") or "").strip().lower() in ("completed", "passed")
    )
    has_completion_evidence = (
        completed_count > 0
        or bool(validation_records)
        or any((item.get("result") or item.get("status")) for item in normalized_steps)
    )
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "status": normalized_status,
        "success": normalized_status == "completed",
        "cancelled": normalized_status == "cancelled",
        "board_id": board_id,
        "ticket_id": ticket_id,
        "project_id": project_id,
        "steps_summary": normalized_steps,
        "step_count": len(normalized_steps),
        "completed_step_count": completed_count,
        "has_completion_evidence": has_completion_evidence,
        "validation_records": list(validation_records or []),
        "result_packet": dict(result_packet or {}),
    }


_E2E_SMOKE_SLUGS = frozenset({
    "dogfood-e2e-smoke",
    "dogfood-spawn-e2e",
    "e2e-until-green",
    "spotify-e2e-ideation",
    "spotify-e2e-dev",
    "spotify-e2e-polish",
})


def _is_e2e_smoke_workflow(workflow_id: int | None) -> bool:
    """Harness/dogfood smoke workflows skip production validation gates."""
    if not workflow_id:
        return False
    try:
        with get_session() as db:
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
            if not wf:
                return False
            try:
                wf_input = json.loads(wf.workflow_input or "{}") or {}
            except Exception:
                wf_input = {}
            slug = str(wf_input.get("slug") or "").strip().lower()
            if slug in _E2E_SMOKE_SLUGS:
                return True
            return bool(wf_input.get("e2e_smoke"))
    except Exception:
        return False


def _finalize_result_packet_for_terminal_run(
    packet: Dict[str, Any],
    *,
    run_id: int,
    status: str,
    risk_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """Ensure the stored run packet reflects the terminal workflow state."""
    updated = dict(packet or {})
    status_label = (status or "completed").strip().lower()
    risk = dict(risk_profile or {})
    risk_level = risk.get("level", "low")

    updated["status"] = status_label
    updated["summary"] = f"Workflow run {run_id} finished with status: {status_label}."

    artifacts = dict(updated.get("artifacts") or {})
    logs = list(artifacts.get("logs") or [])
    run_log = f"workflow_run:{run_id}"
    if run_log not in logs:
        logs.append(run_log)
    artifacts["logs"] = logs
    updated["artifacts"] = artifacts

    audit = dict(updated.get("audit") or {})
    audits_run = list(audit.get("audits_run") or [])
    if not audits_run:
        audits_run = build_audit_gates(
            status=status_label,
            risk_level=risk_level,
            tests_passed=(status_label == "completed"),
        )
    audit["audits_run"] = audits_run
    if status_label == "completed" and audit.get("final_verdict") in (None, "", "cannot_determine"):
        audit["final_verdict"] = "pass"
    elif status_label in ("failed", "cancelled") and audit.get("final_verdict") in (None, "", "cannot_determine"):
        audit["final_verdict"] = "needs_changes"
    audit.setdefault("rationale", "Workflow terminal status mapped to canonical result packet.")
    updated["audit"] = audit
    return updated


def _record_packet_ui_quality_validation(
    packet: Dict[str, Any],
    *,
    workflow_id: Optional[int],
    run_id: int,
    step_id: Optional[int],
    ticket_id: Optional[int],
    board_id: Optional[int],
    project_id: Optional[int],
    execution_session_id: Optional[int],
) -> Dict[str, Any]:
    """Record packet UI artifacts as orchestrator validation and merge the snapshot."""
    updated = dict(packet or {})
    artifacts = dict(updated.get("artifacts") or {})
    ui_quality = dict(artifacts.get("ui_quality") or {})
    if not ui_quality:
        return updated
    execution = dict(updated.get("execution") or {})
    snapshots = list(execution.get("validation_snapshots") or [])
    if any(str(item.get("validation_type") or "").strip().lower() == "ui_quality" for item in snapshots if isinstance(item, dict)):
        return updated
    try:
        from distr.core.orchestrator import (
            build_correction_packet,
            create_correction_attempt,
            list_validation_records,
            record_ui_quality_validation,
        )

        record_id = record_ui_quality_validation(
            artifacts=ui_quality,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=project_id,
            execution_session_id=execution_session_id,
        )
        if not record_id:
            return updated
        records = list_validation_records(run_id=run_id, limit=10)
        record = next((item for item in records if item.get("id") == record_id), None)
        payload = dict((record or {}).get("payload") or {})
        snapshot = dict(payload.get("snapshot") or {})
        if snapshot:
            snapshot["record_id"] = record_id
            if str(snapshot.get("verdict") or "").strip().lower() == "fail":
                correction_packet = build_correction_packet(
                    validation_record=record or {
                        "id": record_id,
                        "workflow_id": workflow_id,
                        "run_id": run_id,
                        "step_id": step_id,
                        "ticket_id": ticket_id,
                        "validation_type": snapshot.get("validation_type"),
                        "expected": snapshot.get("expected"),
                        "observed": snapshot.get("observed"),
                        "verdict": snapshot.get("verdict"),
                        "correction_hint": snapshot.get("correction_hint"),
                        "payload": {"snapshot": snapshot},
                    },
                    step_name="Terminal UI quality gate",
                )
                attempt_id = create_correction_attempt(
                    validation_record_id=record_id,
                    correction_packet=correction_packet,
                    status="queued",
                )
                if attempt_id:
                    snapshot["correction_attempt_id"] = attempt_id
                audit = dict(updated.get("audit") or {})
                audit["final_verdict"] = "needs_changes"
                audit["rationale"] = snapshot.get("observed") or "UI quality validation failed."
                updated["audit"] = audit
                updated["status"] = "partial_success"
                next_actions = dict(updated.get("next_actions") or {})
                recommended = list(next_actions.get("recommended") or [])
                recommended.append("Correct the failed UI quality validation and rerun the visual baseline check.")
                next_actions["recommended"] = recommended
                updated["next_actions"] = next_actions
            snapshots.append(snapshot)
            execution["validation_snapshots"] = snapshots
            updated["execution"] = execution
    except Exception:
        logger.debug("Could not record packet UI quality validation for run %s", run_id, exc_info=True)
    return updated


def _packet_has_failed_ui_quality_validation(packet: Dict[str, Any]) -> bool:
    execution = dict((packet or {}).get("execution") or {})
    for snapshot in execution.get("validation_snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        if str(snapshot.get("validation_type") or "").strip().lower() != "ui_quality":
            continue
        if str(snapshot.get("verdict") or "").strip().lower() == "fail":
            return True
    return False


def _terminal_ui_correction_snapshot(packet: Dict[str, Any]) -> Dict[str, Any]:
    execution = dict((packet or {}).get("execution") or {})
    snapshots = [
        item for item in (execution.get("validation_snapshots") or [])
        if isinstance(item, dict)
        and str(item.get("validation_type") or "").strip().lower() == "ui_quality"
        and str(item.get("verdict") or "").strip().lower() == "fail"
        and item.get("correction_attempt_id")
    ]
    return dict(snapshots[-1]) if snapshots else {}


def _maybe_auto_dispatch_terminal_ui_correction(
    db,
    *,
    run: AutoWorkflowRun,
    packet: Dict[str, Any],
    run_data: Dict[str, Any],
) -> Dict[str, Any] | None:
    """Auto-dispatch corrections were removed; terminal UI failures stay on the run record."""
    return None
_isolated_step_lock = threading.Lock()
_isolated_steps_in_progress: set[int] = set()

# Thread-safe per-run context — keyed by OS thread ID.
# Supplements os.environ so concurrent workflow runs don't overwrite each other.
# os.environ is still set for backward compat with tools that haven't migrated.
_workflow_thread_env: Dict[int, Dict[str, str]] = {}
_wte_lock = threading.Lock()


def _set_workflow_thread_env(tid: int, run_id: int, step_id: int, workflow_id: int) -> None:
    with _wte_lock:
        _workflow_thread_env[tid] = {
            "run_id": str(run_id),
            "step_id": str(step_id),
            "workflow_id": str(workflow_id),
        }


def _update_workflow_thread_step(step_id: int) -> None:
    tid = threading.get_ident()
    with _wte_lock:
        if tid in _workflow_thread_env:
            _workflow_thread_env[tid]["step_id"] = str(step_id)


def _clear_workflow_thread_env(tid: int) -> None:
    with _wte_lock:
        _workflow_thread_env.pop(tid, None)


def get_current_workflow_env() -> Dict[str, Optional[str]]:
    """Return workflow context vars for the current thread.

    Checks the per-thread dict first (concurrent-safe); falls back to
    os.environ for tools that have not yet migrated.
    """
    tid = threading.get_ident()
    with _wte_lock:
        ctx = _workflow_thread_env.get(tid)
    if ctx:
        return dict(ctx)
    return {
        "run_id": os.environ.get("DECISIONS_WORKFLOW_RUN_ID"),
        "step_id": os.environ.get("DECISIONS_WORKFLOW_STEP_ID"),
        "workflow_id": os.environ.get("DECISIONS_WORKFLOW_ID"),
    }


def _cleanup_run(run_id: int) -> None:
    """Clean up a workflow run's WorkflowAgent and event loop."""
    with _runs_lock:
        _initializing_runs.discard(run_id)
        ctx = _active_runs.pop(run_id, None)
    if ctx is None:
        return
    try:
        ctx.workflow_agent.shutdown()
    except Exception:
        pass
    try:
        ctx.event_loop.call_soon_threadsafe(ctx.event_loop.stop)
    except Exception:
        pass


def _cancel_linked_execution_sessions(
    db: Any,
    run_ids: List[int],
    *,
    reason: str,
    event_type: str,
) -> List[int]:
    """Terminalize every queued/running project execution linked to workflow runs.

    This deliberately shares the caller's transaction.  A workflow run must never
    become terminal while its provider session remains visible as active.
    """
    if not run_ids:
        return []

    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession

    completed_at = utc_now_naive()
    sessions = (
        db.query(ProjectExecutionSession)
        .filter(ProjectExecutionSession.run_id.in_(run_ids))
        .filter(ProjectExecutionSession.status.in_(["queued", "running", "waiting"]))
        .all()
    )
    for execution in sessions:
        execution.status = "cancelled"
        execution.error = reason
        execution.updated_at = completed_at
        execution.completed_at = completed_at
        db.add(
            ProjectExecutionEvent(
                session_id=execution.id,
                event_type=event_type,
                status="cancelled",
                message=reason,
                payload=json.dumps(
                    {
                        "run_id": execution.run_id,
                        "recovered": event_type == "recovered_after_restart",
                    }
                ),
            )
        )
    return [int(execution.id) for execution in sessions]


def _reconcile_terminal_execution_sessions(db: Any) -> List[int]:
    """Cancel active provider rows whose owning workflow is terminal or missing."""
    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession

    terminal_run_statuses = {"completed", "failed", "cancelled"}
    completed_at = utc_now_naive()
    reconciled: List[int] = []
    active = (
        db.query(ProjectExecutionSession)
        .filter(ProjectExecutionSession.status.in_(["queued", "running", "waiting"]))
        .all()
    )
    for execution in active:
        run = db.get(AutoWorkflowRun, int(execution.run_id)) if execution.run_id else None
        missing_owner = execution.run_id is not None and run is None
        terminal_owner = run is not None and run.status in terminal_run_statuses
        standalone_worker = execution.run_id is None and execution.status in {"queued", "running"}
        if not (missing_owner or terminal_owner or standalone_worker):
            continue
        previous_status = execution.status
        reason = (
            "Provider session had no workflow run after restart."
            if missing_owner
            else "Provider session outlived its terminal workflow run."
            if terminal_owner
            else "Standalone provider session was interrupted by app restart."
        )
        execution.status = "cancelled"
        execution.error = reason
        execution.updated_at = completed_at
        execution.completed_at = completed_at
        db.add(
            ProjectExecutionEvent(
                session_id=execution.id,
                event_type="recovered_after_restart",
                status="cancelled",
                message=reason,
                payload=json.dumps(
                    {
                        "run_id": execution.run_id,
                        "previous_status": previous_status,
                        "recovered": True,
                    }
                ),
            )
        )
        reconciled.append(int(execution.id))
    return reconciled


def _cleanup_orphaned_runs_on_startup() -> None:
    """Reconcile interrupted runs and provider sessions on app startup.

    Running work has lost its in-memory worker and must become terminal. Waiting
    checkpoints remain durable so a Telegram/web approval can resume after a
    restart. Provider rows whose run is terminal or missing must never remain
    visible as queued/running/waiting forever.
    """
    with get_session() as db:
        orphans = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.status == "running")
            .all()
        )
        orphan_ids = [int(run.id) for run in orphans]
        completed_at = utc_now_naive()
        for run in orphans:
            run.status = "cancelled"
            run.completed_at = completed_at
        from distr.core.db.kanban import KanbanTicket

        stale_tickets = (
            db.query(KanbanTicket)
            .filter(KanbanTicket.workflow_status.in_(["running", "waiting"]))
            .all()
        )
        for ticket in stale_tickets:
            latest_run = (
                db.query(AutoWorkflowRun)
                .filter(AutoWorkflowRun.ticket_id == int(ticket.id))
                .order_by(AutoWorkflowRun.id.desc())
                .first()
            )
            if latest_run and latest_run.status in {
                "running",
                "waiting",
                "completed",
                "failed",
                "cancelled",
            }:
                ticket.workflow_status = str(latest_run.status)
        execution_ids = _cancel_linked_execution_sessions(
            db,
            orphan_ids,
            reason="App restarted before provider completion.",
            event_type="recovered_after_restart",
        )
        reconciled_ids = _reconcile_terminal_execution_sessions(db)
        db.commit()
        all_execution_ids = sorted(set(execution_ids + reconciled_ids))
        if orphan_ids or all_execution_ids:
            logger.info(
                "Reconciled %d interrupted workflow run(s) and %d execution "
                "session(s) from previous session: runs=%s sessions=%s",
                len(orphans),
                len(all_execution_ids),
                orphan_ids,
                all_execution_ids,
            )


def _finalize_terminal_run(run_id: int, workflow_id: int, status: str) -> None:
    """Clean up resources and notify the bridge when a run reaches terminal status."""
    _cleanup_run(run_id)

    steps_summary: List[dict] = []
    board_id: Optional[int] = None
    ticket_id: Optional[int] = None
    project_id: Optional[int] = None
    result_packet: Dict[str, Any] = {}
    validation_records: List[dict] = []
    try:
        with get_session() as db:
            run_rec = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            if run_rec:
                board_id = run_rec.board_id
                ticket_id = run_rec.ticket_id
                try:
                    run_data = json.loads(run_rec.run_data or "{}") or {}
                except Exception:
                    run_data = {}
                project_id_raw = run_data.get("project_id") if isinstance(run_data, dict) else None
                project_id = int(project_id_raw) if str(project_id_raw or "").isdigit() else None
                result_packet = dict(run_data.get("result_packet") or {}) if isinstance(run_data, dict) else {}
            step_results = (
                db.query(AutoWorkflowStepResult)
                .filter(AutoWorkflowStepResult.run_id == run_id)
                .order_by(AutoWorkflowStepResult.created_at)
                .all()
            )
            for sr in step_results:
                step_obj = sr.step
                steps_summary.append({
                    "title": step_obj.name if step_obj else f"Step {sr.step_id}",
                    "status": sr.status,
                    "result": truncate_step_summary(sr.agent_response or "", sr.status or ""),
                })
    except Exception:
        logger.debug("Could not load step results for run %d", run_id)

    try:
        from distr.core.orchestrator import list_validation_records

        validation_records = list_validation_records(run_id=run_id, limit=10)
    except Exception:
        logger.debug("Could not load validation records for run %d", run_id)

    run_result = build_workflow_run_receipt(
        run_id=run_id,
        workflow_id=workflow_id,
        status=status,
        steps_summary=steps_summary,
        board_id=board_id,
        ticket_id=ticket_id,
        project_id=project_id,
        validation_records=validation_records,
        result_packet=result_packet,
    )
    run_result["session_id"] = workflow_id
    try:
        with get_session() as db:
            run_rec = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            if run_rec:
                try:
                    run_data = json.loads(run_rec.run_data or "{}") or {}
                except Exception:
                    run_data = {}
                if not isinstance(run_data, dict):
                    run_data = {}
                run_data["terminal_receipt"] = run_result
                run_rec.run_data = json.dumps(run_data, default=str)
                db.commit()
    except Exception:
        logger.debug("Could not persist terminal receipt for run %d", run_id, exc_info=True)

    # Sync terminal status back to the linked ticket so the board always
    # reflects the actual workflow outcome without waiting for a lane move.
    try:
        with get_session() as db:
            run_rec = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            if run_rec and run_rec.ticket_id:
                from distr.core.db.kanban import KanbanTicket
                ticket = db.query(KanbanTicket).filter(
                    KanbanTicket.id == run_rec.ticket_id
                ).first()
                if ticket:
                    terminal_status = (status or "completed").strip().lower()
                    append_ticket_audit_entry(
                        db,
                        ticket_id=int(run_rec.ticket_id),
                        run_id=run_id,
                        step_id=run_rec.current_step_id,
                        step_result_id=None,
                        execution_lane="workflow",
                        status=terminal_status,
                        final_verdict="pass" if terminal_status == "completed" else "needs_changes",
                        summary=f"Run finished: {terminal_status}",
                        details=f"Workflow run {run_id} finished with status {terminal_status}.",
                    )
                    ticket.workflow_status = status
                    try:
                        from distr.core.kanban.ticket_workflow_engagement import (
                            record_ticket_workflow_elapsed,
                        )

                        warning = ""
                        try:
                            run_data = json.loads(run_rec.run_data or "{}") or {}
                            warning = str(run_data.get("terminal_warning") or "")
                        except Exception:
                            run_data = {}
                        record_ticket_workflow_elapsed(
                            ticket_id=int(run_rec.ticket_id),
                            run_id=run_id,
                            status=status,
                            warning=warning,
                        )
                    except Exception:
                        logger.debug("Could not record ticket workflow elapsed time", exc_info=True)
                    db.commit()
    except Exception:
        logger.debug("Could not sync workflow_status to ticket for run %d", run_id)

    try:
        from distr.core.workflow_engine.agent_bridge import WorkflowAgentBridge
        WorkflowAgentBridge().on_workflow_completed(workflow_id, run_result)
    except Exception:
        logger.error("WorkflowAgentBridge notification failed for run %d", run_id, exc_info=True)

    if status == "completed":
        _maybe_auto_start_next_queued_ticket(run_id, workflow_id)


def _maybe_auto_start_next_queued_ticket(run_id: int, workflow_id: int) -> None:
    """In sequential mode, start the next queued ticket after a successful run."""
    try:
        with get_session() as db:
            run_rec = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
            if not run_rec or not wf or not run_rec.ticket_id:
                return
            run_settings = _workflow_run_settings(wf)
            if run_settings.get("execution_mode") != "sequential":
                return
            from distr.core.db.kanban import KanbanTicket

            current_ticket = db.query(KanbanTicket).filter(
                KanbanTicket.id == int(run_rec.ticket_id)
            ).first()
            if not current_ticket:
                return
            try:
                run_data = json.loads(run_rec.run_data or "{}")
            except Exception:
                run_data = {}
            group_items = (
                run_data.get("ticket_group_items")
                if isinstance(run_data.get("ticket_group_items"), list)
                else []
            )
            group_index = int(run_data.get("ticket_group_index") or 0)
            if group_items and group_index + 1 >= len(group_items):
                return
            if group_items and group_index + 1 < len(group_items):
                next_item = group_items[group_index + 1]
                if not isinstance(next_item, dict) or next_item.get("ticket_id") is None:
                    return
                next_ticket_id = int(next_item["ticket_id"])
                board_id = next_item.get("board_id")
                run_metadata = dict(next_item.get("run_metadata") or {})
                run_metadata.update({
                    "ticket_group_id": run_data.get("ticket_group_id"),
                    "ticket_group_index": group_index + 1,
                    "ticket_group_size": len(group_items),
                    "ticket_group_items": group_items,
                    "auto_queued_from_run_id": run_id,
                })
                group_context = str(next_item.get("context") or "").strip() or None
                next_group_item = {
                    "ticket_id": next_ticket_id,
                    "board_id": int(board_id) if board_id is not None else None,
                    "context": group_context,
                    "run_metadata": run_metadata,
                }
            else:
                next_group_item = None
            current_pos = int(current_ticket.workflow_queue_position or 0)
            next_ticket = None
            if next_group_item is None:
                next_ticket = (
                    db.query(KanbanTicket)
                    .filter(
                        KanbanTicket.linked_workflow_id == workflow_id,
                        KanbanTicket.workflow_queue_position > current_pos,
                    )
                    .order_by(KanbanTicket.workflow_queue_position.asc(), KanbanTicket.id.asc())
                    .first()
                )
            if next_group_item is None and not next_ticket:
                return
            if next_group_item is not None:
                next_ticket_id = next_group_item["ticket_id"]
                board_id = next_group_item["board_id"]
                group_context = next_group_item["context"]
                run_metadata = next_group_item["run_metadata"]
            else:
                next_ticket_id = int(next_ticket.id)
                board_id = run_rec.board_id
                group_context = None
                run_metadata = {
                    "project_id": run_data.get("project_id"),
                    "project_name": run_data.get("project_name"),
                    "project_folder": run_data.get("project_folder"),
                    "ticket_title": next_ticket.title,
                    "auto_queued_from_run_id": run_id,
                }
                run_metadata = {k: v for k, v in run_metadata.items() if v not in (None, "")}

        result = start_workflow_run(
            workflow_id,
            context=group_context,
            board_id=board_id,
            ticket_id=next_ticket_id,
            run_metadata=run_metadata or None,
            dispatch_async=True,
        )
        if result.get("error"):
            logger.info(
                "Queue auto-advance skipped for workflow %s after run %s: %s",
                workflow_id,
                run_id,
                result.get("error"),
            )
            return
        try:
            from distr.gui.web.kanban_events import increment_kanban_updated

            increment_kanban_updated(
                board_id=board_id,
                event_type="queue_auto_advance",
                payload={
                    "workflow_id": workflow_id,
                    "previous_run_id": run_id,
                    "next_run_id": result.get("run_id"),
                    "next_ticket_id": next_ticket_id,
                },
            )
        except Exception:
            logger.debug("Could not emit queue_auto_advance kanban event", exc_info=True)
    except Exception:
        logger.debug("Queue auto-advance failed for run %s", run_id, exc_info=True)


def start_workflow_ticket_group(
    workflow_id: int,
    ticket_items: List[Dict[str, Any]],
    *,
    dispatch_async: bool = True,
) -> Dict[str, Any]:
    """Start an explicit group of ticket runs using the workflow queue policy."""
    normalized: List[Dict[str, Any]] = []
    seen: set[int] = set()
    for raw in ticket_items or []:
        if not isinstance(raw, dict) or raw.get("ticket_id") is None:
            continue
        ticket_id = int(raw["ticket_id"])
        if ticket_id in seen:
            continue
        seen.add(ticket_id)
        normalized.append({
            "ticket_id": ticket_id,
            "board_id": int(raw["board_id"]) if raw.get("board_id") is not None else None,
            "context": str(raw.get("context") or ""),
            "run_metadata": dict(raw.get("run_metadata") or {}),
        })
    if not normalized:
        return {"error": "No valid tickets were supplied"}

    with get_session() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not workflow:
            return {"error": "Workflow not found"}
        run_settings = _workflow_run_settings(workflow)

    group_id = uuid4().hex
    mode = run_settings["execution_mode"]
    to_start = normalized if mode == "parallel" else normalized[:1]
    started: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for index, item in enumerate(to_start):
        metadata = dict(item["run_metadata"])
        metadata.update({
            "ticket_group_id": group_id,
            "ticket_group_index": index,
            "ticket_group_size": len(normalized),
            "ticket_group_items": normalized if mode == "sequential" else [],
        })
        result = start_workflow_run(
            int(workflow_id),
            context=item["context"] or None,
            board_id=item["board_id"],
            ticket_id=item["ticket_id"],
            run_metadata=metadata,
            dispatch_async=dispatch_async,
        )
        if result.get("error"):
            errors.append({"ticket_id": item["ticket_id"], "error": str(result["error"])})
        else:
            started.append({"ticket_id": item["ticket_id"], "run_id": result.get("run_id")})

    return {
        "success": bool(started),
        "group_id": group_id,
        "mode": mode,
        "ticket_count": len(normalized),
        "started": started,
        "errors": errors,
        "queued_count": (len(normalized) - len(started)) if mode == "sequential" and started else 0,
    }


def _clear_workflow_env() -> None:
    """Clear workflow run context environment variables and thread-local entry."""
    os.environ.pop("DECISIONS_WORKFLOW_RUN_ID", None)
    os.environ.pop("DECISIONS_WORKFLOW_STEP_ID", None)
    os.environ.pop("DECISIONS_WORKFLOW_ID", None)
    _clear_workflow_thread_env(threading.get_ident())


# ── Execution-level functions ───────────────────────────────────────
# Thin wrappers that callers (routes, scheduler, ticket board agent) import.


def start_workflow_run(
    workflow_id: int,
    context: Optional[str] = None,
    run_ctx: Optional["WorkflowRunContext"] = None,
    start_step_id: Optional[int] = None,
    board_id: Optional[int] = None,
    ticket_id: Optional[int] = None,
    run_metadata: Optional[Dict[str, Any]] = None,
    event_queue: Optional[Any] = None,
    dispatch_async: bool = False,
) -> Dict[str, Any]:
    """Start a full workflow run.

    Creates a run record, spins up a WorkflowAgent, and dispatches the first step.

    Args:
        context: Free-form context string (legacy — prefer ``run_ctx``).
        run_ctx: Structured context from the parent session, including
                 recent conversation, user intent, and project linkage.
    """
    from distr.core.workflow_agent import WorkflowAgent

    with get_session() as db:
        wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == workflow_id).first()
        if not wf:
            return {"error": "Workflow not found"}
        if not wf.steps:
            return {"error": "Workflow has no steps"}

        run_settings = _workflow_run_settings(wf)
        normalized_metadata = dict(run_metadata or {})
        requested_project_id = str(normalized_metadata.get("project_id") or "") or None
        active_workflow_runs = (
            db.query(AutoWorkflowRun)
            .filter(
                AutoWorkflowRun.workflow_id == workflow_id,
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            )
            .all()
        )
        if run_settings["execution_mode"] == "parallel":
            if len(active_workflow_runs) >= run_settings["max_parallel_tickets"]:
                return {"error": f"Workflow is already running {len(active_workflow_runs)} ticket(s); async limit is {run_settings['max_parallel_tickets']}"}
        elif run_settings["concurrency_scope"] == "workflow" and active_workflow_runs:
            return {"error": "This workflow is set to run one ticket at a time"}
        if run_settings["concurrency_scope"] == "project" and requested_project_id:
            for existing_run in active_workflow_runs:
                if _run_project_id(existing_run) == requested_project_id:
                    return {"error": "This workflow is already running a ticket for this project"}

        active_run = db.query(AutoWorkflowRun).filter(
            AutoWorkflowRun.workflow_id == workflow_id,
            AutoWorkflowRun.status.in_(["running", "waiting"]),
        )
        # Match exact scope including NULLs — unscoped runs (NULL,NULL) must not block
        # board-scoped runs (1, 5), and vice versa (Stage 0 BUG-4).
        if board_id is None:
            active_run = active_run.filter(AutoWorkflowRun.board_id.is_(None))
        else:
            active_run = active_run.filter(AutoWorkflowRun.board_id == board_id)
        if ticket_id is None:
            active_run = active_run.filter(AutoWorkflowRun.ticket_id.is_(None))
        else:
            active_run = active_run.filter(AutoWorkflowRun.ticket_id == ticket_id)
        active_run = active_run.first()
        # Defensive guard for heavily mocked sessions in tests: only treat a result
        # as active when it looks like an AutoWorkflowRun instance.
        if active_run is not None and isinstance(active_run, AutoWorkflowRun):
            # If there is no live RunContext for this run, it's an orphan (e.g. from a
            # previous server session that crashed). Auto-cancel it so the ticket can
            # be pushed again — otherwise it would be blocked forever.
            with _runs_lock:
                is_truly_active = (
                    active_run.id in _active_runs
                    or active_run.id in _initializing_runs
                )
            if is_truly_active:
                return {"error": "A run is already in progress for this board/ticket"}
            logger.info(
                "start_workflow_run: auto-cancelling orphaned run %d (workflow=%d) — no live RunContext",
                active_run.id,
                workflow_id,
            )
            active_run.status = "cancelled"
            active_run.completed_at = utc_now_naive()
            db.flush()

        # Validate all steps before starting
        sorted_steps = sorted(wf.steps, key=lambda s: s.position)
        for step in sorted_steps:
            if step.action_type == "agent_instruction" and (step.instruction is None or step.instruction == ""):
                return {"error": f"Step '{step.name}' (#{step.position}) has no instruction"}
            if step.action_type == "send_to_project_cli" and not (step.instruction or "").strip():
                return {"error": f"Step '{step.name}' (#{step.position}) has no instruction"}
            if step.action_type == "run_command" and not (step.instruction or "").strip() and not (step.code or "").strip() and not (json.loads(step.config) if step.config else {}).get("command", ""):
                return {"error": f"Step '{step.name}' (#{step.position}) has no command configured"}
            if step.action_type == "http_request" and not (json.loads(step.config) if step.config else {}).get("url", ""):
                return {"error": f"Step '{step.name}' (#{step.position}) has no URL configured"}

        first_step = None
        start_idx = 0
        if start_step_id is not None:
            for i, s in enumerate(sorted_steps):
                if s.id == int(start_step_id):
                    first_step = s
                    start_idx = i
                    break
        if first_step is None:
            first_step = sorted_steps[0]
            start_idx = 0

        for i, step in enumerate(sorted_steps):
            if i >= start_idx:
                step.status = "pending"
                step.result = None

        normalized_metadata.setdefault("run_settings", run_settings)
        normalized_metadata.setdefault("workflow_id", workflow_id)
        if ticket_id is not None:
            normalized_metadata.setdefault("ticket_id", ticket_id)
        if (board_id is not None or ticket_id is not None) and not normalized_metadata.get("developer_context"):
            try:
                from distr.core.developer_context import build_developer_context

                developer_context = build_developer_context().to_dict()
                normalized_metadata["developer_context"] = _scope_developer_context_to_run(
                    developer_context,
                    normalized_metadata,
                    board_id=board_id,
                    ticket_id=ticket_id,
                )
            except Exception:
                logger.debug("start_workflow_run: developer context assembly failed", exc_info=True)
        risk_profile = infer_risk_profile((context or ""))
        normalized_metadata.setdefault("risk_profile", risk_profile)
        normalized_metadata.setdefault(
            "validation_rules",
            validation_rules_for_risk(
                risk_profile.get("level", "low"),
                risk_profile.get("signals", []),
            ),
        )
        normalized_metadata.setdefault(
            "result_packet",
            create_initial_result_packet_for_run(
                ticket_id=ticket_id,
                board_id=board_id,
                board_name=normalized_metadata.get("board_name"),
                project_id=normalized_metadata.get("project_id"),
                project_name=normalized_metadata.get("project_name"),
                execution_lane="workflow",
            ),
        )
        loop_input = {}
        if getattr(wf, "workflow_input", None):
            try:
                loop_input = json.loads(wf.workflow_input or "{}") or {}
            except Exception:
                loop_input = {}
        if loop_input.get("goal") or loop_input.get("max_iterations") or loop_input.get("check_command"):
            normalized_metadata["loop_contract"] = loop_input
            normalized_metadata["loop_iteration"] = 0
        if loop_input.get("skip_human_checkpoints"):
            normalized_metadata["skip_human_checkpoints"] = True
        packet = normalized_metadata.get("result_packet") or {}
        packet_audit = dict(packet.get("audit") or {})
        packet_audit["audits_run"] = build_audit_gates(
            status="running",
            risk_level=risk_profile.get("level", "low"),
            tests_passed=True,
        )
        packet["audit"] = packet_audit
        normalized_metadata["result_packet"] = packet
        # A run is visible before its worker/model/tool stack is ready.  Keep
        # that cold-start state explicit so Mission Control and chat never show
        # an unexplained generic "working" spinner.
        normalized_metadata["phase"] = "initializing"

        run = AutoWorkflowRun(
            workflow_id=workflow_id,
            status="running",
            board_id=board_id,
            ticket_id=ticket_id,
            run_data=json.dumps(normalized_metadata),
        )
        db.add(run)
        db.flush()
        run_id = run.id
        project_id = normalized_metadata.get("project_id")
        try:
            from distr.core.workspace_memory.lifecycle import hook_ensure_workspace

            hook_ensure_workspace(
                "runs",
                run_id,
                reason="start_workflow_run",
                run_kwargs={
                    "workflow_id": workflow_id,
                    "board_id": board_id,
                    "ticket_id": ticket_id,
                    "project_id": int(project_id) if project_id else None,
                    "step_id": first_step.id if first_step else None,
                },
            )
        except Exception:
            logger.debug("start_workflow_run: workspace bootstrap failed", exc_info=True)
        loop_contract = dict(normalized_metadata.get("loop_contract") or {})
        workflow_name = str(wf.name or f"Workflow {workflow_id}")
        if ticket_id:
            append_ticket_audit_entry(
                db,
                ticket_id=int(ticket_id),
                run_id=run_id,
                step_id=first_step.id if first_step else None,
                step_result_id=None,
                execution_lane="workflow",
                status="running",
                final_verdict="cannot_determine",
                summary=f"Run started (workflow {workflow_id})",
                details=f"Workflow run {run_id} started.",
            )

        run.current_step_id = first_step.id
        # Keep step at "pending" until StepDispatcher.run_in_workflow runs — otherwise the
        # run_in_workflow idempotency guard sees running + current_step_id match and skips the
        # initial dispatch (start_workflow_run always loads DB then dispatches once).
        first_step_id = first_step.id
        first_step_name = first_step.name
        # Mark the ticket as "running" immediately so the board reflects in-progress state
        if ticket_id:
            from distr.core.db.kanban import KanbanTicket as _KT
            _ticket = db.query(_KT).filter(_KT.id == ticket_id).first()
            if _ticket:
                _ticket.workflow_status = "running"
        db.commit()

    # emit_event uses its own database session. Emit only after the run creation
    # transaction commits; otherwise file-backed SQLite can reject this second
    # writer and silently lose the Mission Control/chat trace event.
    if loop_contract:
        try:
            from distr.core.orchestrator import emit_event

            emit_event(
                source="workflow",
                event_type="loop_started",
                status="running",
                workflow_id=workflow_id,
                run_id=run_id,
                ticket_id=ticket_id,
                board_id=board_id,
                summary=f'Loop started: {loop_contract.get("goal") or workflow_name}',
                payload={
                    "goal": loop_contract.get("goal"),
                    "max_iterations": loop_contract.get("max_iterations"),
                    "check_command": loop_contract.get("check_command"),
                    "exit_when": loop_contract.get("exit_when"),
                },
            )
        except Exception:
            logger.debug("start_workflow_run: loop_started event failed", exc_info=True)

    record_workflow_chat_event(
        run_id,
        "started",
        status="running",
        step_id=first_step_id,
        step_name=first_step_name,
        summary=f"Initializing workflow {workflow_id} worker and tools.",
        phase="initializing",
    )
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source="workflow",
            event_type="workflow_run_started",
            status="running",
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=first_step_id,
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=int(normalized_metadata["project_id"]) if str(normalized_metadata.get("project_id") or "").isdigit() else None,
            summary=f"Started workflow {workflow_id}.",
            payload={
                "workflow_id": workflow_id,
                "first_step_id": first_step_id,
                "first_step_name": first_step_name,
                "run_settings": run_settings,
                "risk_profile": normalized_metadata.get("risk_profile"),
            },
        )
    except Exception:
        logger.debug("Could not emit orchestrator workflow_run_started event", exc_info=True)

    def _initialize_run_context() -> Optional[Dict[str, Any]]:
        """Create heavyweight worker resources outside the caller/UI thread."""
        agent_loop = None
        workflow_agent = None
        try:
            workflow_agent = WorkflowAgent(event_queue=event_queue)
            agent_loop = asyncio.new_event_loop()

            def _run_loop():
                asyncio.set_event_loop(agent_loop)
                agent_loop.run_forever()

            agent_thread = threading.Thread(
                target=_run_loop,
                name=f"workflow-agent-loop-{run_id}",
                daemon=True,
            )
            agent_thread.start()
            with _runs_lock:
                # cancel_run() removes this marker.  Do not resurrect a run that
                # the user cancelled while the model/tool stack was warming up.
                if run_id not in _initializing_runs:
                    cancelled = True
                else:
                    cancelled = False
                    _active_runs[run_id] = _RunContext(
                        run_id=run_id,
                        workflow_agent=workflow_agent,
                        event_loop=agent_loop,
                        thread=agent_thread,
                        context_prefix=context or "",
                        run_ctx=run_ctx,
                    )
                    _initializing_runs.discard(run_id)
            if cancelled:
                workflow_agent.shutdown()
                agent_loop.call_soon_threadsafe(agent_loop.stop)
                return {"error": "Workflow run was cancelled during initialization"}
            return None
        except Exception as exc:
            with _runs_lock:
                _initializing_runs.discard(run_id)
            if workflow_agent is not None:
                try:
                    workflow_agent.shutdown()
                except Exception:
                    pass
            if agent_loop is not None:
                try:
                    agent_loop.call_soon_threadsafe(agent_loop.stop)
                except Exception:
                    pass
            logger.exception("Workflow run %d failed during worker initialization", run_id)
            complete_run(run_id, "failed")
            return {"error": f"Workflow worker initialization failed: {exc}"}

    def _dispatch_first_step() -> Dict[str, Any]:
        _tid = threading.get_ident()
        _set_workflow_thread_env(_tid, run_id, first_step_id, workflow_id)
        # Also keep os.environ for backward compat with tools that read it directly.
        os.environ["DECISIONS_WORKFLOW_RUN_ID"] = str(run_id)
        os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(first_step_id)
        os.environ["DECISIONS_WORKFLOW_ID"] = str(workflow_id)

        dispatcher = StepDispatcher()
        if board_id is not None and ticket_id is not None:
            try:
                increment_kanban_updated(
                    board_id=board_id,
                    event_type="ticket_workflow_status",
                    payload={
                        "ticket_id": int(ticket_id),
                        "run_id": int(run_id),
                        "status": "running",
                        "step_id": int(first_step_id),
                    },
                )
            except Exception:
                logger.debug("Could not emit ticket_workflow_status start event", exc_info=True)
        result = dispatcher.run_in_workflow(first_step_id, run_id)
        if "error" in result:
            _clear_workflow_env()
            complete_run(run_id, "failed")
            return result
        result["run_id"] = run_id
        return result

    def _enter_briefing_wait() -> Optional[Dict[str, Any]]:
        briefing_data = dict(normalized_metadata)
        if ticket_id is not None:
            briefing_data["ticket_id"] = ticket_id
        if ticket_id and not normalized_metadata.get("skip_run_briefing"):
            try:
                from distr.core.workflow.run_briefing import enter_run_briefing_wait, human_checkpoint_enabled

                if human_checkpoint_enabled(briefing_data):
                    briefing_message = enter_run_briefing_wait(run_id, first_step_id)
                    if briefing_message:
                        return {
                            "run_id": run_id,
                            "status": "waiting",
                            "waiting_kind": "run_briefing",
                            "async": bool(dispatch_async),
                        }
            except Exception:
                logger.debug("start_workflow_run: run briefing gate failed", exc_info=True)
        return None

    def _initialize_then_continue() -> Dict[str, Any]:
        init_error = _initialize_run_context()
        if init_error:
            return init_error
        waiting = _enter_briefing_wait()
        if waiting:
            return waiting
        return _dispatch_first_step()

    with _runs_lock:
        _initializing_runs.add(run_id)

    if dispatch_async:
        dispatch_thread = threading.Thread(
            target=_initialize_then_continue,
            name=f"workflow-initialize-{run_id}",
            daemon=True,
        )
        dispatch_thread.start()
        return {
            "run_id": run_id,
            "status": "started",
            "phase": "initializing",
            "async": True,
        }

    return _initialize_then_continue()


def execute_step(step_id: int, isolated: bool = False) -> Dict[str, Any]:
    """Execute a single step via StepDispatcher."""
    dispatcher = StepDispatcher()
    return dispatcher.run_isolated(step_id)


def cancel_run(run_id: int) -> bool:
    """Cancel an active workflow run."""
    active_execution_info = None
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        run.status = "cancelled"
        run.completed_at = utc_now_naive()
        if run.current_step_id:
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == run.current_step_id,
            ).first()
            if step and step.status in ("running", "waiting"):
                step.status = "cancelled"
                step.result = "Cancelled by user."
        _run_id, _wf_id = run.id, run.workflow_id
        try:
            from distr.core.db.kanban import ProjectExecutionSession

            active_execution = (
                db.query(ProjectExecutionSession)
                .filter(ProjectExecutionSession.run_id == run_id)
                .filter(ProjectExecutionSession.status.in_(["queued", "running"]))
                .order_by(ProjectExecutionSession.id.desc())
                .first()
            )
            if active_execution:
                active_execution_info = {
                    "project_id": int(active_execution.project_id),
                    "backend_id": str(active_execution.route_backend or ""),
                    "board_id": int(run.board_id) if run.board_id is not None else None,
                }
            _cancel_linked_execution_sessions(
                db,
                [int(run_id)],
                reason="Cancelled by user.",
                event_type="session_cancelled",
            )
        except Exception:
            logger.debug("Could not locate active provider process for run cancellation", exc_info=True)
        db.commit()
    if active_execution_info:
        try:
            from distr.core.project_cli_backends.registry import terminate_backend_process

            terminate_backend_process(
                active_execution_info["project_id"],
                active_execution_info["backend_id"],
                board_id=active_execution_info["board_id"],
            )
        except Exception:
            logger.debug("Could not terminate provider process for cancelled run", exc_info=True)
    increment_workflow_updated()
    record_workflow_chat_event(
        _run_id,
        "cancelled",
        status="cancelled",
        summary="Workflow run cancelled.",
    )
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source="workflow",
            event_type="workflow_run_cancelled",
            status="cancelled",
            workflow_id=_wf_id,
            run_id=_run_id,
            summary="Workflow run cancelled.",
        )
    except Exception:
        logger.debug("Could not emit orchestrator workflow_run_cancelled event", exc_info=True)
    _finalize_terminal_run(_run_id, _wf_id, "cancelled")
    return True


def cancel_step(step_id: int) -> bool:
    """Cancel a running step."""
    with get_session() as db:
        step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == step_id,
        ).first()
        if not step:
            return False
        step.status = "cancelled"
        step.result = "Cancelled by user."
        db.commit()
    increment_workflow_updated()
    return True


def _dispatch_workflow_step(run_id: int, step_id: int) -> Dict[str, Any]:
    os.environ["DECISIONS_WORKFLOW_RUN_ID"] = str(run_id)
    os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(step_id)
    _update_workflow_thread_step(step_id)
    dispatcher = StepDispatcher()
    return dispatcher.run_in_workflow(int(step_id), int(run_id))


def approve_pre_execution_step(
    run_id: int,
    step_id: int,
    *,
    response_text: str = "",
) -> Dict[str, Any]:
    """Approve a request-scoped gate and dispatch the held step exactly once."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == int(step_id)).first()
        if not run or not step:
            return {"error": "Run or step not found", "status_code": 404}
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        if (
            run.status != "waiting"
            or str(run_data.get("waiting_kind") or "") != "pre_execution_approval"
            or int(run.current_step_id or 0) != int(step_id)
        ):
            return {"error": "Pre-execution approval is no longer pending", "status_code": 409}
        approved = {int(value) for value in (run_data.get("approved_pre_execution_steps") or [])}
        approved.add(int(step_id))
        run_data["approved_pre_execution_steps"] = sorted(approved)
        run_data.pop("waiting_kind", None)
        run_data.pop("waiting_prompt", None)
        if response_text.strip():
            run_data["approval_response"] = response_text.strip()
        run.status = "running"
        step.status = "pending"
        run.run_data = json.dumps(run_data)
        db.commit()
    thread = threading.Thread(
        target=_dispatch_workflow_step,
        args=(int(run_id), int(step_id)),
        name=f"workflow-approval-{run_id}-{step_id}",
        daemon=True,
    )
    thread.start()
    return {
        "success": True,
        "action": "dispatch_step",
        "queued": True,
        "run_id": int(run_id),
        "step_id": int(step_id),
    }


def _handle_run_briefing_response(run_id: int, optional_input: str) -> Dict[str, Any]:
    from distr.core.workflow.run_briefing import (
        build_run_briefing_message,
        classify_human_workflow_response,
        enter_run_briefing_wait,
        gather_run_briefing_context,
    )

    action = classify_human_workflow_response(optional_input, waiting_kind="run_briefing")
    if action == "stop":
        cancel_run(run_id)
        return {"success": True, "action": "end_run", "status": "cancelled"}

    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        first_step_id = int(run_data.get("pending_first_step_id") or run.current_step_id or 0)
        workflow_id = int(run.workflow_id)

    if action == "steer" and optional_input.strip():
        try:
            from distr.core.workflow.steering_memory import record_run_steering_feedback

            record_run_steering_feedback(
                run_id=run_id,
                message=optional_input.strip(),
                source="workflow",
                event_type="run_briefing_steering",
                workflow_id=workflow_id,
            )
        except Exception:
            logger.debug("run briefing steering capture failed", exc_info=True)
        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if run and run.run_data:
                try:
                    run_data = json.loads(run.run_data or "{}") or {}
                except Exception:
                    run_data = {}
                existing = (run_data.get("run_briefing_steering") or "").strip()
                merged = f"{existing}\n{optional_input.strip()}".strip() if existing else optional_input.strip()
                run_data["run_briefing_steering"] = merged[:2000]
                run.run_data = json.dumps(run_data)
                db.commit()
        enter_run_briefing_wait(run_id, first_step_id)
        ctx = gather_run_briefing_context(run_id)
        message = build_run_briefing_message(ctx) if ctx else ""
        return {
            "success": True,
            "action": "waiting",
            "waiting_kind": "run_briefing",
            "message": message or "Updated the plan. Tell me when to begin.",
        }

    if not first_step_id:
        return {"error": "No first step configured for this run", "status_code": 409}

    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if run:
            try:
                run_data = json.loads(run.run_data or "{}") or {}
            except Exception:
                run_data = {}
            run.status = "running"
            run_data.pop("waiting_kind", None)
            run_data.pop("pending_first_step_id", None)
            run.run_data = json.dumps(run_data)
            db.commit()

    os.environ["DECISIONS_WORKFLOW_ID"] = str(workflow_id)
    dispatch_result = _dispatch_workflow_step(run_id, first_step_id)
    if "error" in dispatch_result:
        complete_run(run_id, "failed")
        return dispatch_result
    record_workflow_chat_event(
        run_id,
        "resumed",
        status="running",
        step_id=first_step_id,
        summary="Run confirmed. Starting first step.",
    )
    return {
        "success": True,
        "action": "next_step",
        "step_id": first_step_id,
        "dispatch": dispatch_result,
    }


def _handle_step_review_response(run_id: int, optional_input: str) -> Dict[str, Any]:
    from distr.core.workflow.run_briefing import classify_human_workflow_response
    from distr.core.workflow.router import StepRouter

    action = classify_human_workflow_response(optional_input, waiting_kind="step_review")
    if action == "stop":
        cancel_run(run_id)
        return {"success": True, "action": "end_run", "status": "cancelled"}

    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == int(run.current_step_id or 0),
        ).first()
        if not step:
            return {"error": "No waiting step found", "status_code": 409}
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        next_step_id = int(run_data.get("pending_next_step_id") or 0)
        step_id = int(step.id)
        stored_result = str(run_data.get("step_review_result") or "")
        stored_passed = bool(run_data.get("step_review_passed", True))

    if action == "steer" and optional_input.strip():
        router = StepRouter()
        decision = router.resume_from_feedback(step_id, run_id, optional_input)
        action_name = (decision or {}).get("action")
        if action_name == "next_step" and decision.get("step_id"):
            try:
                dispatch_result = _dispatch_workflow_step(run_id, int(decision["step_id"]))
                return {
                    "success": True,
                    "action": "next_step",
                    "step_id": int(decision["step_id"]),
                    "dispatch": dispatch_result,
                }
            except Exception as exc:
                logger.error("step review steer dispatch failed: %s", exc, exc_info=True)
                return {"error": f"Failed to dispatch next step: {exc}", "status_code": 500}
        if action_name == "end_run":
            status = decision.get("status", "completed")
            complete_run(run_id, status)
            return {"success": True, "action": "end_run", "status": status}
        return decision

    if not next_step_id:
        return {"error": "No pending next step found", "status_code": 409}

    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if run:
            try:
                run_data = json.loads(run.run_data or "{}") or {}
            except Exception:
                run_data = {}
            run.status = "running"
            step_row = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step_row and step_row.status == "waiting":
                step_row.status = "passed" if stored_passed else "failed"
            run_data.pop("waiting_kind", None)
            run_data.pop("pending_next_step_id", None)
            run_data.pop("step_review_text", None)
            run_data.pop("step_review_result", None)
            run_data.pop("step_review_passed", None)
            run.run_data = json.dumps(run_data)
            db.commit()

    dispatch_result = _dispatch_workflow_step(run_id, next_step_id)
    if "error" in dispatch_result:
        complete_run(run_id, "failed")
        return dispatch_result
    record_workflow_chat_event(
        run_id,
        "resumed",
        status="running",
        step_id=next_step_id,
        summary="Continuing to the next step.",
    )
    return {
        "success": True,
        "action": "next_step",
        "step_id": next_step_id,
        "dispatch": dispatch_result,
    }


def continue_waiting_step(run_id: int, optional_input: str = "") -> Dict[str, Any]:
    """Resume a workflow run that is in 'waiting' status."""
    waiting_kind = ""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        if run.status != "waiting":
            return {"error": f"Run is not waiting (status: {run.status})", "status_code": 409}
        if run.run_data:
            try:
                waiting_kind = (json.loads(run.run_data or "{}") or {}).get("waiting_kind") or ""
            except Exception:
                waiting_kind = ""

    if waiting_kind == "run_briefing":
        return _handle_run_briefing_response(run_id, optional_input)
    if waiting_kind == "step_review":
        return _handle_step_review_response(run_id, optional_input)

    from distr.core.workflow.router import StepRouter

    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == run.current_step_id,
        ).first()
        if not step or step.status != "waiting":
            return {"error": "No waiting step found", "status_code": 409}
        step_id = step.id

    router = StepRouter()
    decision = router.resume_from_feedback(step_id, run_id, optional_input)
    record_workflow_chat_event(
        run_id,
        "resumed",
        status="running",
        step_id=step_id,
        summary="Workflow resumed with user input." if optional_input else "Workflow resumed.",
    )
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source="workflow",
            event_type="workflow_run_resumed",
            status="running",
            run_id=run_id,
            step_id=step_id,
            summary="Workflow resumed with user input." if optional_input else "Workflow resumed.",
            payload={"feedback": optional_input or ""},
        )
    except Exception:
        logger.debug("Could not emit orchestrator workflow_run_resumed event", exc_info=True)

    if waiting_kind == "approval":
        try:
            from distr.core.orchestration_events import emit_orchestration_event

            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            emit_orchestration_event(
                source="approval",
                event_type="route_approval_granted",
                status="granted",
                run_id=run_id,
                step_id=step_id,
                workflow_id=getattr(run, "workflow_id", None) if run else None,
                ticket_id=getattr(run, "ticket_id", None) if run else None,
                board_id=getattr(run, "board_id", None) if run else None,
                summary=f"Step #{step_id} approved; workflow resumed.",
                payload={"feedback": optional_input or ""},
            )
        except Exception:
            logger.debug("Could not emit approval_granted event", exc_info=True)
    if optional_input and optional_input.strip():
        try:
            from distr.core.orchestration_events import emit_orchestration_event
            from distr.core.workflow.steering_memory import record_run_steering_feedback

            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            board_id = getattr(run, "board_id", None) if run else None
            workflow_id = getattr(run, "workflow_id", None) if run else None
            ticket_id = getattr(run, "ticket_id", None) if run else None
            project_id = None
            if run and run.run_data:
                try:
                    project_id = (json.loads(run.run_data or "{}") or {}).get("project_id")
                except Exception:
                    project_id = None
            event_type = "ide_iteration_completed" if waiting_kind == "ide_handoff" else "user_continuation"
            emit_orchestration_event(
                source="ide" if waiting_kind == "ide_handoff" else "workflow",
                event_type=event_type,
                status="completed",
                run_id=run_id,
                step_id=step_id,
                workflow_id=workflow_id,
                ticket_id=ticket_id,
                board_id=board_id,
                summary=(optional_input or "")[:500],
                payload={"feedback": optional_input or "", "waiting_kind": waiting_kind},
            )
            record_run_steering_feedback(
                run_id=run_id,
                message=optional_input.strip(),
                source="ide" if waiting_kind == "ide_handoff" else "workflow",
                event_type=event_type,
                workflow_id=workflow_id,
                step_id=step_id,
                board_id=board_id,
                ticket_id=ticket_id,
                project_id=int(project_id) if str(project_id or "").isdigit() else None,
                rule_type="ide_iteration" if waiting_kind == "ide_handoff" else "workflow_steering",
            )
        except Exception:
            logger.debug("Could not record workflow steering feedback", exc_info=True)

    # Resume must continue execution, not only update run state.
    action = (decision or {}).get("action")
    if action == "next_step":
        next_step_id = decision.get("step_id")
        wait_before = int(decision.get("wait_before_next") or 0)
        if next_step_id:
            try:
                from distr.core.workflow.run_briefing import maybe_pause_before_next_step

                with get_session() as db:
                    run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                    run_data = json.loads(run.run_data or "{}") if run and run.run_data else {}
                stored_result = str(run_data.get("waiting_result") or "")
                stored_passed = bool(run_data.get("waiting_passed", True))
                if maybe_pause_before_next_step(
                    run_id=run_id,
                    completed_step_id=step_id,
                    passed=stored_passed,
                    result_text=stored_result or optional_input,
                    next_step_id=int(next_step_id),
                ):
                    return {
                        "success": True,
                        "action": "waiting",
                        "waiting_kind": "step_review",
                    }
            except Exception:
                logger.debug("step review checkpoint on resume failed", exc_info=True)
            try:
                if wait_before > 0:
                    import time
                    time.sleep(wait_before)
                os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(next_step_id)
                _update_workflow_thread_step(next_step_id)
                dispatcher = StepDispatcher()
                dispatch_result = dispatcher.run_in_workflow(int(next_step_id), int(run_id))
                return {
                    "success": True,
                    "action": "next_step",
                    "step_id": int(next_step_id),
                    "dispatch": dispatch_result,
                }
            except Exception as e:
                logger.error("continue_waiting_step dispatch failed: %s", e, exc_info=True)
                try:
                    complete_run(run_id, "failed")
                except Exception:
                    pass
                return {"error": f"Failed to dispatch next step: {e}", "status_code": 500}
    elif action == "end_run":
        status = decision.get("status", "completed")
        complete_run(run_id, status)
        return {"success": True, "action": "end_run", "status": status}

    return decision


def complete_run(run_id: int, status: str = "completed") -> bool:
    """Mark a run as terminal and clean up resources."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        try:
            run_data = json.loads(run.run_data or "{}")
        except Exception:
            run_data = {}
        packet = dict(run_data.get("result_packet") or {})
        risk_profile = dict(run_data.get("risk_profile") or {})
        workflow_id = run.workflow_id
        board_id = run.board_id
        ticket_id = run.ticket_id
        project_id = run_data.get("project_id")
        execution_session_id = run_data.get("execution_session_id")
        auto_retry: Dict[str, Any] | None = None
        if packet:
            packet = _finalize_result_packet_for_terminal_run(
                packet,
                run_id=run_id,
                status=status,
                risk_profile=risk_profile,
            )
            packet = _record_packet_ui_quality_validation(
                packet,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=getattr(run, "current_step_id", None),
                ticket_id=ticket_id,
                board_id=board_id,
                project_id=int(project_id) if str(project_id or "").isdigit() else None,
                execution_session_id=int(execution_session_id) if str(execution_session_id or "").isdigit() else None,
            )
            e2e_smoke = _is_e2e_smoke_workflow(workflow_id)
            if status == "completed" and _packet_has_failed_ui_quality_validation(packet) and not e2e_smoke:
                status = "failed"
                auto_retry = _maybe_auto_dispatch_terminal_ui_correction(
                    db,
                    run=run,
                    packet=packet,
                    run_data=run_data,
                )
                if auto_retry:
                    status = "running"
            if e2e_smoke:
                enforced_status, updated_packet, missing_checks = status, packet, []
            else:
                enforced_status, updated_packet, missing_checks = enforce_validation_requirements(
                    packet=packet,
                    run_status=status,
                    risk_profile=risk_profile,
                )
            from distr.core.workflow.dogfood_gate import enforce_dogfood_exit_gate

            dogfood_status, updated_packet, dogfood_missing = enforce_dogfood_exit_gate(
                packet=updated_packet,
                run_status=enforced_status or status,
                workflow_id=workflow_id,
            )
            if dogfood_missing:
                missing_checks = list(missing_checks or []) + dogfood_missing
                logger.info(
                    "complete_run: dogfood gate for run %s, missing=%s",
                    run_id,
                    ",".join(dogfood_missing),
                )
            status = dogfood_status or enforced_status or status
            run_data["result_packet"] = updated_packet
            run.run_data = json.dumps(run_data)
        run.status = status
        run.completed_at = None if auto_retry else utc_now_naive()
        db.commit()
    increment_workflow_updated()
    if auto_retry:
        try:
            os.environ["DECISIONS_WORKFLOW_STEP_ID"] = str(auto_retry["step_id"])
            _update_workflow_thread_step(int(auto_retry["step_id"]))
            dispatcher = StepDispatcher()
            dispatcher.run_in_workflow(int(auto_retry["step_id"]), int(run_id))
        except Exception:
            logger.error("Auto-dispatched terminal correction failed for run %s", run_id, exc_info=True)
            try:
                complete_run(run_id, "failed")
            except Exception:
                pass
        return True
    try:
        if status == "completed" and workflow_id and isinstance(run_data, dict):
            from distr.core.workspace_memory.demo_artifact import write_demo_artifact
            from distr.core.workspace_memory.pickup_handoff import read_handoff_preview

            packet = run_data.get("result_packet") or {}
            ticket_title = ""
            if ticket_id:
                try:
                    from distr.core.db.kanban import KanbanTicket

                    with get_session() as db:
                        t = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
                        if t:
                            ticket_title = t.title or ""
                except Exception:
                    pass
            write_demo_artifact(
                workflow_id=int(workflow_id),
                run_id=run_id,
                ticket_title=ticket_title,
                handoff_summary=read_handoff_preview("runs", run_id),
                result_packet=packet if isinstance(packet, dict) else {},
            )
        if status == "failed" and workflow_id and isinstance(run_data, dict):
            packet = run_data.get("result_packet") or {}
            steps = packet.get("steps") or []
            last_fail = next(
                (s for s in reversed(steps) if str(s.get("status") or "").lower() == "failed"),
                None,
            )
            if last_fail and "playwright" in str(last_fail.get("step_name") or "").lower():
                from distr.core.workflow.dogfood_gate import create_playwright_failure_followup_ticket

                evidence = str(last_fail.get("result") or last_fail.get("step_result") or "")[:4000]
                create_playwright_failure_followup_ticket(
                    run_id=run_id,
                    workflow_id=workflow_id,
                    ticket_id=ticket_id,
                    board_id=board_id,
                    evidence=evidence,
                )
    except Exception:
        logger.debug("demo artifact / playwright follow-up failed for run %s", run_id, exc_info=True)
    try:
        if workflow_id and isinstance(run_data, dict):
            from distr.core.db.projects import Project
            from distr.core.db.workflow import AutoWorkflow
            from distr.core.workflow.skill_provision import provision_workflow_skills

            project_id = run_data.get("project_id")
            route = run_data.get("execution_route") or {}
            backend_id = route.get("backend") or route.get("backend_id") or "pi"
            with get_session() as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
                project = None
                if project_id:
                    project = db.query(Project).filter(Project.id == int(project_id)).first()
                if wf and project and getattr(project, "folder_location", None):
                    provision_workflow_skills(
                        workflow=wf,
                        project_folder=project.folder_location,
                        backend_id=str(backend_id),
                        chain_type="post_chain",
                        run_id=run_id,
                        workflow_id=workflow_id,
                        ticket_id=ticket_id,
                        board_id=board_id,
                        project_id=getattr(project, "id", None),
                    )
    except Exception:
        logger.debug("Could not provision post_chain skills for run %s", run_id, exc_info=True)
    record_workflow_chat_event(
        run_id,
        "completed",
        status=status,
        summary=f"Workflow run finished with status: {status}.",
    )
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source="workflow",
            event_type="workflow_run_completed",
            status=status,
            workflow_id=workflow_id,
            run_id=run_id,
            ticket_id=ticket_id,
            board_id=board_id,
            summary=f"Workflow run finished with status: {status}.",
        )
        increment_workflow_updated()
    except Exception:
        logger.debug("Could not emit orchestrator workflow_run_completed event", exc_info=True)
    try:
        if board_id is not None:
            from distr.gui.web.kanban_events import increment_kanban_updated
            increment_kanban_updated(board_id=board_id, event_type="run_completed", payload={
                "run_id": run_id,
                "ticket_id": ticket_id,
                "status": status,
            })
    except Exception:
        logger.debug("Could not emit run_completed kanban event", exc_info=True)
    _finalize_terminal_run(run_id, workflow_id, status)
    _clear_workflow_env()
    return True


class StepDispatcher(PostExecutionMixin, StepExecutorMixin):
    """Execute a single workflow step.

    Two public entry points:
    - ``run_isolated(step_id)`` — run one step, record result, done.
    - ``run_in_workflow(step_id, run_id)`` — run step, then hand off to StepRouter.

    Internal method groups (for future extraction):
    - Validation  : _validate_before_dispatch, _build_config
    - Execution   : _execute, _run_code/_run_playwright/_run_code_type,
                    _run_command, _run_http, _run_send_to_project_cli,
                    _run_recording, _run_agent
    - Post-exec   : _record_result_and_route, _record_result,
                    _notify_isolated_step_result, _enter_wait_state,
                    _append_workflow_step_audit
    - Helpers     : _load_step, _set_run_phase, _build_agent_prompt,
                    _resolve_recording_name, _get_run_context,
                    _generate_code, _set_status, _fail_step,
                    _augment_agent_result_with_tool_evidence
    """

    def __init__(self):
        # Guard against double-dispatch when a step completes via concurrent
        # callbacks (e.g. timeout watchdog fires at the same time as success).
        self._routed_steps: set = set()
        self._routed_lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────────

    def run_isolated(self, step_id: int) -> Dict[str, Any]:
        """Execute one step in isolation. No routing afterwards."""
        with _isolated_step_lock:
            if step_id in _isolated_steps_in_progress:
                logger.warning("run_isolated deduped: step_id=%s already in progress", step_id)
                return {"error": "Step execution already in progress"}
            _isolated_steps_in_progress.add(step_id)

        step_data = self._load_step(step_id)
        try:
            if "error" in step_data:
                return step_data
            logger.info("run_isolated: starting step_id=%s action_type=%s", step_id, step_data.get("action_type"))
            errors = self._validate_before_dispatch(step_data)
            if errors:
                error_text = f"Validation failed: {errors}"
                self._fail_step(step_id, error_text)
                self._notify_isolated_step_result(
                    step_data=step_data,
                    passed=False,
                    result_text=error_text,
                )
                return {"error": errors}
            self._set_status(step_id, "running")
            result = self._execute(step_data, run_id=None)
            if result.get("async"):
                return {"success": True, "message": result.get("message", "Step dispatched.")}
            self._record_result(
                step_id,
                run_id=None,
                result_text=result.get("output", ""),
                passed=result.get("passed", False),
                skip_wait=result.get("skip_wait", False),
            )
            self._notify_isolated_step_result(
                step_data=step_data,
                passed=bool(result.get("passed", False)),
                result_text=result.get("output", "") or ("Step completed." if result.get("passed", False) else "Step failed with no output."),
            )
            logger.info(
                "run_isolated: finished step_id=%s status=%s",
                step_id,
                "passed" if result.get("passed") else "failed",
            )
            return {"success": True, "status": "passed" if result.get("passed") else "failed"}
        finally:
            with _isolated_step_lock:
                _isolated_steps_in_progress.discard(step_id)

    def run_in_workflow(self, step_id: int, run_id: int) -> Dict[str, Any]:
        """Execute step within a workflow run, then hand off to StepRouter."""
        # Idempotency guard: if this exact run/step is already actively executing,
        # treat duplicate dispatches as no-ops (UI double-click, duplicate callback,
        # or concurrent orchestration paths).
        try:
            with get_session() as db:
                step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                if not run or run.status not in ("running", "waiting"):
                    logger.info(
                        "run_in_workflow blocked terminal run dispatch: run_id=%s step_id=%s status=%s",
                        run_id,
                        step_id,
                        getattr(run, "status", "missing"),
                    )
                    return {
                        "success": False,
                        "status": getattr(run, "status", "missing"),
                        "cancelled": getattr(run, "status", "") == "cancelled",
                        "message": "Run is terminal; step dispatch was suppressed.",
                    }
                if step and run and run.current_step_id == step_id and step.status == "running":
                    logger.warning(
                        "run_in_workflow deduped: run_id=%s step_id=%s already running",
                        run_id,
                        step_id,
                    )
                    return {"success": True, "message": "Step already in progress.", "deduped": True}
        except Exception:
            logger.debug("run_in_workflow dedupe check failed", exc_info=True)

        step_data = self._load_step(step_id)
        if "error" in step_data:
            return step_data
        if self._enter_requested_pre_execution_gate(step_data, run_id):
            return {
                "success": True,
                "status": "waiting",
                "waiting_kind": "pre_execution_approval",
                "run_id": run_id,
                "step_id": step_id,
            }
        self._set_run_phase(run_id, step_data)
        emit_step_activity(
            run_id=run_id,
            step_id=step_id,
            event_type="workflow_step_started",
            status="running",
            summary=f"Started {step_data.get('name') or f'step {step_id}'}.",
            payload={
                "step_name": step_data.get("name"),
                "action_type": step_data.get("action_type"),
                "position": step_data.get("position"),
            },
        )
        errors = self._validate_before_dispatch(step_data)
        if errors:
            self._fail_step(step_id, f"Validation failed: {errors}")
            emit_step_activity(
                run_id=run_id,
                step_id=step_id,
                event_type="workflow_step_preflight_failed",
                status="failed",
                summary=f"Validation failed: {errors}",
                payload={"phase": "validation", "error": errors},
            )
            record_workflow_chat_event(
                run_id,
                "step_failed",
                status="failed",
                step_id=step_id,
                step_name=step_data.get("name"),
                summary=f"Validation failed: {errors}",
            )
            return {"error": errors}
        preflight = build_step_preflight(step_data, run_id)
        emit_step_activity(
            run_id=run_id,
            step_id=step_id,
            event_type="workflow_step_preflight",
            status="passed" if preflight.get("ok") else "failed",
            summary=preflight.get("summary") or "Preflight checked.",
            payload=preflight,
        )
        if not preflight.get("ok"):
            error_text = preflight.get("summary") or "Preflight failed."
            failed_checks = [
                str(item.get("message") or item.get("name") or "")
                for item in preflight.get("checks", [])
                if not item.get("ok")
            ]
            if failed_checks:
                error_text = f"{error_text} {'; '.join(failed_checks)}"
            self._fail_step(step_id, error_text)
            record_workflow_chat_event(
                run_id,
                "step_failed",
                status="failed",
                step_id=step_id,
                step_name=step_data.get("name"),
                summary=error_text,
            )
            return {"error": error_text, "preflight": preflight}
        self._set_status(step_id, "running")
        record_workflow_chat_event(
            run_id,
            "step_started",
            status="running",
            step_id=step_id,
            step_name=step_data.get("name"),
            summary=f"Started {step_data.get('name') or f'step {step_id}'}.",
            phase=step_data.get("phase"),
        )
        try:
            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                if run and run.ticket_id:
                    append_ticket_audit_entry(
                        db,
                        ticket_id=int(run.ticket_id),
                        run_id=run_id,
                        step_id=step_id,
                        step_result_id=None,
                        execution_lane="workflow",
                        status="running",
                        final_verdict="cannot_determine",
                        summary=f"{step_data.get('name') or f'Step {step_id}'} started",
                        details=f"Step {step_id} entered running state.",
                    )
                    db.commit()
        except Exception:
            logger.debug("Could not write step-start audit entry", exc_info=True)
        try:
            from distr.core.kanban.ticket_workflow_engagement import notify_ticket_workflow_step_started

            notify_ticket_workflow_step_started(run_id, step_id)
        except Exception:
            logger.debug("Could not send ticket workflow step-start engagement", exc_info=True)
        try:
            result = self._execute(step_data, run_id=run_id)
        except Exception as exc:
            # A backend adapter bug must become a terminal step result. Letting it
            # escape leaves the run and ticket marked "running" until restart,
            # which presents as a forever spinner in mission control.
            error_text = f"Step execution failed unexpectedly: {exc}"
            logger.exception("Workflow step crashed run_id=%s step_id=%s", run_id, step_id)
            record_workflow_chat_event(
                run_id,
                "step_failed",
                status="failed",
                step_id=step_id,
                step_name=step_data.get("name"),
                summary=error_text,
            )
            emit_step_activity(
                run_id=run_id,
                step_id=step_id,
                event_type="workflow_step_crashed",
                status="failed",
                summary=error_text,
                payload={"error": str(exc), "phase": "execution"},
            )
            self._record_result_and_route(
                step_id,
                run_id=run_id,
                result_text=error_text,
                passed=False,
                skip_wait=True,
            )
            return {"error": error_text, "status": "failed", "passed": False}
        if result.get("async"):
            return {"success": True, "message": result.get("message", "Step dispatched.")}
        record_workflow_chat_event(
            run_id,
            "step_completed" if result.get("passed") else "step_failed",
            status="passed" if result.get("passed") else "failed",
            step_id=step_id,
            step_name=step_data.get("name"),
            summary=result.get("output", "") or ("Step completed." if result.get("passed") else "Step failed."),
        )
        self._record_result_and_route(
            step_id,
            run_id=run_id,
            result_text=result.get("output", ""),
            passed=result.get("passed", False),
            skip_wait=result.get("skip_wait", False),
        )
        # If step entered waiting state, return early — routing handled by wait state
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step and should_pause_after_step(
                run_id=run_id,
                step_wait_for_continue=bool(step.wait_for_continue),
                skip_wait=bool(result.get("skip_wait", False)),
            ) and step.status == "waiting":
                return {"success": True, "status": "waiting",
                        "output": result.get("output", ""), "passed": result.get("passed", False)}
        return {
            "success": True, "status": "passed" if result.get("passed") else "failed",
            "output": result.get("output", ""), "passed": result.get("passed", False),
        }

    def _set_run_phase(self, run_id: int, step_data: Dict[str, Any]) -> None:
        """Update run_data with current phase for progress reporting."""
        try:
            workflow_id = step_data.get("workflow_id")
            position = int(step_data.get("position", 0))
            name = (step_data.get("name") or "").strip().lower()
            total_steps = 0
            with get_session() as db:
                if workflow_id is not None:
                    total_steps = db.query(AutoWorkflowStep).filter(
                        AutoWorkflowStep.workflow_id == workflow_id).count()
                phase = "execution"
                if "plan" in name:
                    phase = "planning"
                elif "valid" in name or "verify" in name or "test" in name:
                    phase = "validation"
                elif total_steps > 0:
                    if position <= 0:
                        phase = "planning"
                    elif position >= total_steps - 1:
                        phase = "validation"

                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
                if not run:
                    return
                run_data = json.loads(run.run_data or "{}")
                run_data["phase"] = phase
                run_data["current_step_id"] = step_data.get("id")
                run_data["current_step_name"] = step_data.get("name")
                run.run_data = json.dumps(run_data)
                board_id = run.board_id
                ticket_id = run.ticket_id
                db.commit()
            try:
                if board_id is not None:
                    from distr.gui.web.kanban_events import increment_kanban_updated
                    increment_kanban_updated(board_id=board_id, event_type="run_phase", payload={
                        "run_id": run_id,
                        "ticket_id": ticket_id,
                        "phase": phase,
                        "step_id": step_data.get("id"),
                        "step_name": step_data.get("name"),
                    })
            except Exception:
                logger.debug("Could not emit run_phase kanban event", exc_info=True)
        except Exception:
            logger.debug("Could not update run phase for run %s", run_id, exc_info=True)

    # ── Step loading ────────────────────────────────────────────────

    def _load_step(self, step_id: int) -> Dict[str, Any]:
        """Load step data from the database."""
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if not step:
                return {"error": "Step not found"}
            return {
                "id": step.id, "name": step.name,
                "description": step.description or "",
                "position": step.position,
                "action_type": step.action_type or "agent_instruction",
                "step_type": step.step_type or step.action_type or "agent_instruction",
                "instruction": step.instruction or "",
                "code": step.code or "",
                "recording_filename": step.recording_filename or "",
                "action_id": step.action_id,
                "workflow_id": step.workflow_id,
                "config": json.loads(step.config) if step.config else {},
                "wait_for_continue": step.wait_for_continue or False,
                "timeout_seconds": step.timeout_seconds or 300,
                "max_retries": step.max_retries or 0,
                "require_approval": step.require_approval or False,
                "validation_type": step.validation_type or "none",
                "validation_prompt": step.validation_prompt or "",
                "verification": step.verification or "",
                "routing_mode": step.routing_mode or "static",
                "routing_prompt": step.routing_prompt or "",
                "on_pass_goto": step.on_pass_goto,
                "on_fail_goto": step.on_fail_goto,
                "wait_before_next": step.wait_before_next or 0,
            }

    def _enter_requested_pre_execution_gate(
        self,
        step_data: Dict[str, Any],
        run_id: int,
    ) -> bool:
        """Hold an explicitly protected role before any side effect executes."""
        from distr.core.work_intake.execution_policy import infer_step_role

        role = infer_step_role(step_data)
        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run:
                return False
            try:
                run_data = json.loads(getattr(run, "run_data", None) or "{}") or {}
            except Exception:
                run_data = {}
            policy = run_data.get("requested_execution_policy")
            policy = policy if isinstance(policy, dict) else {}
            protected = {
                str(value).strip().lower()
                for value in (policy.get("approval_before_roles") or [])
            }
            approved = {int(value) for value in (run_data.get("approved_pre_execution_steps") or [])}
            if role not in protected or int(step_data["id"]) in approved:
                return False
            if (
                run.status == "waiting"
                and str(run_data.get("waiting_kind") or "") == "pre_execution_approval"
                and int(getattr(run, "current_step_id", None) or 0) == int(step_data["id"])
            ):
                return True
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == int(step_data["id"])
            ).first()
            if not step:
                return False
            run.status = "waiting"
            run.current_step_id = int(step.id)
            step.status = "waiting"
            run_data["waiting_kind"] = "pre_execution_approval"
            run_data["waiting_prompt"] = (
                f"Approve before {step.name or role}. No {role} action has run yet."
            )
            run.run_data = json.dumps(run_data)
            db.commit()
            workflow_id = int(run.workflow_id)
            ticket_id = run.ticket_id

        record_workflow_chat_event(
            run_id,
            "waiting",
            status="waiting",
            step_id=int(step_data["id"]),
            step_name=step_data.get("name"),
            summary=f"Waiting for approval before {step_data.get('name') or role}.",
        )
        try:
            from distr.core.kanban.ticket_workflow_engagement import notify_ticket_workflow_progress

            notify_ticket_workflow_progress(
                run_id=run_id,
                step_id=int(step_data["id"]),
                body=(
                    f"Run #{run_id} is ready for {step_data.get('name') or role}. "
                    f"Approve to proceed or Stop to cancel. No {role} action has run yet."
                ),
                state_fingerprint=f"pre-execution-approval:{run_id}:{step_data['id']}",
                priority="high",
                requires_response=True,
            )
        except Exception:
            logger.warning("Could not notify pre-execution approval run=%s", run_id, exc_info=True)
        try:
            from distr.core.orchestrator import emit_approval_event

            emit_approval_event(
                event_type="approval_requested",
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=int(step_data["id"]),
                ticket_id=ticket_id,
                summary=f"Approval required before {step_data.get('name') or role}.",
                payload={"step_role": role, "pre_execution": True},
            )
        except Exception:
            logger.debug("Could not emit pre-execution approval event", exc_info=True)
        return True

    # ── Validation ──────────────────────────────────────────────────

    def _validate_before_dispatch(self, step_data: Dict[str, Any]) -> Optional[str]:
        """Validate step config before execution. Returns error string or None."""
        return _validate_step(step_data)

    def _build_config(self, step_data: Dict[str, Any]) -> dict:
        """Build a config dict suitable for StepValidator from step data."""
        return build_step_config(step_data)

    # ── Execution, step-type handlers, agent ── see step_executor.py
    # ── Post-execution: routing, recording, notifications ── see post_execution.py

    def _set_status(self, step_id: int, status: str, result: Optional[str] = None) -> None:
        """Update step status in DB and push websocket notification."""
        with get_session() as db:
            step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
            if step:
                step.status = status
                if result is not None:
                    step.result = result
                db.commit()
        increment_workflow_updated()

    def _fail_step(self, step_id: int, error_msg: str) -> None:
        """Mark step as failed with error message."""
        self._set_status(step_id, "failed", result=error_msg)
