"""Durable identity for Development harness threads.

A Development thread represents one piece of work. Workflow definitions are
optional reusable tools, not the identity or default execution path of a
thread.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func

from distr.core.chat import ChatService, _chat_params
from distr.core.db import Chat, get_session
from distr.core.db.workflow import DevelopmentWorkItem


class DevelopmentThreadConflict(ValueError):
    """Raised when a work item is already owned by another thread."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _provider(value: Any) -> str:
    provider = _clean(value).lower()
    if provider in {"database", "decisions", "local"}:
        return "local"
    return provider


def development_identity_key(
    *,
    chat_id: int | None = None,
    source_type: str | None = None,
    source_ref: str | None = None,
    ticket_id: int | None = None,
    board_provider: str | None = None,
    board_key: str | None = None,
    board_ticket_key: str | None = None,
) -> str:
    """Return the stable idempotency key for a Development work item.

    External ticket identity includes both provider and board. Local ticket
    IDs are database-global. Open-ended work is intentionally chat-scoped so
    two prompts using the same workflow never collapse into one conversation.
    """
    provider = _provider(board_provider)
    raw_board = _clean(board_key)
    clean_board = raw_board.lower()
    clean_ticket = _clean(board_ticket_key).lower()
    if provider and provider != "local" and clean_board and clean_ticket:
        return f"ticket:{provider}:{clean_board}:{clean_ticket}"
    if ticket_id is not None:
        return f"ticket:local:{int(ticket_id)}"
    if provider and clean_board and clean_ticket:
        return f"ticket:{provider}:{clean_board}:{clean_ticket}"
    clean_source = _clean(source_type).lower()
    clean_ref = _clean(source_ref).lower()
    if clean_source and clean_ref:
        return f"source:{clean_source}:{clean_ref}"
    if chat_id is not None:
        return f"chat:{int(chat_id)}"
    return ""


def development_thread_metadata(chat: Chat) -> dict[str, Any]:
    params = _chat_params(getattr(chat, "params", None))
    value = params.get("development")
    return value if isinstance(value, dict) else {}


def is_development_thread(db, chat_or_id: Chat | int | None) -> bool:
    """Return authoritative surface ownership for a root or child chat row.

    ``DevelopmentWorkItem`` is the source of truth. The legacy JSON projection
    remains a migration fallback for older rows that have not yet been
    backfilled, but it is never required for an authoritative work item to be
    recognized.
    """
    if chat_or_id is None:
        return False
    if isinstance(chat_or_id, Chat) or hasattr(chat_or_id, "id"):
        chat = chat_or_id
    else:
        chat = db.get(Chat, int(chat_or_id))
    if chat is None:
        return False
    root_id = int(chat.id)
    seen: set[int] = set()
    while getattr(chat, "parent_id", None) is not None and root_id not in seen:
        seen.add(root_id)
        root_id = int(chat.parent_id)
        chat = db.get(Chat, root_id)
        if chat is None:
            return False
    if db.query(DevelopmentWorkItem.id).filter(
        DevelopmentWorkItem.chat_id == root_id
    ).first() is not None:
        return True
    return bool(development_thread_metadata(chat))


def resolve_conversational_chat_id(db, *candidates: Any) -> int | None:
    """Return the first existing root owned by Chat, never Development."""
    for candidate in candidates:
        try:
            chat_id = int(candidate)
        except (TypeError, ValueError):
            continue
        chat = db.get(Chat, chat_id)
        if chat is None or chat.parent_id is not None:
            continue
        if not is_development_thread(db, chat):
            return chat_id
    return None


def development_thread_record(db, chat: Chat) -> dict[str, Any]:
    """Return the compatibility payload hydrated from authoritative ownership."""
    metadata = dict(development_thread_metadata(chat))
    item = db.query(DevelopmentWorkItem).filter(
        DevelopmentWorkItem.chat_id == int(chat.id)
    ).first()
    if item is None:
        return metadata
    authoritative = {
        "workflow_id": item.workflow_id,
        "ticket_id": item.local_ticket_id,
        "source_type": item.source_type,
        "board_provider": item.board_provider,
        "board_key": item.board_key,
        "board_ticket_key": item.ticket_key,
        "board_ticket_title": item.ticket_title,
        "board_ticket_lane": item.ticket_lane,
    }
    for key, value in authoritative.items():
        if value is not None and value != "":
            metadata[key] = value
    return metadata


