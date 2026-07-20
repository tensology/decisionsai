"""Workflow-scoped steering memory for IDE, CLI, browser, and human-in-the-loop steps."""

from __future__ import annotations

import json
import time
from typing import Any

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflowRun


STEERING_LOG_LIMIT = 40


def append_run_steering_entry(
    run_id: int,
    *,
    source: str,
    event_type: str,
    message: str,
    step_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Append a steering entry to run_data.steering_log (bounded)."""
    text = (message or "").strip()
    if not text:
        return False
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return False
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}
        log = list(run_data.get("steering_log") or [])
        log.append({
            "ts": time.time(),
            "source": (source or "workflow").strip() or "workflow",
            "event_type": (event_type or "feedback").strip() or "feedback",
            "message": text[:2000],
            "step_id": step_id,
            **(extra or {}),
        })
        run_data["steering_log"] = log[-STEERING_LOG_LIMIT:]
        run_data["latest_steering"] = text[:500]
        run.run_data = json.dumps(run_data)
        db.commit()
    try:
        from distr.core.workspace_memory.pickup_handoff import append_ledger

        append_ledger(
            "runs",
            run_id,
            event_type=event_type,
            message=text,
            extra={"source": source, "step_id": step_id, **(extra or {})},
        )
    except Exception:
        pass
    return True


def record_run_steering_feedback(
    *,
    run_id: int,
    message: str,
    source: str = "workflow",
    event_type: str = "user_feedback",
    workflow_id: int | None = None,
    step_id: int | None = None,
    board_id: int | None = None,
    ticket_id: int | None = None,
    project_id: int | None = None,
    capture_standard: bool = True,
    rule_type: str = "workflow_steering",
) -> None:
    """Persist steering now; promote it only when evidence warrants reuse."""
    text = (message or "").strip()
    if not text:
        return

    append_run_steering_entry(
        run_id,
        source=source,
        event_type=event_type,
        message=text,
        step_id=step_id,
    )

    from distr.core.workflow.control_policy import classify_learning_signal

    learning = classify_learning_signal(text, event_type=event_type)
    if learning.should_record:
        try:
            from distr.core.orchestrator import record_learning_signal

            record_learning_signal(
                scope="board" if board_id else "project" if project_id else "global",
                scope_id=board_id or project_id,
                rule_type=rule_type,
                summary=text[:500],
                payload={
                    "run_id": run_id,
                    "step_id": step_id,
                    "workflow_id": workflow_id,
                    "source": source,
                    "event_type": event_type,
                    "learning_disposition": learning.disposition,
                    "learning_reason": learning.reason,
                },
                enabled=learning.enabled,
                promote_after=learning.promote_after,
            )
        except Exception:
            pass

    # Adaptive/global standards are stronger than a learned-rule candidate.
    # Only explicit durable instructions are allowed to enter those stores.
    if capture_standard and learning.enabled:
        try:
            from distr.core.workflow.standards_memory import capture_feedback_as_memory

            capture_feedback_as_memory(
                text,
                workflow_id=int(workflow_id) if workflow_id else None,
                board_id=board_id,
                project_id=project_id,
            )
        except Exception:
            pass


def build_steering_context_for_run_id(run_id: int | None, *, limit: int = 8) -> str:
    """Format recent steering for the next step prompt or CLI handoff packet."""
    if not run_id:
        return ""
    try:
        with get_session() as db:
            run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
            if not run or not run.run_data:
                return ""
            run_data = json.loads(run.run_data or "{}") or {}
    except Exception:
        return ""

    parts: list[str] = []
    live = run_data.get("live_agent_context")
    if isinstance(live, dict):
        steer = str(live.get("latest_user_steer") or "").strip()
        if steer:
            parts.append(f"- Latest IDE/CLI steer: {steer[:400]}")
        question = str(run_data.get("worker_question") or "").strip()
        if question:
            parts.append(f"- Worker asked: {question[:400]}")
        terminal = str(live.get("latest_terminal_summary") or "").strip()
        if terminal:
            parts.append(f"- Last worker summary: {terminal[:300]}")

    log = run_data.get("steering_log") or []
    if isinstance(log, list) and log:
        recent = [e for e in log if isinstance(e, dict)][-limit:]
        for entry in recent:
            msg = str(entry.get("message") or "").strip()
            if not msg:
                continue
            src = str(entry.get("source") or "workflow")
            evt = str(entry.get("event_type") or "feedback")
            # Steering is compact, but truncating a path or final constraint can
            # reverse its meaning (for example, "reuse X; do not recapture").
            # Preserve enough of the latest instruction for the next one-shot
            # worker to execute it exactly.
            parts.append(f"- [{src}/{evt}] {msg[:600]}")

    feedback = str(run_data.get("feedback") or "").strip()
    if feedback and feedback not in "\n".join(parts):
        parts.append(f"- User continuation: {feedback[:400]}")

    if not parts:
        return ""
    return "[WORKFLOW STEERING MEMORY]\n" + "\n".join(parts[:limit])


def get_run_steering_snapshot(
    run_id: int,
    *,
    steering_limit: int = 40,
    rules_limit: int = 30,
) -> dict[str, Any] | None:
    """Structured steering + learned memory for workflow Runs UI."""
    with get_session() as db:
        run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return None
        try:
            run_data = json.loads(run.run_data or "{}") or {}
        except Exception:
            run_data = {}

        board_id = int(run.board_id) if run.board_id else None
        workflow_id = int(run.workflow_id) if run.workflow_id else None
        ticket_id = int(run.ticket_id) if run.ticket_id else None
        run_status = run.status
        live = run_data.get("live_agent_context") if isinstance(run_data.get("live_agent_context"), dict) else {}

        steering_log = [
            entry for entry in (run_data.get("steering_log") or [])
            if isinstance(entry, dict)
        ]
        steering_log = list(reversed(steering_log[-max(1, steering_limit):]))

        adaptive_memory = ""
        if workflow_id:
            from distr.core.db.workflow import AutoWorkflowVariable
            from distr.core.workflow.standards_memory import ADAPTIVE_CONTEXT_TITLE

            row = (
                db.query(AutoWorkflowVariable)
                .filter(
                    AutoWorkflowVariable.workflow_id == workflow_id,
                    AutoWorkflowVariable.name == ADAPTIVE_CONTEXT_TITLE,
                )
                .first()
            )
            adaptive_memory = (row.default_value or "").strip() if row else ""

    prompt_preview = build_steering_context_for_run_id(int(run_id))
    learned_rules: list[dict[str, Any]] = []
    learned_rules_preview = ""
    if board_id:
        try:
            from distr.core.orchestrator import build_learned_rules_context, list_learned_rules

            learned_rules_preview = build_learned_rules_context(board_id)
            rows = list_learned_rules(board_id=board_id, enabled_only=False, limit=rules_limit)
            for rule in rows:
                payload = rule.get("payload") if isinstance(rule.get("payload"), dict) else {}
                run_linked = int(payload.get("run_id") or 0) == int(run_id)
                workflow_linked = (
                    workflow_id is not None
                    and int(payload.get("workflow_id") or 0) == int(workflow_id)
                )
                learned_rules.append({
                    **rule,
                    "run_linked": run_linked,
                    "workflow_linked": workflow_linked,
                })
            learned_rules.sort(
                key=lambda r: (
                    0 if r.get("run_linked") else 1,
                    0 if r.get("enabled") else 2,
                    -(int(r.get("evidence_count") or 0)),
                )
            )
        except Exception:
            pass

    return {
        "run_id": int(run_id),
        "workflow_id": workflow_id,
        "board_id": board_id,
        "ticket_id": ticket_id,
        "project_id": int(run_data.get("project_id")) if run_data.get("project_id") not in (None, "") else None,
        "status": run_status,
        "latest_steering": str(run_data.get("latest_steering") or "").strip(),
        "worker_question": str(run_data.get("worker_question") or "").strip(),
        "live_agent_summary": {
            "latest_user_steer": str(live.get("latest_user_steer") or "").strip(),
            "latest_terminal_summary": str(live.get("latest_terminal_summary") or "").strip(),
            "backend_id": str(live.get("backend_id") or "").strip(),
        },
        "steering_log": steering_log,
        "prompt_preview": prompt_preview,
        "adaptive_quality_memory": adaptive_memory,
        "learned_rules": learned_rules,
        "learned_rules_preview": learned_rules_preview,
    }
