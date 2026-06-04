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
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from distr.core.db import get_session
from distr.core.workflow_engine.context_assembly import WorkflowRunContext
from distr.core.db.workflow import (
    AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun,
    AutoWorkflowStepResult,
)
from distr.core.workflow.verification import _run_verification
from distr.core.workflow.context_limits import truncate_step_summary
from distr.core.kanban.result_packet import (
    build_result_packet,
    format_result_packet_note,
    create_initial_result_packet_for_run,
)
from distr.core.kanban.evidence import format_evidence_block
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
    "max_correction_attempts": 1,
    "auto_dispatch_corrections": False,
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
    try:
        settings["max_correction_attempts"] = max(0, min(5, int(settings.get("max_correction_attempts") or 1)))
    except Exception:
        settings["max_correction_attempts"] = 1
    settings["auto_dispatch_corrections"] = bool(settings.get("auto_dispatch_corrections"))
    return settings


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
_runs_lock = threading.Lock()


def _append_workflow_summary_to_ticket(ticket, run_id: int, status: str, steps_summary: List[dict]) -> None:
    """Append a bounded workflow completion note to a ticket description.

    Keeps user-visible ticket history without storing unbounded step output.
    """
    try:
        status_label = (status or "unknown").strip().lower()
        step_lines: List[str] = []
        for s in steps_summary[-5:]:
            title = (s.get("title") or "Step").strip()
            st = (s.get("status") or "").strip()
            result = (s.get("result") or "").strip()
            snippet = result[:180]
            if len(result) > 180:
                snippet += "..."
            line = f"{title}: {st}"
            if snippet:
                line += f" ({snippet})"
            step_lines.append(line)

        run_text = " ".join(
            [str(getattr(ticket, "title", "") or ""), str(getattr(ticket, "description", "") or "")]
            + [str((s.get("result") or "")) for s in steps_summary[-5:]]
        )
        risk = infer_risk_profile(run_text)
        audits_run = build_audit_gates(
            status=status_label,
            risk_level=risk.get("level", "low"),
            tests_passed=(status_label == "completed"),
        )
        validation_rules = validation_rules_for_risk(
            risk.get("level", "low"),
            risk.get("signals", []),
        )
        packet = build_result_packet(
            ticket_id=str(getattr(ticket, "id", "") or ""),
            board_id=str(getattr(ticket, "board_id", "") or "") if getattr(ticket, "board_id", None) is not None else None,
            project_id=str(getattr(ticket, "linked_project_id", "") or "")
            if getattr(ticket, "linked_project_id", None) is not None
            else None,
            execution_lane="workflow",
            status=status_label,
            summary=f"Workflow run {run_id} finished with {len(steps_summary)} recorded step result(s).",
            files_changed=[],
            change_summary=step_lines,
            commands_suggested=["Run deterministic validation checks in CLI for high-risk changes."],
            tests_run=[],
            test_results=[],
            limitations=["Workflow note contains compact per-step summary only."],
            next_recommended=(
                ["Inspect step outputs and move ticket based on risk policy."]
                + validation_rules[:4]
            ),
            logs=[f"workflow_run:{run_id}"],
            assumptions=[
                f"risk_level={risk.get('level', 'low')}",
                f"risk_type={risk.get('risk_type', 'standard')}",
            ],
            audits_run=audits_run,
            final_verdict="pass" if status_label == "completed" else "needs_changes",
            audit_rationale="Workflow terminal status mapped to canonical verdict.",
        )
        note = format_result_packet_note(packet, title=f"Workflow Run #{run_id}")
        note = f"{note}\n\n{format_evidence_block()}"
        existing = (getattr(ticket, "description", "") or "").strip()
        if existing:
            ticket.description = f"{existing}\n\n{note}"
        else:
            ticket.description = note
        # Cap growth to keep ticket text responsive in UI.
        if len(ticket.description) > 12000:
            ticket.description = ticket.description[-12000:]
    except Exception:
        logger.debug("Could not append workflow summary note to ticket", exc_info=True)


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
    """Record packet UI artifacts as Hermes validation and merge the snapshot."""
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
        from distr.core.hermes import (
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
    """Prepare a failed terminal UI validation for immediate correction retry."""
    snapshot = _terminal_ui_correction_snapshot(packet)
    if not snapshot:
        return None
    try:
        from distr.core.db.hermes import HermesCorrectionAttempt
        from distr.core.hermes import (
            count_correction_attempts,
            get_hermes_role_model,
            mark_correction_dispatched,
        )

        settings = _workflow_run_settings(run.workflow)
        if not settings.get("auto_dispatch_corrections"):
            return None
        step_id = int(getattr(run, "current_step_id", None) or 0)
        if not step_id:
            return None
        max_attempts = int(settings.get("max_correction_attempts") or 1)
        attempt_count = count_correction_attempts(run_id=int(run.id), step_id=step_id)
        if attempt_count > max_attempts:
            return None
        attempt_id = int(snapshot.get("correction_attempt_id"))
        attempt = (
            db.query(HermesCorrectionAttempt)
            .filter(HermesCorrectionAttempt.id == attempt_id)
            .first()
        )
        if not attempt:
            return None
        try:
            correction_packet = json.loads(attempt.correction_packet or "{}") or {}
        except Exception:
            correction_packet = {}
        correction_provider, correction_model = get_hermes_role_model("correction")
        target_backend = correction_provider or correction_packet.get("target_backend") or ""
        target_model = correction_model or correction_packet.get("target_model") or ""
        mark_correction_dispatched(
            attempt_id,
            dispatch_result={
                "auto_dispatch": True,
                "terminal_ui_quality_gate": True,
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "target_backend": target_backend,
                "target_model": target_model,
            },
        )
        run_data["pending_correction"] = {
            "step_id": step_id,
            "correction_attempt_id": attempt_id,
            "packet": correction_packet,
            "target_backend": target_backend,
            "target_model": target_model,
        }
        step = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.id == step_id).first()
        if step:
            step.status = "pending"
            step.result = (step.result or "") + "\n\n[Auto-correction dispatched]"
        return {
            "step_id": step_id,
            "correction_attempt_id": attempt_id,
        }
    except Exception:
        logger.debug("Could not auto-dispatch terminal UI correction", exc_info=True)
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


