"""Telegram commands for project execution backends."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
import threading
from typing import Any, Callable

from distr.core.human_engagement import human_project_label

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramProjectCommand:
    kind: str
    backend_id: str = ""
    instruction: str = ""
    project_hint: str = ""


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

    try:
        from distr.core.agent.ticket_intent import classify_ticket_intent

        if classify_ticket_intent(raw).kind == "ide_conversation":
            return None
    except Exception:
        pass

    project_hint = _extract_project_hint(raw)
    natural = _parse_natural_dispatch(raw, project_hint=project_hint)
    if natural:
        return natural

    command_specs = [
        ("codex", "codex", r"^/(?:codex)\s+(.+)$"),
        ("codex", "codex", r"^(?:codex|ask codex|tell codex|send to codex|run codex)\s+(?:to\s+)?(.+)$"),
        ("cursor", "cursor", r"^/(?:cursor-cli|cursor cli)\s+(.+)$"),
        ("cursor", "cursor", r"^(?:cursor cli|ask cursor cli|tell cursor cli|send to cursor cli)\s+(?:to\s+)?(.+)$"),
        ("cursor", "cursor", r"^/(?:cursor)\s+(.+)$"),
        ("cursor", "cursor", r"^(?:cursor|ask cursor|tell cursor|send to cursor|run cursor)\s+(?:to\s+)?(.+)$"),
    ]
    for _name, backend_id, pattern in command_specs:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if match:
            instruction = (match.group(1) or "").strip()
            if instruction:
                return TelegramProjectCommand(kind="dispatch", backend_id=backend_id, instruction=instruction, project_hint=project_hint)
    return None


def _extract_project_hint(text: str) -> str:
    for pattern in (
        r"\b(?:in|for)\s+(?:the\s+)?(.+?)\s+project\b",
        r"\bproject\s+(.+?)(?:\s+(?:to|that|which|and)\b|[?.!,]|$)",
    ):
        match = re.search(pattern, text or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            return _clean_project_hint(match.group(1))
    return ""


def _clean_project_hint(value: str) -> str:
    text = (value or "").strip(" \t\r\n'\"`.,!?")
    text = re.sub(r"\s+", " ", text)
    return text


def _clean_instruction(value: str) -> str:
    text = (value or "").strip(" \t\r\n'\"`")
    text = re.sub(r"^[?.!,;:\s]+", "", text)
    text = re.sub(r"^(?:basically|please|just)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_natural_dispatch(raw: str, *, project_hint: str = "") -> TelegramProjectCommand | None:
    text = raw or ""
    backend_match = re.search(r"\b(codex|cursor)\b", text, flags=re.IGNORECASE)
    if not backend_match or not project_hint:
        return None
    backend_id = backend_match.group(1).lower()

    project_match = re.search(r"\bproject\b", text, flags=re.IGNORECASE)
    tail = text[project_match.end():] if project_match else text[backend_match.end():]
    instruction = ""
    for pattern in (
        r"\basks?\s+(?:it|codex|cursor|them)\s+to\s+(.+)$",
        r"\btell\s+(?:it|codex|cursor|them)\s+to\s+(.+)$",
        r"\binstruct\s+(?:it|codex|cursor|them)\s+to\s+(.+)$",
        r"\bto\s+(.+)$",
    ):
        match = re.search(pattern, tail, flags=re.IGNORECASE | re.DOTALL)
        if match:
            instruction = _clean_instruction(match.group(1))
            break

    if not instruction:
        # Fall back to the whole request minus the project target. This keeps
        # natural voice phrasing usable while still preserving the user's intent.
        without_target = re.sub(
            r"\b(?:in|for)\s+(?:the\s+)?.+?\s+project\b",
            "",
            text,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        instruction = _clean_instruction(without_target)

    if not instruction:
        return None
    return TelegramProjectCommand(
        kind="dispatch",
        backend_id=backend_id,
        instruction=instruction,
        project_hint=project_hint,
    )


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
    project = _resolve_project(command.project_hint)
    label = _backend_label(command.backend_id)
    if not project:
        manager.send_to_telegram("I need an active project before I can send work to Codex or Cursor.")
        return
    project_label = human_project_label(
        getattr(project, "name", ""),
        workspace_path=getattr(project, "folder_location", ""),
        surface=command.backend_id,
    )
    if not (getattr(project, "folder_location", "") or "").strip():
        manager.send_to_telegram(f"{project_label} has no folder path set.")
        return

    manager.send_to_telegram(f"Sending that to {label} for {project_label}.")

    def _runner() -> None:
        ide_session_id: int | None = None
        ide_chat_id: int | None = None
        board_id_override: int | None = None
        try:
            try:
                raw_board_id = getattr(project, "kanban_board_id", None)
                board_id_override = int(raw_board_id) if raw_board_id else None
            except Exception:
                board_id_override = None

            try:
                from distr.core.ide_bridge import record_ide_event

                bridge = record_ide_event(
                    source=command.backend_id,
                    cwd=project.folder_location or "",
                    project_id=int(project.id),
                    event_type=f"{command.backend_id}_prompt_submitted",
                    status="observed",
                    input_text=command.instruction,
                    message=command.instruction,
                    allow_chat_creation=False,
                )
                session_data = bridge.get("session") if isinstance(bridge, dict) else {}
                ide_session_id = int(session_data.get("id")) if str(session_data.get("id") or "").isdigit() else None
                ide_chat_id = int(bridge.get("chat_id")) if str(bridge.get("chat_id") or "").isdigit() else None
            except Exception as exc:
                logger.warning("[Telegram] Could not create IDE bridge session for %s: %s", label, exc, exc_info=True)

            result = asyncio.run(
                _dispatch_project_task(
                    project.id,
                    command,
                    board_id_override=board_id_override,
                )
            )
            try:
                from distr.core.ide_bridge import record_ide_event

                record_ide_event(
                    source=command.backend_id,
                    cwd=project.folder_location or "",
                    project_id=int(project.id),
                    session_id=ide_session_id,
                    chat_id=ide_chat_id,
                    event_type=(
                        f"{command.backend_id}_completed"
                        if result.success
                        else f"{command.backend_id}_failed"
                    ),
                    status="completed" if result.success else "failed",
                    output_text=result.output if result.success else (result.error or result.output),
                    message=result.output if result.success else (result.error or result.output),
                    evidence={
                        "engine": getattr(result, "engine", ""),
                        "execution_session_id": getattr(result, "execution_session_id", None),
                    },
                    allow_chat_creation=False,
                )
            except Exception as exc:
                logger.warning("[Telegram] Could not record IDE bridge result for %s: %s", label, exc, exc_info=True)
            if result.success:
                manager.send_to_telegram(_success_message(label, project_label, result))
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


async def _dispatch_project_task(
    project_id: int,
    command: TelegramProjectCommand,
    board_id_override: int | None = None,
):
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
            board_id_override=board_id_override,
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


def _resolve_project(project_hint: str = ""):
    if not (project_hint or "").strip():
        return _resolve_active_project()

    hint = _clean_project_hint(project_hint).lower()
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    def _matches(project: Any) -> bool:
        name = (getattr(project, "name", "") or "").lower()
        folder = (getattr(project, "folder_location", "") or "").lower()
        return hint in name or hint in folder

    with get_session() as session:
        projects = session.query(Project).all()
        matches = [project for project in projects if _matches(project)]
        if matches:
            matches.sort(
                key=lambda project: (
                    len(getattr(project, "name", "") or ""),
                    len(getattr(project, "folder_location", "") or ""),
                )
            )
            project = matches[0]
            session.expunge(project)
            return project
    return None


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
    }.get(backend_id, backend_id or "the coding agent")


def _success_message(label: str, project_name: str, result: Any) -> str:
    output = (getattr(result, "output", "") or "").strip()
    if output:
        return f"{label} finished the run for {project_name}.\n\n{output[:1800]}"
    return f"{label} finished the run for {project_name}."
