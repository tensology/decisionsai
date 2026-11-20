"""Resolve or create a board before Development work starts.

Starting a large request with no board used to create a thread, launch an
execution session, and fail within a few seconds ("Project execution session
failed") with no way to pick a board. Callers should resolve a board first:
use the one that was chosen, use or create the project's board, or return a
clear "choose a board" error without starting work.
"""

from __future__ import annotations

from typing import Any


class StudioBoardRequired(ValueError):
    """No board could be resolved; the UI should ask the user to choose one."""

    def __init__(self, message: str = "Choose a board before starting this work.") -> None:
        super().__init__(message)
        self.code = "board_required"


def _clean_board_key(value: Any) -> str:
    return str(value or "").strip()


def _project_board(session, project_id: int):
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    project = session.get(Project, int(project_id))
    if project is None or getattr(project, "deleted_at", None) is not None:
        return None, None
    explicit = None
    if getattr(project, "kanban_board_id", None):
        explicit = session.get(KanbanBoard, int(project.kanban_board_id))
        if explicit is not None and getattr(explicit, "archived", False):
            explicit = None
    if explicit is not None:
        return project, explicit
    board = (
        session.query(KanbanBoard)
        .filter(
            KanbanBoard.default_project_id == int(project_id),
            (KanbanBoard.archived == False) | (KanbanBoard.archived == None),  # noqa: E712
        )
        .order_by(KanbanBoard.id.asc())
        .first()
    )
    return project, board


def ensure_board_for_project(session, project_id: int):
    """Return the project's local board, creating one with delivery lanes if needed."""
    from distr.core.db.kanban import KanbanBoard
    from distr.core.kanban.lifecycle import ensure_delivery_lanes

    project, board = _project_board(session, project_id)
    if project is None:
        return None
    if board is None:
        board = KanbanBoard(
            name=project.name or "Untitled",
            description=f"Board for project: {project.name or 'Untitled'}",
            source="database",
            default_project_id=int(project_id),
        )
        session.add(board)
        session.flush()
        ensure_delivery_lanes(session, int(board.id))
    elif board.default_project_id is None:
        board.default_project_id = int(project_id)
    if getattr(project, "kanban_board_id", None) != int(board.id):
        project.kanban_board_id = int(board.id)
    if getattr(board, "archived", False):
        board.archived = False
    session.flush()
    return board


def resolve_studio_work_board(
    *,
    project_id: int | None = None,
    board_key: str | None = None,
    create_if_missing: bool = True,
    session_provider=None,
) -> dict[str, Any]:
    """Return board_key / board_id / project_id for a new Development task.

    Raises StudioBoardRequired when the user has not chosen a board and there
    is no project to default or create one from.
    """
    from distr.core.db import get_session
    from distr.core.workflow.development_threads import development_board

    key = _clean_board_key(board_key)
    pid = int(project_id) if project_id not in (None, "", 0, "0") else None
    with (session_provider or get_session)() as session:
        if key:
            board = development_board(session, board_key=key)
            if board is None:
                raise StudioBoardRequired("The selected board is not available. Choose another board before starting this work.")
            linked = getattr(board, "default_project_id", None)
            return {
                "board_key": key,
                "board_id": int(board.id),
                "project_id": int(linked) if linked else pid,
                "created": False,
                "board_name": board.name,
            }
        if pid is None:
            raise StudioBoardRequired(
                "Choose a board before starting this work. Large work needs a board so tickets, approvals and the workflow have a place to live."
            )
        board = None
        if create_if_missing:
            board = ensure_board_for_project(session, pid)
        else:
            _project, board = _project_board(session, pid)
        if board is None:
            raise StudioBoardRequired(
                "This project does not have a board yet. Choose a board, or open the project and create one, before starting this work."
            )
        return {
            "board_key": f"decisions:{int(board.id)}",
            "board_id": int(board.id),
            "project_id": pid,
            "created": True,
            "board_name": board.name,
        }
