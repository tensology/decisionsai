"""Automation scheduler — separate tick path from workflow engine."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from distr.core.db import get_session
from distr.core.db.automation import Automation
from distr.core.db.workflow import AutoWorkflow

logger = logging.getLogger(__name__)


def ensure_automation_schema() -> None:
    """Create tables and migrate legacy workflow-backed automations."""
    from distr.core.db import Base, engine
    from distr.core.db.automation import Automation, AutomationRun

    Base.metadata.create_all(bind=engine, tables=[Automation.__table__, AutomationRun.__table__])
    from distr.core.automation.store import migrate_legacy_automation_workflows

    migrate_legacy_automation_workflows()


def get_due_automations() -> list[dict[str, Any]]:
    from distr.core.automation.store import list_due_automations

    return list_due_automations()


def _advance_legacy_workflow_next_run(workflow: AutoWorkflow) -> None:
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
    """Fire one due automation and advance its next run time."""
    from distr.core.automation.store import serialize_legacy_workflow, utc_now

    record_id = automation.get("record_id")
    workflow_id = automation.get("workflow_id")
    now = utc_now()

    if record_id:
        with get_session() as session:
            row = session.query(Automation).filter(Automation.id == int(record_id)).first()
            if not row or not row.schedule_enabled:
                return False
            if not row.next_run_at or row.next_run_at > now:
                return False
            due_at = row.next_run_at
            timing_metadata = {
                **_scheduled_timing_metadata(due_at),
                "phase": "scheduled_automation",
            }
            from distr.core.automation.scheduler_advance import advance_next_run_for_automation

            advance_next_run_for_automation(row)
            row.modified_date = now
            session.commit()
    elif workflow_id:
        with get_session() as session:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
            if not wf or not wf.schedule_enabled:
                return False
            if not wf.next_run_at or wf.next_run_at > now:
                return False
            due_at = wf.next_run_at
            timing_metadata = {
                **_scheduled_timing_metadata(due_at),
                "phase": "scheduled_automation",
            }
            from distr.core.automation.store import serialize_legacy_workflow

            automation = serialize_legacy_workflow(wf)
            _advance_legacy_workflow_next_run(wf)
            wf.modified_date = now
            session.commit()
    else:
        return False

    try:
        from distr.core.automation_orchestrator import dispatch_automation_to_current_chat

        dispatch_automation_to_current_chat(
            automation,
            manual=False,
            schedule_metadata=timing_metadata,
        )
        return True
    except Exception:
        logger.error("Automation scheduler: dispatch failed for %s", automation.get("id"), exc_info=True)
        return False
