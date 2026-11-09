"""Read-only Development run history and time log, with explicit source identities."""
from sqlalchemy import func, or_

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
        reset_ids = (
            db.query(
                DevelopmentTimeEntry.chat_id.label("chat_id"),
                func.max(DevelopmentTimeEntry.id).label("reset_id"),
            )
            .filter(DevelopmentTimeEntry.source == "reset")
            .group_by(DevelopmentTimeEntry.chat_id)
            .subquery()
        )
        rows = (
            db.query(DevelopmentTimeEntry, DevelopmentWorkItem, Chat)
            .outerjoin(DevelopmentWorkItem, DevelopmentTimeEntry.work_item_id == DevelopmentWorkItem.id)
            .outerjoin(Chat, Chat.id == DevelopmentTimeEntry.chat_id)
            .outerjoin(reset_ids, reset_ids.c.chat_id == DevelopmentTimeEntry.chat_id)
            .filter(
                or_(
                    reset_ids.c.reset_id.is_(None),
                    DevelopmentTimeEntry.id > reset_ids.c.reset_id,
                )
            )
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


def _known_text(value) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def list_work_history(*, limit=100):
    """One history list of runs and closed timers.

    Descriptions, statuses, and dates come from stored fields. A missing field
    stays null. Execution rows do not invent a name from the harness. Row
    durations are not merged; clock time lives on the period report.
    """
    limit = max(1, min(int(limit), 200))
    runs = list_reports(limit=limit)["items"]
    times = list_time_entries(limit=limit)["items"]
    execution_ids = []
    for row in runs:
        if row.get("kind") != "execution":
            continue
        try:
            execution_ids.append(int(str(row["id"]).split(":", 1)[1]))
        except (IndexError, ValueError):
            continue
    ticket_by_session = {}
    chat_project = {}
    chat_ids = {int(row["chat_id"]) for row in times if row.get("chat_id") is not None}
    if execution_ids or chat_ids:
        with get_session() as db:
            if execution_ids:
                sessions = db.query(ProjectExecutionSession).filter(ProjectExecutionSession.id.in_(execution_ids)).all()
                ticket_ids = {session.ticket_id for session in sessions if session.ticket_id}
                titles = {}
                if ticket_ids:
                    from distr.core.db.kanban import KanbanTicket
                    titles = dict(db.query(KanbanTicket.id, KanbanTicket.title).filter(KanbanTicket.id.in_(ticket_ids)).all())
                ticket_by_session = {int(session.id): _known_text(titles.get(session.ticket_id)) for session in sessions}
            if chat_ids:
                chats = db.query(Chat.id, Chat.project_id).filter(Chat.id.in_(chat_ids)).all()
                project_ids = {pid for _, pid in chats if pid}
                names = dict(db.query(Project.id, Project.name).filter(Project.id.in_(project_ids)).all()) if project_ids else {}
                chat_project = {int(cid): _known_text(names.get(pid)) if pid else None for cid, pid in chats}

    items = []
    for row in runs:
        description = _known_text(row.get("name"))
        if row.get("kind") == "execution":
            try:
                session_id = int(str(row["id"]).split(":", 1)[1])
            except (IndexError, ValueError):
                session_id = None
            description = ticket_by_session.get(session_id) if session_id is not None else None
        items.append({
            "id": row["id"],
            "description": description,
            "status": _known_text(row.get("status")),
            "started_at": row.get("started_at") or None,
            "ended_at": row.get("completed_at") or None,
            "duration_seconds": row.get("duration_seconds"),
            "project_name": _known_text(row.get("project_name")),
            "chat_id": row.get("chat_id"),
        })
    for row in times:
        fallback = f"Thread {row.get('chat_id')}"
        description = _known_text(row.get("ticket_title")) or _known_text(row.get("thread_title"))
        if description == fallback:
            description = None
        items.append({
            "id": row["id"],
            "description": description,
            "status": None,
            "started_at": row.get("started_at") or None,
            "ended_at": row.get("ended_at") or None,
            "duration_seconds": row.get("seconds"),
            "project_name": chat_project.get(int(row["chat_id"])) if row.get("chat_id") is not None else None,
            "chat_id": row.get("chat_id"),
        })
    items.sort(key=lambda row: (row["started_at"] is not None, row["started_at"] or "", row["id"]), reverse=True)
    return {
        "items": items[:limit],
        "limit": limit,
        "counting": {
            "rows": "Each row is one recorded run or closed timer interval. Rows are not merged.",
            "duration": "Duration is that row only. Adding durations double-counts overlap.",
            "wall_clock": "Clock time on Stats unions overlapping intervals and counts that span once.",
            "unknown": "Missing description, status, start, or end stays unknown. Nothing is filled in.",
        },
    }
