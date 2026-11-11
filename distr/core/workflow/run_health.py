"""Read-only run health projection. A stalled worker is never retried implicitly."""
from __future__ import annotations

import json
import logging

from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.db.time import utc_now_naive
from distr.core.workflow.run_prompts import run_label

HEARTBEAT_CHECK_SECONDS = 30
# One missed check interval: a worker that sends heartbeats (about every 10s)
# is stalled once none arrived during a whole check interval.
HEARTBEAT_TIMEOUT_SECONDS = HEARTBEAT_CHECK_SECONDS
# Backends that never send heartbeats are judged on their last activity
# instead, with a longer window so quiet long-running steps are not flagged.
ACTIVITY_TIMEOUT_SECONDS = 600
_HUMAN_WAIT_KEYS = ("pending_route_approval", "route_approval_pending", "provider_preflight_pending")


def run_health(db, run, data, *, now=None, execution=None):
    now = now or utc_now_naive()
    if execution is None and run.id is not None:
        execution = db.query(ProjectExecutionSession).filter(
            ProjectExecutionSession.run_id == run.id,
            ProjectExecutionSession.step_id == run.current_step_id,
        ).order_by(ProjectExecutionSession.id.desc()).first()
    # Legacy sessions can lack a step ID.
    if execution is None and run.id is not None:
        execution = db.query(ProjectExecutionSession).filter(
            ProjectExecutionSession.run_id == run.id,
            ProjectExecutionSession.step_id.is_(None),
        ).order_by(ProjectExecutionSession.id.desc()).first()
    activity = heartbeat = None
    changed = set()
    if execution is not None:
        heartbeat = db.query(ProjectExecutionEvent).filter(
            ProjectExecutionEvent.session_id == execution.id,
            ProjectExecutionEvent.event_type == "heartbeat",
        ).order_by(ProjectExecutionEvent.created_at.desc(), ProjectExecutionEvent.id.desc()).first()
        activity = db.query(ProjectExecutionEvent).filter(
            ProjectExecutionEvent.session_id == execution.id,
            ProjectExecutionEvent.event_type != "heartbeat",
        ).order_by(ProjectExecutionEvent.created_at.desc(), ProjectExecutionEvent.id.desc()).first()
        file_events = db.query(ProjectExecutionEvent).join(
            ProjectExecutionSession, ProjectExecutionSession.id == ProjectExecutionEvent.session_id)
        file_scope = (ProjectExecutionSession.run_id == run.id) if run.id is not None else (ProjectExecutionSession.id == execution.id)
        for event in file_events.filter(
            file_scope,
            ProjectExecutionEvent.event_type.in_(["file_change", "file_changed", "file_updated", "item.completed"]),
        ).all():
            try:
                payload = json.loads(event.payload or "{}")
                path = payload.get("path") or payload.get("file_path")
                if path:
                    changed.add(str(path))
                item = payload.get("item") or {}
                if item.get("type") == "file_change" and item.get("status") in {None, "completed"}:
                    for change in item.get("changes") or []:
                        if isinstance(change, dict) and change.get("path"):
                            changed.add(str(change["path"]))
            except (ValueError, TypeError, AttributeError):
                pass
    if heartbeat is not None:
        reference, timeout, basis = heartbeat.created_at, HEARTBEAT_TIMEOUT_SECONDS, "heartbeat"
    else:
        reference = (activity.created_at if activity else None) or (
            execution.started_at if execution else None) or run.started_at
        timeout, basis = ACTIVITY_TIMEOUT_SECONDS, "activity"
    age = max(0, int((now - reference).total_seconds())) if reference else None
    active_worker = execution is not None and execution.status in {"running", "queued"}
    waiting_on_human = bool(str(data.get("waiting_kind") or "").strip()) or any(data.get(k) for k in _HUMAN_WAIT_KEYS) \
        or data.get("human_intervention_state") == "needs_human_input"
    stalled = (run.status == "running" and active_worker and not waiting_on_human
               and age is not None and age > timeout)
    label = run_label(data, ticket_id=run.ticket_id)
    minutes = max(0, int((now - run.started_at).total_seconds() // 60)) if run.started_at else 0
    kind = str(data.get("waiting_kind") or "")
    recovery = data.get("restart_recovery") or {}
    reason = str(recovery.get("blocked_reason") or "")
    if run.status == "waiting":
        doing = ("paused after restart" + (f": {reason}" if reason else "")) if kind == "restart_recovery" else f"waiting ({kind.replace('_', ' ') or 'checkpoint'})"
    elif stalled:
        doing = (f"Stalled: worker heartbeat missing for {age} seconds" if basis == "heartbeat"
                 else f"Stalled: no worker activity for {age} seconds")
    else:
        doing = "doing " + str((activity.message if activity else "") or "starting the worker").strip()[:180]
    handoff = data.get("latest_backend_handoff") or {}
    git_status = data.get("git_status_current") or data.get("git_status_after") or handoff.get("git_status_current") or []
    # A checkout can already be dirty. Report it as modified, never attribute it
    # to this worker without a file-change event.
    files = f"{len(changed)} {'file' if len(changed) == 1 else 'files'} changed" if changed else (f"{len(git_status)} modified files in checkout" if git_status else "no files changed yet")
    summary = f"{label}: {doing}, {minutes} minutes in, {files}."
    prompt = f"{label}: worker heartbeat stopped. No retry has been started. Safe default if you do nothing: keep monitoring without launching another worker. Inspect or stop the run before retrying."
    return {"status_summary": summary, "stalled": stalled,
            "worker_health": {"state": "stalled" if stalled else "active" if active_worker else "idle",
                              "heartbeat_age_seconds": age, "check_interval_seconds": HEARTBEAT_CHECK_SECONDS,
                              "basis": basis, "timeout_seconds": timeout},
            **({"waiting_prompt": prompt} if stalled else {})}


def check_worker_heartbeats():
    """Persist health annotations, without changing the workflow execution state."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.gui.web.workflow_events import increment_workflow_updated

    changed = False
    with get_session() as db:
        for run in db.query(AutoWorkflowRun).filter(AutoWorkflowRun.status == "running").all():
            try:
                data = json.loads(run.run_data or "{}")
            except (ValueError, TypeError):
                logging.getLogger(__name__).warning("Run %s has invalid metadata; heartbeat check skipped", run.id)
                continue
            if not isinstance(data, dict):
                continue
            health = run_health(db, run, data)["worker_health"]
            previous = data.get("worker_health") or {}
            if previous.get("state") != health["state"]:
                logging.getLogger(__name__).info("Run %s worker heartbeat health: %s (age=%s seconds)",
                                                run.id, health["state"], health["heartbeat_age_seconds"])
                data["worker_health"] = health
                run.run_data = json.dumps(data)
                changed = True
        if changed:
            db.commit()
    if changed:
        increment_workflow_updated()