def _cleanup_orphaned_runs_on_startup() -> None:
    """Mark any 'running'/'waiting' runs as 'cancelled' on app startup.

    Runs that were left open from a previous session (crash, force-quit) would
    otherwise block every subsequent "Send to Workflow" for the same ticket.
    Safe to call before any WorkflowAgent or RunContext is created.
    """
    with get_session() as db:
        orphans = (
            db.query(AutoWorkflowRun)
            .filter(AutoWorkflowRun.status.in_(["running", "waiting"]))
            .all()
        )
        if not orphans:
            return
        for run in orphans:
            run.status = "cancelled"
            run.completed_at = datetime.utcnow()
        db.commit()
        logger.info(
            "Cancelled %d orphaned workflow run(s) from previous session: %s",
            len(orphans),
            [r.id for r in orphans],
        )


def _finalize_terminal_run(run_id: int, workflow_id: int, status: str) -> None:
    """Clean up resources and notify the bridge when a run reaches terminal status."""
    _cleanup_run(run_id)

    steps_summary: List[dict] = []
    try:
        with get_session() as db:
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

    run_result = {
        "session_id": workflow_id,
        "run_id": run_id,
        "success": status == "completed",
        "cancelled": status == "cancelled",
        "steps_summary": steps_summary,
    }

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
                    append_ticket_audit_entry(
                        db,
                        ticket_id=int(run_rec.ticket_id),
                        run_id=run_id,
                        step_id=run_rec.current_step_id,
                        step_result_id=None,
                        execution_lane="workflow",
                        status=(status or "completed").strip().lower(),
                        final_verdict="pass" if (status or "").strip().lower() == "completed" else "needs_changes",
                        summary=f"Run finished: {(status or 'completed').strip().lower()}",
                        details=f"Workflow run {run_id} finished with status {(status or 'completed').strip().lower()}.",
                    )
                    ticket.workflow_status = status
                    _append_workflow_summary_to_ticket(ticket, run_id, status, steps_summary)
                    db.commit()
    except Exception:
        logger.debug("Could not sync workflow_status to ticket for run %d", run_id)

    try:
        from distr.core.workflow_engine.agent_bridge import WorkflowAgentBridge
        WorkflowAgentBridge().on_workflow_completed(workflow_id, run_result)
    except Exception:
        logger.error("WorkflowAgentBridge notification failed for run %d", run_id, exc_info=True)


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
                is_truly_active = active_run.id in _active_runs
            if is_truly_active:
                return {"error": "A run is already in progress for this board/ticket"}
            logger.info(
                "start_workflow_run: auto-cancelling orphaned run %d (workflow=%d) — no live RunContext",
                active_run.id,
                workflow_id,
            )
            active_run.status = "cancelled"
            active_run.completed_at = datetime.utcnow()
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
        if (board_id is not None or ticket_id is not None) and not normalized_metadata.get("developer_context"):
            try:
                from distr.core.developer_context import build_developer_context

                normalized_metadata["developer_context"] = build_developer_context().to_dict()
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
        packet = normalized_metadata.get("result_packet") or {}
        packet_audit = dict(packet.get("audit") or {})
        packet_audit["audits_run"] = build_audit_gates(
            status="running",
            risk_level=risk_profile.get("level", "low"),
            tests_passed=True,
        )
        packet["audit"] = packet_audit
        normalized_metadata["result_packet"] = packet

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

        workflow_agent = WorkflowAgent()
        agent_loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(agent_loop)
            agent_loop.run_forever()

        agent_thread = threading.Thread(target=_run_loop, daemon=True)
        agent_thread.start()

        with _runs_lock:
            _active_runs[run_id] = _RunContext(
                run_id=run_id,
                workflow_agent=workflow_agent,
                event_loop=agent_loop,
                thread=agent_thread,
                context_prefix=context or "",
                run_ctx=run_ctx,
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

    record_workflow_chat_event(
        run_id,
        "started",
        status="running",
        step_id=first_step_id,
        step_name=first_step_name,
        summary=f"Started workflow {workflow_id}.",
        phase="planning",
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
        logger.debug("Could not emit Hermes workflow_run_started event", exc_info=True)
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


def execute_step(step_id: int, isolated: bool = False) -> Dict[str, Any]:
    """Execute a single step via StepDispatcher."""
    dispatcher = StepDispatcher()
    return dispatcher.run_isolated(step_id)


def cancel_run(run_id: int) -> bool:
    """Cancel an active workflow run."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return False
        run.status = "cancelled"
        run.completed_at = datetime.utcnow()
        if run.current_step_id:
            step = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.id == run.current_step_id,
            ).first()
            if step and step.status in ("running", "waiting"):
                step.status = "cancelled"
                step.result = "Cancelled by user."
        _run_id, _wf_id = run.id, run.workflow_id
        db.commit()
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
        logger.debug("Could not emit Hermes workflow_run_cancelled event", exc_info=True)
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


def continue_waiting_step(run_id: int, optional_input: str = "") -> Dict[str, Any]:
    """Resume a workflow run that is in 'waiting' status."""
    from distr.core.workflow.router import StepRouter

    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if not run:
            return {"error": "Run not found", "status_code": 404}
        if run.status != "waiting":
            return {"error": f"Run is not waiting (status: {run.status})", "status_code": 409}
        step = db.query(AutoWorkflowStep).filter(
            AutoWorkflowStep.id == run.current_step_id,
        ).first()
        if not step or step.status != "waiting":
            return {"error": "No waiting step found", "status_code": 409}
        step_id = step.id

    router = StepRouter()
    waiting_kind = ""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
        if run and run.run_data:
            try:
                waiting_kind = (json.loads(run.run_data or "{}") or {}).get("waiting_kind") or ""
            except Exception:
                waiting_kind = ""

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
        logger.debug("Could not emit Hermes workflow_run_resumed event", exc_info=True)

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
    elif waiting_kind == "ide_handoff" and optional_input:
        try:
            from distr.core.hermes import record_learning_signal
            from distr.core.orchestration_events import emit_orchestration_event

            with get_session() as db:
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == run_id).first()
            board_id = getattr(run, "board_id", None) if run else None
            emit_orchestration_event(
                source="ide",
                event_type="ide_iteration_completed",
                status="completed",
                run_id=run_id,
                step_id=step_id,
                workflow_id=getattr(run, "workflow_id", None) if run else None,
                ticket_id=getattr(run, "ticket_id", None) if run else None,
                board_id=board_id,
                summary=(optional_input or "")[:500] or "IDE iteration reported back.",
                payload={"feedback": optional_input or ""},
            )
            record_learning_signal(
                scope="board" if board_id else "global",
                scope_id=board_id,
                rule_type="ide_iteration",
                summary=(optional_input or "")[:500],
                payload={"run_id": run_id, "step_id": step_id},
            )
        except Exception:
            logger.debug("Could not emit ide_iteration_completed event", exc_info=True)

    # Resume must continue execution, not only update run state.
    action = (decision or {}).get("action")
    if action == "next_step":
        next_step_id = decision.get("step_id")
        wait_before = int(decision.get("wait_before_next") or 0)
        if next_step_id:
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
            if status == "completed" and _packet_has_failed_ui_quality_validation(packet):
                status = "failed"
                auto_retry = _maybe_auto_dispatch_terminal_ui_correction(
                    db,
                    run=run,
                    packet=packet,
                    run_data=run_data,
                )
                if auto_retry:
                    status = "running"
            enforced_status, updated_packet, missing_checks = enforce_validation_requirements(
                packet=packet,
                run_status=status,
                risk_profile=risk_profile,
            )
            if missing_checks:
                logger.info(
                    "complete_run: enforcing required checks for run %s, missing=%s",
                    run_id,
                    ",".join(missing_checks),
                )
            status = enforced_status or status
            run_data["result_packet"] = updated_packet
            run.run_data = json.dumps(run_data)
        run.status = status
        run.completed_at = None if auto_retry else datetime.utcnow()
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
    except Exception:
        logger.debug("Could not emit Hermes workflow_run_completed event", exc_info=True)
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
        self._set_run_phase(run_id, step_data)
        errors = self._validate_before_dispatch(step_data)
        if errors:
            self._fail_step(step_id, f"Validation failed: {errors}")
            record_workflow_chat_event(
                run_id,
                "step_failed",
                status="failed",
                step_id=step_id,
                step_name=step_data.get("name"),
                summary=f"Validation failed: {errors}",
            )
            return {"error": errors}
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
        result = self._execute(step_data, run_id=run_id)
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
            if step and step.wait_for_continue and step.status == "waiting":
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