def synchronize_linked_ticket_title(db, ticket, *, title: str | None = None) -> str | None:
    """Keep a local ticket and its one Development thread on one title."""
    chat_id = getattr(ticket, "source_chat_id", None)
    if not chat_id:
        return None
    root = db.get(Chat, int(chat_id))
    if root is None or root.parent_id is not None:
        return None
    clean_title = _clean(title if title is not None else getattr(ticket, "title", None))
    if not clean_title:
        return None
    ticket.title = clean_title
    root.title = clean_title
    item = (
        db.query(DevelopmentWorkItem)
        .filter(DevelopmentWorkItem.chat_id == int(root.id))
        .first()
    )
    if item is not None:
        item.ticket_title = clean_title
    params = _chat_params(getattr(root, "params", None))
    development = params.get("development") if isinstance(params.get("development"), dict) else {}
    development["ticket_id"] = int(ticket.id)
    development["board_ticket_title"] = clean_title
    params["development"] = development
    root.params = json.dumps(params)
    return clean_title


def update_development_model_route(
    chat_id: int,
    *,
    route_mode: str,
    provider: str | None = None,
    model_name: str | None = None,
    reasoning_effort: str | None = None,
    service_tier: str | None = None,
    _session_provider=None,
) -> dict[str, Any]:
    """Persist one thread's model route without mutating its reusable workflow."""
    session_provider = _session_provider or get_session
    mode = _clean(route_mode).lower() or "auto"
    if mode not in {"auto", "manual"}:
        raise ValueError("route_mode must be auto or manual")
    clean_provider = _clean(provider).lower()
    clean_model = _clean(model_name)
    if mode == "manual" and (not clean_provider or not clean_model):
        raise ValueError("A provider and model are required for manual routing.")
    effort = _clean(reasoning_effort).lower()
    tier = _clean(service_tier).lower()
    if effort and effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("Unsupported reasoning effort.")
    if tier and tier not in {"standard", "priority"}:
        raise ValueError("Unsupported speed tier.")

    with session_provider() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None:
            raise LookupError("Chat not found.")
        metadata = development_thread_metadata(root)
        workflow_id = int(metadata.get("workflow_id") or 0) or None
        route = {
            "route_mode": mode,
            "provider": clean_provider if mode == "manual" else "",
            "model_name": clean_model if mode == "manual" else "",
            "reasoning_effort": effort,
            "service_tier": tier,
        }
        params = _chat_params(getattr(root, "params", None))
        development = params.get("development") if isinstance(params.get("development"), dict) else {}
        development["model_route"] = route
        params["development"] = development
        root.params = json.dumps(params)
        root.route_mode = mode
        if mode == "manual":
            root.provider = clean_provider
            root.model_name = clean_model
        db.commit()

    return {
        "id": int(chat_id),
        **route,
        "workflow_id": workflow_id,
    }


def _identity_from_legacy(chat: Chat) -> str:
    metadata = development_thread_metadata(chat)
    return development_identity_key(
        chat_id=int(chat.id),
        source_type=metadata.get("source_type"),
        source_ref=metadata.get("source_ref"),
        ticket_id=metadata.get("ticket_id"),
        board_provider=metadata.get("board_provider"),
        board_key=metadata.get("board_key"),
        board_ticket_key=metadata.get("board_ticket_key"),
    )


