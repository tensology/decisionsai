"""Proactive work radar and project orchestration helpers.

This layer treats boards and external-intake tickets as work signals, scores
them, matches them to projects, and prepares approval-first dispatches through
the existing project backend harness.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from distr.core.db import get_session
from distr.core.project_cli_backends.registry import run_project_task


ACTIVE_SOURCES = {"gmail", "slack", "whatsapp", "telegram", "trello", "jira", "board", "manual", "web"}
DONE_LANE_HINTS = {"done", "closed", "complete", "completed", "cancelled", "canceled", "archive", "archived"}
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
    external_context = _external_context()
    candidates = [_build_candidate(signal, external_context) for signal in signals]
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
        "generated_at": datetime.utcnow().isoformat(),
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

    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.projects import Project

    with get_session() as session:
        project = session.query(Project).filter(Project.id == int(project_id)).first()
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first() if ticket_id else None
        if not project:
            return {"success": False, "error": "The linked project no longer exists."}
        instruction = _dispatch_instruction(candidate, ticket)
        complexity = str(candidate.get("complexity") or getattr(ticket, "complexity", None) or "medium")
        resolved_backend = (backend_id or candidate.get("route", {}).get("backend") or getattr(project, "coding_backend", "") or "pi").strip()
        resolved_model = (model or candidate.get("route", {}).get("model") or "").strip()

    result = _run_backend_task(
        project,
        instruction,
        ticket_id=int(ticket_id) if ticket_id else None,
        ticket_complexity=complexity,
        backend_id_override=resolved_backend,
        model_override=resolved_model if resolved_model and resolved_model.lower() != "auto" else None,
        origin="proactive_orchestrator",
        codex_reasoning_effort_override=(candidate.get("route", {}).get("codex_reasoning_effort") or None),
        codex_service_tier_override=(candidate.get("route", {}).get("codex_service_tier") or None),
    )
    payload = {
        "candidate_id": int(candidate_id),
        "candidate": candidate,
        "approved_by": approved_by or "user",
        "backend_id": result.backend_id,
        "engine": result.engine,
        "execution_session_id": result.execution_session_id,
    }
    _emit(
        source="proactive_orchestrator",
        event_type="proactive_work_dispatched",
        status="dispatched" if result.success else "failed",
        ticket_id=int(ticket_id) if ticket_id else None,
        board_id=candidate.get("board_id"),
        project_id=int(project_id),
        execution_session_id=result.execution_session_id,
        summary=f"Approved proactive work dispatched to {_backend_label(result.backend_id)}.",
        payload=payload,
        evidence={"output": result.output, "error": result.error},
    )
    return {
        "success": bool(result.success),
        "candidate_id": int(candidate_id),
        "backend_id": result.backend_id,
        "engine": result.engine,
        "execution_session_id": result.execution_session_id,
        "output": result.output,
        "error": result.error,
        "spoken_summary": (
            f"I sent that {candidate.get('project_name') or 'project'} work item to {_backend_label(result.backend_id)}."
            if result.success
            else f"I could not dispatch that work item: {result.error or 'the backend did not accept it'}."
        ),
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
                | (KanbanBoard.default_project_id == int(project_id))
            )
        rows = query.order_by(KanbanTicket.modified_date.desc(), KanbanTicket.created_date.desc()).limit(200).all()
        signals: list[dict[str, Any]] = []
        for ticket, lane, board, linked_project in rows:
            lane_name = str(getattr(lane, "name", "") or "")
            if lane_name.strip().lower() in DONE_LANE_HINTS:
                continue
            if str(getattr(ticket, "workflow_status", "") or "").lower() in {"completed", "cancelled", "canceled"}:
                continue
            source = _signal_source(ticket)
            if wanted_source and source != wanted_source:
                continue
            default_project = None
            if not linked_project and getattr(board, "default_project_id", None):
                default_project = session.query(Project).filter(Project.id == int(board.default_project_id)).first()
            project = linked_project or default_project
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
                "project_id": project.id if project else None,
                "project_name": project.name if project else "",
                "project_folder": project.folder_location if project else "",
                "project_backend": project.coding_backend if project else "",
                "project_model": project.coding_backend_model if project else "",
                "created_at": ticket.created_date.isoformat() if ticket.created_date else "",
                "modified_at": ticket.modified_date.isoformat() if ticket.modified_date else "",
            }
            signal["priority_score"] = _priority_score(signal)
            signals.append(signal)
        signals.sort(key=lambda item: int(item.get("priority_score") or 0), reverse=True)
        return signals[:limit]


def _build_candidate(signal: dict[str, Any], external_context: dict[str, Any]) -> dict[str, Any]:
    route = _resolve_route(signal)
    developer_context = _developer_context_for_signal(signal, external_context)
    candidate = dict(signal)
    candidate["priority_score"] = int(signal.get("priority_score") or 0)
    candidate["priority"] = _priority_label(candidate["priority_score"], signal.get("priority"))
    candidate["route"] = route
    candidate["developer_context"] = developer_context
    candidate["recommended_action"] = (
        "ask_approval_to_dispatch" if signal.get("project_id") else "ask_project_mapping"
    )
    candidate["approval_question"] = _approval_question(candidate)
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


def _developer_context_for_signal(signal: dict[str, Any], external_context: dict[str, Any]) -> dict[str, Any]:
    folder = str(signal.get("project_folder") or "")
    name = str(signal.get("project_name") or "")
    surfaces: list[str] = []
    codex_titles: list[str] = []
    for item in external_context.get("codex_threads") or []:
        haystack = " ".join(str(item.get(k) or "") for k in ("cwd", "title", "preview"))
        if _matches_project(haystack, folder, name):
            surfaces.append("Codex")
            if item.get("title"):
                codex_titles.append(str(item["title"])[:160])
    for item in external_context.get("cursor_workspaces") or []:
        haystack = str(item.get("folder") or "")
        if _matches_project(haystack, folder, name):
            surfaces.append("Cursor")
    return {
        "recent_surfaces": _unique(surfaces),
        "recent_codex_titles": _unique(codex_titles)[:3],
        "has_recent_developer_context": bool(surfaces),
    }


def _matches_project(value: str, folder: str, name: str) -> bool:
    haystack = str(value or "").lower()
    if folder and folder.lower() in haystack:
        return True
    leaf = folder.rstrip("/").split("/")[-1].lower() if folder else ""
    if leaf and leaf in haystack:
        return True
    normalized_name = re.sub(r"[^a-z0-9]+", "", name.lower())
    normalized_haystack = re.sub(r"[^a-z0-9]+", "", haystack)
    return bool(normalized_name and normalized_name in normalized_haystack)


def _approval_question(candidate: dict[str, Any]) -> str:
    source = _source_label(candidate.get("source"))
    project = candidate.get("project_name") or "a project"
    backend = _backend_label((candidate.get("route") or {}).get("backend") or candidate.get("project_backend") or "project agent")
    title = candidate.get("title") or "this work item"
    if not candidate.get("project_id"):
        return f"{source} has work that looks important: {title}. Which project should I attach it to?"
    return f"{source} has important work for {project}: {title}. Should I send it to {backend}?"


def _spoken_summary(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "I checked the work sources and did not find anything that needs approval right now."
    top = candidates[0]
    count = len(candidates)
    project = top.get("project_name") or "an unmatched project"
    source = _source_label(top.get("source"))
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
            candidate.setdefault("candidate_id", int(candidate_id))
            return candidate
    return None


def _run_backend_task(project: Any, instruction: str, **kwargs: Any) -> Any:
    async def _call():
        return await run_project_task(project, instruction, **kwargs)

    try:
        return asyncio.run(_call())
    except RuntimeError:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_call())).result()


def _emit_candidate(candidate: dict[str, Any]) -> int | None:
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
