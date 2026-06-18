"""Resolve workflow IDs from numeric id or fuzzy name."""

from __future__ import annotations

from typing import Any


def resolve_workflow_id(
    *,
    workflow_id: int | None = None,
    workflow_name: str | None = None,
    limit: int = 10,
) -> tuple[int | None, str | None]:
    """Return (workflow_id, error_message). error_message set on ambiguity or not found."""
    if workflow_id is not None:
        try:
            wid = int(workflow_id)
        except (TypeError, ValueError):
            return None, f"Invalid workflow_id: {workflow_id!r}"
        from distr.core.workflow.service import get_workflow

        if not get_workflow(wid):
            return None, f"Workflow {wid} not found."
        return wid, None

    name = (workflow_name or "").strip()
    if not name:
        return None, "Provide workflow_id or workflow_name."

    from distr.core.workflow.service import list_workflows

    matches = list_workflows(limit=limit, search=name)
    if not matches:
        return None, f"No workflow matching {name!r}."

    exact = [w for w in matches if (w.get("name") or "").strip().lower() == name.lower()]
    if len(exact) == 1:
        return int(exact[0]["id"]), None
    if len(exact) > 1:
        ids = ", ".join(f"#{w['id']} {w.get('name')}" for w in exact[:5])
        return None, f"Ambiguous workflow name {name!r}. Matches: {ids}"

    if len(matches) == 1:
        return int(matches[0]["id"]), None

    ids = ", ".join(f"#{w['id']} {w.get('name')}" for w in matches[:8])
    return None, f"Ambiguous workflow name {name!r}. Did you mean: {ids}"


def validate_run_belongs_to_workflow(run_id: int, workflow_id: int) -> bool:
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflowRun

    with get_session() as session:
        run = session.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
        if not run:
            return False
        return int(run.workflow_id) == int(workflow_id)
