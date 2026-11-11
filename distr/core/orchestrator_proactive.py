"""Proactive work radar and project orchestration helpers.

This layer treats boards and external-intake tickets as work signals, scores
them, matches them to projects, and prepares approval-first dispatches through
the existing project backend harness.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import json
import hashlib
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from distr.core.db import get_session
from distr.core.project_cli_backends.registry import run_project_task

logger = logging.getLogger(__name__)
_active_dispatches: set[tuple[int, int, int]] = set()
_active_dispatches_lock = threading.Lock()

ACTIVE_SOURCES = {"gmail", "slack", "whatsapp", "telegram", "trello", "jira", "board", "manual", "web"}
DONE_LANE_HINTS = {"done", "closed", "complete", "completed", "cancelled", "canceled", "archive", "archived"}
UNRESOLVED_DISPATCH_STATUSES = {"dispatching", "unknown", "waiting"}
URGENT_TERMS = (
    "urgent",
    "asap",
    "today",
    "blocked",
    "broken",
    "failing",
    "failed",
    "down",
    "client",
    "customer",
    "production",
    "prod",
    "cannot",
    "can't",
)


def run_proactive_check(
    *,
    limit: int = 12,
    source_filter: str | None = None,
    project_id: int | None = None,
    board_id: int | None = None,
    emit: bool = True,
) -> dict[str, Any]:
    """Scan boards/source tickets for important work and return approval-ready candidates."""
    signals = _collect_board_signals(
        limit=max(1, min(int(limit or 12), 50)),
        source_filter=source_filter,
        project_id=project_id,
        board_id=board_id,
    )
    developer_context = _developer_context_by_project(_external_context())
    candidates = [_build_candidate(signal, developer_context.get(signal["project_id"], {})) for signal in signals]
    candidates.sort(key=lambda item: (int(item.get("priority_score") or 0), item.get("modified_at") or ""), reverse=True)
    emitted_ids: list[int] = []
    if emit:
        for candidate in candidates:
            event_id = _emit_candidate(candidate)
            if event_id:
                candidate["candidate_id"] = int(event_id)
                emitted_ids.append(int(event_id))
        _emit_check_run(candidates, emitted_ids)
    summary = _summarize_candidates(candidates)
    return {
        "success": True,
        "summary": summary,
        "candidates": candidates,
        "spoken_summary": _spoken_summary(candidates),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def dispatch_proactive_candidate(
    candidate_id: int,
    *,
    approved_by: str = "",
    backend_id: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Dispatch an approved proactive candidate through the project backend harness."""
    candidate = _candidate_from_event(candidate_id)
    if not candidate:
        return {"success": False, "error": "I could not find that work candidate."}
    project_id = candidate.get("project_id")
    ticket_id = candidate.get("ticket_id")
    if not project_id:
        return {"success": False, "error": "That work candidate is not linked to a project yet."}

    state = _dispatch_state(candidate)
    if state and state.get("candidate_id") == int(candidate_id) and state.get("status") in {"dispatching", "unknown"}:
        receipt = _record_dispatch_result(candidate, recover=True)
        if receipt:
            _advance_proactive_queue(candidate, receipt)
            return {
                "success": receipt["status"] == "dispatched",
                "candidate_id": int(candidate_id),
                **_dispatch_thread_link(receipt),
                "backend_id": receipt["backend_id"],
                "model": receipt.get("model"),
                "engine": receipt["engine"],
                "execution_session_id": receipt["execution_session_id"],
                "outcome_recorded": True,
                "outcome_recovered": True,
                "error": receipt["error"],
                "spoken_summary": (
                    f"I recovered the previous dispatch outcome ({receipt['status']}). "
                    "Review the existing execution for details."
                ),
            }
    if state and state.get("candidate_id") == int(candidate_id) and state.get("chat_id") is not None:
        _advance_proactive_queue(candidate, state)
        return {
            "success": False, "candidate_id": int(candidate_id), **_dispatch_thread_link(state),
            "execution_session_id": state.get("execution_session_id"),
            "error": "This approval already has a Development turn. Review its existing outcome.",
            "spoken_summary": "This approval already has a Development turn. Review its existing outcome.",
        }

    if candidate.get("workflow_draft"):
        message = "This candidate has a workflow draft for review. Direct dispatch cannot launch it."
        return {"success": False, "candidate_id": int(candidate_id), "error": message, "spoken_summary": message}

    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.workflow.development_threads import DevelopmentThreadConflict

    from distr.core.db.kanban import KanbanLane, KanbanBoard
    with get_session() as session:
        project = session.query(Project).filter(Project.id == int(project_id)).first()
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first() if ticket_id else None
        if not project or project.deleted_at is not None:
            return {"success": False, "error": "The linked project no longer exists."}
        lane = session.get(KanbanLane, ticket.lane_id) if ticket else None
        board = session.get(KanbanBoard, lane.board_id) if lane else None
        if not ticket or not lane or not board:
            return {"success": False, "error": "The ticket or its board no longer exists. Scan again."}
        current_project = ticket.linked_project_id if ticket.linked_project_id is not None else board.default_project_id
        if current_project != int(project_id) or board.id != candidate.get('board_id'):
            return {"success": False, "error": "The ticket changed project or board. Scan again before approving it."}
        if _inactive_ticket(ticket, lane, board, session):
            return {"success": False, "error": "This ticket is completed, archived, or already running. Scan again."}
        if (str(ticket.title or '') != str(candidate.get('title') or '')
                or str(ticket.description or '') != str(candidate.get('description') or '')
                or (candidate.get('lane_id') is not None and candidate['lane_id'] != lane.id)
                or (candidate.get('modified_at') and candidate['modified_at'] != ticket.modified_date.isoformat())):
            return {"success": False, "error": "The ticket changed after this candidate was prepared. Scan again."}
        instruction = _dispatch_instruction(candidate, ticket)
        complexity = str(candidate.get("complexity") or getattr(ticket, "complexity", None) or "medium")
        resolved_backend = (backend_id or candidate.get("route", {}).get("backend") or getattr(project, "coding_backend", "") or "pi").strip()
        resolved_model = (model or candidate.get("route", {}).get("model") or "").strip()

    try:
        claim = _claim_dispatch(candidate, approved_by=approved_by or "user", instruction=instruction)
    except DevelopmentThreadConflict as exc:
        return {"success": False, "candidate_id": int(candidate_id), "error": str(exc), "spoken_summary": str(exc)}
    if not claim:
        return {"success": False, "error": "This ticket already has a dispatch or changed before approval. Review its current execution outcome before sending it again."}
    thread = _dispatch_thread_link(claim)
    dispatch_key = (int(candidate_id), claim["chat_id"], claim["turn_id"])
    with _active_dispatches_lock:
        _active_dispatches.add(dispatch_key)
    try:
        result = _run_backend_task(
            project,
            instruction,
            ticket_id=int(ticket_id) if ticket_id else None,
            ticket_complexity=complexity,
            backend_id_override=resolved_backend,
            model_override=resolved_model if resolved_model and resolved_model.lower() != "auto" else None,
            origin="proactive_orchestrator",
            chat_id=claim["chat_id"],
            turn_id=claim["turn_id"],
            board_id_override=candidate.get("board_id"),
            proactive_candidate_id=int(candidate_id),
            codex_reasoning_effort_override=(candidate.get("route", {}).get("codex_reasoning_effort") or None),
            codex_service_tier_override=(candidate.get("route", {}).get("codex_service_tier") or None),
        )
    except Exception as exc:
        receipt = _record_dispatch_result(candidate, success=False, error=str(exc), unknown=True,
                                          approved_by=approved_by or 'user')
        _emit(source='proactive_orchestrator', event_type='proactive_work_dispatched', status='unknown',
              ticket_id=int(ticket_id), board_id=candidate.get('board_id'), project_id=int(project_id),
              parent_event_id=int(candidate_id),
              summary='Approved proactive dispatch outcome could not be confirmed.',
              payload={'candidate_id':int(candidate_id)}, evidence={'error':str(exc)})
        return {'success': False, 'candidate_id': int(candidate_id), 'error': str(exc), **thread,
                'outcome_recorded': receipt is not None, 'spoken_summary': 'I could not confirm the dispatch outcome. Review the existing execution before retrying.'}
    finally:
        with _active_dispatches_lock:
            _active_dispatches.discard(dispatch_key)
    waits_for_human = bool(getattr(result, 'waits_for_human', False))
    receipt = _record_dispatch_result(
        candidate, success=bool(result.success), error=result.error or '', waits_for_human=waits_for_human,
        backend_id=result.backend_id, engine=result.engine, execution_session_id=result.execution_session_id,
        approved_by=approved_by or 'user', output=result.output,
    )
    if receipt:
        _advance_proactive_queue(candidate, receipt)
    recorded = receipt is not None
    execution_id = receipt['execution_session_id'] if receipt else None
    payload = {
        "candidate_id": int(candidate_id),
        "candidate": candidate,
        **thread,
        "approved_by": approved_by or "user",
        "backend_id": result.backend_id,
        "model": receipt.get("model") if receipt else None,
        "engine": result.engine,
        "execution_session_id": execution_id,
    }
    _emit(
        source="proactive_orchestrator",
        event_type="proactive_work_dispatched",
        status="waiting" if waits_for_human else "dispatched" if result.success else "failed",
        ticket_id=int(ticket_id) if ticket_id else None,
        board_id=candidate.get("board_id"),
        project_id=int(project_id),
        execution_session_id=execution_id,
        parent_event_id=int(candidate_id),
        summary=("Approved proactive work is waiting for human review." if waits_for_human
                 else f"Approved proactive work dispatched to {_backend_label(result.backend_id)}." if result.success
                 else f"Approved proactive work could not be dispatched: {result.error or 'backend rejected the task'}."),
        payload=payload,
        evidence={"output": result.output, "error": result.error},
    )
    if not recorded:
        spoken = "The worker returned, but I could not save its outcome. Review the existing execution before retrying."
    elif waits_for_human:
        spoken = "The worker paused for human review. Check the existing execution before retrying."
    elif result.success:
        spoken = f"I sent that {candidate.get('project_name') or 'project'} work item to {_backend_label(result.backend_id)}."
    else:
        spoken = f"I could not dispatch that work item: {result.error or 'the backend did not accept it'}."
    return {
        "success": bool(result.success) and not waits_for_human,
        "candidate_id": int(candidate_id),
        **thread,
        "backend_id": result.backend_id,
        "model": receipt.get("model") if receipt else None,
        "engine": result.engine,
        "execution_session_id": execution_id,
        "outcome_recorded": recorded,
        "output": result.output,
        "error": result.error,
        "spoken_summary": spoken,
    }


