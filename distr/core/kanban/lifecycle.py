"""Canonical lane lifecycle for DecisionsAI delivery boards."""

from __future__ import annotations

from typing import Any, Iterable

from distr.core.db.kanban import KanbanLane


DELIVERY_LANES: tuple[str, ...] = (
    "Backlog",
    "In Progress",
    "QA",
    "Complete",
)

DELIVERY_SOURCE_LANE = DELIVERY_LANES[0]
DELIVERY_DONE_LANE = DELIVERY_LANES[-1]


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
