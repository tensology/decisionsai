"""Idempotently register Ember & Crust as a real DecisionsAI project and board."""

from __future__ import annotations

import json
from pathlib import Path

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow


PROJECT_NAME = "Ember & Crust Pizza House"
BOARD_NAME = "Ember & Crust Delivery"
WORKFLOW_NAME = "Development: Ticket to Implementation"
PROJECT_FOLDER = Path(__file__).resolve().parent


def register() -> dict:
    work = json.loads((PROJECT_FOLDER / "project-work.json").read_text(encoding="utf-8"))
    with get_session() as db:
        workflow = db.query(AutoWorkflow).filter(AutoWorkflow.name == WORKFLOW_NAME).first()
        if not workflow:
            raise RuntimeError(f'Required workflow "{WORKFLOW_NAME}" is not installed')

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
            "free_only": True,
            "prefer_local": True,
            "independent_validation": True,
            "max_correction_iterations": 3,
            "preferred_model": "ornith:35b",
            "complexity_routing": {
                "low": {"backend": "pi", "model": "ornith:35b"},
                "medium": {"backend": "pi", "model": "ornith:35b"},
                "high": {"backend": "pi", "model": "ornith:35b"},
            },
        }, sort_keys=True)
        project.kanban_board_id = board.id

        lane = db.query(KanbanLane).filter(
            KanbanLane.board_id == board.id,
            KanbanLane.name == "Scoped work",
        ).first()
        if not lane:
            lane = KanbanLane(board_id=board.id, name="Scoped work", position=0)
            db.add(lane)
            db.flush()

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
        return {
            "project_id": project.id,
            "board_id": board.id,
            "workflow_id": workflow.id,
            "ticket_ids": [ticket.id for ticket in tickets],
            "folder": str(PROJECT_FOLDER),
            "model": project.coding_backend_model,
        }


if __name__ == "__main__":
    print(json.dumps(register(), indent=2))
