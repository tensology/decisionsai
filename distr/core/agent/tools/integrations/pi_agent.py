"""
Pi Agent Tool — sends coding instructions to pi (coding agent) via RPC mode.

The agent uses this tool when the user asks it to work on code, run tasks,
or perform development actions on the current project. Pi is the specialist
that does the actual coding work, communicating via structured JSONL events.

Replaces the old kiro_cli.py which used kiro-cli in fire-and-forget mode.
"""
import logging
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
    description: str = (
        "Send a coding instruction to pi (coding agent) to execute in a project's folder.\n"
        "Use this when the user asks you to:\n"
        "- Work on code in a project (fix bugs, add features, refactor)\n"
        "- Run tests, linting, or builds in a project\n"
        "- Query a project for information (file counts, configs, stats)\n"
        "- Perform git operations on a project\n"
        "- Deploy or manage a project's codebase\n"
        "- Any development task that should happen in the project folder\n\n"
        "Pi is an AI coding agent that works directly on the codebase with read/write/bash tools.\n"
        "You are the orchestrator — Pi is the specialist.\n\n"
        "The tool will wait for Pi to complete and return the result directly.\n"
        "If no project_name is given, uses the currently active project."
    )
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
        project_id = self._get_project_id(project_name)

        # Build rich project context to pass to pi
        project_context = self._build_project_context(project_id, resolved_name, folder)

        # Enrich the instruction with project context so pi knows what it's working on
        enriched_instruction = instruction
        if project_context:
            enriched_instruction = f"{project_context}\n\n---\n\n{instruction}"
            logger.info(f"PiAgentTool: enriched instruction with {len(project_context)} chars of project context")

        # Build append_system_prompt with project context (not just name)
        system_prompt_for_pi = f"You are working on project: {resolved_name}"
        if folder:
            system_prompt_for_pi += f"\nProject folder: {folder}"
        if project_context:
            # For the system prompt, include a condensed version
            condensed = self._condense_context_for_system_prompt(project_context)
            system_prompt_for_pi += f"\n{condensed}"

        # Acknowledge immediately so the user isn't left in silence
        from distr.core.signals import speak_text_directly_event_queue
        try:
            speak_text_directly_event_queue(f"Let me check that in {resolved_name}.")
        except Exception:
            pass

        # Use the unified CLI dispatch — creates RPC session if needed,
        # sends via send_prompt (not send_and_wait) so CLI tab shows real-time output
        from distr.core.agent.tools.integrations.unified_cli import dispatch_to_cli

        if project_id and folder:
            result = dispatch_to_cli(
                project_id=project_id,
                cwd=folder,
                instruction=enriched_instruction,
                project_name=resolved_name,
                append_system_prompt=system_prompt_for_pi,
            )
            self._create_audit(resolved_name, instruction, status="completed" if result["success"] else "failed", result=result["message"][:2000], project_id=project_id)
            if result["success"]:
                return f"[Pi — {resolved_name}] {result['message']}"
            return f"[Pi — {resolved_name}] Failed: {result['message']}"

        # Fallback: use pi in print mode (non-interactive, one-shot)
        import subprocess
        try:
            result = subprocess.run(
                ["pi", "-p", "--append-system-prompt", system_prompt_for_pi,
                 enriched_instruction],
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

    def _build_project_context(self, project_id: Optional[int], project_name: str, folder: str) -> str:
        """Build rich project context to pass to pi so it understands what it's working on."""
        parts = []
        parts.append(f"PROJECT: {project_name}")
        parts.append(f"FOLDER: {folder}")

        # Add context items from the database
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project, ProjectContextItem, ProjectFile
            with get_session() as session:
                if project_id:
                    # Context items
                    context_items = session.query(ProjectContextItem).filter(
                        ProjectContextItem.project_id == project_id
                    ).all()
                    if context_items:
                        parts.append("\nCONTEXT ITEMS:")
                        for item in context_items:
                            content = (item.content or "")[:500]  # Truncate long items
                            parts.append(f"- {item.title}: {content}")

                    # Files
                    project_files = session.query(ProjectFile).filter(
                        ProjectFile.project_id == project_id
                    ).all()
                    if project_files:
                        parts.append("\nPROJECT FILES:")
                        for pf in project_files:
                            parts.append(f"- {pf.filename}")

                # Startup instructions
                project = session.query(Project).filter(Project.id == project_id).first() if project_id else None
                if project and project.startup_instructions and project.startup_instructions.strip():
                    parts.append("\nSTARTUP INSTRUCTIONS:")
                    parts.append(project.startup_instructions.strip()[:500])

                # Trigger words
                if project and project.additional_trigger_words:
                    try:
                        import json
                        trigger_words = json.loads(project.additional_trigger_words)
                        if trigger_words:
                            parts.append(f"\nTRIGGER WORDS: {', '.join(trigger_words)}")
                    except (json.JSONDecodeError, ValueError):
                        pass

                # Description
                if project and project.description:
                    parts.append(f"\nDESCRIPTION: {project.description}")
        except Exception as e:
            logger.debug(f"Could not load project context items: {e}")

        # Add key file listing from folder (top-level + .tickets)
        try:
            import os
            if folder and os.path.isdir(folder):
                top_files = []
                for f in sorted(os.listdir(folder)):
                    fp = os.path.join(folder, f)
                    if os.path.isfile(fp) and not f.startswith('.'):
                        top_files.append(f)
                if top_files:
                    parts.append(f"\nTOP-LEVEL FILES: {', '.join(top_files[:20])}")

                # .tickets contents
                tickets_dir = os.path.join(folder, '.tickets')
                if os.path.isdir(tickets_dir):
                    ticket_files = [f for f in os.listdir(tickets_dir) if f.endswith('.md')]
                    if ticket_files:
                        parts.append(f"\nOPEN TICKETS: {', '.join(sorted(ticket_files)[:10])}")
        except Exception as e:
            logger.debug(f"Could not list project folder: {e}")

        return '\n'.join(parts)

    def _condense_context_for_system_prompt(self, full_context: str) -> str:
        """Create a condensed version of project context for the pi system prompt.
        Full context goes in the instruction; system prompt gets a shorter version."""
        lines = full_context.split('\n')
        condensed = []
        for line in lines:
            # Keep PROJECT, FOLDER, DESCRIPTION, TRIGGER WORDS — skip verbose items
            if any(line.startswith(prefix) for prefix in ['PROJECT:', 'FOLDER:', 'DESCRIPTION:', 'TRIGGER WORDS:', 'CONTEXT ITEMS:', 'STARTUP INSTRUCTIONS:']):
                condensed.append(line)
        return '\n'.join(condensed)

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