def _collect_board_signals(
    *,
    limit: int,
    source_filter: str | None = None,
    project_id: int | None = None,
    board_id: int | None = None,
) -> list[dict[str, Any]]:
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project

    wanted_source = _normalize_source_filter(source_filter)
    with get_session() as session:
        query = (
            session.query(KanbanTicket, KanbanLane, KanbanBoard, Project)
            .join(KanbanLane, KanbanLane.id == KanbanTicket.lane_id)
            .join(KanbanBoard, KanbanBoard.id == KanbanLane.board_id)
            .outerjoin(Project, Project.id == KanbanTicket.linked_project_id)
            .filter(KanbanBoard.archived == False)  # noqa: E712
        )
        if board_id:
            query = query.filter(KanbanBoard.id == int(board_id))
        if project_id:
            query = query.filter(
                (KanbanTicket.linked_project_id == int(project_id))
                | ((KanbanTicket.linked_project_id.is_(None)) & (KanbanBoard.default_project_id == int(project_id)))
            )
        rows = query.order_by(KanbanTicket.modified_date.desc(), KanbanTicket.created_date.desc()).yield_per(200)
        signals: list[dict[str, Any]] = []
        for ticket, lane, board, linked_project in rows:
            lane_name = str(getattr(lane, "name", "") or "")
            if _inactive_ticket(ticket, lane, board, session):
                continue
            source = _signal_source(ticket)
            if wanted_source and source != wanted_source:
                continue
            default_project = None
            if ticket.linked_project_id is not None and (not linked_project or linked_project.deleted_at is not None):
                continue
            if ticket.linked_project_id is None and getattr(board, "default_project_id", None):
                default_project = session.query(Project).filter(Project.id == int(board.default_project_id)).first()
            project = linked_project or default_project
            if project is not None and project.deleted_at is not None:
                continue
            if source not in ACTIVE_SOURCES and not project:
                continue
            signal = {
                "ticket_id": ticket.id,
                "title": ticket.title or "",
                "description": ticket.description or "",
                "priority": ticket.priority or "medium",
                "complexity": ticket.complexity or "medium",
                "source": source,
                "source_contact": ticket.source_contact or "",
                "source_thread_id": ticket.source_thread_id or "",
                "source_label": ticket.source_label or "",
                "source_url": ticket.source_url or ticket.external_url or "",
                "board_id": board.id,
                "board_name": board.name or "",
                "lane_name": lane_name,
                "lane_id": lane.id,
                "project_id": project.id if project else None,
                "project_name": project.name if project else "",
                "project_folder": project.folder_location if project else "",
                "project_backend": project.coding_backend if project else "",
                "project_model": project.coding_backend_model if project else "",
                "can_draft_workflow": bool(project and _can_draft_workflow(ticket, board)),
                "created_at": ticket.created_date.isoformat() if ticket.created_date else "",
                "modified_at": ticket.modified_date.isoformat() if ticket.modified_date else "",
            }
            signal["priority_score"] = _priority_score(signal)
            signals.append(signal)
            # Stream the collection while retaining only its strongest signals.
            # A recent slice can otherwise hide an older production blocker.
            signals.sort(key=lambda item: (int(item.get('priority_score') or 0), item.get('modified_at') or ''), reverse=True)
            del signals[limit:]
        signals.sort(key=lambda item: (int(item.get("priority_score") or 0), item.get('modified_at') or ''), reverse=True)
        draft_signals = {signal["ticket_id"]: signal for signal in signals if signal.pop("can_draft_workflow")}
        checklists = _workflow_checklists(session, draft_signals)
        for ticket_id, signal in draft_signals.items():
            draft = _workflow_draft(signal, checklists.get(ticket_id))
            if draft:
                signal["workflow_draft"] = draft
        return signals[:limit]