def _write_legacy_projection(
    root: Chat,
    *,
    workflow_id: int | None,
    ticket_id: int | None,
    source_type: str,
    source_ref: str | None,
    board_key: str | None,
    board_provider: str | None,
    board_ticket_key: str | None,
    board_ticket_title: str | None,
    board_ticket_lane: str | None,
) -> None:
    params = _chat_params(getattr(root, "params", None))
    existing = params.get("development") if isinstance(params.get("development"), dict) else {}
    development = {
        **existing,
        "ticket_id": int(ticket_id) if ticket_id is not None else None,
    }
    if workflow_id is not None:
        development["workflow_id"] = int(workflow_id)
    else:
        development.pop("workflow_id", None)
    values = {
        # Keep the compatibility projection compact. ``prompt`` is the
        # default in the authoritative row and need not pollute older JSON.
        "source_type": source_type if source_type != "prompt" or source_ref else None,
        "source_ref": source_ref,
        "board_key": board_key,
        "board_provider": board_provider,
        "board_ticket_key": board_ticket_key,
        "board_ticket_title": board_ticket_title,
        "board_ticket_lane": board_ticket_lane,
    }
    for key, value in values.items():
        clean = _clean(value)
        if clean:
            development[key] = clean
        else:
            development.pop(key, None)
    params["development"] = development
    root.params = json.dumps(params)


def _ensure_local_ticket_for_thread(
    db,
    root: Chat,
    *,
    board_key: str | None,
    ticket_id: int | None,
    title: str | None,
) -> tuple[int | None, str | None, str | None]:
    """Create the one ticket owned by a thread when it joins a local board."""
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

    existing = db.query(KanbanTicket).filter(KanbanTicket.source_chat_id == int(root.id)).first()
    raw_board = _clean(board_key)
    clean_board = raw_board.lower()
    target_board = None
    if clean_board.startswith("decisions:"):
        try:
            board_id = int(clean_board.split(":", 1)[1])
        except (TypeError, ValueError):
            raise ValueError("Invalid local board identity.")
        target_board = db.get(KanbanBoard, board_id)
    elif ":" in clean_board:
        provider = clean_board.split(":", 1)[0]
        external_id = raw_board.split(":", 1)[1]
        target_board = db.query(KanbanBoard).filter(
            KanbanBoard.source == provider,
            KanbanBoard.external_board_id == external_id,
        ).first()

    def target_progress_lane():
        if target_board is None:
            return None
        lane = db.query(KanbanLane).filter(
            KanbanLane.board_id == int(target_board.id),
            KanbanLane.name.ilike("%progress%"),
        ).order_by(KanbanLane.position).first()
        if lane is None:
            lane = KanbanLane(board_id=int(target_board.id), name="In Progress", position=1)
            db.add(lane)
            db.flush()
        return lane

    if ticket_id is not None:
        owner = (
            db.query(DevelopmentWorkItem)
            .filter(
                DevelopmentWorkItem.local_ticket_id == int(ticket_id),
                DevelopmentWorkItem.chat_id != int(root.id),
            )
            .first()
        )
        if owner is not None:
            raise DevelopmentThreadConflict(
                f"Ticket #{ticket_id} already belongs to Development thread #{owner.chat_id}."
            )
        ticket = db.get(KanbanTicket, int(ticket_id))
        if ticket is None:
            raise ValueError("Selected ticket was not found.")
        if ticket.source_chat_id not in (None, int(root.id)):
            raise DevelopmentThreadConflict(
                f"Ticket #{ticket.id} already belongs to Development thread #{ticket.source_chat_id}."
            )
        if existing is not None and int(existing.id) != int(ticket.id):
            existing.source_chat_id = None
        ticket.source_chat_id = int(root.id)
        lane = db.get(KanbanLane, int(ticket.lane_id)) if ticket.lane_id else None
        return int(ticket.id), str(ticket.id), lane.name if lane else None
    if existing is not None:
        lane = db.get(KanbanLane, int(existing.lane_id)) if existing.lane_id else None
        if target_board is not None and (lane is None or int(lane.board_id) != int(target_board.id)):
            lane = target_progress_lane()
            max_position = db.query(func.coalesce(func.max(KanbanTicket.position), -1)).filter(
                KanbanTicket.lane_id == int(lane.id)
            ).scalar()
            existing.lane_id = int(lane.id)
            existing.position = int(max_position if max_position is not None else -1) + 1
            existing.linked_project_id = target_board.default_project_id
            existing.linked_workflow_id = target_board.default_workflow_id or existing.linked_workflow_id
        return int(existing.id), str(existing.id), lane.name if lane else None
    if target_board is None:
        if clean_board.startswith("decisions:"):
            raise ValueError("Selected board was not found.")
        return None, None, None
    board = target_board
    board_id = int(board.id)
    lane = target_progress_lane()
    max_position = db.query(func.coalesce(func.max(KanbanTicket.position), -1)).filter(
        KanbanTicket.lane_id == int(lane.id)
    ).scalar()
    ticket = KanbanTicket(
        lane_id=int(lane.id),
        title=_clean(title) or root.title or "Development work",
        description="Created from a Development thread.",
        position=int(max_position if max_position is not None else -1) + 1,
        linked_project_id=board.default_project_id,
        linked_workflow_id=board.default_workflow_id,
        source_chat_id=int(root.id),
        source_provider="development",
        source_external_id=f"thread:{int(root.id)}",
        source_label="Development",
    )
    db.add(ticket)
    db.flush()
    return int(ticket.id), str(ticket.id), lane.name


