"""
Kiro CLI Tool — sends coding instructions to Kiro CLI in the active project's folder.

The agent uses this tool when the user asks it to work on code, run tasks,
or perform development actions on the current project. Kiro CLI is the
specialist that does the actual coding work.
"""
import logging
import shutil
import subprocess
from typing import Any, Optional, List

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class KiroCliInput(BaseModel):
    instruction: str = Field(description="The coding task or instruction to send to Kiro CLI")
    project_name: Optional[str] = Field(default=None, description="Project name to run in. Uses active project if not specified.")


class KiroCliTool(BaseTool):
    """Send coding instructions to Kiro CLI in a project folder."""

    name: str = "kiro_cli"
    description: str = """Send a coding instruction to Kiro CLI to execute in a project's folder.
Use this when the user asks you to:
- Work on code in a project (fix bugs, add features, refactor)
- Run tests, linting, or builds in a project
- Perform git operations on a project
- Deploy or manage a project's codebase
- Any development task that should happen in the project folder

Kiro CLI is an AI coding agent that works directly on the codebase.
You are the orchestrator — Kiro CLI is the specialist.

If no project_name is given, uses the currently active project.
"""
    args_schema: type[BaseModel] = KiroCliInput
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
            "kiro cli", "run kiro", "use kiro",
            "work on the project", "work on the code",
            "fix the code", "update the code",
        ]

    def _run(self, instruction: str = "", project_name: Optional[str] = None, **kwargs) -> str:
        instruction = (instruction or kwargs.get("instruction", "")).strip()
        if not instruction:
            return "Please provide an instruction for Kiro CLI."

        # Check Kiro CLI is installed
        kiro_path = shutil.which("kiro-cli")
        if not kiro_path:
            return "Kiro CLI is not installed. Install it with: curl -fsSL https://cli.kiro.dev/install | bash"

        # Resolve project folder
        folder = self._resolve_project_folder(project_name)
        if not folder:
            return "No active project with a folder set. Please set a project folder first."

        resolved_name = project_name or self._get_active_project_name() or "project"

        # Log to audit trail
        chat_id = None
        if self.chat_manager:
            chat_id = self.chat_manager.get_current_chat()

        audit_id, step_id = self._create_audit_session(resolved_name, instruction, chat_id)

        # Execute Kiro CLI
        try:
            result = subprocess.run(
                [kiro_path, "chat", "--no-interactive", "--trust-all-tools", instruction],
                capture_output=True, text=True, timeout=300,
                cwd=folder,
            )
            output = (result.stdout + result.stderr).strip()[:3000]
            status = "completed" if result.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            output = "Kiro CLI timed out after 5 minutes"
            status = "failed"
        except Exception as e:
            output = f"Kiro CLI error: {e}"
            status = "failed"

        # Update audit trail (legacy StepRunner — removed in task 6.3)
        if audit_id and step_id:
            pass

        if self.event_queue:
            try:
                self.event_queue.put(("step_runner_updated", {}), block=False)
            except Exception:
                pass

        if not output:
            return f"Kiro CLI completed with no output (exit code: {result.returncode})"

        # Truncate for the agent response but keep it useful
        if len(output) > 1500:
            return f"[Kiro CLI — {resolved_name}]\n{output[:1500]}...\n\n(output truncated)"
        return f"[Kiro CLI — {resolved_name}]\n{output}"

    def _resolve_project_folder(self, project_name: Optional[str]) -> Optional[str]:
        """Get the folder for the named project, or the active project."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                if project_name:
                    project = session.query(Project).filter(
                        Project.name.ilike(f"%{project_name}%")
                    ).first()
                else:
                    project = session.query(Project).filter(Project.in_use == True).first()
                if project and project.folder_location:
                    return project.folder_location
        except Exception as e:
            logger.debug("Could not resolve project folder: %s", e)
        return None

    def _get_active_project_name(self) -> Optional[str]:
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                project = session.query(Project).filter(Project.in_use == True).first()
                return project.name if project else None
        except Exception:
            return None

    def _create_audit_session(self, project_name, instruction, chat_id):
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
            with get_session() as session:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] {instruction}",
                    status="in_progress",
                    chat_id=int(chat_id) if chat_id else None,
                    workflow_type="kiro_cli",
                )
                session.add(audit)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id,
                    position=0,
                    name="Kiro CLI",
                    instruction=instruction[:500],
                    status="running",
                    tool_used="kiro-cli",
                )
                session.add(step)
                session.commit()
                return audit.id, step.id
        except Exception as e:
            logger.debug("Could not create audit session: %s", e)
            return None, None

    async def _arun(self, instruction: str = "", project_name: Optional[str] = None, **kwargs) -> str:
        return self._run(instruction=instruction, project_name=project_name, **kwargs)
