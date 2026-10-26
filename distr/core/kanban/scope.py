"""Single active project/board scope for DecisionsAI.

A project and its delivery board are the same thing here: creating a project
creates its board, and external intake (WhatsApp/Gmail/Jira) links to that same
pair. Historically the project and board carried independent ``in_use`` flags,
and they drifted apart (for example project = Spoortrack (Old) while
board = Jackcat). Chat context read the board pin, work routing read the
project pin, and the two disagreed.

Every activation now goes through this module, which moves both flags together,
and ``reconcile_active_scope`` heals any legacy divergence by letting the most
recently activated side win.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from distr.core.db.kanban import KanbanBoard
from distr.core.db.projects import Project


def _as_naive(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if getattr(value, "tzinfo", None) is not None:
        return value.replace(tzinfo=None)
    return value


def _newer(a: Any, b: Any) -> bool:
    a_dt, b_dt = _as_naive(a), _as_naive(b)
    if a_dt is None:
        return False
    if b_dt is None:
        return True
    return a_dt >= b_dt


def linked_board(session: Any, project: Optional[Project]) -> Optional[KanbanBoard]:
    """Return the delivery board for a project, or None."""
    if project is None:
        return None
    board_id = getattr(project, "kanban_board_id", None)
    if board_id:
        board = session.get(KanbanBoard, int(board_id))
        if board is not None and not board.archived:
            return board
    return (
        session.query(KanbanBoard)
        .filter(
            KanbanBoard.default_project_id == int(project.id),
            KanbanBoard.archived.is_(False),  # noqa: E712
        )
        .order_by(KanbanBoard.id)
        .first()
    )


def linked_project(session: Any, board: Optional[KanbanBoard]) -> Optional[Project]:
    """Return the project a board belongs to, or None for a standalone board."""
    if board is None or not board.default_project_id:
        return None
    return session.get(Project, int(board.default_project_id))


def active_project(session: Any) -> Optional[Project]:
    return (
        session.query(Project)
        .filter(Project.in_use == True)  # noqa: E712
        .order_by(Project.modified_date.desc(), Project.id.desc())
        .first()
    )


def active_board(session: Any) -> Optional[KanbanBoard]:
    return (
        session.query(KanbanBoard)
        .filter(
            KanbanBoard.in_use == True,  # noqa: E712
            KanbanBoard.archived.is_(False),  # noqa: E712
        )
        .order_by(KanbanBoard.modified_date.desc(), KanbanBoard.id.desc())
        .first()
    )


def _single_project(session: Any, project_id: int) -> Optional[Project]:
    session.query(Project).filter(Project.in_use == True).update({Project.in_use: False})  # noqa: E712
    project = session.get(Project, int(project_id))
    if project is not None:
        project.in_use = True
    return project


def _single_board(session: Any, board_id: int) -> Optional[KanbanBoard]:
    session.query(KanbanBoard).filter(KanbanBoard.in_use == True).update({KanbanBoard.in_use: False})  # noqa: E712
    board = session.get(KanbanBoard, int(board_id))
    if board is not None:
        board.in_use = True
    return board


def set_active_project(
    session: Any, project_id: int
) -> tuple[Optional[Project], Optional[KanbanBoard]]:
    """Activate a project and, in the same transaction, its board."""
    project = _single_project(session, project_id)
    if project is None:
        return (None, None)
    board = linked_board(session, project)
    if board is not None:
        _single_board(session, int(board.id))
        project.kanban_board_id = int(board.id)
    else:
        session.query(KanbanBoard).filter(KanbanBoard.in_use == True).update({KanbanBoard.in_use: False})  # noqa: E712
    session.flush()
    return (project, board)


def set_active_board(
    session: Any, board_id: int
) -> tuple[Optional[Project], Optional[KanbanBoard]]:
    """Activate a board and, in the same transaction, its project."""
    board = _single_board(session, board_id)
    if board is None:
        return (None, None)
    project = linked_project(session, board)
    if project is not None:
        _single_project(session, int(project.id))
        if not getattr(project, "kanban_board_id", None):
            project.kanban_board_id = int(board.id)
    session.flush()
    return (project, board)


def reconcile_active_scope(
    session: Any,
) -> tuple[Optional[Project], Optional[KanbanBoard]]:
    """Return the active (project, board), healing any legacy divergence.

    When both flags point at the same scope nothing changes. When they
    disagree, the side that was activated most recently wins and the other is
    aligned to it.
    """
    project = active_project(session)
    board = active_board(session)
    if project is None and board is None:
        return (None, None)
    if project is not None and board is not None:
        if int(board.default_project_id or 0) == int(project.id):
            return (project, board)
        if _newer(getattr(board, "modified_date", None), getattr(project, "modified_date", None)):
            return set_active_board(session, int(board.id))
        return set_active_project(session, int(project.id))
    if board is not None:
        return set_active_board(session, int(board.id))
    return set_active_project(session, int(project.id))
