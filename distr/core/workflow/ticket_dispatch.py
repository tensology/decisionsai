"""Build normalized workflow-run inputs from ticket records."""

from __future__ import annotations

import logging
from typing import Any

from distr.core.db import get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.db.projects import Project
from distr.core.kanban.lifecycle import DELIVERY_DONE_LANE
from distr.core.kanban.ticket_policy import normalize_ticket_complexity

logger = logging.getLogger(__name__)

IMPLICIT_QUEUE_TERMINAL = ("completed", "completed_noop", "cancelled", "canceled")


def _ticket_already_finished(ticket: KanbanTicket) -> bool:
    status = str(ticket.workflow_status or "").strip().lower()
    lane_name = str(ticket.lane.name or "").strip().lower() if ticket.lane is not None else ""
    return status in IMPLICIT_QUEUE_TERMINAL or lane_name == DELIVERY_DONE_LANE.lower()


def implicit_queue_ticket_error(ticket: KanbanTicket | None, workflow_id: int,
                                board_id: int | None, project_id: int | str | None) -> str | None:
    if ticket is None or ticket.linked_workflow_id != workflow_id:
        return "The queued ticket is no longer linked to this workflow."
    lane = ticket.lane
    if board_id is None or lane is None or lane.board_id != board_id:
        return "The queued ticket is no longer on this board."
    current_project = ticket.linked_project_id or (lane.board.default_project_id if lane.board else None)
    if str(current_project or "") != str(project_id or ""):
        return "The queued ticket's project assignment changed."
    if _ticket_already_finished(ticket):
        return "The queued ticket has already finished or been cancelled."
    return None


def compact_ticket_run_ref(item: dict[str, Any]) -> dict[str, Any]:
    """Return the durable queue identity without embedding hydrated ticket context."""
    if not isinstance(item, dict) or item.get("ticket_id") is None:
        raise ValueError("A queued ticket reference requires ticket_id")
    ref = {"ticket_id": int(item["ticket_id"])}
    if item.get("board_id") is not None:
        ref["board_id"] = int(item["board_id"])
    if "project_id" in item:
        ref["project_id"] = int(item["project_id"]) if item["project_id"] is not None else None
    return ref


def ticket_group_scope_error(
    ticket: KanbanTicket | None, workflow_id: int, ref: dict[str, Any], *, automatic: bool = False,
) -> str | None:
    if ticket is None:
        return f"Queued ticket #{ref['ticket_id']} was not found."
    if ticket.linked_workflow_id not in (None, workflow_id):
        return f"Queued ticket #{ticket.id} is now linked to a different workflow."
    lane = ticket.lane
    if ref.get("board_id") is not None and (lane is None or lane.board_id != ref["board_id"]):
        return f"Queued ticket #{ticket.id} is no longer on its selected board."
    project_id = ticket.linked_project_id or (lane.board.default_project_id if lane and lane.board else None)
    if "project_id" in ref and project_id != ref["project_id"]:
        return f"Queued ticket #{ticket.id} is no longer assigned to its selected project."
    if automatic and _ticket_already_finished(ticket):
        return "This ticket has already finished or been cancelled."
    return None


def snapshot_ticket_group_ref(db, item: dict[str, Any], workflow_id: int) -> dict[str, Any]:
    ref = compact_ticket_run_ref(item)
    ticket = db.get(KanbanTicket, ref["ticket_id"])
    error = ticket_group_scope_error(ticket, workflow_id, ref)
    if error:
        raise ValueError(error)
    lane = ticket.lane
    project_id = ticket.linked_project_id or (lane.board.default_project_id if lane and lane.board else None)
    return compact_ticket_run_ref({**ref, "board_id": lane.board_id if lane else None, "project_id": project_id})


def hydrate_ticket_run_ref(item: dict[str, Any], workflow_id: int, *, automatic: bool = False) -> dict[str, Any]:
    """Load complete context only when a queued ticket is about to execute."""
    ref = compact_ticket_run_ref(item)
    return build_ticket_run_item(int(ref["ticket_id"]), int(workflow_id), expected_scope=ref, automatic=automatic)


def build_ticket_run_item(
    ticket_id: int, workflow_id: int, *, expected_scope: dict[str, Any] | None = None, automatic: bool = False,
) -> dict[str, Any]:
    """Return context and metadata needed to dispatch one queued ticket."""
    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        if not ticket:
            raise ValueError(f"Ticket #{ticket_id} was not found")
        if ticket.linked_workflow_id and int(ticket.linked_workflow_id) != int(workflow_id):
            raise ValueError(f"Ticket #{ticket_id} belongs to workflow #{ticket.linked_workflow_id}")
        if expected_scope:
            error = ticket_group_scope_error(ticket, workflow_id, expected_scope, automatic=automatic)
            if error:
                raise ValueError(error)
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