def _upsert_work_item(
    db,
    root: Chat,
    *,
    workflow_id: int | None,
    ticket_id: int | None,
    source_type: str,
    source_ref: str | None,
    board_key: str | None,
    board_provider: str | None,
    board_ticket_key: str | None,
    board_ticket_title: str | None,
    board_ticket_lane: str | None,
) -> DevelopmentWorkItem:
    identity_key = development_identity_key(
        chat_id=int(root.id),
        source_type=source_type,
        source_ref=source_ref,
        ticket_id=ticket_id,
        board_provider=board_provider,
        board_key=board_key,
        board_ticket_key=board_ticket_key,
    )
    conflict = (
        db.query(DevelopmentWorkItem)
        .filter(
            DevelopmentWorkItem.identity_key == identity_key,
            DevelopmentWorkItem.chat_id != int(root.id),
        )
        .first()
    )
    if conflict is not None:
        raise DevelopmentThreadConflict(
            f"Work item {identity_key} already belongs to Development thread #{conflict.chat_id}."
        )
    item = (
        db.query(DevelopmentWorkItem)
        .filter(DevelopmentWorkItem.chat_id == int(root.id))
        .first()
    )
    if item is None:
        item = DevelopmentWorkItem(chat_id=int(root.id), identity_key=identity_key)
        db.add(item)
    item.identity_key = identity_key
    item.source_type = _clean(source_type).lower() or "prompt"
    item.board_provider = _provider(board_provider) or None
    item.board_key = _clean(board_key) or None
    item.ticket_key = _clean(board_ticket_key) or None
    item.local_ticket_id = int(ticket_id) if ticket_id is not None else None
    item.project_id = int(root.project_id) if root.project_id is not None else None
    item.workflow_id = int(workflow_id) if workflow_id is not None else None
    item.ticket_title = _clean(board_ticket_title) or None
    item.ticket_lane = _clean(board_ticket_lane) or None
    _write_legacy_projection(
        root,
        workflow_id=workflow_id,
        ticket_id=ticket_id,
        source_type=item.source_type,
        source_ref=source_ref,
        board_key=item.board_key,
        board_provider=item.board_provider,
        board_ticket_key=item.ticket_key,
        board_ticket_title=item.ticket_title,
        board_ticket_lane=item.ticket_lane,
    )
    return item