def _can_draft_workflow(ticket, board) -> bool:
    return (
        not ticket.send_to_cli
        and not (ticket.linked_workflow_id or board.default_workflow_id)
        and str(ticket.complexity or '').strip().casefold() in {"high", "complex", "very_complex"}
    )


def _workflow_checklists(session, ticket_ids) -> dict[int, list[str]]:
    from distr.core.db.kanban import KanbanTicketTodo

    if not ticket_ids:
        return {}
    todos = (
        session.query(KanbanTicketTodo.ticket_id, KanbanTicketTodo.text, KanbanTicketTodo.done)
        .filter(KanbanTicketTodo.ticket_id.in_(ticket_ids))
        .order_by(KanbanTicketTodo.ticket_id, KanbanTicketTodo.position, KanbanTicketTodo.id)
        .all()
    )
    by_ticket: dict[int, dict[str, str]] = {}
    for ticket_id, label, done in todos:
        pending = by_ticket.setdefault(ticket_id, {})
        step = (label or "").strip()
        if step and not done:
            pending.setdefault(step.casefold(), step)
    return {ticket_id: list(labels.values()) for ticket_id, labels in by_ticket.items()}


def _description_workflow_steps(description: str) -> list[str]:
    steps: dict[str, str] = {}
    in_steps = False
    fence = ""
    for line in description.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
            continue
        if marker:
            fence = marker[1]
            continue
        heading = re.match(r"^ {0,3}#{1,6}[ \t]+(.+)$", line)
        if heading:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[1]).strip().casefold()
            in_steps = title in {"implementation steps", "implementation plan", "workflow steps", "workflow stages"}
            continue
        if not in_steps:
            continue
        item = re.match(r"^(?:[-*+]|[0-9]{1,9}[.)])[ \t]+(.+)$", line)
        if not item:
            continue
        label = item[1].strip()
        task = re.match(r"^\[([ xX])\][ \t]+(.+)$", label)
        if task:
            if task[1] != " ":
                continue
            label = task[2].strip()
        if label:
            steps.setdefault(label.casefold(), label)
    return list(steps.values())


def _workflow_draft(signal: dict[str, Any], checklist: list[str] | None) -> dict[str, Any] | None:
    from_description = checklist is None
    labels = _description_workflow_steps(signal.get("description") or "") if from_description else checklist
    if len(labels) < 3:
        return None
    revision_data = {"description_steps": labels} if from_description else labels
    # The candidate holds review data only. It cannot be passed to the workflow runner.
    step_limit = 12
    return {
        "status": "awaiting_review",
        "name": signal["title"],
        "reason": (
            f"The ticket description lists {len(labels)} distinct implementation steps."
            if from_description else f"The ticket has {len(labels)} distinct pending checklist items."
        ),
        "project_id": signal["project_id"],
        "board_id": signal["board_id"],
        "ticket_id": signal["ticket_id"],
        "steps": [
            {"instruction": label, "acceptance": "Record the check performed and its result for this item."}
            for label in labels[:step_limit]
        ],
        "omitted_steps": max(0, len(labels) - step_limit),
        "checklist_revision": hashlib.sha256(json.dumps(revision_data, ensure_ascii=False).encode()).hexdigest(),
    }


