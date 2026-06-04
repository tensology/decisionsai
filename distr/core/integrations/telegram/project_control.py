"""Telegram commands for project execution backends."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
import threading
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramProjectCommand:
    kind: str
    backend_id: str = ""
    instruction: str = ""


_STATUS_PATTERNS = (
    r"^/workload$",
    r"^/agent-status$",
    r"^/dev-status$",
    r"^workload$",
    r"^what('?s| is) running\??$",
    r"^what('?s| is) active\??$",
    r"^where am i with (the )?workload\??$",
    r"^what (is|are) (codex|cursor) (doing|working on)\??$",
    r"^show (active )?(codex|cursor|project )?(work|runs|sessions)\.?$",
)


def parse_project_control_command(text: str) -> TelegramProjectCommand | None:
    raw = (text or "").strip()
    if not raw:
        return None
    lowered = raw.lower().strip()

    if any(re.search(pattern, lowered) for pattern in _STATUS_PATTERNS):
        return TelegramProjectCommand(kind="status")

    command_specs = [
        ("codex", "codex", r"^/(?:codex)\s+(.+)$"),
        ("codex", "codex", r"^(?:codex|ask codex|tell codex|send to codex|run codex)\s+(?:to\s+)?(.+)$"),
        ("cursor", "cursor", r"^/(?:cursor-cli|cursor cli)\s+(.+)$"),
        ("cursor", "cursor", r"^(?:cursor cli|ask cursor cli|tell cursor cli|send to cursor cli)\s+(?:to\s+)?(.+)$"),
        ("cursor", "cursor_ide", r"^/(?:cursor)\s+(.+)$"),
        ("cursor", "cursor_ide", r"^(?:cursor|ask cursor|tell cursor|send to cursor|run cursor)\s+(?:to\s+)?(.+)$"),
    ]
    for _name, backend_id, pattern in command_specs:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if match:
            instruction = (match.group(1) or "").strip()
            if instruction:
                return TelegramProjectCommand(kind="dispatch", backend_id=backend_id, instruction=instruction)
    return None


def handle_project_control_message(
    manager: Any,
    text: str,
    *,
    start_thread: Callable[..., threading.Thread] | None = None,
) -> bool:
    command = parse_project_control_command(text)
    if not command:
        return False
    if command.kind == "status":
        manager.send_to_telegram(build_project_workload_status())
        return True
    if command.kind == "dispatch":
        _start_dispatch(manager, command, start_thread=start_thread)
        return True
    return False


def _start_dispatch(
    manager: Any,
    command: TelegramProjectCommand,
    *,
    start_thread: Callable[..., threading.Thread] | None = None,
) -> None:
    project = _resolve_active_project()
    label = _backend_label(command.backend_id)
    if not project:
        manager.send_to_telegram("I need an active project before I can send work to Codex or Cursor.")
        return
    if not (getattr(project, "folder_location", "") or "").strip():
        manager.send_to_telegram(f"{getattr(project, 'name', 'The active project')} has no folder path set.")
        return

    manager.send_to_telegram(f"Sending that to {label} for {project.name}.")

    def _runner() -> None:
        try:
            result = asyncio.run(_dispatch_project_task(project.id, command))
            if result.success:
                manager.send_to_telegram(_success_message(label, project.name, result))
            else:
                manager.send_to_telegram(
                    f"{label} could not start or finish that run: {result.error or result.output or 'No detail returned.'}"
                )
        except Exception as exc:
            logger.error("[Telegram] Project backend dispatch failed: %s", exc, exc_info=True)
            manager.send_to_telegram(f"{label} dispatch failed: {exc}")

    thread_factory = start_thread or threading.Thread
    thread = thread_factory(target=_runner, daemon=True)
    thread.start()


async def _dispatch_project_task(project_id: int, command: TelegramProjectCommand):
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.project_cli_backends import run_project_task

    with get_session() as session:
        project = session.query(Project).filter(Project.id == int(project_id)).first()
        if not project:
            raise ValueError(f"Project #{project_id} not found")
        return await run_project_task(
            project,
            command.instruction,
            origin="telegram",
            backend_id_override=command.backend_id,
        )


def _resolve_active_project():
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    with get_session() as session:
        project = (
            session.query(Project)
            .filter(Project.in_use == True)  # noqa: E712
            .order_by(Project.modified_date.desc())
            .first()
        )
        if project:
            session.expunge(project)
            return project
        board = (
            session.query(KanbanBoard)
            .filter(KanbanBoard.in_use == True, KanbanBoard.default_project_id.isnot(None))  # noqa: E712
            .first()
        )
        if board and board.default_project_id:
            project = session.query(Project).filter(Project.id == int(board.default_project_id)).first()
            if project:
                session.expunge(project)
                return project
        project = session.query(Project).filter(Project.folder_location != "").order_by(Project.modified_date.desc()).first()
        if project:
            session.expunge(project)
        return project


def build_project_workload_status(limit: int = 6) -> str:
    try:
        from distr.core.developer_context import build_developer_context

        context = build_developer_context().to_prompt_text(max_chars=1800)
        if context.strip():
            return _clean_status_text(context)
    except Exception as exc:
        logger.warning("[Telegram] Could not build project workload status: %s", exc, exc_info=True)
    return "I could not read the current project workload right now."


def _clean_status_text(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- debug_mode") or stripped.startswith("- cwd") or stripped.startswith("- current_chat_id"):
            continue
        stripped = stripped.replace("Developer workflow context:", "Current project workload:")
        stripped = stripped.replace("live_agent_context:", "live context:")
        lines.append(stripped)
    return "\n".join(lines) or "No active project work is visible right now."


def _backend_label(backend_id: str) -> str:
    return {
        "codex": "Codex",
        "cursor": "Cursor CLI",
        "cursor_ide": "Cursor",
        "vscode_ide": "VS Code",
    }.get(backend_id, backend_id or "the coding agent")


def _success_message(label: str, project_name: str, result: Any) -> str:
    output = (getattr(result, "output", "") or "").strip()
    if getattr(result, "engine", "") == "ide_ticket":
        return f"{label} has a work packet for {project_name}. Open Cursor and use the DecisionsAI extension to pick it up."
    if output:
        return f"{label} finished the run for {project_name}.\n\n{output[:1800]}"
    return f"{label} finished the run for {project_name}."
