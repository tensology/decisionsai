"""Canonical developer workflow identity, defaults, and consolidation."""

from __future__ import annotations

from typing import Any


DEVELOPER_WORKFLOW_NAME = "Development"
DEVELOPER_WORKFLOW_SLUG = "development-ticket-to-implementation"
DEVELOPER_WORKFLOW_VERSION = 12

DEVELOPER_WORKFLOW_RUN_SETTINGS: dict[str, Any] = {
    "execution_mode": "sequential",
    "concurrency_scope": "project",
    "max_parallel_tickets": 1,
    "branch_per_ticket": True,
    "auto_route_models": True,
    "adaptive_multi_model_enabled": True,
    "max_parallel_evaluators": 2,
    "prefer_free_local": True,
    "independent_validation": True,
    "allow_provider_failover": True,
    "memory_enabled": True,
    "load_workflow_memory": True,
    "load_project_memory": True,
    "capture_memory_deltas": True,
    "capture_failures_and_lessons": True,
}


def get_or_create_development_workflow() -> int:
    """Return the one canonical user-visible development workflow."""
    from distr.core.db import get_session
    from distr.core.db.workflow import AutoWorkflow

    aliases = {DEVELOPER_WORKFLOW_NAME, "Development: Ticket to Implementation"}
    with get_session() as db:
        rows = db.query(AutoWorkflow).filter(AutoWorkflow.name.in_(aliases)).all()
        active = [row for row in rows if str(row.status or "").lower() != "archived"]
        candidates = active or rows
        if candidates:
            workflow = max(candidates, key=lambda row: (len(row.runs), len(row.steps), -(row.id or 0)))
        else:
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
        has_steps = bool(workflow.steps)
        db.commit()
    if not has_steps:
        from distr.core.workflow.loop_presets import apply_loop_preset

        applied = apply_loop_preset(workflow_id, DEVELOPER_WORKFLOW_NAME, mode="replace")
        if not applied.get("success"):
            raise RuntimeError(applied.get("error") or "Could not initialise Development")
    return workflow_id


