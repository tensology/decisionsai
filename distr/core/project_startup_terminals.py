"""Start/stop project startup terminals with consistent user feedback."""

from __future__ import annotations

import logging
import os
import time
import webbrowser
import json
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

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
    url: str = ""
    opened: bool = False


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
    text = (message or "").strip()
    if not text:
        return
    try:
        from distr.core.signals import signal_manager

        signal_manager.speak_text_directly.emit(text)
        return
    except Exception:
        pass
    from distr.core.signals import speak_text_directly_event_queue

    speak_text_directly_event_queue(text)


def project_startup_terminals_running(project_id: int) -> bool:
    from distr.core.terminal import get_startup_sessions_for_project

    return bool(get_startup_sessions_for_project(project_id, purpose="startup"))


def _rank_runtime_urls(snapshot: dict) -> list[str]:
    """Prefer likely front-end URLs while preserving terminal discovery order."""
    ranked: dict[str, int] = {}
    for session in snapshot.get("sessions") or []:
        command = str(session.get("command") or "").lower()
        command_score = 20 if any(
            token in command
            for token in ("frontend", "vite", "next", "react", "npm run dev", "yarn dev", "pnpm dev")
        ) else 0
        if any(token in command for token in ("celery", "worker", "backend", "runserver")):
            command_score -= 10
        for item in session.get("urls") or []:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            port = item.get("port")
            port_score = 10 if port in {3000, 4200, 5173, 5174, 8000, 8080} else 0
            ranked[url] = max(ranked.get(url, -100), command_score + port_score)

    for item in snapshot.get("urls") or []:
        url = str(item.get("url") or "").strip()
        if url:
            ranked.setdefault(url, 0)
    return sorted(ranked, key=lambda url: ranked[url], reverse=True)


def _get_live_project_runtime_snapshot(project_id: int) -> dict:
    """Read terminal buffers from the web process that owns the PTYs."""
    try:
        from distr.core.web_runtime import (
            internal_api_headers,
            resolve_local_web_base_url,
        )

        base_url = resolve_local_web_base_url()
        if base_url:
            request = urllib.request.Request(
                f"{base_url}/api/projects/{int(project_id)}/startup-sessions",
                headers=internal_api_headers(content_type=""),
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return {"sessions": payload.get("sessions") or [], "urls": []}
    except Exception as exc:
        logger.debug("Live project runtime lookup failed: %s", exc)

    from distr.core.terminal import get_project_runtime_snapshot

    return get_project_runtime_snapshot(project_id)


def launch_project_in_browser(
    project_id: int,
    *,
    announce: bool = True,
    timeout_sec: float = 20.0,
    discovery_delay_sec: float = 1.0,
    opener: Optional[Callable[[str], bool]] = None,
    snapshot_getter: Optional[Callable[[int], dict]] = None,
) -> ProjectTerminalActionResult:
    """Ensure project terminals are running, then open their best URL."""
    project = _load_project(project_id)
    if not project:
        result = ProjectTerminalActionResult(
            False, project_id, "Project", "error",
            message="Project not found.", speak_message="Project not found.",
        )
        if announce:
            announce_project_terminal_feedback(result.speak_message)
        return result

    start_result = start_project_startup_terminals(project_id, announce=False)
    if not start_result.success:
        if announce:
            announce_project_terminal_feedback(start_result.speak_message)
        return start_result

    deadline = time.monotonic() + max(0.0, timeout_sec)
    discovered_at: Optional[float] = None
    urls: list[str] = []
    get_snapshot = snapshot_getter or _get_live_project_runtime_snapshot
    while True:
        urls = _rank_runtime_urls(get_snapshot(project_id))
        if urls:
            discovered_at = discovered_at or time.monotonic()
            if time.monotonic() - discovered_at >= max(0.0, discovery_delay_sec):
                break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.2)

    if not urls:
        speak = f"Project {project['name']} is running, but no local URL appeared in its terminals."
        result = ProjectTerminalActionResult(
            False, project_id, project["name"], "no_url",
            started=start_result.started, message=speak, speak_message=speak,
        )
    else:
        url = urls[0]
        open_url = opener or (lambda value: webbrowser.open(value, new=2, autoraise=True))
        opened = bool(open_url(url))
        speak = (
            f"Project {project['name']} launched in your default browser."
            if opened else
            f"Project {project['name']} is running at {url}, but the browser did not open."
        )
        result = ProjectTerminalActionResult(
            opened, project_id, project["name"], "launched" if opened else "open_failed",
            started=start_result.started, message=speak, speak_message=speak,
            url=url, opened=opened,
        )
    if announce:
        announce_project_terminal_feedback(result.speak_message)
    return result


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
            "provider": (project.provider or "").strip() or None,
            "board_id": (project.board_id or "").strip() or None,
            "start_time_tracker": bool(getattr(project, "start_time_tracker", True)),
        }


