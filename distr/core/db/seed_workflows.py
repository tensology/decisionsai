"""Seed the single canonical user-visible Development workflow."""

from __future__ import annotations

import logging

from distr.core.db import get_session
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
from distr.core.workflow.developer_workflow import DEVELOPER_WORKFLOW_NAME

logger = logging.getLogger(__name__)

WORKFLOW_SEEDS = {DEVELOPER_WORKFLOW_NAME: "development-ticket-to-implementation"}


def seed_workflows(force_reset: bool = False, workflow_names=None):
    """Create/repair Development without recreating legacy role workflow tabs."""
    requested = set(workflow_names or [DEVELOPER_WORKFLOW_NAME])
    aliases = {DEVELOPER_WORKFLOW_NAME, "Development: Ticket to Implementation"}
    if not requested.intersection(aliases):
        return {
            "seeded_count": 0,
            "skipped_count": 0,
            "seeded_names": [],
            "skipped_names": [],
            "force_reset": force_reset,
        }

    with get_session() as db:
        workflows = db.query(AutoWorkflow).filter(AutoWorkflow.name.in_(aliases)).all()
        active = [row for row in workflows if str(row.status or "").lower() != "archived"]
        workflow = max(active or workflows, key=lambda row: (len(row.runs), len(row.steps)), default=None)
        if workflow is None:
            workflow = AutoWorkflow(
                name=DEVELOPER_WORKFLOW_NAME,
                description="Canonical end-to-end software development workflow.",
                status="active",
                workflow_type="manual",
            )
            db.add(workflow)
            db.flush()
        workflow.name = DEVELOPER_WORKFLOW_NAME
        workflow.status = "active"
        workflow.workflow_type = "manual"
        workflow_id = int(workflow.id)
        has_steps = db.query(AutoWorkflowStep).filter(AutoWorkflowStep.workflow_id == workflow_id).count() > 0
        db.commit()

    if has_steps and not force_reset:
        return {
            "seeded_count": 0,
            "skipped_count": 1,
            "seeded_names": [],
            "skipped_names": [DEVELOPER_WORKFLOW_NAME],
            "force_reset": force_reset,
        }

    from distr.core.workflow.loop_presets import apply_loop_preset

    result = apply_loop_preset(workflow_id, DEVELOPER_WORKFLOW_NAME, mode="replace")
    if not result.get("success"):
        logger.warning("Could not seed Development workflow: %s", result.get("error"))
        return {
            "seeded_count": 0,
            "skipped_count": 0,
            "seeded_names": [],
            "skipped_names": [],
            "force_reset": force_reset,
            "error": result.get("error"),
        }
    return {
        "seeded_count": 1,
        "skipped_count": 0,
        "seeded_names": [DEVELOPER_WORKFLOW_NAME],
        "skipped_names": [],
        "force_reset": force_reset,
    }
