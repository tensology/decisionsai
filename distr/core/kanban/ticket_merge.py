"""Merge one board ticket into another without hard-deleting the duplicate."""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Any, Optional

from distr.core.db.kanban import (
    KanbanBoard,
    KanbanLane,
    KanbanTicket,
    KanbanTicketFile,
    KanbanTicketLink,
    KanbanTicketTodo,
)
from distr.core.db.time import utc_now_naive

logger = logging.getLogger(__name__)


class TicketMergeError(ValueError):
    """Structured validation failure for merge_ticket."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# (module, class, column) foreign keys that should follow the kept ticket.
_REFERENCE_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("distr.core.db.workflow", "AutoWorkflowRun", "ticket_id"),
    ("distr.core.db.workflow", "DevelopmentWorkItem", "local_ticket_id"),
    ("distr.core.db.orchestrator", "OrchestratorEvent", "ticket_id"),
    ("distr.core.db.orchestrator", "OrchestratorValidationRecord", "ticket_id"),
    ("distr.core.db.orchestrator", "OrchestratorCorrectionAttempt", "ticket_id"),
    ("distr.core.db.kanban", "ProjectExecutionSession", "ticket_id"),
    ("distr.core.db.schedule_blocks", "ScheduleBlock", "ticket_id"),
    ("distr.core.db.kanban", "KanbanTicket", "parent_ticket_id"),
)


def is_merged_duplicate(ticket: Any) -> bool:
    """Return True when a ticket has already been merged into another."""
    return bool(getattr(ticket, "merged_into_ticket_id", None))


def merged_from_note(duplicate_id: int) -> str:
    return f"Merged from #{int(duplicate_id)}"


def merged_into_note(keep_id: int) -> str:
    return f"Merged into #{int(keep_id)}"


def _as_ticket_id(value: Any) -> Optional[int]:
    if value is None or value is False:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _append_unique(existing: Optional[str], addition: str) -> str:
    current = (existing or "").rstrip()
    block = (addition or "").strip()
    if not block:
        return current
    if block in current:
        return current
    if not current:
        return block
    return f"{current}\n\n{block}"


def _project_id(ticket: KanbanTicket) -> Optional[int]:
    linked = getattr(ticket, "linked_project_id", None)
    if linked:
        return int(linked)
    lane = getattr(ticket, "lane", None)
    board = getattr(lane, "board", None) if lane is not None else None
    default = getattr(board, "default_project_id", None)
    return int(default) if default else None


def _board_id(ticket: KanbanTicket) -> Optional[int]:
    lane = getattr(ticket, "lane", None)
    if lane is None:
        return None
    board_id = getattr(lane, "board_id", None)
    if board_id:
        return int(board_id)
    board = getattr(lane, "board", None)
    return int(board.id) if board is not None and getattr(board, "id", None) else None


def _load_ticket(session: Any, ticket_id: int) -> Optional[KanbanTicket]:
    return (
        session.query(KanbanTicket)
        .join(KanbanLane, KanbanTicket.lane_id == KanbanLane.id)
        .join(KanbanBoard, KanbanLane.board_id == KanbanBoard.id)
        .filter(KanbanTicket.id == int(ticket_id))
        .first()
    ) or session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()


def merge_ticket(
    session: Any,
    keep_id: Any,
    duplicate_id: Any,
    *,
    allow_cross_project: bool = False,
    actor: str = "tool",
) -> dict[str, Any]:
    """Copy duplicate content into keep, re-point references, and archive the duplicate.

    The duplicate row is kept (not deleted). ``merged_into_ticket_id`` plus a
    "Merged into #M" note mark it as archived. Re-running the same pair is
    idempotent.
    """
    del actor  # reserved for audit/history callers
    keep_ticket_id = _as_ticket_id(keep_id)
    duplicate_ticket_id = _as_ticket_id(duplicate_id)
    if keep_ticket_id is None or duplicate_ticket_id is None:
        raise TicketMergeError(
            "missing_id",
            "I need both a keep ticket id and a duplicate ticket id to merge.",
        )
    if keep_ticket_id == duplicate_ticket_id:
        raise TicketMergeError(
            "same_id",
            "The keep ticket and the duplicate ticket must be different tickets.",
        )

    keep = _load_ticket(session, keep_ticket_id)
    duplicate = _load_ticket(session, duplicate_ticket_id)
    if keep is None and duplicate is None:
        raise TicketMergeError(
            "not_found",
            f"Tickets #{keep_ticket_id} and #{duplicate_ticket_id} were not found.",
        )
    if keep is None:
        raise TicketMergeError("not_found", f"Ticket #{keep_ticket_id} was not found.")
    if duplicate is None:
        raise TicketMergeError("not_found", f"Ticket #{duplicate_ticket_id} was not found.")

    already_into = _as_ticket_id(getattr(duplicate, "merged_into_ticket_id", None))
    if already_into == keep_ticket_id:
        return {
            "keep_id": keep_ticket_id,
            "duplicate_id": duplicate_ticket_id,
            "already_merged": True,
            "copied": {"description": False, "notes": False, "links": 0, "files": 0, "todos": 0},
            "repointed": {},
        }
    if already_into:
        raise TicketMergeError(
            "already_merged_elsewhere",
            f"Ticket #{duplicate_ticket_id} has already been merged into #{already_into}.",
        )

    keep_already = _as_ticket_id(getattr(keep, "merged_into_ticket_id", None))
    if keep_already:
        raise TicketMergeError(
            "keep_already_merged",
            f"Ticket #{keep_ticket_id} has already been merged into #{keep_already}. "
            f"Merge into #{keep_already} instead.",
        )

    keep_project = _project_id(keep)
    duplicate_project = _project_id(duplicate)
    keep_board = _board_id(keep)
    duplicate_board = _board_id(duplicate)
    different_projects = (
        keep_project is not None
        and duplicate_project is not None
        and keep_project != duplicate_project
    )
    unscoped_different_boards = (
        (keep_project is None or duplicate_project is None)
        and keep_board is not None
        and duplicate_board is not None
        and keep_board != duplicate_board
    )
    if (different_projects or unscoped_different_boards) and not allow_cross_project:
        raise TicketMergeError(
            "cross_project",
            "Those tickets belong to different projects. Pass allow_cross_project=true "
            "if you really want to merge them.",
        )

    copied = {
        "description": False,
        "notes": False,
        "links": 0,
        "files": 0,
        "todos": 0,
        "source_label": False,
    }

    if duplicate.description and (duplicate.description or "").strip():
        if (duplicate.description or "").strip() not in (keep.description or ""):
            keep.description = _append_unique(keep.description, duplicate.description)
            copied["description"] = True
    keep.description = _append_unique(keep.description, merged_from_note(duplicate_ticket_id))

    if duplicate.context_notes and (duplicate.context_notes or "").strip():
        if (duplicate.context_notes or "").strip() not in (keep.context_notes or ""):
            keep.context_notes = _append_unique(keep.context_notes, duplicate.context_notes)
            copied["notes"] = True
    keep.context_notes = _append_unique(keep.context_notes, merged_from_note(duplicate_ticket_id))

    if not (keep.source_label or "").strip() and (duplicate.source_label or "").strip():
        keep.source_label = duplicate.source_label
        copied["source_label"] = True

    if not keep.linked_workflow_id and duplicate.linked_workflow_id:
        keep.linked_workflow_id = duplicate.linked_workflow_id
    if not keep.linked_snippet_id and duplicate.linked_snippet_id:
        keep.linked_snippet_id = duplicate.linked_snippet_id
    if not keep.linked_action_id and duplicate.linked_action_id:
        keep.linked_action_id = duplicate.linked_action_id
    if not keep.linked_project_id and duplicate.linked_project_id:
        keep.linked_project_id = duplicate.linked_project_id

    existing_urls = {
        (link.url or "").strip()
        for link in session.query(KanbanTicketLink).filter(KanbanTicketLink.ticket_id == keep_ticket_id)
        if (link.url or "").strip()
    }
    for link in list(session.query(KanbanTicketLink).filter(KanbanTicketLink.ticket_id == duplicate_ticket_id)):
        url = (link.url or "").strip()
        if not url or url in existing_urls:
            continue
        session.add(KanbanTicketLink(ticket_id=keep_ticket_id, title=link.title or url, url=url))
        existing_urls.add(url)
        copied["links"] += 1

    existing_files = {
        ((file.filename or "").strip(), (file.file_path or "").strip())
        for file in session.query(KanbanTicketFile).filter(KanbanTicketFile.ticket_id == keep_ticket_id)
    }
    for file in list(session.query(KanbanTicketFile).filter(KanbanTicketFile.ticket_id == duplicate_ticket_id)):
        key = ((file.filename or "").strip(), (file.file_path or "").strip())
        if not key[1] or key in existing_files:
            continue
        session.add(
            KanbanTicketFile(
                ticket_id=keep_ticket_id,
                filename=file.filename,
                file_path=file.file_path,
                description=file.description,
            )
        )
        existing_files.add(key)
        copied["files"] += 1

    existing_todos = {
        (todo.text or "").strip().lower()
        for todo in session.query(KanbanTicketTodo).filter(KanbanTicketTodo.ticket_id == keep_ticket_id)
        if (todo.text or "").strip()
    }
    next_pos = max(
        (
            int(todo.position or 0)
            for todo in session.query(KanbanTicketTodo).filter(KanbanTicketTodo.ticket_id == keep_ticket_id)
        ),
        default=-1,
    ) + 1
    for todo in list(
        session.query(KanbanTicketTodo)
        .filter(KanbanTicketTodo.ticket_id == duplicate_ticket_id)
        .order_by(KanbanTicketTodo.position, KanbanTicketTodo.id)
    ):
        text = (todo.text or "").strip()
        if not text or text.lower() in existing_todos:
            continue
        session.add(
            KanbanTicketTodo(
                ticket_id=keep_ticket_id,
                text=text,
                done=bool(todo.done),
                position=next_pos,
            )
        )
        existing_todos.add(text.lower())
        next_pos += 1
        copied["todos"] += 1

    if keep.source_chat_id is None and duplicate.source_chat_id is not None:
        chat_id = int(duplicate.source_chat_id)
        duplicate.source_chat_id = None
        session.flush()
        keep.source_chat_id = chat_id

    keep.modified_date = utc_now_naive()
    session.flush()

    repointed: dict[str, int] = {}
    for module_name, class_name, column_name in _REFERENCE_TARGETS:
        try:
            model = getattr(import_module(module_name), class_name)
            column = getattr(model, column_name)
        except Exception:
            # A model/column may not exist in this build; log so a genuinely
            # missing reference target is not silently skipped during a merge.
            logger.debug(
                "Merge: reference target %s.%s unavailable", module_name, class_name, exc_info=True
            )
            continue
        try:
            rows = session.query(model).filter(column == duplicate_ticket_id).all()
        except Exception:
            logger.warning(
                "Merge: could not repoint %s.%s from ticket %s to %s",
                class_name, column_name, duplicate_ticket_id, keep_ticket_id, exc_info=True,
            )
            continue
        updated = 0
        for row in rows:
            if column_name == "parent_ticket_id" and getattr(row, "id", None) == keep_ticket_id:
                continue
            setattr(row, column_name, keep_ticket_id)
            updated += 1
        if updated:
            repointed[f"{class_name}.{column_name}"] = updated

    duplicate.context_notes = _append_unique(
        duplicate.context_notes, merged_into_note(keep_ticket_id)
    )
    duplicate.description = _append_unique(
        duplicate.description, merged_into_note(keep_ticket_id)
    )
    duplicate.merged_into_ticket_id = keep_ticket_id
    duplicate.modified_date = utc_now_naive()
    session.flush()

    try:
        from distr.core.kanban.ticket_audit import append_ticket_audit_entry

        append_ticket_audit_entry(
            session,
            ticket_id=keep_ticket_id,
            run_id=None,
            step_id=None,
            step_result_id=None,
            execution_lane="cli",
            status="completed",
            final_verdict="merged",
            summary=merged_from_note(duplicate_ticket_id),
            details=f"Merged ticket #{duplicate_ticket_id} into #{keep_ticket_id}.",
        )
        append_ticket_audit_entry(
            session,
            ticket_id=duplicate_ticket_id,
            run_id=None,
            step_id=None,
            step_result_id=None,
            execution_lane="cli",
            status="completed",
            final_verdict="merged",
            summary=merged_into_note(keep_ticket_id),
            details=f"Archived after merge into #{keep_ticket_id}.",
        )
    except Exception:
        pass

    return {
        "keep_id": keep_ticket_id,
        "duplicate_id": duplicate_ticket_id,
        "already_merged": False,
        "copied": copied,
        "repointed": repointed,
    }
