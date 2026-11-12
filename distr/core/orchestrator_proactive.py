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
UNRESOLVED_DISPATCH_STATUSES = {"dispatching", "unknown", "waiting", "held"}
UNCONFIRMED_DISPATCH_MESSAGE = "The previous dispatch outcome is unconfirmed. Review it before approving this ticket again."
PROJECT_HIDDEN_FROM_AGENT = "This project is hidden from the agent. Change its project setting before approving new work."
PROJECT_FOLDER_CHANGED = "The project folder changed after this candidate was prepared. Scan again before approving."
TICKET_SCOPE_CHANGED = "The ticket changed project or board. Scan again before approving it."
TICKET_CHANGED = "The ticket changed after this candidate was prepared. Scan again."
PROACTIVE_RESULT_HELD = ("The approved scope changed while the worker was running. "
                         "The result is held for review in the original execution.")
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
    if state and state.get('status') == 'unknown' and state.get('candidate_id') is None:
        return {"success": False, "candidate_id": int(candidate_id),
                "error": UNCONFIRMED_DISPATCH_MESSAGE, "spoken_summary": UNCONFIRMED_DISPATCH_MESSAGE}
    if state and _is_prelaunch_scope_stop(state):
        return {"success": False, **state, "outcome_recorded": True, "spoken_summary": state["error"]}
    if state and state.get("status") == "held":
        return {"success": False, "candidate_id": int(candidate_id), **_dispatch_thread_link(state),
                "execution_session_id": state.get("execution_session_id"), "outcome_recorded": True,
                "error": PROACTIVE_RESULT_HELD, "spoken_summary": PROACTIVE_RESULT_HELD}
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
                    receipt["error"] if receipt["status"] == "held" else
                    f"I recovered the previous dispatch outcome ({receipt['status']}). Review the existing execution for details."
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
        if candidate["workflow_draft"].get("workflow_id"):
            message = "Open Workflow review on the ticket to approve a launch."
            return {"success": False, "candidate_id": int(candidate_id), "error": message, "spoken_summary": message}
        message = "This candidate has a workflow draft for review. Direct dispatch cannot launch it."
        return {"success": False, "candidate_id": int(candidate_id), "error": message, "spoken_summary": message}

    from distr.core.workflow.development_threads import DevelopmentThreadConflict

    with get_session() as session:
        project, ticket, lane, board = _direct_scope_records(session, candidate)
        scope_error = _direct_ticket_error(session, candidate, project, ticket, lane, board)
        if scope_error:
            return {"success": False, "candidate_id": int(candidate_id),
                    "error": scope_error, "spoken_summary": scope_error}
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
            proactive_scope_error=lambda folder, execution_id: _dispatch_scope_error(
                candidate, claim, folder, execution_id,
            ),
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
    status = receipt["status"] if receipt else "unknown"
    held = status == "held"
    output = result.output if recorded and not held and (not waits_for_human or status == "waiting") else ""
    error = receipt["error"] if recorded else ""
    if recorded:
        thread = _dispatch_thread_link(receipt)
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
        status=status,
        ticket_id=int(ticket_id) if ticket_id else None,
        board_id=candidate.get("board_id"),
        project_id=int(project_id),
        execution_session_id=execution_id,
        parent_event_id=int(candidate_id),
        summary=(PROACTIVE_RESULT_HELD if held
                 else "Approved proactive work outcome could not be saved." if not recorded
                 else "Approved proactive work is waiting for human review." if status == "waiting"
                 else f"Approved proactive work dispatched to {_backend_label(result.backend_id)}." if status == "dispatched"
                 else f"Approved proactive work could not be dispatched: {result.error or 'backend rejected the task'}."),
        payload=payload,
        evidence={"output": output, "error": error},
    )
    if not recorded:
        spoken = "The worker returned, but I could not save its outcome. Review the existing execution before retrying."
    elif held:
        spoken = PROACTIVE_RESULT_HELD
    elif status == "waiting":
        spoken = "The worker paused for human review. Check the existing execution before retrying."
    elif status == "dispatched":
        spoken = f"I sent that {candidate.get('project_name') or 'project'} work item to {_backend_label(result.backend_id)}."
    else:
        spoken = f"I could not dispatch that work item: {result.error or 'the backend did not accept it'}."
    return {
        "success": status == "dispatched",
        "candidate_id": int(candidate_id),
        **thread,
        "backend_id": result.backend_id,
        "model": receipt.get("model") if receipt else None,
        "engine": result.engine,
        "execution_session_id": execution_id,
        "outcome_recorded": recorded,
        "output": output,
        "error": error,
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
    from distr.core.workflow.development_threads import ticket_development_thread_ids

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
            if project is not None and getattr(project, "agent_ignored", False):
                continue  # Hidden from the agent in Projects.
            if source not in ACTIVE_SOURCES and not project:
                continue
            assignment = _workflow_assignment(ticket, board) if project else None
            signal = {
                "ticket_id": ticket.id,
                "title": ticket.title or "",
                "description": ticket.description or "",
                "priority": ticket.priority or "medium",
                "complexity": ticket.complexity or "medium",
                "source": source,
                "source_contact": ticket.source_contact or "",
                "source_thread_id": ticket.source_thread_id or "",
                "source_chat_id": ticket.source_chat_id,
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
                "assigned_workflow": assignment,
                "can_draft_workflow": bool(project and not assignment and _can_draft_workflow(ticket, board)),
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
        checklists = _workflow_checklists(session, [signal["ticket_id"] for signal in signals if signal["can_draft_workflow"]])
        for signal in signals:
            assignment = signal.pop("assigned_workflow", None)
            can_draft = signal.pop("can_draft_workflow")
            source_chat_id = signal.pop("source_chat_id")
            if not (assignment or can_draft):
                continue
            ticket_id = signal["ticket_id"]
            thread_ids = ticket_development_thread_ids(session, ticket_id, source_chat_id)
            if assignment:
                draft = _assigned_workflow_review(session, signal, assignment, thread_ids=thread_ids)
            else:
                draft = _workflow_draft(signal, checklists.get(ticket_id), thread_ids=thread_ids)
            if draft:
                signal["workflow_draft"] = draft
        return signals[:limit]


def _can_draft_workflow(ticket, board) -> bool:
    return (
        not (ticket.send_to_cli or board.send_to_cli)
        and str(ticket.complexity or '').strip().casefold() in {"high", "complex", "very_complex"}
    )


def _workflow_assignment(ticket, board):
    if ticket.linked_workflow_id:
        return ticket.linked_workflow_id, "ticket", "Assigned to this ticket."
    if not ticket.send_to_cli and not board.send_to_cli and board.default_workflow_id:
        return board.default_workflow_id, "board", "Assigned as the board default."
    if not ticket.send_to_cli and not board.send_to_cli:
        # Built-in fallback (2026-11-11): explicit new/existing-project work or high complexity only.
        from distr.core.workflow.builtin import builtin_recommendation

        recommendation = builtin_recommendation(ticket, board)
        if recommendation:
            workflow_id, reason = recommendation
            return workflow_id, "builtin", reason
    return None


def _assigned_workflow_review(session, signal, assignment, *, thread_ids):
    from distr.core.chat_turns import redact_text
    from distr.core.db.workflow import AutoWorkflow
    from distr.core.workflow.draft_launch import project_folder_revision, workflow_definition_revision, workflow_step_action
    from distr.core.workflow.step_validator import workflow_start_error

    workflow_id, assigned_by, reason = assignment
    workflow = session.get(AutoWorkflow, workflow_id)
    base = {"workflow_id": workflow_id, "assigned_by": assigned_by, "development_thread_ids": thread_ids,
            "project_folder_revision": project_folder_revision(signal.get("project_folder"))}
    if workflow is None:
        return {**base, "error": "The assigned workflow no longer exists. Update the ticket or board assignment."}
    error = workflow_start_error(workflow)
    steps = sorted(workflow.steps, key=lambda step: (step.position or 0, step.id))
    if error or not steps:
        return {**base, "name": workflow.name, "error": error or "The assigned workflow has no steps."}
    if any(step.linked_project_id not in (None, signal["project_id"]) for step in steps):
        return {**base, "name": workflow.name,
                "error": "The assigned workflow includes work in another project. Review it from Workflows."}
    revision = hashlib.sha256(json.dumps(
        [base, _candidate_identity(signal), workflow_definition_revision(session, workflow)], sort_keys=True,
    ).encode()).hexdigest()
    step_review = []
    for step in steps:
        action = workflow_step_action(session, step)
        detail = (action.get("instruction_text") or action.get("title")) if action else step.instruction
        step_review.append({"instruction": step.name, "detail": redact_text(detail or "", limit=1000)})
    return {
        **base, "status": "awaiting_review", "name": workflow.name,
        "reason": reason,
        "steps": step_review,
        "start_step_position": int(workflow.start_step_position or 0),
        "omitted_steps": 0, "checklist_revision": revision,
    }


def _same_workflow_draft(saved, current):
    if saved == current:
        return True
    # Older built-in recommendations used the board-default label. Keep their
    # approval identity when the executable revision and all other fields match.
    return (isinstance(saved, dict) and isinstance(current, dict)
            and saved.get("assigned_by") == "builtin"
            and saved.get("reason") == "Assigned as the board default."
            and {**saved, "reason": current.get("reason")} == current)


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


def _description_lines(description: str):
    fence = ""
    for line in description.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ""
            continue
        if marker:
            fence = marker[1]
            yield ""
            continue
        yield line


def _description_workflow_steps(description: str) -> list[str]:
    steps: dict[str, str] = {}
    in_steps = False
    for line in _description_lines(description):
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


def _ordered_prose_workflow_steps(description: str) -> list[str]:
    # Only use a sequence explicitly stated together in the ticket. Do not
    # guess the missing stages or lift instructions out of a quoted example.
    paragraphs = re.split(r"\n[ \t]*\n", "\n".join(_description_lines(description)))
    sequences = []
    for paragraph in paragraphs:
        start = re.match(r"^ {0,3}first[,:][ \t]+", paragraph, re.IGNORECASE)
        if not start:
            continue
        parts = re.split(
            r"(?:\.[ \t]+|\n[ \t]*)(then|next|after that|finally)[,:][ \t]+",
            paragraph[start.end():],
            flags=re.IGNORECASE,
        )
        markers = [marker.casefold() for marker in parts[1::2]]
        if len(markers) < 2 or markers[-1] != "finally" or "finally" in markers[:-1]:
            continue
        labels = [" ".join(stage.strip().removesuffix(".").splitlines()).strip() for stage in parts[::2]]
        if all(labels) and len({label.casefold() for label in labels}) == len(labels):
            sequences.append(labels)
    return sequences[0] if len(sequences) == 1 else []


_OUTCOME_HEADING = {"requirements", "deliverables", "requested outcomes"}
_OUTCOME_SUBJECT = r"[A-Z][^.!?;:\n]{0,90}?[ \t]+(?i:must|needs? to)[ \t]+"
_OUTCOME_BOUNDARY = re.compile(rf"(?<=[.!?])[ \t]+(?={_OUTCOME_SUBJECT})")
_OUTCOME_STATEMENT = re.compile(rf"^{_OUTCOME_SUBJECT}\S", re.IGNORECASE)
_OUTCOME_EXCLUDED = re.compile(
    r"""^(?:[>"“'‘]|(?:for example|do not|never|avoid)\b)|\b(?:must|needs? to)[ \t]+(?:not|never)\b""",
    re.IGNORECASE,
)


def _requested_workflow_outcomes(description: str) -> list[str]:
    """Keep the ticket's independently stated outcomes in their original order."""
    outcomes: dict[str, str] = {}
    section = ""
    for raw_line in _description_lines(description):
        heading = re.match(r"^ {0,3}#{1,6}[ \t]+(.+?)\s*#*\s*$", raw_line)
        if heading:
            section = heading[1].strip().casefold()
            continue
        if section and section not in _OUTCOME_HEADING:
            continue
        line = raw_line.strip()
        if not line or raw_line.startswith(("    ", "\t")):
            continue
        bullet = re.match(r"^(?:[-*+]|[0-9]{1,9}[.)])[ \t]+(.+)$", line)
        if bullet:
            if not section or bullet[1].startswith(("[x]", "[X]")):
                continue
            labels = [bullet[1].removeprefix("[ ]").strip()]
        else:
            labels = [label for label in _OUTCOME_BOUNDARY.split(line) if _OUTCOME_STATEMENT.match(label)]
        for label in labels:
            if _OUTCOME_EXCLUDED.search(label):
                continue
            label = label.removesuffix(".").strip()
            if label:
                outcomes.setdefault(label.casefold(), label)
    return list(outcomes.values())


def _workflow_draft(signal: dict[str, Any], checklist: list[str] | None, *, complete: bool = False,
                    thread_ids=()) -> dict[str, Any] | None:
    from distr.core.workflow.draft_launch import project_folder_revision

    from_description = checklist is None
    description = signal.get("description") or ""
    labels = _description_workflow_steps(description) if from_description else checklist
    from_prose = False
    from_outcomes = False
    if from_description and not labels:
        labels = _ordered_prose_workflow_steps(description)
        from_prose = bool(labels)
    if from_description and not labels:
        labels = _requested_workflow_outcomes(description)
        from_outcomes = bool(labels)
    if len(labels) < 3:
        return None
    if from_outcomes:
        revision_data = {"requested_outcomes": labels, "brief": description}
        reason = f"The ticket describes {len(labels)} distinct outcomes. Review their proposed order before launching."
    elif from_prose:
        revision_data = {"ordered_prose_steps": labels}
        reason = f"The ticket description sets out {len(labels)} stages in order."
    elif from_description:
        revision_data = {"description_steps": labels}
        reason = f"The ticket description lists {len(labels)} distinct implementation steps."
    else:
        revision_data = labels
        reason = f"The ticket has {len(labels)} distinct pending checklist items."
    # Keep the scan compact. Ticket review expands the same revision before approval.
    step_limit = len(labels) if complete else 12
    folder_revision = project_folder_revision(signal.get("project_folder"))
    return {
        "status": "awaiting_review",
        "name": signal["title"],
        "reason": reason,
        "project_id": signal["project_id"],
        "board_id": signal["board_id"],
        "ticket_id": signal["ticket_id"],
        "development_thread_ids": list(thread_ids),
        "project_folder_revision": folder_revision,
        "steps": [
            {"instruction": label, "acceptance": "Record the check performed and its result for this item."}
            for label in labels[:step_limit]
        ],
        "omitted_steps": max(0, len(labels) - step_limit),
        "checklist_revision": hashlib.sha256(
            json.dumps([revision_data, thread_ids, folder_revision], ensure_ascii=False).encode(),
        ).hexdigest(),
    }


def review_workflow_draft(*, board_id: int, ticket_id: int, _session=None) -> dict[str, Any]:
    """Read the latest candidate draft when it still matches the ticket."""
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.projects import Project
    from distr.core.workflow.development_threads import ticket_development_thread_ids

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
        event = events.first()
        if ticket.linked_workflow_id is not None:
            launch = events.filter(
                OrchestratorEvent.status.in_(("workflow_launched", "workflow_review_invalidated")),
                OrchestratorEvent.workflow_id == ticket.linked_workflow_id,
            ).first()
            if launch and (launch.status == "workflow_launched" or event.id == launch.id):
                from distr.core.workflow.draft_launch import approved_draft_receipt

                return approved_draft_receipt(session, launch)
        if project.agent_ignored:
            return {"status": "blocked", "error": PROJECT_HIDDEN_FROM_AGENT}
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
        current_signal = {**scope, "title": ticket.title or "", "description": ticket.description or "",
                          "lane_id": lane.id, "modified_at": ticket.modified_date.isoformat() if ticket.modified_date else "",
                          "complexity": ticket.complexity or "medium", "source": _signal_source(ticket),
                          "source_thread_id": ticket.source_thread_id or "", "project_folder": project.folder_location}
        assignment = _workflow_assignment(ticket, board)
        thread_ids = ticket_development_thread_ids(session, ticket_id, ticket.source_chat_id)
        current = (_assigned_workflow_review(session, current_signal, assignment, thread_ids=thread_ids) if assignment
                   else _workflow_draft(current_signal, checklist, thread_ids=thread_ids) if _can_draft_workflow(ticket, board) else None)
        if (event.status != "approval_required"
                or any(candidate.get(key) != value for key, value in scope.items())
                or _inactive_ticket(ticket, lane, board, session)
                or candidate.get("lane_id") != lane.id
                or candidate.get("description") != (ticket.description or "")
                or candidate.get("modified_at") != (ticket.modified_date.isoformat() if ticket.modified_date else "")
                or not _same_workflow_draft(draft, current)):
            return {"status": "stale"}
        if draft.get("error"):
            return {"status": "blocked", "error": draft["error"]}
        if draft["omitted_steps"]:
            draft = _workflow_draft(current_signal, checklist, complete=True, thread_ids=thread_ids)
        else:
            draft = current
        return {"status": "awaiting_review", "candidate_id": event.id, "project_name": project.name, "draft": draft}


def _build_candidate(signal: dict[str, Any], context: dict[str, list[str]]) -> dict[str, Any]:
    from distr.core.workflow.draft_launch import project_folder_revision

    route = _resolve_route(signal)
    candidate = dict(signal)
    if candidate.get("project_id"):
        candidate["project_folder_revision"] = project_folder_revision(signal.get("project_folder"))
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
        candidate["review_url"] = (
            f"/development/boards/decisions/{candidate['board_id']}/kanban/?ticket_id={candidate['ticket_id']}"
        )
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
        if candidate['dispatch_status'] == 'unknown' and previous.get('candidate_id') is None:
            candidate['approval_question'] = UNCONFIRMED_DISPATCH_MESSAGE
        else:
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
        draft = candidate["workflow_draft"]
        if draft.get("error"):
            return draft["error"]
        if draft.get("assigned_by") == "builtin":
            return f"I recommend {draft['name']} for {title}. {draft['reason']} Open Workflow review before approving a launch."
        if draft.get("workflow_id"):
            return f"{title} has an assigned workflow, {draft['name']}. Open Workflow review on the ticket before approving a launch."
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
            if review[0].get("dispatch_status") == "unknown":
                return f"The dispatch outcome for {project} is unconfirmed. Review it before approving the work again."
            return f"The work item for {project} has already been submitted. Review its dispatch outcome."
        return f"I found {len(review)} work items that need their dispatch outcomes reviewed."
    top = next(candidate for candidate in candidates if candidate.get("recommended_action") != "review_dispatch_outcome")
    count = len(candidates)
    project = top.get("project_name") or "an unmatched project"
    source = _source_label(top.get("source"))
    if top.get("workflow_draft"):
        if top["workflow_draft"].get("workflow_id"):
            return _approval_question(top)
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


def _inactive_ticket(ticket, lane, board, session, *, own_execution_id=None) -> bool:
    if board.archived or str(lane.name or '').strip().lower() in DONE_LANE_HINTS:
        return True
    if board.agent_done_lane and str(lane.name or '').strip().casefold() == board.agent_done_lane.strip().casefold():
        return True
    if str(ticket.workflow_status or '').lower() in {'completed', 'cancelled', 'canceled', 'queued', 'running', 'waiting'}:
        return True
    from distr.core.db.kanban import ProjectExecutionSession
    active = session.query(ProjectExecutionSession.id).filter(
        ProjectExecutionSession.ticket_id == ticket.id,
        ProjectExecutionSession.status.in_(['queued', 'running', 'waiting']))
    if own_execution_id is not None:
        active = active.filter(ProjectExecutionSession.id != own_execution_id)
    return active.first() is not None


def _candidate_identity(candidate):
    fields = ('ticket_id', 'project_id', 'board_id', 'lane_id', 'title', 'description',
              'modified_at', 'complexity', 'source', 'source_thread_id')
    return hashlib.sha256(json.dumps({key:candidate.get(key) for key in fields}, sort_keys=True, default=str).encode()).hexdigest()


def _candidate_folder_matches(candidate, folder):
    from distr.core.workflow.draft_launch import project_folder_matches, project_folder_revision

    if "project_folder_revision" in candidate:
        return project_folder_matches(folder, candidate["project_folder_revision"])
    if "project_folder" in candidate:
        previous = project_folder_revision(candidate["project_folder"], include_directory=False)
        return project_folder_matches(folder, previous)
    return True


def _direct_scope_records(session, candidate):
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project

    project = session.get(Project, candidate["project_id"])
    ticket = session.get(KanbanTicket, candidate["ticket_id"]) if candidate.get("ticket_id") else None
    lane = session.get(KanbanLane, ticket.lane_id) if ticket else None
    board = session.get(KanbanBoard, lane.board_id) if lane else None
    return project, ticket, lane, board


def _direct_ticket_error(session, candidate, project, ticket, lane, board, *, own_execution_id=None):
    if project is None or project.deleted_at is not None:
        return "The linked project no longer exists."
    if project.agent_ignored:
        return PROJECT_HIDDEN_FROM_AGENT
    if not ticket or not lane or not board:
        return "The ticket or its board no longer exists. Scan again."
    current_project = ticket.linked_project_id if ticket.linked_project_id is not None else board.default_project_id
    if current_project != project.id or board.id != candidate.get("board_id"):
        return TICKET_SCOPE_CHANGED
    if not _candidate_folder_matches(candidate, project.folder_location):
        return PROJECT_FOLDER_CHANGED
    if _inactive_ticket(ticket, lane, board, session, own_execution_id=own_execution_id):
        return "This ticket is completed, archived, or already running. Scan again."
    current_request = {
        "title": ticket.title or "", "description": ticket.description or "",
        "complexity": ticket.complexity or "medium", "source": _signal_source(ticket),
        "source_contact": ticket.source_contact or "", "source_thread_id": ticket.source_thread_id or "",
        "lane_id": lane.id, "modified_at": ticket.modified_date.isoformat() if ticket.modified_date else "",
    }
    if any(candidate.get(key) != value for key, value in current_request.items() if candidate.get(key) is not None):
        return TICKET_CHANGED
    return ""


def _direct_workflow_error(session, candidate, ticket, board):
    if _workflow_assignment(ticket, board):
        return "Review this ticket's current workflow before approving a launch."
    if _can_draft_workflow(ticket, board):
        checklist = _workflow_checklists(session, [ticket.id]).get(ticket.id)
        if _workflow_draft(candidate, checklist):
            return "This ticket now has a workflow draft for review. Scan again before approving."
    return ""


def _dispatch_scope_error(candidate, claim, task_folder, execution_id):
    with get_session() as session:
        return _approved_scope_error(session, candidate, claim, task_folder, execution_id)


def _approved_scope_error(session, candidate, claim, task_folder, execution_id):
    from distr.core.chat import _chat_params
    from distr.core.db import Chat
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.workflow.development_threads import development_thread_metadata, ticket_development_thread_ids

    project, ticket, lane, board = _direct_scope_records(session, candidate)
    error = _direct_ticket_error(session, candidate, project, ticket, lane, board, own_execution_id=execution_id)
    if error:
        return error
    chat_id, turn_id = claim["chat_id"], claim["turn_id"]
    root, turn = session.get(Chat, chat_id), session.get(Chat, turn_id)
    item = session.query(DevelopmentWorkItem).filter_by(chat_id=chat_id).first()
    if (ticket.source_chat_id != chat_id or ticket_development_thread_ids(session, ticket.id, chat_id) != [chat_id]
            or root is None or root.parent_id is not None or root.is_archived or root.project_id != project.id
            or turn is None or turn.parent_id != chat_id or turn.response is not None
            or _chat_params(root.params).get("active_turn_chat_row_id") != turn_id or item is None
            or (item.local_ticket_id, item.project_id) != (ticket.id, project.id)
            or development_thread_metadata(root).get("proactive_dispatch") != {
                "candidate_id": candidate["candidate_id"], "turn_id": turn_id,
            }):
        return "The ticket's Development thread changed before work started. Scan again."
    if turn.input != _dispatch_instruction(candidate, ticket):
        return "The approved Development prompt changed before work started. Scan again."
    if item.workflow_id or str(root.autonomy_level or "").strip().lower() in {"plan", "approval"}:
        return "Review the Development thread's current workflow or approval settings before starting work."
    error = _direct_workflow_error(session, candidate, ticket, board)
    if error:
        return error
    if task_folder is not None and not _candidate_folder_matches(candidate, task_folder):
        return PROJECT_FOLDER_CHANGED
    return ""


def _dispatch_thread_link(claim):
    chat_id = claim.get("chat_id")
    if chat_id is None:
        return {}
    return {"chat_id": chat_id, "turn_id": claim["turn_id"], "chat_url": f"/development/threads/{chat_id}/"}


def proactive_waiting_reviews(session, chat_id, *, turn_id=None):
    """Read waiting receipts, including approvals saved before turn status tracked the pause."""
    from sqlalchemy import case, func
    from distr.core.db.orchestrator import OrchestratorEvent

    payload = case((func.json_valid(OrchestratorEvent.payload), OrchestratorEvent.payload), else_="{}")
    receipt_turn = func.json_extract(payload, "$.dispatch_result.turn_id")
    rows = session.query(OrchestratorEvent, receipt_turn).filter(
        OrchestratorEvent.event_type == "proactive_work_candidate",
        OrchestratorEvent.status == "waiting",
        func.json_extract(payload, "$.dispatch_result.status") == "waiting",
        func.json_extract(payload, "$.dispatch_result.chat_id") == chat_id,
    )
    if turn_id is not None:
        rows = rows.filter(receipt_turn == turn_id)
    return {saved_turn: event for event, saved_turn in rows if type(saved_turn) is int and saved_turn > 0}


def proactive_waiting_review_state(session, root, scope, turn_id):
    from distr.core.db import Chat

    if scope is None:
        return None
    turn = session.get(Chat, turn_id)
    if turn is None or turn.parent_id != root.id or not turn.response:
        return None
    event = proactive_waiting_reviews(session, root.id, turn_id=turn_id).get(turn_id)
    if event is None or (event.project_id, event.ticket_id) != scope:
        return None
    project, ticket, _, board = _direct_scope_records(
        session, {"project_id": event.project_id, "ticket_id": event.ticket_id},
    )
    if (project is None or project.deleted_at is not None or ticket is None
            or ticket.source_chat_id != root.id or board is None or board.id != event.board_id):
        return None
    current_project = ticket.linked_project_id if ticket.linked_project_id is not None else board.default_project_id
    if current_project != project.id:
        return None
    if session.query(Chat.id).filter(Chat.parent_id == root.id, Chat.id > turn_id, Chat.input != "").first():
        return None
    return {
        "id": event.execution_session_id or f"proactive-{event.id}",
        "direct": True, "origin": "proactive_orchestrator", "turn_id": turn_id,
        "status": "blocked", "waiting_kind": "human_review", "can_stop": False,
    }


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
    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorMaintenanceState
    from distr.core.workflow.development_threads import DevelopmentThreadConflict, bind_ticket_development_thread
    with get_session() as session:
        # Reserve at ticket scope under a short write transaction. Different
        # prepared revisions cannot both pass a pre-dispatch snapshot check.
        session.execute(text('BEGIN IMMEDIATE'))
        if candidate.get("candidate_id") is not None and _scope_stop_receipt(
                session.get(OrchestratorEvent, candidate["candidate_id"])):
            return False
        project, ticket, lane, board = _direct_scope_records(session, candidate)
        error = _direct_ticket_error(session, candidate, project, ticket, lane, board)
        if error in {PROJECT_HIDDEN_FROM_AGENT, PROJECT_FOLDER_CHANGED}:
            raise DevelopmentThreadConflict(error)
        if error:
            return False
        error = _direct_workflow_error(session, candidate, ticket, board)
        if error:
            raise DevelopmentThreadConflict(error)
        ticket_key = 'proactive-ticket:' + str(ticket.id)
        ticket_claim = session.query(OrchestratorMaintenanceState).filter_by(key=ticket_key).first()
        if ticket_claim and _read_dispatch_receipt(ticket_claim, {'finished'}, ticket_id=ticket.id)['status'] != 'finished':
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


def _json_object(raw):
    try:
        parsed = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_dispatch_receipt(row, terminal_statuses, *, ticket_id=None):
    state = _json_object(row.value_json)
    status = state.get('status')
    if not isinstance(status, str) or status not in UNRESOLVED_DISPATCH_STATUSES | terminal_statuses:
        return {'status': 'unknown'}
    if ticket_id is not None and (state.get('ticket_id') != ticket_id or not state.get('candidate_identity')):
        return {'status': 'unknown'}
    chat_id, turn_id = state.get('chat_id'), state.get('turn_id')
    if (chat_id is not None or turn_id is not None) and not (type(chat_id) is int and type(turn_id) is int):
        return {'status': 'unknown'}
    return state


def _is_prelaunch_scope_stop(payload):
    return payload.get("proactive_scope_changed") is True or payload.get("proactive_checkout_changed") is True


def _scope_stop_receipt(event):
    if event is None or event.event_type != "proactive_work_candidate" or event.status != "failed":
        return None
    receipt = _json_object(event.payload).get("dispatch_result")
    if (isinstance(receipt, dict) and _is_prelaunch_scope_stop(receipt)
            and receipt.get("candidate_id") == event.id and receipt.get("status") == "failed"):
        return receipt
    return None


def _dispatch_state(candidate):
    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorMaintenanceState
    with get_session() as session:
        if candidate.get("candidate_id") is not None:
            stopped = _scope_stop_receipt(session.get(OrchestratorEvent, candidate["candidate_id"]))
            if stopped:
                return stopped
        row = session.query(OrchestratorMaintenanceState).filter_by(
            key='proactive-dispatch:' + _candidate_identity(candidate)).first()
        if row:
            return _read_dispatch_receipt(row, {'dispatched', 'failed'})
        ticket_claim = session.query(OrchestratorMaintenanceState).filter_by(
            key='proactive-ticket:' + str(candidate.get('ticket_id'))).first()
        if ticket_claim:
            state = _read_dispatch_receipt(ticket_claim, {'finished'}, ticket_id=candidate.get('ticket_id'))
            if state['status'] != 'finished':
                return state
        return None


def _record_dispatch_result(candidate, **result):
    from distr.core.db.orm_compat import retry_sqlite_locked

    try:
        return retry_sqlite_locked(lambda: _finish_dispatch_claim(candidate, **result))
    except SQLAlchemyError:
        logger.exception("Could not record proactive dispatch outcome for candidate %s", candidate.get('candidate_id'))
        return None


def complete_proactive_ide_result(candidate_id, execution_id, *, status, source, output, error):
    candidate = _candidate_from_event(candidate_id)
    if not candidate:
        raise ValueError("The approved IDE work item is unavailable.")
    receipt = _record_dispatch_result(
        candidate, success=status == "completed", error=error, output=output,
        backend_id=f"{source}_ide", engine=f"{source}_ide", execution_session_id=execution_id, ide_status=status,
    )
    if receipt is None:
        raise ValueError("The IDE result does not match a pending approval.")
    _advance_proactive_queue(candidate, receipt)
    return receipt


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
    from distr.core.workflow.development_threads import development_thread_metadata

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
    registered = active and backend_process_registered(
        event.project_id, backend, board_id=event.board_id,
        execution_id=execution.id, execution_kind="proactive",
    )
    state.update(
        id=execution.id, execution_session_id=execution.id, backend_id=backend,
        model=execution.selected_model, started_at=execution.started_at.isoformat() if execution.started_at else None,
    )
    if active and (live or registered):
        stopping = development_thread_metadata(root).get("proactive_stop_turn_id") == turn_id
        progress = session.query(ProjectExecutionEvent.message).filter(
            ProjectExecutionEvent.session_id == execution.id,
            ProjectExecutionEvent.message.isnot(None),
            ProjectExecutionEvent.message != "",
        ).order_by(ProjectExecutionEvent.id.desc()).first()
        state.update(
            status=execution.status,
            summary=progress[0][:240] if progress else state["summary"],
            can_stop=registered and not stopping,
            stop_pending=stopping,
        )
    elif execution.route_type == "ide_bridge" and execution.status == "waiting":
        state.update(status="waiting", waiting_kind="ide_handoff",
                     summary="Waiting for the IDE worker to report its result.")
    elif live:
        state.update(status="running", summary="Saving the approved work result.")
    return state


def _finish_dispatch_claim(candidate, *, success=False, error='', unknown=False, waits_for_human=False,
                           backend_id='', engine='', execution_session_id=None, approved_by='', recover=False, output='',
                           ide_status=''):
    from sqlalchemy import text

    from distr.core.chat import _chat_params
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket, ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorMaintenanceState
    from distr.core.db.workflow import DevelopmentWorkItem
    from distr.core.workflow.development_harness import _remember_turn_route
    from distr.core.workflow.development_threads import development_thread_metadata

    identity = _candidate_identity(candidate)
    with get_session() as session:
        session.execute(text('BEGIN IMMEDIATE'))
        event = session.get(OrchestratorEvent, candidate['candidate_id']) if candidate.get('candidate_id') else None
        stopped = _scope_stop_receipt(event)
        if stopped:
            return stopped
        row = session.query(OrchestratorMaintenanceState).filter_by(
            key='proactive-dispatch:' + identity).first()
        if row is None:
            return None
        try:
            claim = json.loads(row.value_json or '{}')
        except ValueError:
            return None
        if not isinstance(claim, dict) or claim.get("candidate_id") != candidate["candidate_id"]:
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
            execution = session.get(ProjectExecutionSession, execution_session_id) if execution_session_id else None
            if ide_status and (execution is None or execution.route_type != "ide_bridge"
                               or not _execution_matches_claim(execution, candidate, claim)):
                return None
            active = {"dispatching", "unknown"}
            if ide_status and not execution.completed_at:
                active.update({"waiting", "held"})
            if claim.get("status") not in active:
                return claim
        if execution and not _execution_matches_claim(execution, candidate, claim):
            execution = None
        if ide_status:
            from distr.core.db.time import utc_now_naive
            from distr.core.orchestrator import redact_handoff_payload

            if execution.completed_at or execution.status not in {"queued", "running", "waiting"}:
                return None
            output = str(redact_handoff_payload(output or ""))
            error = str(redact_handoff_payload(error or ""))
            approved_by = claim.get("approved_by") or ""
            execution.status = ide_status
            execution.completed_at = execution.updated_at = utc_now_naive()
            execution.error = error
            execution.output_packet = json.dumps({
                "backend_id": backend_id, "engine": engine, "output": output,
                "proactive_result_pending_review": True,
            }, ensure_ascii=False)
            session.add(ProjectExecutionEvent(
                session_id=execution.id, event_type="ide_callback_result", status=ide_status,
                message="IDE worker reported its result.", payload="{}",
            ))
        model = execution.selected_model if execution else None
        input_packet = _json_object(execution.input_packet) if execution else {}
        provider = input_packet.get("model_provider")
        status = 'waiting' if waits_for_human else 'unknown' if unknown else 'dispatched' if success else 'failed'
        scope_changed = (
            event is not None and status == "failed" and execution is not None
            and execution.status == "failed" and execution.completed_at is not None
            and _is_prelaunch_scope_stop(_json_object(execution.output_packet))
        )
        stale_result = (claim.get("chat_id") and not unknown and not scope_changed and _approved_scope_error(
            session, candidate, claim, input_packet.get("folder"), execution.id if execution else None,
        ))
        if claim.get("status") == "held" or stale_result:
            status, error, output = "held", PROACTIVE_RESULT_HELD, ""
        feedback_packet = None
        if execution is not None and not unknown:
            packet = _json_object(execution.output_packet)
            pending = packet.pop("proactive_result_pending_review", None) is True
            if status == "held":
                packet["proactive_result_held"] = True
            if pending or status == "held":
                execution.output_packet = json.dumps(packet, ensure_ascii=False)
            if pending and status == "dispatched" and execution.status == "completed":
                feedback_packet = packet
        receipt = {'status': status, 'candidate_id': candidate.get('candidate_id'),
                   'error': str(error or '')[:2000], 'backend_id': backend_id, 'approved_by': approved_by,
                   'engine': engine, 'model': model, 'execution_session_id': execution.id if execution else None,
                   **_dispatch_thread_link(claim)}
        if scope_changed:
            receipt["proactive_scope_changed"] = True
        waiting_for_ide = status == "waiting" and execution is not None and execution.route_type == "ide_bridge"
        if claim.get("chat_id") and not unknown and not waiting_for_ide:
            root = session.get(Chat, claim["chat_id"])
            turn = session.get(Chat, claim["turn_id"])
            ticket = session.get(KanbanTicket, candidate["ticket_id"])
            original_scope = (candidate["ticket_id"], candidate["project_id"])
            item = session.query(DevelopmentWorkItem).filter_by(chat_id=claim["chat_id"]).first()
            owns_turn = (root is not None and root.project_id == candidate["project_id"] and root.parent_id is None
                         and turn is not None and turn.parent_id == root.id
                         and development_thread_metadata(root).get("ticket_id") == candidate["ticket_id"]
                         and (item is None or (item.local_ticket_id, item.project_id) == original_scope))
            if not owns_turn and (scope_changed or status == "held"):
                for key in _dispatch_thread_link(claim):
                    receipt.pop(key, None)
            if owns_turn and (scope_changed or status == "held" or (ticket and ticket.source_chat_id == root.id)):
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
                    development["last_proactive_result_turn_id"] = turn.id
                    if params.get("active_turn_chat_row_id") == turn.id:
                        params.pop("active_turn_chat_row_id")
                    root.params = json.dumps(params, ensure_ascii=False, default=str)
                    turn_status = "completed" if status in {"dispatched", "waiting"} else "failed"
                    _add_proactive_turn_event(
                        session, root.id, turn.id, f"turn_{turn_status}",
                        "waiting" if status == "waiting" else turn_status, turn.response,
                    )
        if scope_changed:
            session.delete(row)
        else:
            row.value_json = json.dumps(receipt)
        ticket_claim = session.query(OrchestratorMaintenanceState).filter_by(
            key='proactive-ticket:' + str(candidate['ticket_id'])).first()
        if ticket_claim and _json_object(ticket_claim.value_json).get('candidate_identity') == identity:
            ticket_claim.value_json = json.dumps({'status':status if status in UNRESOLVED_DISPATCH_STATUSES else 'finished',
                'ticket_id':candidate['ticket_id'], 'candidate_identity':identity, 'error':receipt['error']})
        if event:
            event.status = status
            event.execution_session_id = receipt['execution_session_id']
            payload = _json_object(event.payload)
            if not isinstance(payload.get('candidate'), dict):
                payload['candidate'] = candidate
            event.payload = json.dumps({**payload, 'dispatch_result': receipt})
        session.commit()
        if feedback_packet:
            from distr.core.kanban.project_execution import persist_execution_feedback

            project, ticket, lane, board = _direct_scope_records(session, candidate)
            if not _direct_ticket_error(session, candidate, project, ticket, lane, board,
                                        own_execution_id=execution.id):
                persist_execution_feedback(execution, feedback_packet)
        return receipt


def _candidate_from_event(candidate_id: int) -> dict[str, Any] | None:
    from distr.core.db.orchestrator import OrchestratorEvent

    with get_session() as session:
        row = session.query(OrchestratorEvent).filter(OrchestratorEvent.id == int(candidate_id)).first()
        if not row or row.event_type != "proactive_work_candidate":
            return None
        candidate = _json_object(row.payload).get("candidate")
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
            previous = _json_object(row.payload).get('candidate')
            if not isinstance(previous, dict):
                continue
            same_draft = _same_workflow_draft(previous.get("workflow_draft"), candidate.get("workflow_draft"))
            reviewing_outcome = candidate.get("recommended_action") == "review_dispatch_outcome"
            if _candidate_identity(previous) == _candidate_identity(candidate):
                if row.status == "workflow_review_invalidated" or _scope_stop_receipt(row):
                    break
                same_folder = previous.get("project_folder_revision") == candidate.get("project_folder_revision")
                if (same_draft and same_folder) or reviewing_outcome:
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