def review_workflow_draft(*, board_id: int, ticket_id: int, _session=None) -> dict[str, Any]:
    """Read the latest candidate draft when it still matches the ticket."""
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.projects import Project

    with (get_session() if _session is None else nullcontext(_session)) as session:
        ticket = session.get(KanbanTicket, ticket_id)
        lane = session.get(KanbanLane, ticket.lane_id) if ticket else None
        board = session.get(KanbanBoard, board_id)
        if not lane or not board or lane.board_id != board_id or board.archived:
            raise LookupError("This ticket is unavailable on the selected board.")
        project_id = ticket.linked_project_id if ticket.linked_project_id is not None else board.default_project_id
        project = session.get(Project, project_id) if project_id is not None else None
        if not project or project.deleted_at is not None:
            return {"status": "unavailable"}
        events = session.query(OrchestratorEvent).filter_by(
            source="proactive_orchestrator", event_type="proactive_work_candidate",
            board_id=board_id, ticket_id=ticket_id, project_id=project_id,
        ).order_by(OrchestratorEvent.id.desc())
        if ticket.linked_workflow_id is not None:
            launch = events.filter_by(status="workflow_launched", workflow_id=ticket.linked_workflow_id).first()
            if launch:
                from distr.core.workflow.draft_launch import approved_draft_receipt

                return approved_draft_receipt(session, launch)
        event = events.first()
        if event is None:
            return {"status": "missing"}
        try:
            candidate = json.loads(event.payload or "{}")["candidate"]
            draft = candidate.get("workflow_draft")
        except (ValueError, KeyError, TypeError, AttributeError):
            return {"status": "stale"}
        if not draft:
            return {"status": "missing"}
        scope = {"board_id": board_id, "ticket_id": ticket_id, "project_id": project_id}
        checklist = _workflow_checklists(session, [ticket_id]).get(ticket_id)
        current_signal = {**scope, "title": ticket.title or "", "description": ticket.description or ""}
        if (event.status != "approval_required"
                or any(candidate.get(key) != value for key, value in scope.items())
                or not _can_draft_workflow(ticket, board) or _inactive_ticket(ticket, lane, board, session)
                or candidate.get("lane_id") != lane.id
                or candidate.get("description") != (ticket.description or "")
                or candidate.get("modified_at") != (ticket.modified_date.isoformat() if ticket.modified_date else "")
                or draft != _workflow_draft(current_signal, checklist)):
            return {"status": "stale"}
        return {"status": "awaiting_review", "candidate_id": event.id, "project_name": project.name, "draft": draft}


def _build_candidate(signal: dict[str, Any], context: dict[str, list[str]]) -> dict[str, Any]:
    route = _resolve_route(signal)
    candidate = dict(signal)
    candidate["priority_score"] = int(signal.get("priority_score") or 0)
    candidate["priority"] = _priority_label(candidate["priority_score"], signal.get("priority"))
    candidate["route"] = route
    surfaces = context.get("surfaces", [])
    candidate["developer_context"] = {
        "recent_surfaces": _unique(surfaces),
        "recent_codex_titles": _unique(context.get("titles", []))[:3],
        "has_recent_developer_context": bool(surfaces),
    }
    candidate["recommended_action"] = (
        "ask_approval_to_dispatch" if signal.get("project_id") else "ask_project_mapping"
    )
    if candidate.get("workflow_draft"):
        candidate["recommended_action"] = "review_workflow_draft"
    candidate["approval_question"] = _approval_question(candidate)
    previous = _dispatch_state(candidate)
    if previous:
        candidate.pop("workflow_draft", None)
        candidate['dispatch_status'] = previous.get('status') or 'unknown'
        if previous.get('execution_session_id') is not None:
            candidate['dispatch_execution_session_id'] = previous['execution_session_id']
        if previous.get("chat_id") is not None:
            candidate.update(_dispatch_thread_link(previous))
        candidate['recommended_action'] = 'review_dispatch_outcome'
        candidate['approval_question'] = (
            f"{candidate.get('project_name') or 'This project'}: {candidate.get('title') or 'this work'} was already submitted "
            f"(dispatch: {candidate['dispatch_status']}). Review that execution outcome before sending it again.")
    candidate["candidate_id"] = None
    return candidate


def _resolve_route(signal: dict[str, Any]) -> dict[str, Any]:
    if not signal.get("project_id"):
        return {"backend": "", "model": "", "source": "unmatched", "rationale": "No project linked yet."}
    try:
        from distr.core.db.kanban import KanbanBoard, KanbanTicket
        from distr.core.db.projects import Project
        from distr.core.orchestrator_routing import resolve_execution_route

        with get_session() as session:
            project = session.query(Project).filter(Project.id == int(signal["project_id"])).first()
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == int(signal["ticket_id"])).first()
            board = session.query(KanbanBoard).filter(KanbanBoard.id == int(signal["board_id"])).first()
            if not project:
                raise ValueError("Project missing")
            decision = resolve_execution_route(
                project=project,
                ticket=ticket,
                board=board,
                complexity=signal.get("complexity") or "medium",
                allow_orchestrator_override=True,
                emit_event=False,
            )
            return decision.to_route_dict()
    except Exception:
        return {
            "backend": signal.get("project_backend") or "pi",
            "model": signal.get("project_model") or "auto",
            "source": "fallback",
            "rationale": "Used project backend default.",
        }


def _priority_score(signal: dict[str, Any]) -> int:
    priority = str(signal.get("priority") or "medium").lower()
    score = {"critical": 80, "urgent": 80, "high": 62, "medium": 36, "low": 18}.get(priority, 36)
    text = f"{signal.get('title') or ''}\n{signal.get('description') or ''}".lower()
    score += min(24, sum(6 for term in URGENT_TERMS if term in text))
    if signal.get("source") in {"gmail", "whatsapp", "slack", "telegram"}:
        score += 8
    if signal.get("source") in {"jira", "trello"}:
        score += 5
    if signal.get("project_id"):
        score += 8
    if signal.get("source_contact"):
        score += 4
    return max(0, min(score, 100))


def _priority_label(score: int, raw_priority: Any = "") -> str:
    raw = str(raw_priority or "").strip().lower()
    if raw == "critical" or score >= 86:
        return "critical"
    if raw == "high" or score >= 66:
        return "high"
    if score >= 36:
        return "medium"
    return "low"


def _signal_source(ticket: Any) -> str:
    source = (
        getattr(ticket, "source_provider", None)
        or getattr(ticket, "external_source", None)
        or getattr(ticket, "source_label", None)
        or "board"
    )
    normalized = re.sub(r"[^a-z0-9]+", "", str(source).lower())
    if "whatsapp" in normalized:
        return "whatsapp"
    if "gmail" in normalized or "email" in normalized:
        return "gmail"
    if "slack" in normalized:
        return "slack"
    if "trello" in normalized:
        return "trello"
    if "jira" in normalized:
        return "jira"
    if "telegram" in normalized:
        return "telegram"
    return normalized or "board"


