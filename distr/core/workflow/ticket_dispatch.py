"""Build normalized workflow-run inputs from ticket records."""

from __future__ import annotations

import logging
from typing import Any

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.kanban.ticket_policy import normalize_ticket_complexity

logger = logging.getLogger(__name__)


def build_ticket_run_item(ticket_id: int, workflow_id: int) -> dict[str, Any]:
    """Return context and metadata needed to dispatch one queued ticket."""
    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        if not ticket:
            raise ValueError(f"Ticket #{ticket_id} was not found")
        if ticket.linked_workflow_id and int(ticket.linked_workflow_id) != int(workflow_id):
            raise ValueError(f"Ticket #{ticket_id} belongs to workflow #{ticket.linked_workflow_id}")
        lane = db.query(KanbanLane).filter(KanbanLane.id == ticket.lane_id).first() if ticket.lane_id else None
        board = db.query(KanbanBoard).filter(KanbanBoard.id == lane.board_id).first() if lane else None
        board_id = int(board.id) if board else None
        project_id = ticket.linked_project_id or (board.default_project_id if board else None)
        project = db.query(Project).filter(Project.id == int(project_id)).first() if project_id else None

        context = f"Ticket: {ticket.title or ('#' + str(ticket.id))}"
        workflow_brief = None
        try:
            from distr.core.kanban.ticket_workflow_brief import build_ticket_workflow_brief, render_ticket_workflow_brief

            workflow_brief = build_ticket_workflow_brief(
                db,
                ticket.id,
                board_id=board_id,
                board_name=board.name if board else None,
                project_id=project_id,
            )
            context = render_ticket_workflow_brief(workflow_brief)
        except Exception:
            if ticket.description:
                context += f"\n\nDescription: {ticket.description}"

        execution_route = {}
        if project:
            try:
                from distr.core.orchestrator_routing import resolve_execution_route

                execution_route = resolve_execution_route(
                    project=project,
                    ticket=ticket,
                    board=board,
                    complexity=normalize_ticket_complexity(ticket.complexity),
                    emit_event=False,
                    allow_orchestrator_override=False,
                ).to_route_dict()
            except Exception:
                logger.debug("Ticket group route resolution failed ticket=%s", ticket.id, exc_info=True)

        metadata = {
            "source_type": "ticket_group",
            "board_id": board_id,
            "board_name": board.name if board else None,
            "ticket_id": int(ticket.id),
            "ticket_title": ticket.title or "",
            "project_id": str(project_id) if project_id else None,
            "project_name": project.name if project else None,
            "project_folder": project.folder_location if project else None,
            "execution_route": execution_route,
            "phase": "planning",
        }
        if workflow_brief:
            metadata["ticket_workflow_brief"] = workflow_brief
        metadata = {key: value for key, value in metadata.items() if value not in (None, "")}
        return {
            "ticket_id": int(ticket.id),
            "board_id": board_id,
            "context": context,
            "run_metadata": metadata,
        }
