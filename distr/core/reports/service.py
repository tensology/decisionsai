"""Read-only Development run history and time log, with explicit source identities."""
from distr.core.db import Chat, get_session
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, DevelopmentTimeEntry, DevelopmentWorkItem
from distr.core.db.kanban import ProjectExecutionSession
from distr.core.db.projects import Project

# Cancelled / abandoned runs that sat for days must not report multi-week durations.
MAX_REPORT_DURATION_SECONDS = 24 * 60 * 60
MAX_CANCELLED_DURATION_SECONDS = 6 * 60 * 60


def _safe_duration_seconds(record, *, status: str | None = None) -> int | None:
    if not record.completed_at or not record.started_at:
        return None
    raw = max(0, int((record.completed_at - record.started_at).total_seconds()))
    clean_status = str(status or getattr(record, "status", "") or "").lower()
    if clean_status in {"cancelled", "canceled", "failed"} and raw > MAX_CANCELLED_DURATION_SECONDS:
        return None
    if raw > MAX_REPORT_DURATION_SECONDS:
        return None
    return raw


def list_reports(*, limit=100):
    limit = max(1, min(int(limit), 200))
    rows = []

    def append(kind, record, name, project_id=None, chat_id=None, run_id=None):
        duration = _safe_duration_seconds(record, status=getattr(record, "status", None))
        rows.append(
            {
                "id": f"{kind}:{record.id}",
                "kind": kind,
                "name": name,
                "status": record.status or "unknown",
                "project_id": project_id,
                "chat_id": chat_id,
                "run_id": run_id if run_id is not None else getattr(record, "id", None),
                "started_at": record.started_at.isoformat() if record.started_at else None,
                "completed_at": record.completed_at.isoformat() if record.completed_at else None,
                "duration_seconds": duration,
            }
        )

    with get_session() as db:
        for run, definition in (
            db.query(AutomationRun, Automation)
            .join(Automation, AutomationRun.automation_id == Automation.id)
            .filter(AutomationRun.status != "dispatch_accepted")
            .order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc())
            .limit(limit)
        ):
            append("automation", run, definition.name, definition.project_id, getattr(definition, "chat_id", None))
        for run, definition in (
            db.query(AutoWorkflowRun, AutoWorkflow)
            .join(AutoWorkflow, AutoWorkflowRun.workflow_id == AutoWorkflow.id)
            .filter(AutoWorkflowRun.status != "dispatch_accepted")
            .order_by(AutoWorkflowRun.started_at.desc(), AutoWorkflowRun.id.desc())
            .limit(limit)
        ):
            append("workflow", run, definition.name, None, getattr(definition, "chat_id", None), run.id)
        for run in (
            db.query(ProjectExecutionSession)
            .filter(ProjectExecutionSession.workflow_id.is_(None), ProjectExecutionSession.run_id.is_(None))
            .order_by(ProjectExecutionSession.started_at.desc(), ProjectExecutionSession.id.desc())
            .limit(limit)
        ):
            append(
                "execution",
                run,
                f"{run.route_backend or 'Agent'} execution",
                run.project_id,
                getattr(run, "chat_id", None),
            )
        ids = {row["project_id"] for row in rows if row["project_id"] is not None}
        names = dict(db.query(Project.id, Project.name).filter(Project.id.in_(ids)).all()) if ids else {}
    rows.sort(key=lambda row: (row["started_at"] or "", row["id"]), reverse=True)
    for row in rows:
        row["project_name"] = names.get(row["project_id"], "")
    return {
        "items": rows[:limit],
        "limit": limit,
        "description": (
            "Recent recorded workflow, automation and standalone execution runs. "
            "Linked records may describe stages of the same work."
        ),
    }


def list_time_entries(*, limit=100):
    """Closed Development thread time intervals for the Reports Time log tab."""
    limit = max(1, min(int(limit), 200))
    with get_session() as db:
        rows = (
            db.query(DevelopmentTimeEntry, DevelopmentWorkItem, Chat)
            .outerjoin(DevelopmentWorkItem, DevelopmentTimeEntry.work_item_id == DevelopmentWorkItem.id)
            .outerjoin(Chat, Chat.id == DevelopmentTimeEntry.chat_id)
            .order_by(DevelopmentTimeEntry.started_at.desc(), DevelopmentTimeEntry.id.desc())
            .limit(limit)
            .all()
        )
        items = []
        for entry, work_item, chat in rows:
            seconds = max(0, int(entry.seconds or 0))
            if seconds > MAX_REPORT_DURATION_SECONDS:
                continue
            title = None
            if work_item is not None and work_item.ticket_title:
                title = work_item.ticket_title
            elif chat is not None:
                title = chat.title or (chat.input or "")[:80] or f"Thread {entry.chat_id}"
            else:
                title = f"Thread {entry.chat_id}"
            items.append(
                {
                    "id": f"time:{entry.id}",
                    "chat_id": int(entry.chat_id),
                    "thread_title": title,
                    "ticket_title": (work_item.ticket_title if work_item else None) or "",
                    "ticket_key": (work_item.ticket_key if work_item else None) or "",
                    "board_key": (work_item.board_key if work_item else None) or "",
                    "started_at": entry.started_at.isoformat() if entry.started_at else None,
                    "ended_at": entry.ended_at.isoformat() if entry.ended_at else None,
                    "seconds": seconds,
                    "source": str(entry.source or "play"),
                }
            )
    return {
        "items": items,
        "limit": limit,
        "description": "Closed Development thread time intervals. The live counter is separate until paused.",
    }


def list_cost_entries(*, limit=100, display=None, project_id=None, client_key=None, run_id=None, source=None):
    """Reports facade over the durable cost ledger."""
    from distr.core.cost_ledger.service import list_entries

    return list_entries(
        limit=limit,
        display=display,
        project_id=project_id,
        client_key=client_key,
        run_id=run_id,
        source=source,
    )


def cost_rollups(*, display=None):
    """Project / client / SAST-day cost rollups for the Reports Costs tab."""
    from distr.core.cost_ledger.service import cost_rollups as _rollups

    return _rollups(display=display)
