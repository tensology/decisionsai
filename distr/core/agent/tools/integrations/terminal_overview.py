"""
Terminal Overview Tool — lets the agent query a project's terminal session state.

The agent uses this when the user asks about what's happening in a project's
terminal, what commands have run, or what the latest output was. It returns
a concise summary of the last user command and its output from the pi RPC
session for that project.
"""
import logging
import json
from dataclasses import asdict
from typing import Any, Optional, List

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TerminalOverviewInput(BaseModel):
    action: str = Field(
        default="status",
        description="Explicit action: list, status, read, save, start, or stop.",
    )
    project_id: Optional[int] = Field(default=None, description="Exact project id.")
    project_name: Optional[str] = Field(
        default=None,
        description="Project name to get terminal overview for. Uses the active project if not specified."
    )
    startup_instructions: Optional[str] = Field(
        default=None,
        description="For save/start: one terminal command per line.",
    )
    lines: int = Field(default=100, ge=1, le=500)


class TerminalOverviewTool(BaseTool):
    """Get the current state of a project's terminal session — last command and output."""

    name: str = "terminal_overview"
    description: str = """Get an overview of what's happening in a project's terminal session.
Use this when the user asks:
- "What's happening in the terminal?"
- "What did the last command output?"
- "What's the status of the project terminal?"
- "What's running in the terminal?"
- "Read out the terminal"

Returns the last command sent and its output, plus the project name.
If no project is specified, uses the currently active project.
"""
    args_schema: type[BaseModel] = TerminalOverviewInput
    event_queue: Any = Field(default=None, exclude=True)
    chat_manager: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, chat_manager=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue
        if chat_manager:
            self.chat_manager = chat_manager

    def get_triggers(self) -> List[str]:
        return [
            "terminal overview", "terminal status", "what's in the terminal",
            "terminal output", "read the terminal", "read out",
            "what's running", "what happened", "terminal say",
        ]

    def _run(
        self,
        action: str = "status",
        project_id: Optional[int] = None,
        project_name: Optional[str] = None,
        startup_instructions: Optional[str] = None,
        lines: int = 100,
        **kwargs,
    ) -> str:
        from distr.core.db import get_session
        from distr.core.db.projects import Project
        from distr.core.project_startup_terminals import (
            parse_startup_command_lines,
            start_project_startup_terminals,
            stop_project_startup_terminals,
        )
        from distr.core.terminal import get_startup_session, get_startup_sessions_for_project

        normalized_action = str(action or "status").strip().lower()
        if normalized_action == "list":
            with get_session() as session:
                projects = session.query(Project).order_by(Project.position, Project.name).all()
                payload = []
                for project in projects:
                    sessions = get_startup_sessions_for_project(int(project.id), purpose="startup")
                    commands = parse_startup_command_lines(project.startup_instructions or "")
                    payload.append({
                        "project_id": int(project.id),
                        "project_name": project.name,
                        "commands": commands,
                        "configured_count": len(commands),
                        "running_count": len(sessions),
                        "running": bool(sessions),
                        "terminals": sessions,
                    })
            return json.dumps({"surface": "development", "projects": payload}, ensure_ascii=False, default=str)

        # Resolve project ID
        project_display_name = "project"

        with get_session() as session:
            if project_id is not None:
                project = session.get(Project, int(project_id))
            elif project_name:
                project = session.query(Project).filter(
                    Project.name.ilike(f"%{project_name}%")
                ).first()
            else:
                project = session.query(Project).filter(Project.in_use == True).first()

            if project:
                project_id = project.id
                project_display_name = project.name
                configured_instructions = project.startup_instructions or ""

        if not project_id:
            if project_name:
                return f"No project found matching '{project_name}'. Available projects can be listed with the project tools."
            return "No active project. Switch to a project first."

        if normalized_action == "save":
            if startup_instructions is None:
                return "Error: startup_instructions is required for save."
            with get_session() as session:
                project = session.get(Project, int(project_id))
                project.startup_instructions = startup_instructions
                session.commit()
            return json.dumps({
                "surface": "development",
                "action": "saved",
                "project_id": int(project_id),
                "project_name": project_display_name,
                "commands": parse_startup_command_lines(startup_instructions),
            }, ensure_ascii=False)

        if normalized_action == "start":
            result = start_project_startup_terminals(
                int(project_id),
                announce=False,
                startup_instructions=startup_instructions,
            )
            return json.dumps({"surface": "development", **asdict(result)}, ensure_ascii=False, default=str)
        if normalized_action == "stop":
            result = stop_project_startup_terminals(int(project_id), announce=False)
            return json.dumps({"surface": "development", **asdict(result)}, ensure_ascii=False, default=str)
        if normalized_action not in {"status", "read"}:
            return "Error: action must be list, status, read, save, start, or stop."

        sessions = get_startup_sessions_for_project(int(project_id), purpose="startup")
        payload = {
            "surface": "development",
            "action": normalized_action,
            "project_id": int(project_id),
            "project_name": project_display_name,
            "commands": parse_startup_command_lines(configured_instructions),
            "running_count": len(sessions),
            "running": bool(sessions),
            "terminals": sessions,
        }
        if normalized_action == "read":
            from distr.core.chat_turns import redact_text

            payload["output"] = [
                {
                    "terminal_id": item.get("process_id"),
                    "command": item.get("shell_command") or "",
                    "output": (
                        redact_text(
                            get_startup_session(str(item.get("process_id"))).get_buffer(lines=max(1, min(int(lines), 500))),
                            limit=12000,
                            preserve_paths=True,
                        )
                        if get_startup_session(str(item.get("process_id")))
                        else ""
                    ),
                }
                for item in sessions
            ]
        return json.dumps(payload, ensure_ascii=False, default=str)

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
