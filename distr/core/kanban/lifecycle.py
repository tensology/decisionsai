"""Canonical lane lifecycle for DecisionsAI delivery boards."""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func

from distr.core.db.kanban import KanbanLane, KanbanTicket


DELIVERY_LANES: tuple[str, ...] = (
    "Backlog",
    "In Progress",
    "QA",
    "Complete",
)

DELIVERY_SOURCE_LANE = DELIVERY_LANES[0]
DELIVERY_DONE_LANE = DELIVERY_LANES[-1]


def require_automation_lane(lane_name: str) -> str:
    """Validate a lane requested by an agent or background automation.

    ``Complete`` is an acceptance decision, not an execution state.  Keeping
    this guard independent of the workflow dispatcher lets every automated
    entry point (Initiative, chat tools, recovery jobs) enforce the same human
    boundary before it writes a ticket directly.
    """
    target_name = str(lane_name or "").strip()
    if target_name.lower() == DELIVERY_DONE_LANE.lower():
        raise ValueError("Only a human may move a QA ticket to Complete")
    return target_name


def ensure_delivery_lanes(
    session: Any,
    board_id: int,
    *,
    legacy_source_names: Iterable[str] = (),
) -> dict[str, KanbanLane]:
    """Ensure a board has the canonical lifecycle without dropping tickets.

    A legacy source lane is renamed to Backlog only when Backlog does not
    already exist. Renaming retains the lane id, so its ticket relationships
    remain untouched.
    """
    lanes = (
        session.query(KanbanLane)
        .filter(KanbanLane.board_id == int(board_id))
        .order_by(KanbanLane.position, KanbanLane.id)
        .all()
    )
    by_name = {str(item.name or "").strip().lower(): item for item in lanes}
    source = by_name.get(DELIVERY_SOURCE_LANE.lower())
    if source is None:
        for legacy_name in legacy_source_names:
            legacy_key = str(legacy_name or "").strip().lower()
            legacy = by_name.get(legacy_key)
            if legacy is None:
                continue
            legacy.name = DELIVERY_SOURCE_LANE
            source = legacy
            by_name[DELIVERY_SOURCE_LANE.lower()] = legacy
            by_name.pop(legacy_key, None)
            break

    for position, lane_name in enumerate(DELIVERY_LANES):
        lane = by_name.get(lane_name.lower())
        if lane is None:
            lane = KanbanLane(
                board_id=int(board_id),
                name=lane_name,
                position=position,
            )
            session.add(lane)
            session.flush()
            by_name[lane_name.lower()] = lane
        else:
            lane.position = position
    return {name: by_name[name.lower()] for name in DELIVERY_LANES}


def move_ticket_to_delivery_lane(
    session: Any,
    ticket_id: int,
    lane_name: str,
) -> bool:
    """Move a local ticket through the canonical human-visible lifecycle.

    DecisionsAI owns Backlog -> In Progress -> QA.  Complete is deliberately
    excluded: a human must accept QA and make that final move themselves.
    Returns whether the ticket actually changed lanes.
    """
    target_name = require_automation_lane(lane_name)
    if target_name not in {"In Progress", "QA"}:
        raise ValueError("Automation may only move tickets to In Progress or QA")
    ticket = session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
    if not ticket:
        return False
    current_lane = session.query(KanbanLane).filter(KanbanLane.id == ticket.lane_id).first()
    if not current_lane:
        return False
    lanes = ensure_delivery_lanes(
        session,
        int(current_lane.board_id),
        legacy_source_names=("Scoped work", "To Do", "Current", "Queue"),
    )
    target = lanes[target_name]
    if int(ticket.lane_id) == int(target.id):
        return False
    current_name = str(current_lane.name or "").strip()
    allowed_transitions = {
        ("Backlog", "In Progress"),
        ("In Progress", "QA"),
        # A rejected QA result may be sent back through the workflow for rework.
        ("QA", "In Progress"),
    }
    if (current_name, target_name) not in allowed_transitions:
        raise ValueError(
            f"Automation may not move a ticket from {current_name or 'an unknown lane'} "
            f"to {target_name}; expected Backlog → In Progress → QA"
        )
    max_position = (
        session.query(func.max(KanbanTicket.position))
        .filter(KanbanTicket.lane_id == int(target.id))
        .scalar()
    )
    ticket.lane_id = int(target.id)
    ticket.position = (int(max_position) + 1) if max_position is not None else 0
    return True