def ensure_development_thread(
    *,
    workflow_id: int | None = None,
    title: str,
    project_id: int | None = None,
    ticket_id: int | None = None,
    starting_question: str | None = None,
    source_type: str = "prompt",
    source_ref: str | None = None,
    board_key: str | None = None,
    board_provider: str | None = None,
    board_ticket_key: str | None = None,
    board_ticket_title: str | None = None,
    board_ticket_lane: str | None = None,
    _session_provider=None,
) -> int:
    """Return the stable Development thread for a workflow/ticket work item."""
    identity_key = development_identity_key(
        source_type=source_type,
        source_ref=source_ref,
        ticket_id=ticket_id,
        board_provider=board_provider,
        board_key=board_key,
        board_ticket_key=board_ticket_key,
    )
    session_provider = _session_provider or get_session
    with session_provider() as db:
        if identity_key:
            item = (
                db.query(DevelopmentWorkItem)
                .filter(DevelopmentWorkItem.identity_key == identity_key)
                .first()
            )
            if item is not None:
                root = db.get(Chat, int(item.chat_id))
                if root is not None:
                    if root.is_archived:
                        root.is_archived = False
                        db.commit()
                    return int(root.id)

            # Compatibility discovery for installations created before the
            # authoritative table existed. Promote an exact legacy match.
            roots = db.query(Chat).filter(Chat.parent_id.is_(None)).all()
            for root in roots:
                if _identity_from_legacy(root) != identity_key:
                    continue
                metadata = development_thread_metadata(root)
                _upsert_work_item(
                    db,
                    root,
                    workflow_id=(
                        int(metadata["workflow_id"])
                        if metadata.get("workflow_id") is not None
                        else int(workflow_id)
                        if workflow_id is not None
                        else None
                    ),
                    ticket_id=metadata.get("ticket_id"),
                    source_type=metadata.get("source_type") or source_type,
                    source_ref=metadata.get("source_ref") or source_ref,
                    board_key=metadata.get("board_key") or board_key,
                    board_provider=metadata.get("board_provider") or board_provider,
                    board_ticket_key=metadata.get("board_ticket_key") or board_ticket_key,
                    board_ticket_title=metadata.get("board_ticket_title") or board_ticket_title,
                    board_ticket_lane=metadata.get("board_ticket_lane") or board_ticket_lane,
                )
                if root.is_archived:
                    root.is_archived = False
                db.commit()
                return int(root.id)

    chat_id, _ = ChatService.create_new_chat(
        title=(title or "Development work").strip(),
        starting_question=(starting_question or "").strip() or None,
        project_id=project_id,
        route_mode="auto",
        execution_profile="code",
        autonomy_level="full",
        activate=False,
        _session_provider=session_provider,
    )
    mark_development_thread(
        chat_id,
        workflow_id=workflow_id,
        ticket_id=ticket_id,
        source_type=source_type,
        source_ref=source_ref,
        board_key=board_key,
        board_provider=board_provider,
        board_ticket_key=board_ticket_key,
        board_ticket_title=board_ticket_title,
        board_ticket_lane=board_ticket_lane,
        _session_provider=session_provider,
    )
    return int(chat_id)


def mark_development_thread(
    chat_id: int,
    *,
    workflow_id: int | None = None,
    ticket_id: int | None = None,
    source_type: str = "prompt",
    source_ref: str | None = None,
    board_key: str | None = None,
    board_provider: str | None = None,
    board_ticket_key: str | None = None,
    board_ticket_title: str | None = None,
    board_ticket_lane: str | None = None,
    _session_provider=None,
) -> None:
    session_provider = _session_provider or get_session
    with session_provider() as db:
        root = db.get(Chat, int(chat_id))
        if root is None:
            raise ValueError(f"Development thread #{chat_id} was not found.")
        if ticket_id is not None:
            from distr.core.db.kanban import KanbanTicket

            ticket = db.get(KanbanTicket, int(ticket_id))
            if ticket is not None:
                if ticket.source_chat_id not in (None, int(root.id)):
                    raise DevelopmentThreadConflict(
                        f"Ticket #{ticket.id} already belongs to Development thread #{ticket.source_chat_id}."
                    )
                ticket.source_chat_id = int(root.id)
                board_ticket_title = synchronize_linked_ticket_title(db, ticket) or board_ticket_title
        _upsert_work_item(
            db,
            root,
            workflow_id=workflow_id,
            ticket_id=ticket_id,
            source_type=source_type,
            source_ref=source_ref,
            board_key=board_key,
            board_provider=board_provider,
            board_ticket_key=board_ticket_key,
            board_ticket_title=board_ticket_title,
            board_ticket_lane=board_ticket_lane,
        )
        db.commit()


