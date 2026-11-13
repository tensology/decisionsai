"""Create or reuse a Backlog card when an Incoming proposal becomes a thread."""

from __future__ import annotations

from typing import Any

from distr.core.kanban.lifecycle import DELIVERY_SOURCE_LANE, ensure_delivery_lanes, ensure_project_boards


def _positive_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _is_merged_proposal(proposal: dict[str, Any]) -> bool:
    return (bool(proposal.get('merged_from') or proposal.get('requires_merge_receipt'))
            or str(proposal.get('id') or '').startswith(('local:merge:', 'local:merged:')))


def _requires_scoped_ticket_match(proposal: dict[str, Any]) -> bool:
    source = str(proposal.get('source') or proposal.get('source_label') or '').strip().lower()
    return source == 'telegram' or _is_merged_proposal(proposal)


def _proposal_keys(proposal: dict[str, Any]) -> set[str]:
    from distr.core.incoming.proposals import _proposal_handoff_key, _tensology_id

    keys: set[str] = set()
    try:
        key = _proposal_handoff_key(proposal)
    except Exception:
        key = ""
    if _requires_scoped_ticket_match(proposal):
        return {key} if key else set()
    if key:
        keys.add(str(key))
        keys.add(str(key).split(":", 1)[-1] if ":" in str(key) else str(key))
    raw_id = str(proposal.get("id") or "").strip()
    if raw_id:
        keys.add(raw_id)
    tensology_id = _tensology_id(proposal.get("id"))
    if tensology_id is not None:
        keys.add(f"tensology:{tensology_id}")
        keys.add(str(tensology_id))
    for mid in proposal.get("message_ids") or []:
        if mid:
            keys.add(str(mid))
    for field in ("source_external_id", "source_message_id", "dedupe_key"):
        if proposal.get(field):
            keys.add(str(proposal.get(field)))
    return {item for item in keys if item}


def _message_ids(proposal: dict[str, Any]) -> set[str]:
    ids = {str(value) for value in (proposal.get("message_ids") or []) if value}
    for field in ("source_external_id", "source_message_id"):
        if proposal.get(field):
            ids.add(str(proposal.get(field)))
    return ids


def _find_scoped_proposal_ticket(session, proposal, project_id):
    from sqlalchemy import and_, or_
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.incoming.proposals import _tensology_id

    query = (session.query(KanbanTicket)
             .join(KanbanLane, KanbanLane.id == KanbanTicket.lane_id)
             .join(KanbanBoard, KanbanBoard.id == KanbanLane.board_id))
    if project_id:
        query = query.filter(or_(KanbanTicket.linked_project_id == project_id, and_(
            KanbanTicket.linked_project_id.is_(None), KanbanBoard.default_project_id == project_id,
        )))
    keys = _proposal_keys(proposal)
    if keys:
        exact = [KanbanTicket.source_external_id.in_(keys), KanbanTicket.external_id.in_(keys)]
        if (remote_id := _tensology_id(proposal.get('id'))) is not None:
            exact.append(and_(KanbanTicket.external_source == 'tensology', KanbanTicket.external_id == str(remote_id)))
        match = query.filter(or_(*exact)).order_by(KanbanTicket.id).first()
        if match is not None:
            return match
    if _is_merged_proposal(proposal):
        return None
    thread = proposal.get('source_thread_id')
    if isinstance(thread, str) and thread and (messages := _message_ids(proposal)):
        return query.filter(KanbanTicket.source_provider == 'telegram', KanbanTicket.source_thread_id == thread,
                            KanbanTicket.source_external_id.in_(messages)).order_by(KanbanTicket.id).first()
    return None


def ensure_linked_project_board(session: Any, project_id: int):
    """Return the project's local board, creating canonical delivery lanes if needed."""
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    project = session.get(Project, int(project_id))
    if project is None:
        raise ValueError("Link the proposal to a local project before creating its thread.")
    boards = ensure_project_boards(session)
    board = boards.get(int(project_id))
    if board is None:
        board = (
            session.query(KanbanBoard)
            .filter(KanbanBoard.default_project_id == int(project_id), KanbanBoard.archived.is_(False))
            .order_by(KanbanBoard.id.asc())
            .first()
        )
    if board is None:
        board = KanbanBoard(
            name=project.name or "Untitled",
            description=f"Board for project: {project.name or 'Untitled'}",
            source="database",
            default_project_id=int(project_id),
            archived=False,
        )
        session.add(board)
        session.flush()
        project.kanban_board_id = int(board.id)
    ensure_delivery_lanes(session, int(board.id))
    session.flush()
    return board