def _normalize_source_filter(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        from distr.core.kanban.ticket_policy import normalize_source_provider

        normalized = normalize_source_provider(raw)
        if normalized:
            return normalized
    except Exception:
        pass
    return re.sub(r"[^a-z0-9]+", "", raw.lower())


def _developer_context_by_project(external_context: dict[str, Any]) -> dict[int, dict[str, list[str]]]:
    from distr.core.db.projects import Project
    from distr.core.ide_bridge import project_for_cwd

    context: dict[int, dict[str, list[str]]] = {}
    with get_session() as session:
        # Include roots outside the scan filter so child activity cannot be
        # attributed to a parent merely because the child has no candidate.
        projects = session.query(Project).filter(Project.deleted_at.is_(None)).all()
        for source, collection, path_field in (
            ("Codex", "codex_threads", "cwd"),
            ("Cursor", "cursor_workspaces", "folder"),
        ):
            for item in external_context.get(collection) or []:
                owner = project_for_cwd(item.get(path_field), projects)
                if owner is None:
                    continue
                project_context = context.setdefault(owner.id, {"surfaces": [], "titles": []})
                project_context["surfaces"].append(source)
                if source == "Codex" and item.get("title"):
                    project_context["titles"].append(str(item["title"])[:160])
    return context


def _approval_question(candidate: dict[str, Any]) -> str:
    source = _source_label(candidate.get("source"))
    project = candidate.get("project_name") or "a project"
    backend = _backend_label((candidate.get("route") or {}).get("backend") or candidate.get("project_backend") or "project agent")
    title = candidate.get("title") or "this work item"
    if not candidate.get("project_id"):
        return f"{source} has work that looks important: {title}. Which project should I attach it to?"
    if candidate.get("workflow_draft"):
        return f"I drafted workflow steps for {project}: {title}. Would you like to review them before approving a launch?"
    return f"{source} has important work for {project}: {title}. Should I send it to {backend}?"


def _spoken_summary(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "I checked the work sources and did not find anything that needs approval right now."
    review = [candidate for candidate in candidates if candidate.get("recommended_action") == "review_dispatch_outcome"]
    if len(review) == len(candidates):
        if len(review) == 1:
            project = review[0].get("project_name") or "the linked project"
            if review[0].get("chat_id") is not None:
                return f"The work item for {project} already has a Development turn. Open it to review the current outcome."
            return f"The work item for {project} has already been submitted. Review its dispatch outcome."
        return f"I found {len(review)} previously submitted work items. Review their current dispatch outcomes."
    top = next(candidate for candidate in candidates if candidate.get("recommended_action") != "review_dispatch_outcome")
    count = len(candidates)
    project = top.get("project_name") or "an unmatched project"
    source = _source_label(top.get("source"))
    if top.get("workflow_draft"):
        if count == 1:
            return f"I drafted a workflow for {project}. It is ready for review before launch."
        return f"I found {count} work items. The most important is for {project}, and its workflow draft is ready for review."
    backend = _backend_label((top.get("route") or {}).get("backend") or top.get("project_backend") or "")
    if count == 1:
        return f"I found one important work item from {source} for {project}. I would ask before sending it to {backend or 'a project agent'}."
    return f"I found {count} work items worth reviewing. The most important one is from {source} for {project}, and I would ask before sending it to {backend or 'a project agent'}."


def _summarize_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_project: dict[str, int] = {}
    for item in candidates:
        by_source[item.get("source") or "board"] = by_source.get(item.get("source") or "board", 0) + 1
        project = item.get("project_name") or "Unmatched"
        by_project[project] = by_project.get(project, 0) + 1
    return {
        "total_candidates": len(candidates),
        "critical_count": sum(1 for item in candidates if item.get("priority") == "critical"),
        "high_count": sum(1 for item in candidates if item.get("priority") == "high"),
        "by_source": by_source,
        "by_project": by_project,
    }


def _dispatch_instruction(candidate: dict[str, Any], ticket: Any | None) -> str:
    title = candidate.get("title") or getattr(ticket, "title", "") or "Proactive work item"
    description = candidate.get("description") or getattr(ticket, "description", "") or ""
    source = _source_label(candidate.get("source"))
    contact = candidate.get("source_contact") or ""
    return (
        "Approved proactive project work.\n\n"
        f"Source: {source}\n"
        f"Contact: {contact or 'not specified'}\n"
        f"Ticket: {title}\n\n"
        "Request:\n"
        f"{description or title}\n\n"
        "Return a concise completion packet with status, summary, files changed, tests run, evidence, blockers, and next step."
    )


def _inactive_ticket(ticket, lane, board, session) -> bool:
    if board.archived or str(lane.name or '').strip().lower() in DONE_LANE_HINTS:
        return True
    if board.agent_done_lane and str(lane.name or '').strip().casefold() == board.agent_done_lane.strip().casefold():
        return True
    if str(ticket.workflow_status or '').lower() in {'completed', 'cancelled', 'canceled', 'queued', 'running', 'waiting'}:
        return True
    from distr.core.db.kanban import ProjectExecutionSession
    return session.query(ProjectExecutionSession.id).filter(
        ProjectExecutionSession.ticket_id == ticket.id,
        ProjectExecutionSession.status.in_(['queued', 'running', 'waiting'])).first() is not None


def _candidate_identity(candidate):
    fields = ('ticket_id', 'project_id', 'board_id', 'lane_id', 'title', 'description',
              'modified_at', 'complexity', 'source', 'source_thread_id')
    return hashlib.sha256(json.dumps({key:candidate.get(key) for key in fields}, sort_keys=True, default=str).encode()).hexdigest()


def _dispatch_thread_link(claim):
    chat_id = claim.get("chat_id")
    if chat_id is None:
        return {}
    return {"chat_id": chat_id, "turn_id": claim["turn_id"], "chat_url": f"/development/threads/{chat_id}/"}


def _add_proactive_turn_event(session, chat_id, turn_id, event_type, status, summary):
    from sqlalchemy import func
    from distr.core.chat_turns import redact_text
    from distr.core.db import ChatTurnEvent
    from distr.core.db.time import utc_now_naive

    sequence = session.query(func.max(ChatTurnEvent.sequence)).filter_by(chat_id=chat_id, turn_id=turn_id).scalar()
    now = utc_now_naive()
    session.add(ChatTurnEvent(
        chat_id=chat_id, turn_id=turn_id, sequence=(sequence or 0) + 1,
        event_id=f"cte_proactive_{chat_id}_{turn_id}_{event_type}", event_type=event_type,
        status=status, summary=redact_text(summary, limit=1200), created_date=now, modified_date=now,
        completed_date=now if status != "running" else None,
    ))


def _claim_dispatch(candidate, *, approved_by="user", instruction=None):
    from sqlalchemy import text
    from sqlalchemy.dialects.sqlite import insert
    from distr.core.chat import _append_user_turn, _chat_params
    from distr.core.db.orchestrator import OrchestratorMaintenanceState
    from distr.core.db.kanban import KanbanTicket, KanbanLane, KanbanBoard
    from distr.core.db.projects import Project
    from distr.core.workflow.development_threads import DevelopmentThreadConflict, bind_ticket_development_thread
    with get_session() as session:
        # Reserve at ticket scope under a short write transaction. Different
        # prepared revisions cannot both pass a pre-dispatch snapshot check.
        session.execute(text('BEGIN IMMEDIATE'))
        ticket = session.get(KanbanTicket, candidate.get('ticket_id'))
        lane = session.get(KanbanLane, ticket.lane_id) if ticket else None
        board = session.get(KanbanBoard, lane.board_id) if lane else None
        project = session.get(Project, candidate.get('project_id'))
        if not ticket or not lane or not board or not project or project.deleted_at is not None:
            return False
        current_project = ticket.linked_project_id if ticket.linked_project_id is not None else board.default_project_id
        if (current_project != candidate.get('project_id') or board.id != candidate.get('board_id')
                or _inactive_ticket(ticket, lane, board, session)
                or str(ticket.title or '') != str(candidate.get('title') or '')
                or str(ticket.description or '') != str(candidate.get('description') or '')
                or (candidate.get('lane_id') is not None and lane.id != candidate['lane_id'])
                or (candidate.get('modified_at') and candidate['modified_at'] != ticket.modified_date.isoformat())):
            return False
        if _can_draft_workflow(ticket, board):
            checklist = _workflow_checklists(session, [ticket.id]).get(ticket.id)
            if _workflow_draft(candidate, checklist):
                raise DevelopmentThreadConflict("This ticket now has a workflow draft for review. Scan again before approving.")
        ticket_key = 'proactive-ticket:' + str(ticket.id)
        ticket_claim = session.query(OrchestratorMaintenanceState).filter_by(key=ticket_key).first()
        if ticket_claim:
            try:
                state = json.loads(ticket_claim.value_json or '{}')
            except ValueError:
                return False
            if state.get('status') in UNRESOLVED_DISPATCH_STATUSES:
                return False
        claim = {"status": "dispatching", "candidate_id": candidate.get("candidate_id"), "approved_by": approved_by}
        result = session.execute(insert(OrchestratorMaintenanceState).values(
            key='proactive-dispatch:' + _candidate_identity(candidate),
            value_json=json.dumps(claim),
        ).on_conflict_do_nothing(index_elements=['key']))
        if not result.rowcount:
            return False
        if instruction is not None:
            root = bind_ticket_development_thread(
                session, ticket, project_id=project.id, board_id=board.id, lane_name=lane.name,
            )
            params = _chat_params(root.params)
            turn_id = _append_user_turn(session, root, params, instruction)
            _add_proactive_turn_event(session, root.id, turn_id, "turn_started", "running", "Approved work started.")
            claim.update(chat_id=root.id, turn_id=turn_id)
            development = params.setdefault("development", {})
            development["ticket_id"] = ticket.id
            development["proactive_dispatch"] = {"candidate_id": claim["candidate_id"], "turn_id": turn_id}
            root.params = json.dumps(params, ensure_ascii=False, default=str)
            session.query(OrchestratorMaintenanceState).filter_by(
                key="proactive-dispatch:" + _candidate_identity(candidate),
            ).one().value_json = json.dumps(claim)
        value = json.dumps({'status':'dispatching', 'ticket_id':ticket.id, 'candidate_identity':_candidate_identity(candidate)})
        if ticket_claim:
            ticket_claim.value_json = value
        else:
            session.add(OrchestratorMaintenanceState(key=ticket_key, value_json=value))
        session.commit()
        return claim


def _dispatch_state(candidate):
    from distr.core.db.orchestrator import OrchestratorMaintenanceState
    with get_session() as session:
        row = session.query(OrchestratorMaintenanceState).filter_by(
            key='proactive-dispatch:' + _candidate_identity(candidate)).first()
        try:
            if row:
                return json.loads(row.value_json or '{}')
            ticket_claim = session.query(OrchestratorMaintenanceState).filter_by(
                key='proactive-ticket:' + str(candidate.get('ticket_id'))).first()
            state = json.loads(ticket_claim.value_json or '{}') if ticket_claim else {}
            return state if state.get('status') in UNRESOLVED_DISPATCH_STATUSES else None
        except ValueError:
            return {'status':'unknown'}


def _record_dispatch_result(candidate, **result):
    from distr.core.db.orm_compat import retry_sqlite_locked

    try:
        return retry_sqlite_locked(lambda: _finish_dispatch_claim(candidate, **result))
    except SQLAlchemyError:
        logger.exception("Could not record proactive dispatch outcome for candidate %s", candidate.get('candidate_id'))
        return None


def _advance_proactive_queue(candidate, receipt):
    if not receipt.get("advance_queue"):
        return
    from distr.core.workflow.development_harness import DevelopmentExecutionBusy, _start_next_queued_instruction

    try:
        _start_next_queued_instruction(receipt["chat_id"], after_proactive={
            "project_id": candidate["project_id"], "board_id": candidate["board_id"],
            "ticket_id": candidate["ticket_id"], "turn_id": receipt["turn_id"],
        }, _session_provider=get_session)
    except DevelopmentExecutionBusy:
        logger.debug("Proactive follow-up remains queued for thread %s", receipt["chat_id"])
    except Exception:
        logger.exception("Could not start the follow-up for proactive thread %s", receipt["chat_id"])


def _execution_matches_claim(execution, candidate, claim):
    if (execution.project_id, execution.ticket_id) != (candidate["project_id"], candidate["ticket_id"]):
        return False
    if execution.origin != "proactive_orchestrator":
        return False
    try:
        packet = json.loads(execution.input_packet or "{}")
    except ValueError:
        return False
    return (
        isinstance(packet, dict)
        and packet.get("proactive_candidate_id") == candidate["candidate_id"]
        and packet.get("board_id") == candidate["board_id"]
        and (not claim.get("chat_id") or (
            packet.get("chat_id") == claim["chat_id"] and packet.get("turn_id") == claim["turn_id"]
        ))
    )


def _exact_proactive_execution(session, candidate, claim):
    from distr.core.db.kanban import ProjectExecutionSession

    matches = []
    for execution in session.query(ProjectExecutionSession).filter_by(
        project_id=candidate["project_id"], ticket_id=candidate["ticket_id"], origin="proactive_orchestrator",
    ):
        if _execution_matches_claim(execution, candidate, claim):
            matches.append(execution)
            if len(matches) > 1:
                return None
    return matches[0] if matches else None


def proactive_development_execution(session, root, scope, marker):
    """Read the live backend state for the exact approved Development turn."""
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionEvent
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.project_cli_backends.registry import backend_process_registered

    if not isinstance(marker, dict) or scope is None:
        return None
    candidate_id, turn_id = marker.get("candidate_id"), marker.get("turn_id")
    if type(candidate_id) is not int or type(turn_id) is not int:
        return None
    event = session.get(OrchestratorEvent, candidate_id)
    turn = session.get(Chat, turn_id)
    if (event is None or event.event_type != "proactive_work_candidate"
            or (event.project_id, event.ticket_id) != scope or event.board_id is None
            or turn is None or turn.parent_id != root.id):
        return None
    ticket = session.get(KanbanTicket, event.ticket_id)
    if ticket is None or ticket.source_chat_id != root.id:
        return None
    candidate = {"candidate_id": candidate_id, "project_id": event.project_id,
                 "ticket_id": event.ticket_id, "board_id": event.board_id}
    execution = _exact_proactive_execution(session, candidate, {"chat_id": root.id, "turn_id": turn_id})
    with _active_dispatches_lock:
        live = (candidate_id, root.id, turn_id) in _active_dispatches
    state = {
        "origin": "proactive_orchestrator", "direct": True, "turn_id": turn_id,
        "project_id": event.project_id, "board_id": event.board_id, "can_stop": False,
        "id": f"proactive-{candidate_id}", "status": "initializing" if live else "blocked",
        "summary": "Starting approved work." if live else "Review the previous approval to recover its outcome.",
    }
    if execution is None:
        return state
    packet = json.loads(execution.input_packet)
    backend = str(packet.get("backend_id") or execution.route_backend or "")
    active = execution.status in {"queued", "running"}
    can_stop = active and backend_process_registered(
        event.project_id, backend, board_id=event.board_id,
        execution_id=execution.id, execution_kind="proactive",
    )
    state.update(
        id=execution.id, execution_session_id=execution.id, backend_id=backend,
        model=execution.selected_model, started_at=execution.started_at.isoformat() if execution.started_at else None,
    )
    if active and (live or can_stop):
        progress = session.query(ProjectExecutionEvent.message).filter(
            ProjectExecutionEvent.session_id == execution.id,
            ProjectExecutionEvent.message.isnot(None),
            ProjectExecutionEvent.message != "",
        ).order_by(ProjectExecutionEvent.id.desc()).first()
        state.update(
            status=execution.status,
            summary=progress[0][:240] if progress else state["summary"],
            can_stop=can_stop,
        )
    elif live:
        state.update(status="running", summary="Saving the approved work result.")
    return state


def _finish_dispatch_claim(candidate, *, success=False, error='', unknown=False, waits_for_human=False,
                           backend_id='', engine='', execution_session_id=None, approved_by='', recover=False, output=''):
    from sqlalchemy import text

    from distr.core.chat import _chat_params
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorMaintenanceState
    from distr.core.workflow.development_harness import _remember_turn_route
    from distr.core.workflow.development_threads import development_thread_metadata

    identity = _candidate_identity(candidate)
    with get_session() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        row = session.query(OrchestratorMaintenanceState).filter_by(
            key='proactive-dispatch:' + identity).first()
        if row is None:
            return None
        try:
            claim = json.loads(row.value_json or '{}')
        except ValueError:
            return None
        if not isinstance(claim, dict):
            return None
        if recover:
            if claim.get("candidate_id") != candidate["candidate_id"] or claim.get("status") not in {"dispatching", "unknown"}:
                return None
            execution = _exact_proactive_execution(session, candidate, claim)
            if (execution is None or not execution.completed_at
                    or execution.status not in {"completed", "failed", "blocked", "cancelled"}):
                return None
            try:
                packet = json.loads(execution.output_packet or '{}')
            except ValueError:
                return None
            if not isinstance(packet, dict):
                return None
            success = execution.status == "completed"
            waits_for_human = execution.status == "blocked" or packet.get("waits_for_human") is True
            error = execution.error or ""
            output = str(packet.get("output") or "")
            backend_id = packet.get("backend_id") or execution.route_backend
            engine = packet.get("engine") or ""
            approved_by = claim.get("approved_by") or ""
        else:
            # A retry may already have restored the receipt while the original
            # caller was waiting to write its outcome.
            if claim.get("status") not in {"dispatching", "unknown"}:
                return claim
            execution = session.get(ProjectExecutionSession, execution_session_id) if execution_session_id else None
        if execution and not _execution_matches_claim(execution, candidate, claim):
            execution = None
        model = execution.selected_model if execution else None
        provider = json.loads(execution.input_packet or "{}").get("model_provider") if execution else None
        status = 'waiting' if waits_for_human else 'unknown' if unknown else 'dispatched' if success else 'failed'
        receipt = {'status': status, 'candidate_id': candidate.get('candidate_id'),
                   'error': str(error or '')[:2000], 'backend_id': backend_id, 'approved_by': approved_by,
                   'engine': engine, 'model': model, 'execution_session_id': execution.id if execution else None,
                   **_dispatch_thread_link(claim)}
        if claim.get("chat_id") and not unknown:
            root = session.get(Chat, claim["chat_id"])
            turn = session.get(Chat, claim["turn_id"])
            ticket = session.get(KanbanTicket, candidate["ticket_id"])
            if (root and root.project_id == candidate["project_id"] and root.parent_id is None
                    and ticket and ticket.source_chat_id == root.id and turn and turn.parent_id == root.id
                    and development_thread_metadata(root).get("ticket_id") == ticket.id):
                params = _chat_params(root.params)
                development = params["development"]
                if development.get("proactive_dispatch") == {
                    "candidate_id": candidate["candidate_id"], "turn_id": turn.id,
                }:
                    development.pop("proactive_dispatch")
                    stop_requested = development.pop("proactive_stop_turn_id", None) == turn.id
                    receipt["advance_queue"] = bool(
                        status == "dispatched" and not stop_requested and execution is not None
                        and execution.status == "completed" and execution.completed_at is not None
                    )
                    if execution is not None and isinstance(development.get("turn_routes", {}), dict):
                        _remember_turn_route(session, root.id, development, {
                            "complexity": execution.complexity,
                            "selected": {"backend": backend_id, "model": model, "provider": provider},
                        }, turn_id=turn.id)
                    turn.response = str(output or error or (
                        "Waiting for human review." if waits_for_human else
                        "Approved work completed." if success else "Approved work could not start."
                    ))
                    if params.get("active_turn_chat_row_id") == turn.id:
                        params.pop("active_turn_chat_row_id")
                    root.params = json.dumps(params, ensure_ascii=False, default=str)
                    turn_status = "completed" if success or waits_for_human else "failed"
                    _add_proactive_turn_event(session, root.id, turn.id, f"turn_{turn_status}", turn_status, turn.response)
        row.value_json = json.dumps(receipt)
        ticket_claim = session.query(OrchestratorMaintenanceState).filter_by(
            key='proactive-ticket:' + str(candidate['ticket_id'])).first()
        if ticket_claim and json.loads(ticket_claim.value_json or '{}').get('candidate_identity') == identity:
            ticket_claim.value_json = json.dumps({'status':status if status in UNRESOLVED_DISPATCH_STATUSES else 'finished',
                'ticket_id':candidate['ticket_id'], 'candidate_identity':identity, 'error':receipt['error']})
        event = session.get(OrchestratorEvent, candidate['candidate_id']) if candidate.get('candidate_id') else None
        if event:
            event.status = status
            event.execution_session_id = receipt['execution_session_id']
            event.payload = json.dumps({**json.loads(event.payload or '{}'), 'dispatch_result': receipt})
        session.commit()
        return receipt


def _candidate_from_event(candidate_id: int) -> dict[str, Any] | None:
    from distr.core.db.orchestrator import OrchestratorEvent

    with get_session() as session:
        row = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == int(candidate_id)).first()
        if not row or row.event_type != "proactive_work_candidate":
            return None
        try:
            payload = json.loads(row.payload or "{}")
        except Exception:
            payload = {}
        candidate = payload.get("candidate") if isinstance(payload, dict) else None
        if isinstance(candidate, dict):
            candidate['candidate_id'] = int(candidate_id)
            return candidate
    return None


def _run_backend_task(project: Any, instruction: str, **kwargs: Any) -> Any:
    async def _call():
        return await run_project_task(project, instruction, **kwargs)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call())
    else:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_call())).result()