def rebind_development_thread(
    chat_id: int,
    *,
    project_id: int | None,
    board_key: str | None,
    board_provider: str | None,
    title: str | None = None,
    ticket_id: int | None = None,
    board_ticket_key: str | None = None,
    board_ticket_title: str | None = None,
    board_ticket_lane: str | None = None,
    permission_profile: dict[str, Any] | None = None,
    remote_continuation: bool | None = None,
) -> dict[str, Any]:
    """Atomically move a Development thread and clear stale scope fields."""
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None:
            raise ValueError(f"Development thread #{chat_id} was not found.")
        metadata = development_thread_metadata(root)
        previous_ticket_id = metadata.get("ticket_id")
        item = (
            db.query(DevelopmentWorkItem)
            .filter(DevelopmentWorkItem.chat_id == int(chat_id))
            .first()
        )
        workflow_id = metadata.get("workflow_id")
        if workflow_id is None and item is not None:
            workflow_id = item.workflow_id
        if not metadata and item is None:
            raise ValueError("This conversation is not a Development thread.")
        if title is not None:
            clean_title = _clean(title)
            if not clean_title:
                raise ValueError("Thread title cannot be empty.")
            root.title = clean_title
            try:
                context = json.loads(root.additional_context or "{}")
            except (TypeError, ValueError):
                context = {}
            title_auto = context.get("title_auto") if isinstance(context.get("title_auto"), dict) else {}
            title_auto["manual"] = True
            context["title_auto"] = title_auto
            root.additional_context = json.dumps(context)
        root.project_id = int(project_id) if project_id is not None else None
        ticket_id, created_ticket_key, created_ticket_lane = _ensure_local_ticket_for_thread(
            db,
            root,
            board_key=board_key,
            ticket_id=ticket_id,
            title=board_ticket_title or root.title,
        )
        if ticket_id is not None:
            board_ticket_key = board_ticket_key or created_ticket_key
            board_ticket_lane = board_ticket_lane or created_ticket_lane
            from distr.core.db.kanban import KanbanTicket

            ticket = db.get(KanbanTicket, int(ticket_id))
            same_ticket = previous_ticket_id is not None and int(previous_ticket_id) == int(ticket_id)
            local_ticket = ticket is not None and not _clean(ticket.external_source)
            if ticket is not None and title is not None and same_ticket and local_ticket:
                board_ticket_title = synchronize_linked_ticket_title(db, ticket, title=root.title)
            elif ticket is not None:
                board_ticket_title = synchronize_linked_ticket_title(db, ticket)
            else:
                board_ticket_title = board_ticket_title or root.title
        item = _upsert_work_item(
            db,
            root,
            workflow_id=int(workflow_id) if workflow_id is not None else None,
            ticket_id=ticket_id,
            source_type=metadata.get("source_type") or "prompt",
            source_ref=metadata.get("source_ref"),
            board_key=board_key,
            board_provider=board_provider,
            board_ticket_key=board_ticket_key,
            board_ticket_title=board_ticket_title,
            board_ticket_lane=board_ticket_lane,
        )
        if permission_profile is not None or remote_continuation is not None:
            params = _chat_params(getattr(root, "params", None))
            development = params.get("development") if isinstance(params.get("development"), dict) else {}
            if permission_profile is not None:
                development["permission_profile"] = dict(permission_profile)
            if remote_continuation is not None:
                development["remote_continuation"] = bool(remote_continuation)
            params["development"] = development
            root.params = json.dumps(params)
        db.commit()
        return {
            "chat_id": int(root.id),
            "title": root.title or "New Chat",
            "project_id": root.project_id,
            "identity_key": item.identity_key,
            "board_key": item.board_key,
            "board_provider": item.board_provider,
            "ticket_id": item.local_ticket_id,
            "board_ticket_key": item.ticket_key,
            "permission_profile": permission_profile,
            "remote_continuation": remote_continuation,
        }