def find_existing_ticket_for_proposal(session: Any, proposal: dict[str, Any], project_id: int, *, chat_id: int | None = None):
    """Reuse a card already created for this source (e.g. a Gmail intake ticket)."""
    from distr.core.db.kanban import KanbanLane, KanbanTicket
    from distr.core.db.workflow import DevelopmentWorkItem

    if chat_id is not None:
        owned = session.query(KanbanTicket).filter(KanbanTicket.source_chat_id == int(chat_id)).first()
        if owned is not None:
            return owned
        item = session.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.chat_id == int(chat_id)).first()
        if item is not None and item.local_ticket_id:
            ticket = session.get(KanbanTicket, int(item.local_ticket_id))
            if ticket is not None:
                return ticket

    if _requires_scoped_ticket_match(proposal):
        return _find_scoped_proposal_ticket(session, proposal, project_id)

    message_ids = _message_ids(proposal)
    keys = _proposal_keys(proposal)
    source = str(proposal.get("source") or proposal.get("source_label") or "").strip().lower()

    query = session.query(KanbanTicket)
    if project_id:
        query = query.filter(KanbanTicket.linked_project_id == int(project_id))

    def first_legacy_match(candidates):
        from distr.core.chat import _chat_params
        from distr.core.db import Chat

        for ticket in candidates.order_by(KanbanTicket.id):
            if str(ticket.source_provider or '').lower() == 'telegram':
                continue
            # Older Development cards kept a bare message number on the card
            # and the provider only in the thread's saved proposal context.
            chat = session.get(Chat, ticket.source_chat_id) if ticket.source_chat_id else None
            if chat is not None:
                params = _chat_params(chat.params)
                handoff = params.get('incoming_proposal_handoff')
                saved = handoff.get('proposal') if isinstance(handoff, dict) else None
                context = params.get('incoming_reply_context')
                if any(isinstance(item, dict) and _requires_scoped_ticket_match(item) for item in (saved, context)):
                    continue
            return ticket
        return None

    if message_ids:
        match = first_legacy_match(query.filter(KanbanTicket.source_external_id.in_(message_ids)))
        if match is not None:
            return match

    if keys:
        match = first_legacy_match(query.filter(KanbanTicket.source_external_id.in_(keys)))
        if match is not None:
            return match
        match = first_legacy_match(query.filter(KanbanTicket.external_id.in_(keys)))
        if match is not None:
            return match

    # Broaden to the project's board if linked_project_id was unset on an older ticket.
    if message_ids or (source in {"gmail", "email", "mail"} and keys):
        from distr.core.db.kanban import KanbanBoard

        board_ids = [
            int(row.id)
            for row in session.query(KanbanBoard).filter(KanbanBoard.default_project_id == int(project_id)).all()
        ]
        if board_ids:
            candidates = first_legacy_match(
                session.query(KanbanTicket)
                .join(KanbanLane, KanbanLane.id == KanbanTicket.lane_id)
                .filter(KanbanLane.board_id.in_(board_ids))
                .filter(KanbanTicket.source_external_id.in_(message_ids or keys))
            )
            if candidates is not None:
                return candidates
    return None


def find_thread_for_proposal(proposal: dict[str, Any], project_id: int | None = None) -> int | None:
    """Find an Incoming or Tensology-created Development thread for this request."""
    from distr.core.chat import _chat_params
    from distr.core.db import Chat, get_session
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.incoming.proposals import _proposal_handoff_key
    from distr.core.workflow.development_threads import development_identity_key

    keys = _proposal_keys(proposal)
    try:
        handoff = _proposal_handoff_key(proposal)
    except Exception:
        handoff = ""
    identity_keys = set()
    if handoff:
        identity_keys.add(development_identity_key(source_type="incoming_proposal", source_ref=handoff))
        identity_keys.add(f"source:incoming_proposal:{handoff}")
    with get_session() as db:
        if identity_keys:
            item = (
                db.query(DevelopmentWorkItem)
                .filter(DevelopmentWorkItem.identity_key.in_(list(identity_keys)))
                .first()
            )
            if item is not None:
                if project_id is not None and item.project_id not in (None, int(project_id)):
                    raise ValueError("The proposal thread belongs to another project.")
                return int(item.chat_id)
        if keys:
            for chat, item in (
                db.query(Chat, DevelopmentWorkItem)
                .join(DevelopmentWorkItem, DevelopmentWorkItem.chat_id == Chat.id)
                .filter(Chat.parent_id.is_(None))
                .all()
            ):
                receipt = _chat_params(chat.params).get("incoming_proposal_handoff") or {}
                receipt_key = str(receipt.get("key") or "")
                source_ref = str((development_identity_key and None) or "")
                metadata_ref = ""
                if receipt_key and receipt_key in keys:
                    if project_id is not None and chat.project_id not in (None, int(project_id)):
                        raise ValueError("The proposal thread belongs to another project.")
                    return int(chat.id)
                if item.identity_key and any(item.identity_key.endswith(f":{key}") or key in item.identity_key for key in keys if "tensology" in key or key.isdigit()):
                    # Prefer exact identity matches already handled above; skip loose contains for numeric ids.
                    pass
        ticket = find_existing_ticket_for_proposal(db, proposal, int(project_id or 0) or 0)
        if ticket is not None and ticket.source_chat_id:
            return int(ticket.source_chat_id)
        if ticket is not None:
            item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.local_ticket_id == int(ticket.id)).first()
            if item is not None:
                return int(item.chat_id)
    return None