def _emit_candidate(candidate: dict[str, Any]) -> int | None:
    from distr.core.db.orchestrator import OrchestratorEvent
    with get_session() as session:
        rows = session.query(OrchestratorEvent).filter(
            OrchestratorEvent.event_type == 'proactive_work_candidate',
            OrchestratorEvent.ticket_id == candidate.get('ticket_id')).order_by(OrchestratorEvent.id.desc()).all()
        for row in rows:
            try:
                previous = json.loads(row.payload or '{}').get('candidate') or {}
            except (ValueError, AttributeError):
                continue
            same_draft = previous.get("workflow_draft") == candidate.get("workflow_draft")
            reviewing_outcome = candidate.get("recommended_action") == "review_dispatch_outcome"
            if _candidate_identity(previous) == _candidate_identity(candidate):
                if same_draft or reviewing_outcome:
                    return int(row.id)
                break
    return _emit(
        source="proactive_orchestrator",
        event_type="proactive_work_candidate",
        status="approval_required",
        ticket_id=candidate.get("ticket_id"),
        board_id=candidate.get("board_id"),
        project_id=candidate.get("project_id"),
        summary=f"{_source_label(candidate.get('source'))} work candidate: {candidate.get('title') or 'Untitled'}",
        payload={"candidate": candidate},
        evidence={
            "priority_score": candidate.get("priority_score"),
            "route": candidate.get("route") or {},
            "developer_context": candidate.get("developer_context") or {},
        },
    )