def update_development_thread_settings(
    chat_id: int,
    *,
    title: str,
    permission_profile: dict[str, Any] | None = None,
    remote_continuation: bool | None = None,
) -> dict[str, Any]:
    """Update mutable thread settings while preserving its creation-time scope."""
    with get_session() as db:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None:
            raise ValueError(f"Development thread #{chat_id} was not found.")
        metadata = development_thread_metadata(root)
        project_id = int(root.project_id) if root.project_id is not None else None
    return rebind_development_thread(
        int(chat_id),
        title=title,
        project_id=project_id,
        board_key=metadata.get("board_key"),
        board_provider=metadata.get("board_provider"),
        ticket_id=metadata.get("ticket_id"),
        board_ticket_key=metadata.get("board_ticket_key"),
        board_ticket_title=metadata.get("board_ticket_title"),
        board_ticket_lane=metadata.get("board_ticket_lane"),
        permission_profile=permission_profile,
        remote_continuation=remote_continuation,
    )


def workflow_id_for_development_chat(chat: Chat) -> int | None:
    metadata = development_thread_metadata(chat)
    try:
        return int(metadata.get("workflow_id")) if metadata.get("workflow_id") is not None else None
    except (TypeError, ValueError):
        return None


def repair_development_ownership_integrity(*, _session_provider=None) -> dict[str, int]:
    """Repair stale cross-surface references without deleting user transcripts.

    Missing-root ownership rows are removed, invalid ticket links are detached,
    legacy Development projections are backfilled where identity is unambiguous,
    and conversational pointers are cleared when they target Development.
    """
    from distr.core.db import Settings
    from distr.core.db.kanban import KanbanTicket

    counts = {
        "orphan_work_items_removed": 0,
        "ticket_links_detached": 0,
        "work_items_backfilled": 0,
        "backfill_conflicts": 0,
        "chat_pointers_cleared": 0,
    }
    session_provider = _session_provider or get_session
    with session_provider() as db:
        for item in db.query(DevelopmentWorkItem).all():
            chat = db.get(Chat, int(item.chat_id))
            if chat is None or chat.parent_id is not None:
                db.delete(item)
                counts["orphan_work_items_removed"] += 1

        db.flush()
        for ticket in db.query(KanbanTicket).filter(KanbanTicket.source_chat_id.isnot(None)).all():
            chat = db.get(Chat, int(ticket.source_chat_id))
            if chat is None or chat.parent_id is not None or not is_development_thread(db, chat):
                ticket.source_chat_id = None
                counts["ticket_links_detached"] += 1

        roots = db.query(Chat).filter(Chat.parent_id.is_(None)).all()
        for root in roots:
            metadata = development_thread_metadata(root)
            if not metadata:
                continue
            if db.query(DevelopmentWorkItem.id).filter_by(chat_id=int(root.id)).first():
                continue
            try:
                _upsert_work_item(
                    db,
                    root,
                    workflow_id=metadata.get("workflow_id"),
                    ticket_id=metadata.get("ticket_id"),
                    source_type=metadata.get("source_type") or "prompt",
                    source_ref=metadata.get("source_ref"),
                    board_key=metadata.get("board_key"),
                    board_provider=metadata.get("board_provider"),
                    board_ticket_key=metadata.get("board_ticket_key"),
                    board_ticket_title=metadata.get("board_ticket_title") or root.title,
                    board_ticket_lane=metadata.get("board_ticket_lane"),
                )
                counts["work_items_backfilled"] += 1
            except DevelopmentThreadConflict:
                counts["backfill_conflicts"] += 1

        settings = db.query(Settings).first()
        if settings is not None:
            for key in ("agent_current_chat_id", "last_chat_id"):
                candidate = getattr(settings, key, None)
                if candidate is not None and resolve_conversational_chat_id(db, candidate) is None:
                    setattr(settings, key, None)
                    counts["chat_pointers_cleared"] += 1
        db.commit()
    return counts
