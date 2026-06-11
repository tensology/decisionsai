"""Start/stop project startup terminals with consistent user feedback."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProjectTerminalActionResult:
    success: bool
    project_id: int
    project_name: str
    action: str
    started: int = 0
    failed: int = 0
    stopped: int = 0
    message: str = ""
    speak_message: str = ""
    diagnostics: list[dict[str, str]] = field(default_factory=list)


def parse_startup_command_lines(startup_instructions: str) -> list[str]:
    """Split startup instructions into runnable lines; skip blanks and # comments."""
    commands: list[str] = []
    for line in (startup_instructions or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        commands.append(stripped)
    return commands


def announce_project_terminal_feedback(message: str) -> None:
    """Speak a short confirmation through desktop TTS / remote audio paths."""
    from distr.core.signals import speak_text_directly_event_queue

    text = (message or "").strip()
    if text:
        speak_text_directly_event_queue(text)


def project_startup_terminals_running(project_id: int) -> bool:
    from distr.core.terminal import get_startup_sessions_for_project

    return bool(get_startup_sessions_for_project(project_id, purpose="startup"))


def _load_project(project_id: int) -> Optional[dict]:
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    with get_session() as session:
        project = session.query(Project).filter(Project.id == project_id).first()
        if not project:
            return None
        return {
            "id": project.id,
            "name": (project.name or "Project").strip() or "Project",
            "folder_location": (project.folder_location or "").strip(),
            "startup_instructions": project.startup_instructions or "",
            "kanban_board_id": project.kanban_board_id,
            "start_time_tracker": bool(getattr(project, "start_time_tracker", True)),
        }


def _build_start_speak_message(project_name: str, started: int, failed: int) -> str:
    if started <= 0 and failed > 0:
        return f"Project {project_name} startup terminals failed to start."
    if failed > 0:
        suffix = "y" if failed == 1 else "ies"
        return (
            f"Project {project_name} startup terminals started with "
            f"{failed} fail{suffix}."
        )
    if started > 0:
        return f"Project {project_name} startup terminals started."
    return f"Project {project_name} startup terminals are already running or queued."


def _build_stop_speak_message(project_name: str, stopped: int) -> str:
    if stopped > 0:
        return f"Project {project_name} startup terminals stopped."
    return f"Project {project_name} had no running startup terminals."


def stop_project_startup_terminals(
    project_id: int,
    *,
    announce: bool = True,
) -> ProjectTerminalActionResult:
    project = _load_project(project_id)
    if not project:
        result = ProjectTerminalActionResult(
            success=False,
            project_id=project_id,
            project_name="Project",
            action="error",
            message="Project not found.",
            speak_message="Project not found.",
        )
        if announce:
            announce_project_terminal_feedback(result.speak_message)
        return result

    from distr.core.terminal import kill_all_startup_sessions_for_project

    stopped = kill_all_startup_sessions_for_project(project_id, purpose="startup")
    speak = _build_stop_speak_message(project["name"], stopped)
    result = ProjectTerminalActionResult(
        success=True,
        project_id=project_id,
        project_name=project["name"],
        action="stopped",
        stopped=stopped,
        message=speak,
        speak_message=speak,
    )
    if announce:
        announce_project_terminal_feedback(speak)
    logger.info(
        "Project startup terminals stopped: project_id=%s stopped=%s",
        project_id,
        stopped,
    )
    return result


def start_project_startup_terminals(
    project_id: int,
    *,
    commands: Optional[list[str]] = None,
    announce: bool = True,
) -> ProjectTerminalActionResult:
    project = _load_project(project_id)
    if not project:
        result = ProjectTerminalActionResult(
            success=False,
            project_id=project_id,
            project_name="Project",
            action="error",
            message="Project not found.",
            speak_message="Project not found.",
        )
        if announce:
            announce_project_terminal_feedback(result.speak_message)
        return result

    if project_startup_terminals_running(project_id):
        speak = f"Project {project['name']} startup terminals are already running."
        result = ProjectTerminalActionResult(
            success=True,
            project_id=project_id,
            project_name=project["name"],
            action="already_running",
            message=speak,
            speak_message=speak,
        )
        if announce:
            announce_project_terminal_feedback(speak)
        return result

    resolved_commands = [
        (command or "").strip()
        for command in (
            commands
            if commands is not None
            else parse_startup_command_lines(project["startup_instructions"])
        )
        if (command or "").strip()
    ]
    folder = project["folder_location"]
    if not resolved_commands:
        speak = f"Project {project['name']} has no startup terminal instructions."
        result = ProjectTerminalActionResult(
            success=False,
            project_id=project_id,
            project_name=project["name"],
            action="no_commands",
            message=speak,
            speak_message=speak,
        )
        if announce:
            announce_project_terminal_feedback(speak)
        return result

    if not folder or not os.path.isdir(folder):
        speak = f"Project {project['name']} does not have a valid folder location."
        result = ProjectTerminalActionResult(
            success=False,
            project_id=project_id,
            project_name=project["name"],
            action="error",
            message=speak,
            speak_message=speak,
        )
        if announce:
            announce_project_terminal_feedback(speak)
        return result

    from distr.core.agent.tools.system.project_tools import (
        _format_startup_diagnostics,
        _start_inapp_terminals,
    )

    canonical = os.path.realpath(folder)
    started, failed, diagnostics = _start_inapp_terminals(
        project_id,
        canonical,
        resolved_commands,
    )
    if started > 0 and project.get("start_time_tracker", True):
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.services import schedule_blocks as schedule_service

            with get_session() as session:
                db_project = session.query(Project).filter(Project.id == project_id).first()
                if db_project:
                    schedule_service.start_project_time_tracker(session, db_project)
        except Exception as exc:
            logger.warning("Project time tracker start skipped: %s", exc)
    speak = _build_start_speak_message(project["name"], started, failed)
    message = speak
    if diagnostics:
        message = f"{speak}\n\n{_format_startup_diagnostics(diagnostics)}"
    result = ProjectTerminalActionResult(
        success=started > 0 or failed == 0,
        project_id=project_id,
        project_name=project["name"],
        action="started",
        started=started,
        failed=failed,
        message=message,
        speak_message=speak,
        diagnostics=list(diagnostics or []),
    )
    if announce:
        announce_project_terminal_feedback(speak)
    logger.info(
        "Project startup terminals started: project_id=%s started=%s failed=%s",
        project_id,
        started,
        failed,
    )
    return result
