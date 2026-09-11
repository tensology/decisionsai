"""Automation scheduler — separate tick path from workflow engine."""

from __future__ import annotations

import logging
import json
import sys
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from distr.core.db.workflow import AutoWorkflow

logger = logging.getLogger(__name__)


def ensure_automation_schema() -> None:
    """Create tables and migrate legacy workflow-backed automations."""
    from distr.core.db import Base, engine
    from distr.core.db.automation import Automation, AutomationRun

    Base.metadata.create_all(bind=engine, tables=[Automation.__table__, AutomationRun.__table__])
    # create_all does not add columns to an existing SQLite table.
    from sqlalchemy import inspect, text

    columns = {column["name"] for column in inspect(engine).get_columns("automations")}
    additions = {
        "board_id": "INTEGER",
        "project_id": "INTEGER",
        "thread_chat_id": "INTEGER",
        "linked_workflow_id": "INTEGER",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE automations ADD COLUMN {name} {sql_type}"))
            connection.execute(
                text(f"CREATE INDEX IF NOT EXISTS ix_automations_{name} ON automations ({name})")
            )
    # Database bootstrap can reach this function while automation.store itself
    # is still importing. Avoid re-importing that partial module. The app's
    # post-bootstrap reconciliation performs the legacy copy once imports are
    # complete.
    store_module = sys.modules.get("distr.core.automation.store")
    migrate = getattr(store_module, "migrate_legacy_automation_workflows", None)
    if callable(migrate):
        migrate()


def reconcile_automation_startup_state() -> dict[str, int]:
    """Finish migrations and close runs whose worker died with the prior process."""
    from distr.core.automation.store import migrate_legacy_automation_workflows, utc_now
    from distr.core.db import get_session
    from distr.core.db.automation import AutomationRun
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

    migrated = int(migrate_legacy_automation_workflows() or 0)
    recovered = 0
    now = utc_now()
    with get_session() as session:
        rows = (
            session.query(AutomationRun)
            .filter(AutomationRun.status.in_(["running", "waiting", "dispatched", "initializing", "queued", "dispatching"]))
            .all()
        )
        legacy_attempts = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.status == "dispatching").all()
        for row in [*rows, *legacy_attempts]:
            data: dict[str, Any]
            try:
                loaded = json.loads(row.run_data or "{}")
                data = loaded if isinstance(loaded, dict) else {}
            except Exception:
                data = {}
            if row.status == "dispatching":
                from distr.core.db.automation import Automation
                legacy = isinstance(row, AutoWorkflowRun)
                automation = session.get(AutoWorkflow, row.workflow_id) if legacy else session.get(Automation, row.automation_id)
                if automation and automation.next_run_at is None:
                    automation.schedule_enabled = False
                    if not legacy:
                        automation.status = "paused"
                data["retry_policy"] = "manual_review"
            data["summary"] = "Automation was interrupted when DecisionsAI stopped. Review execution history before retrying."
            data["message"] = data["summary"]
            data["recovered_on_startup"] = True
            row.run_data = json.dumps(data, ensure_ascii=False, default=str)
            row.status = "failed"
            row.completed_at = now
            recovered += 1
        if recovered:
            session.commit()
    return {"migrated": migrated, "recovered_runs": recovered}


def get_due_automations() -> list[dict[str, Any]]:
    from distr.core.automation.store import list_due_automations

    return list_due_automations()


def _advance_legacy_workflow_next_run(workflow: "AutoWorkflow") -> None:
    from distr.core.workflow.scheduler import _advance_next_run

    _advance_next_run(workflow)


def _scheduled_timing_metadata(due_at: datetime | None) -> dict[str, Any]:
    from distr.core.workflow.scheduler import _scheduled_timing_metadata as _timing

    return _timing(due_at)


def run_scheduled_automation(
    automation: dict[str, Any],
    *,
    event_queue=None,
    on_start_orchestration: Callable[..., Any] | None = None,
) -> bool:
    """Claim a due occurrence durably; advance only after dispatch acknowledgement.

    Failed or ambiguous dispatch pauses for operator review. It is never blindly
    retried, because a worker may have accepted work before its acknowledgement
    was lost. The attempt records the due time and returned execution identity.
    """
    from sqlalchemy import text
    from distr.core.automation.store import serialize_legacy_workflow, utc_now
    from distr.core.db import get_session
    from distr.core.db.automation import Automation, AutomationRun
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun

    record_id = automation.get("record_id")
    identity = record_id or automation.get("workflow_id")
    if not identity:
        return False
    model, run_model = (Automation, AutomationRun) if record_id else (AutoWorkflow, AutoWorkflowRun)
    now = utc_now()
    with get_session() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        row = session.get(model, int(identity), with_for_update=True)
        if not row or not row.schedule_enabled or not row.next_run_at or row.next_run_at > now:
            return False
        due_at = row.next_run_at
        timing_metadata = {**_scheduled_timing_metadata(due_at), "phase": "scheduled_automation", "due_at": due_at.isoformat()}
        if not record_id:
            automation = serialize_legacy_workflow(row)
        attempt = run_model(**({"automation_id": row.id} if record_id else {"workflow_id": row.id}), status="dispatching", run_data=json.dumps({**timing_metadata, "summary": "Dispatch acknowledgement pending.", "retry_policy": "manual_review"}))
        session.add(attempt)
        # Clearing the due marker claims the occurrence without consuming a one-shot.
        row.next_run_at = None
        session.commit()
        attempt_id = attempt.id

    result = {}
    try:
        from distr.core.automation_orchestrator import dispatch_automation_to_current_chat
        result = dispatch_automation_to_current_chat(automation, manual=False, schedule_metadata={**timing_metadata, "dispatch_attempt_id": attempt_id})
    except Exception:
        logger.error("Automation scheduler: dispatch acknowledgement failed for %s", automation.get("id"), exc_info=True)
        result = {"status": "unknown", "summary": "Dispatch acknowledgement was lost. Review execution history before retrying."}
    if not isinstance(result, dict):
        result = {"status": "unknown", "summary": "Dispatch returned no acknowledgement. Review before retrying."}
    accepted = result.get("status") in {"running", "queued", "dispatched", "completed", "waiting"}
    with get_session() as session:
        if session.get_bind().dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))
        row = session.get(model, int(identity), with_for_update=True)
        attempt = session.get(run_model, attempt_id)
        if attempt:
            attempt.status = "dispatch_accepted" if accepted else "failed"
            attempt.completed_at = utc_now()
            attempt.run_data = json.dumps({**timing_metadata, "summary": result.get("summary") or ("Dispatch accepted." if accepted else "Dispatch failed; schedule paused for review."), "retry_policy": "manual_review", "dispatch_result": result, "chat_id": result.get("chat_id"), "execution_mode": "scheduled_dispatch"}, default=str)
        # Do not undo a concurrent user edit or pause.
        if row and row.schedule_enabled and row.next_run_at is None:
            if accepted:
                if record_id:
                    from distr.core.automation.scheduler_advance import advance_next_run_for_automation
                    advance_next_run_for_automation(row)
                else:
                    _advance_legacy_workflow_next_run(row)
                row.last_run_at = utc_now()
            else:
                row.schedule_enabled = False
                row.next_run_at = due_at
                if record_id:
                    row.status = "paused"
            row.modified_date = utc_now()
        session.commit()
    return accepted