def _persist_project_startup_preferences(
    project_id: int,
    *,
    startup_instructions: Optional[str] = None,
    start_time_tracker: Optional[bool] = None,
) -> None:
    if startup_instructions is None and start_time_tracker is None:
        return
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    with get_session() as session:
        project = session.query(Project).filter(Project.id == project_id).first()
        if not project:
            return
        if startup_instructions is not None:
            project.startup_instructions = startup_instructions
        if start_time_tracker is not None:
            project.start_time_tracker = bool(start_time_tracker)
        session.commit()


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

    from distr.core.terminal import kill_all_startup_sessions_for_project, discard_queued_startup_terminals_for_project

    discard_queued_startup_terminals_for_project(project_id)
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
    startup_instructions: Optional[str] = None,
    start_time_tracker: Optional[bool] = None,
) -> ProjectTerminalActionResult:
    _persist_project_startup_preferences(
        project_id,
        startup_instructions=startup_instructions,
        start_time_tracker=start_time_tracker,
    )
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
    from distr.core.terminal import materialize_queued_startup_terminals_sync

    mat_started, mat_failed = materialize_queued_startup_terminals_sync(project_id)
    if mat_started or mat_failed:
        started += mat_started
        failed = max(0, failed - mat_started) + mat_failed
        logger.info(
            "Project startup terminals materialized from queue: project_id=%s started=%s failed=%s",
            project_id,
            mat_started,
            mat_failed,
        )
    if started > 0 and project.get("start_time_tracker", True):
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.services import schedule_blocks as schedule_service

            with get_session() as session:
                db_project = session.query(Project).filter(Project.id == project_id).first()
                if db_project and schedule_service.project_has_linked_board(db_project):
                    schedule_service.start_project_time_tracker(session, db_project)
        except Exception as exc:
            logger.warning("Project time tracker start skipped: %s", exc)
    speak = _build_start_speak_message(project["name"], started, failed)
    message = speak
    if diagnostics:
        message = f"{speak}\n\n{_format_startup_diagnostics(diagnostics)}"
    result = ProjectTerminalActionResult(
        success=started > 0,
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


async def start_project_startup_terminals_async(
    project_id: int,
    *,
    commands: Optional[list[str]] = None,
    announce: bool = True,
    startup_instructions: Optional[str] = None,
    start_time_tracker: Optional[bool] = None,
) -> ProjectTerminalActionResult:
    """Web API entry: spawn PTYs on the running server loop (no sync bridge)."""
    _persist_project_startup_preferences(
        project_id,
        startup_instructions=startup_instructions,
        start_time_tracker=start_time_tracker,
    )
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

    from distr.core.terminal import (
        materialize_queued_startup_terminals,
        spawn_startup_shell_sessions,
    )

    canonical = os.path.realpath(folder)
    started, failed = await spawn_startup_shell_sessions(
        project_id, canonical, resolved_commands
    )
    mat_started, mat_failed = await materialize_queued_startup_terminals(project_id)
    if mat_started or mat_failed:
        started += mat_started
        failed = max(0, failed - mat_started) + mat_failed
        logger.info(
            "Project startup terminals materialized from queue: project_id=%s started=%s failed=%s",
            project_id,
            mat_started,
            mat_failed,
        )
    if started > 0 and project.get("start_time_tracker", True):
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.services import schedule_blocks as schedule_service

            with get_session() as session:
                db_project = session.query(Project).filter(Project.id == project_id).first()
                if db_project and schedule_service.project_has_linked_board(db_project):
                    schedule_service.start_project_time_tracker(session, db_project)
        except Exception as exc:
            logger.warning("Project time tracker start skipped: %s", exc)
    speak = _build_start_speak_message(project["name"], started, failed)
    result = ProjectTerminalActionResult(
        success=started > 0,
        project_id=project_id,
        project_name=project["name"],
        action="started",
        started=started,
        failed=failed,
        message=speak,
        speak_message=speak,
    )
    if announce:
        announce_project_terminal_feedback(speak)
    logger.info(
        "Project startup terminals started (async): project_id=%s started=%s failed=%s",
        project_id,
        started,
        failed,
    )
    return result
