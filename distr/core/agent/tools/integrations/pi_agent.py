"""
Pi Agent Tool — sends coding instructions to pi (coding agent) via RPC mode.

The agent uses this tool when the user asks it to work on code, run tasks,
or perform development actions on the current project. Pi is the specialist
that does the actual coding work, communicating via structured JSONL events.

Replaces the old kiro_cli.py which used kiro-cli in fire-and-forget mode.
"""
import logging
import shutil
from typing import Any, Optional, List

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class PiAgentInput(BaseModel):
    instruction: str = Field(description="The coding task or instruction to send to pi")
    project_name: Optional[str] = Field(default=None, description="Project name to run in. Uses active project if not specified.")


class PiAgentTool(BaseTool):
    """Send coding instructions to pi (coding agent) in a project folder."""

    name: str = "pi_agent"
    description: str = """Send a coding instruction to pi (coding agent) to execute in a project's folder.
Use this when the user asks you to:
- Work on code in a project (fix bugs, add features, refactor)
- Run tests, linting, or builds in a project
- Query a project for information (file counts, configs, stats)
- Perform git operations on a project
- Deploy or manage a project's codebase
- Any development task that should happen in the project folder

Pi is an AI coding agent that works directly on the codebase with read/write/bash tools.
You are the orchestrator — Pi is the specialist.

The tool will wait for Pi to complete and return the result directly.
If no project_name is given, uses the currently active project.""",
    args_schema: type[BaseModel] = PiAgentInput
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
            "pi agent", "run pi", "use pi",
            "work on the project", "work on the code",
            "fix the code", "update the code",
        ]

    def _run(self, instruction: str = "", project_name: Optional[str] = None, **kwargs) -> str:
        instruction = (instruction or kwargs.get("instruction", "")).strip()
        if not instruction:
            return "Please provide an instruction for pi."

        # Check pi is available
        from distr.core.pi_rpc import PiRpcSession
        pi_path = PiRpcSession.find_pi()
        if not pi_path:
            return "Pi coding agent is not installed. Install it with: npm install -g @mariozechner/pi-coding-agent"

        # Resolve project folder
        folder = self._resolve_project_folder(project_name)
        if not folder:
            return "No active project with a folder set. Please set a project folder first."

        resolved_name = project_name or self._get_active_project_name() or "project"

        # Acknowledge immediately so the user isn't left in silence
        from distr.core.signals import signal_manager
        try:
            signal_manager.speak_text_directly.emit(f"Let me check that in {resolved_name}.")
        except Exception:
            pass

        # Try to use an existing RPC session for the project, or create one
        from distr.core.pi_rpc import get_rpc_session, PiRpcSession as _PiRpcSession
        project_id = self._get_project_id(project_name)

        if project_id:
            rpc = get_rpc_session(project_id)
            if not rpc or not rpc.is_alive:
                # No terminal session yet — create one so the output is visible in the terminal tab
                if folder and _PiRpcSession.find_pi():
                    try:
                        rpc = _PiRpcSession(project_id, folder, append_system_prompt=f"You are working on project: {resolved_name}")
                        rpc.start()
                        from distr.core.pi_rpc import _rpc_sessions
                        _rpc_sessions[project_id] = rpc
                        logger.info(f"PiAgentTool: created new RPC session for project {project_id} ({resolved_name})")
                    except Exception as e:
                        logger.warning(f"PiAgentTool: could not create RPC session: {e}")
                        rpc = None

            if rpc and rpc.is_alive:
                # Send via RPC and wait for Pi to finish — get the result back
                result = rpc.send_and_wait(instruction, timeout=120)
                self._create_audit(resolved_name, instruction, status="completed", result=(result or "")[:2000], project_id=project_id)
                if not result:
                    return f"[Pi — {resolved_name}] Pi completed but produced no output."
                if len(result) > 2000:
                    return f"[Pi — {resolved_name}]\n{result[:2000]}...\n\n(result truncated)"
                return f"[Pi — {resolved_name}]\n{result}"

        # Fallback: use pi in print mode (non-interactive, one-shot)
        import subprocess
        try:
            result = subprocess.run(
                ["pi", "-p", "--append-system-prompt", f"You are working on project: {resolved_name}",
                 instruction],
                capture_output=True, text=True, timeout=600,
                cwd=folder,
            )
            output = (result.stdout + result.stderr).strip()[:3000]
            status = "completed" if result.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            output = "Pi timed out after 10 minutes"
            status = "failed"
        except Exception as e:
            output = f"Pi error: {e}"
            status = "failed"

        self._create_audit(resolved_name, instruction, status=status, result=output[:2000])

        if self.event_queue:
            try:
                self.event_queue.put(("step_runner_updated", {}), block=False)
            except Exception:
                pass

        if not output:
            return f"[Pi — {resolved_name}] Completed with no output."

        if len(output) > 1500:
            return f"[Pi — {resolved_name}]\n{output[:1500]}...\n\n(output truncated)"
        return f"[Pi — {resolved_name}]\n{output}"

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

    def _get_project_id(self, project_name: Optional[str]) -> Optional[int]:
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
                return project.id if project else None
        except Exception:
            return None

    def _create_audit(self, project_name, instruction, status="running", result="", project_id=None):
        try:
            from distr.core.db import get_session
            from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
            with get_session() as session:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] {instruction[:100]}",
                    status=status,
                    chat_id=None,
                    workflow_type="pi_agent",
                )
                session.add(audit)
                session.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id,
                    position=0,
                    name="Pi Agent",
                    instruction=instruction[:500],
                    status=status,
                    result=result[:2000] if result else None,
                    tool_used="pi",
                )
                session.add(step)
                session.commit()
        except Exception as e:
            logger.debug("Could not create audit session: %s", e)

    async def _arun(self, instruction: str = "", project_name: Optional[str] = None, **kwargs) -> str:
        return self._run(instruction=instruction, project_name=project_name, **kwargs)