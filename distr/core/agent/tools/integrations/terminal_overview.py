"""
Terminal Overview Tool — lets the agent query a project's terminal session state.

The agent uses this when the user asks about what's happening in a project's
terminal, what commands have run, or what the latest output was. It returns
a concise summary of the last user command and its output from the pi RPC
session for that project.
"""
import logging
from typing import Any, Optional, List

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TerminalOverviewInput(BaseModel):
    project_name: Optional[str] = Field(
        default=None,
        description="Project name to get terminal overview for. Uses the active project if not specified."
    )


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

    def _run(self, project_name: Optional[str] = None, **kwargs) -> str:
        from distr.core.pi_rpc import get_rpc_session
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        # Resolve project ID
        project_id = None
        project_display_name = "project"

        with get_session() as session:
            if project_name:
                project = session.query(Project).filter(
                    Project.name.ilike(f"%{project_name}%")
                ).first()
            else:
                project = session.query(Project).filter(Project.in_use == True).first()

            if project:
                project_id = project.id
                project_display_name = project.name

        if not project_id:
            if project_name:
                return f"No project found matching '{project_name}'. Available projects can be listed with the project tools."
            return "No active project. Switch to a project first."

        # Get the RPC session
        rpc = get_rpc_session(project_id)
        if not rpc:
            return f"No terminal session for {project_display_name}. The terminal hasn't been opened yet."

        if not rpc.is_alive:
            return f"Terminal session for {project_display_name} is not running. Open the terminal tab to start it."

        # Extract user commands and assistant responses (skip tool noise)
        messages = rpc.get_messages()
        if not messages:
            return f"Terminal for {project_display_name} is empty — no commands have been sent yet."

        user_cmds = []
        assistant_resps = []
        for msg in messages:
            role = msg.get("role", "")
            content = (msg.get("content", "") or "").strip()
            if role == "user" and content:
                user_cmds.append(content)
            elif role == "assistant" and content:
                assistant_resps.append(content)

        if not user_cmds and not assistant_resps:
            return f"Terminal for {project_display_name} has activity but no readable output yet."

        # Build a concise, natural summary for the agent to use
        last_cmd = user_cmds[-1] if user_cmds else None
        last_resp = assistant_resps[-1] if assistant_resps else None

        parts = [f"**{project_display_name} terminal:**"]
        if last_cmd:
            # Truncate for agent context
            cmd_preview = last_cmd[:150] + "..." if len(last_cmd) > 150 else last_cmd
            parts.append(f"Last command: {cmd_preview}")
        if last_resp:
            resp_preview = last_resp[:500] + "..." if len(last_resp) > 500 else last_resp
            parts.append(f"Last output: {resp_preview}")
        parts.append(f"({len(user_cmds)} command(s) total)")

        return "\n".join(parts)

    async def _arun(self, project_name: Optional[str] = None, **kwargs) -> str:
        return self._run(project_name=project_name, **kwargs)