def find_thread_for_tensology_ticket(ticket: dict[str, Any]) -> int | None:
    """Reuse an Incoming proposal thread or an earlier Tensology thread for this ticket."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.incoming.proposals import _proposal_handoff_key

    claimed = str(ticket.get("decisions_thread_id") or "").strip()
    if claimed.isdigit():
        return int(claimed)

    proposal = ticket.get("incoming_proposal") if isinstance(ticket.get("incoming_proposal"), dict) else {}
    meta = ticket.get("source_metadata") if isinstance(ticket.get("source_metadata"), dict) else {}
    proposal_id = meta.get("incoming_proposal_id") or proposal.get("id")
    shaped = dict(proposal)
    from distr.core.incoming.proposals import _tensology_id
    numeric = _tensology_id(proposal_id) or _tensology_id(shaped.get("id"))
    if numeric is None and str(proposal_id or "").isdigit():
        numeric = int(proposal_id)
    if numeric is not None:
        shaped["id"] = f"tensology:{numeric}"
    elif proposal_id and not shaped.get("id"):
        shaped["id"] = str(proposal_id)
    if ticket.get("reference"):
        shaped.setdefault("source_external_id", ticket.get("reference"))
        shaped.setdefault("id", shaped.get("id") or str(ticket.get("reference")))
    project_id = _positive_id(ticket.get("decisions_project_id") or (shaped.get("project_id")))
    found = find_thread_for_proposal(shaped, project_id) if shaped else None
    if found:
        return found

    reference = str(ticket.get("reference") or "").strip()
    with get_session() as db:
        if reference:
            item = (
                db.query(DevelopmentWorkItem)
                .filter(DevelopmentWorkItem.ticket_key == reference)
                .first()
            )
            if item is not None:
                return int(item.chat_id)
            mirrored = (
                db.query(KanbanTicket)
                .filter(KanbanTicket.external_source == "tensology", KanbanTicket.external_id == reference)
                .first()
            )
            if mirrored is not None and mirrored.source_chat_id:
                return int(mirrored.source_chat_id)
    return None


def bind_proposal_thread_ticket(chat_id: int, proposal: dict[str, Any], project_id: int) -> dict[str, Any]:
    """Ensure the thread owns a card on the project board in Backlog, then bind metadata.

    Must run before start_work dispatch so workflow/CLI hooks can move the card.
    Re-accepting or an existing source ticket (for example a prior Gmail card)
    reuses that ticket and does not move it out of its current lane.
    """
    from distr.core.db import Chat, get_session
    from distr.core.workflow.development_threads import project_board_key, rebind_development_thread

    with get_session() as db:
        chat = db.get(Chat, int(chat_id))
        if chat is None:
            # Unit tests (and a mocked ensure_development_thread) may pass a
            # synthetic id that has no chat row. Do not invent a card for it.
            return {"skipped": True, "reason": "thread_not_found", "chat_id": int(chat_id)}
        from distr.core.db.workflow import DevelopmentWorkItem
        from distr.core.workflow.development_threads import development_thread_metadata

        item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.chat_id == int(chat_id)).first()
        if item is None and not development_thread_metadata(chat):
            # ensure_development_thread was mocked; there is no work item to bind.
            return {"skipped": True, "reason": "not_a_development_thread", "chat_id": int(chat_id)}
        board = ensure_linked_project_board(db, int(project_id))
        existing = find_existing_ticket_for_proposal(db, proposal, int(project_id), chat_id=int(chat_id))
        ticket_id = int(existing.id) if existing is not None else None
        title = str(proposal.get("title") or "").strip() or None
        board_id = int(board.id)
        db.commit()

    board_key = f"decisions:{board_id}"
    # project_board_key is the same value when the board is the project's default.
    resolved = project_board_key(int(project_id)) or board_key
    result = rebind_development_thread(
        int(chat_id),
        project_id=int(project_id),
        board_key=resolved,
        board_provider="local",
        ticket_id=ticket_id,
        board_ticket_title=title,
    )
    _stamp_proposal_source_on_ticket(int(chat_id), proposal)
    return result

def _stamp_proposal_source_on_ticket(chat_id: int, proposal: dict[str, Any]) -> None:
    """Persist a stable source key so later Tensology/Gmail lookups can reuse the card."""
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket
    from distr.core.incoming.proposals import _proposal_handoff_key, _tensology_id

    tensology_id = _tensology_id(proposal.get("id"))
    message_ids = _message_ids(proposal)
    if _requires_scoped_ticket_match(proposal) or tensology_id is not None:
        preferred = _proposal_handoff_key(proposal)
    else:
        preferred = next(iter(message_ids), None)
    if not preferred:
        return
    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.source_chat_id == int(chat_id)).first()
        if ticket is None:
            return
        current = str(ticket.source_external_id or "").strip()
        if current and not current.startswith("thread:"):
            return
        ticket.source_external_id = preferred
        if tensology_id is not None:
            ticket.source_provider = ticket.source_provider or "tensology"
            ticket.source_label = ticket.source_label or f"tensology:{tensology_id}"
        db.commit()