def _emit_check_run(candidates: list[dict[str, Any]], emitted_ids: list[int]) -> None:
    _emit(
        source="proactive_orchestrator",
        event_type="proactive_check_run",
        status="completed",
        summary=_spoken_summary(candidates),
        payload={
            "candidate_ids": emitted_ids,
            "summary": _summarize_candidates(candidates),
        },
    )


def _emit(**kwargs: Any) -> int | None:
    try:
        from distr.core.orchestrator import emit_event

        return emit_event(**kwargs)
    except Exception:
        return None


def _external_context() -> dict[str, Any]:
    try:
        from distr.core.external_agent_context import build_external_agent_context

        return build_external_agent_context(limit=12)
    except Exception:
        return {}


def _source_label(value: Any) -> str:
    source = str(value or "board").strip().lower()
    labels = {
        "gmail": "Gmail",
        "slack": "Slack",
        "whatsapp": "WhatsApp",
        "telegram": "Telegram",
        "trello": "Trello",
        "jira": "Jira",
        "board": "the board",
        "manual": "the board",
        "web": "the web intake",
    }
    return labels.get(source, source[:1].upper() + source[1:] if source else "the board")


def _backend_label(value: Any) -> str:
    backend = str(value or "").strip().lower()
    labels = {
        "codex": "Codex",
        "cursor": "Cursor",
        "pi": "Pi",
        "claude_code": "Claude Code",
    }
    return labels.get(backend, backend.replace("_", " ").title() if backend else "")


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item or "").strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out
