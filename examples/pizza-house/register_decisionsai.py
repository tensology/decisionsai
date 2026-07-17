"""Idempotently register Ember & Crust as a real DecisionsAI project and board."""

from __future__ import annotations

import json
from pathlib import Path

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow
from distr.core.kanban.lifecycle import (
    DELIVERY_DONE_LANE,
    DELIVERY_SOURCE_LANE,
    ensure_delivery_lanes,
)
from distr.core.workflow.developer_workflow import (
    DEVELOPER_WORKFLOW_NAME,
    DEVELOPER_WORKFLOW_RUN_SETTINGS,
)


PROJECT_NAME = "Ember & Crust Pizza House"
BOARD_NAME = "Ember & Crust Delivery"
WORKFLOW_NAME = DEVELOPER_WORKFLOW_NAME
PROJECT_FOLDER = Path(__file__).resolve().parent
LEGACY_PROVIDER_PROOF_TITLES = {
    "Harden Pizza House menu data integrity with independent model review",
    "Independent HY3 review of Pizza House menu integrity",
    "Add reusable Pizza House menu validation",
}


def register() -> dict:
    work = json.loads((PROJECT_FOLDER / "project-work.json").read_text(encoding="utf-8"))
    with get_session() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.name == WORKFLOW_NAME).first()
        if not workflow:
            workflow = AutoWorkflow(
                name=WORKFLOW_NAME,
                description="Canonical ticket-to-green software engineering workflow.",
                status="active",
                workflow_type="manual",
            )
            db.add(workflow)
            db.flush()
        workflow.status = "active"
        workflow.run_settings = json.dumps(DEVELOPER_WORKFLOW_RUN_SETTINGS, sort_keys=True)

        project = db.query(Project).filter(Project.name == PROJECT_NAME).first()
        if not project:
            project = Project(name=PROJECT_NAME)
            db.add(project)
            db.flush()
        project.description = work["objective"]
        project.folder_location = str(PROJECT_FOLDER)
        project.coding_backend = "pi"
        project.coding_backend_model = "ornith:35b"

        board = db.query(KanbanBoard).filter(KanbanBoard.name == BOARD_NAME).first()
        if not board:
            board = KanbanBoard(name=BOARD_NAME, source="database", color="#d64b2a")
            db.add(board)
            db.flush()
        board.description = work["objective"]
        board.default_project_id = project.id
        board.default_workflow_id = workflow.id
        board.send_to_cli = False
        board.orchestrator_policy = json.dumps({
            "auto_route_models": True,
            "prefer_free_local": True,
            "independent_validation": True,
            "max_correction_iterations": 3,
            "require_approval_for_override": True,
        }, sort_keys=True)
        board.agent_source_lane = DELIVERY_SOURCE_LANE
        board.agent_done_lane = DELIVERY_DONE_LANE
        project.kanban_board_id = board.id

        # Early versions of this acceptance fixture incorrectly used a single
        # workflow-oriented bucket. Preserve its tickets while migrating the
        # board to the same delivery lifecycle as every real project board.
        lane = ensure_delivery_lanes(
            db,
            board.id,
            legacy_source_names=("Scoped work",),
        )[DELIVERY_SOURCE_LANE]

        # Remove provider benchmark artifacts from the human acceptance board.
        # Model/provider coverage belongs in automated tests and run history,
        # never in the names of product tickets or workflow steps.
        db.query(KanbanTicket).filter(
            KanbanTicket.lane_id == lane.id,
            KanbanTicket.title.in_(LEGACY_PROVIDER_PROOF_TITLES),
        ).delete(synchronize_session=False)

        tickets = []
        for position, spec in enumerate(work["tickets"]):
            ticket = db.query(KanbanTicket).filter(
                KanbanTicket.lane_id == lane.id,
                KanbanTicket.title == spec["title"],
            ).first()
            if not ticket:
                ticket = KanbanTicket(lane_id=lane.id, title=spec["title"])
                db.add(ticket)
            ticket.description = (
                f"Project objective: {work['objective']}\n\n"
                f"Required capabilities: {', '.join(spec.get('required_capabilities') or [])}."
            )
            ticket.priority = "high" if position in {1, 3} else "medium"
            ticket.complexity = spec.get("complexity") or "medium"
            ticket.position = position
            ticket.workflow_queue_position = position
            ticket.workflow_status = None
            ticket.time_spent = ""
            ticket.linked_project_id = project.id
            ticket.linked_workflow_id = workflow.id
            ticket.send_to_cli = False
            ticket.source_provider = "manual"
            ticket.source_label = "Ember & Crust project scope"
            tickets.append(ticket)

        db.commit()
        result = {
            "project_id": project.id,
            "board_id": board.id,
            "workflow_id": workflow.id,
            "ticket_ids": [ticket.id for ticket in tickets],
            "folder": str(PROJECT_FOLDER),
            "model": project.coding_backend_model,
        }

    from distr.core.workflow.loop_presets import apply_loop_preset

    applied = apply_loop_preset(result["workflow_id"], WORKFLOW_NAME, mode="replace")
    if not applied.get("success"):
        raise RuntimeError(applied.get("error") or "Could not apply developer workflow preset")
    result["workflow_steps"] = int(applied.get("step_count") or 0)
    result["auto_route_models"] = True
    return result


if __name__ == "__main__":
    print(json.dumps(register(), indent=2))