def consolidate_development_workflows(*, refresh_models: bool = True) -> dict[str, Any]:
    """Collapse visible development/test tabs into one history-preserving workflow."""
    import json

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanTicket
    from distr.core.db.time import utc_now_naive
    from distr.core.db.workflow import (
        AutoWorkflow,
        AutoWorkflowRun,
        AutoWorkflowStep,
        AutoWorkflowVariable,
    )

    aliases = {DEVELOPER_WORKFLOW_NAME, "Development: Ticket to Implementation"}
    user_types = {"manual", "instruction", "review", "deploy"}
    with get_session() as db:
        candidates = db.query(AutoWorkflow).filter(AutoWorkflow.name.in_(aliases)).all()
        if candidates:
            def score(row: AutoWorkflow) -> tuple[int, int, int]:
                linked = db.query(KanbanTicket).filter(KanbanTicket.linked_workflow_id == row.id).count()
                return linked, len(row.runs), -(row.id or 0)
            canonical = max(candidates, key=score)
        else:
            canonical = AutoWorkflow(
                name=DEVELOPER_WORKFLOW_NAME,
                description="Canonical end-to-end software development workflow.",
                status="active",
                workflow_type="manual",
            )
            db.add(canonical)
            db.flush()

        visible = db.query(AutoWorkflow).filter(AutoWorkflow.workflow_type.in_(sorted(user_types))).all()
        obsolete = [row for row in visible if row.id != canonical.id]
        obsolete_ids = [int(row.id) for row in obsolete]
        obsolete_names = [str(row.name or f"Workflow {row.id}") for row in obsolete]

        existing_positions = [
            int(value or 0) for (value,) in
            db.query(KanbanTicket.workflow_queue_position)
            .filter(KanbanTicket.linked_workflow_id == canonical.id)
            .all()
        ]
        next_position = max(existing_positions or [0]) + 1
        migrated_tickets = 0
        if obsolete_ids:
            tickets = (
                db.query(KanbanTicket)
                .filter(KanbanTicket.linked_workflow_id.in_(obsolete_ids))
                .order_by(KanbanTicket.workflow_queue_position, KanbanTicket.id)
                .all()
            )
            for ticket in tickets:
                ticket.linked_workflow_id = canonical.id
                ticket.workflow_queue_position = next_position
                next_position += 1
                migrated_tickets += 1
            db.query(KanbanBoard).filter(KanbanBoard.default_workflow_id.in_(obsolete_ids)).update(
                {KanbanBoard.default_workflow_id: canonical.id}, synchronize_session=False
            )
            running = db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.workflow_id.in_(obsolete_ids),
                AutoWorkflowRun.status.in_(["running", "waiting"]),
            ).all()
            for run in running:
                run.status = "cancelled"
                run.completed_at = utc_now_naive()
            for workflow in obsolete:
                workflow.status = "archived"

        try:
            existing_run_settings = json.loads(canonical.run_settings or "{}") or {}
        except Exception:
            existing_run_settings = {}
        needs_definition_upgrade = (
            int(existing_run_settings.get("canonical_workflow_version") or 0)
            < DEVELOPER_WORKFLOW_VERSION
        )
        historical_workflow_id = None
        if needs_definition_upgrade and (canonical.steps or canonical.runs or canonical.variables):
            # Never delete live run/step evidence while replacing an obsolete definition.
            # Move the complete old definition into a hidden audit workflow first.
            history = AutoWorkflow(
                name=f"{canonical.name} — history before v{DEVELOPER_WORKFLOW_VERSION}",
                description=(
                    "Read-only workflow definition and run history preserved during "
                    "Development workflow consolidation."
                ),
                status="archived",
                workflow_type="audit",
                run_settings=canonical.run_settings,
                workflow_input=canonical.workflow_input,
                context_rules=canonical.context_rules,
            )
            db.add(history)
            db.flush()
            historical_workflow_id = int(history.id)
            db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.workflow_id == canonical.id
            ).update({AutoWorkflowStep.workflow_id: history.id}, synchronize_session=False)
            db.query(AutoWorkflowVariable).filter(
                AutoWorkflowVariable.workflow_id == canonical.id
            ).update({AutoWorkflowVariable.workflow_id: history.id}, synchronize_session=False)
            db.query(AutoWorkflowRun).filter(
                AutoWorkflowRun.workflow_id == canonical.id
            ).update({AutoWorkflowRun.workflow_id: history.id}, synchronize_session=False)
            db.flush()

        canonical.name = DEVELOPER_WORKFLOW_NAME
        canonical.status = "active"
        canonical.workflow_type = "manual"
        canonical.description = (
            "One durable development workflow for scoped tickets and large requests: context and memory, planning, "
            "implementation, independent review, automated/browser validation, correction, reporting, and learning."
        )
        db.commit()
        canonical_id = int(canonical.id)

    from distr.core.workflow.loop_presets import apply_loop_preset

    if needs_definition_upgrade:
        applied = apply_loop_preset(canonical_id, DEVELOPER_WORKFLOW_NAME, mode="replace")
        if not applied.get("success"):
            raise RuntimeError(applied.get("error") or "Could not apply the Development preset")
    else:
        with get_session() as db:
            step_count = db.query(AutoWorkflowStep).filter(
                AutoWorkflowStep.workflow_id == canonical_id
            ).count()
        applied = {"success": True, "step_count": step_count}

    with get_session() as db:
        canonical = db.query(AutoWorkflow).filter(AutoWorkflow.id == canonical_id).one()
        try:
            run_settings = json.loads(canonical.run_settings or "{}") or {}
        except Exception:
            run_settings = {}
        run_settings.update(DEVELOPER_WORKFLOW_RUN_SETTINGS)
        run_settings["canonical_workflow_version"] = DEVELOPER_WORKFLOW_VERSION
        canonical.run_settings = json.dumps(run_settings, sort_keys=True)
        canonical.status = "active"
        db.commit()

    try:
        from distr.core.workspace_memory.pickup_handoff import append_ledger

        append_ledger(
            "workflows",
            canonical_id,
            event_type="workflow_consolidated",
            message=(
                "Consolidated obsolete visible workflows into Development. Historical runs remain archived. "
                f"Archived: {', '.join(obsolete_names) or 'none'}."
            ),
            extra={
                "archived_workflow_ids": obsolete_ids,
                "historical_workflow_id": historical_workflow_id,
                "migrated_tickets": migrated_tickets,
            },
        )
    except Exception:
        pass

    try:
        from distr.core.workspace_memory.provision import bootstrap_workflow
        from distr.core.workspace_memory.stages import sync_workflow_stages

        bootstrap_workflow(canonical_id, force=True)
        sync_workflow_stages(canonical_id)
    except Exception:
        pass

    model_policy = None
    if refresh_models:
        try:
            from distr.core.project_cli_backends.policy_manager import (
                apply_model_policy_plan,
                build_model_policy_plan,
            )

            plan = build_model_policy_plan(
                scope="workflow", workflow_id=canonical_id, mode="auto", preference="free"
            )
            model_policy = apply_model_policy_plan(plan)
        except Exception:
            model_policy = None

    return {
        "workflow_id": canonical_id,
        "name": DEVELOPER_WORKFLOW_NAME,
        "step_count": int(applied.get("step_count") or 0),
        "archived_workflow_ids": obsolete_ids,
        "archived_workflow_names": obsolete_names,
        "historical_workflow_id": historical_workflow_id,
        "migrated_tickets": migrated_tickets,
        "model_policy_applied": model_policy is not None,
    }
