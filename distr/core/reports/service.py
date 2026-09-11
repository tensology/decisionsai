"""Read-only Development run history, with explicit source identities."""
from distr.core.db import get_session
from distr.core.db.automation import Automation, AutomationRun
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun
from distr.core.db.kanban import ProjectExecutionSession
from distr.core.db.projects import Project


def list_reports(*, limit=100):
    limit = max(1, min(int(limit), 200))
    rows = []
    def append(kind, record, name, project_id=None):
        duration = max(0, int((record.completed_at - record.started_at).total_seconds())) if record.completed_at and record.started_at else None
        rows.append({"id": f"{kind}:{record.id}", "kind": kind, "name": name, "status": record.status or "unknown", "project_id": project_id, "started_at": record.started_at.isoformat() if record.started_at else None, "completed_at": record.completed_at.isoformat() if record.completed_at else None, "duration_seconds": duration})
    with get_session() as db:
        for run, definition in db.query(AutomationRun, Automation).join(Automation, AutomationRun.automation_id == Automation.id).filter(AutomationRun.status != "dispatch_accepted").order_by(AutomationRun.started_at.desc(), AutomationRun.id.desc()).limit(limit):
            append("automation", run, definition.name, definition.project_id)
        for run, definition in db.query(AutoWorkflowRun, AutoWorkflow).join(AutoWorkflow, AutoWorkflowRun.workflow_id == AutoWorkflow.id).filter(AutoWorkflowRun.status != "dispatch_accepted").order_by(AutoWorkflowRun.started_at.desc(), AutoWorkflowRun.id.desc()).limit(limit):
            append("workflow", run, definition.name)
        for run in db.query(ProjectExecutionSession).filter(ProjectExecutionSession.workflow_id.is_(None), ProjectExecutionSession.run_id.is_(None)).order_by(ProjectExecutionSession.started_at.desc(), ProjectExecutionSession.id.desc()).limit(limit):
            append("execution", run, f"{run.route_backend or 'Agent'} execution", run.project_id)
        ids = {row["project_id"] for row in rows if row["project_id"] is not None}
        names = dict(db.query(Project.id, Project.name).filter(Project.id.in_(ids)).all()) if ids else {}
    rows.sort(key=lambda row: (row["started_at"] or "", row["id"]), reverse=True)
    for row in rows:
        row["project_name"] = names.get(row["project_id"], "")
    return {"items": rows[:limit], "limit": limit, "description": "Recent recorded workflow, automation and standalone execution runs. Linked records may describe stages of the same work."}
