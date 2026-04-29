"""
Project Management Tools for LangChain.

Tools for managing projects, switching contexts, adding files, and creating tickets.
"""

from typing import Any, Optional, List
from langchain.tools import BaseTool
from pydantic import Field, BaseModel
import logging
import re
import json
import os
import subprocess
import shutil
import platform
import shlex
import urllib.request
import urllib.error
from datetime import datetime

logger = logging.getLogger(__name__)


def _get_internal_api_token_for_web() -> str:
    token = (os.getenv("DECISIONSAI_INTERNAL_API_TOKEN") or "").strip()
    if token:
        return token
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/settings", timeout=2.0) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'<meta name="decisionsai-internal-api-token" content="([^"]+)"', html)
        return (m.group(1).strip() if m else "")
    except Exception:
        return ""


def _start_via_web_runtime(project_id: int, folder: str, commands: list[str]) -> tuple[int, int, list[dict[str, str]]]:
    token = _get_internal_api_token_for_web()
    if not token:
        diagnostics = [{"command": c, "status": "failed", "reason": "Missing internal API token"} for c in commands]
        return 0, len(commands), diagnostics

    started = 0
    failed = 0
    diagnostics: list[dict[str, str]] = []
    url = "http://127.0.0.1:8765/api/projects/startup-terminal"
    for cmd in commands:
        body = json.dumps({
            "project_id": int(project_id),
            "command": cmd,
            "working_dir": folder,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-DecisionsAI-Internal-Token": token,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            if payload.get("success"):
                started += 1
                diagnostics.append({"command": cmd, "status": "started", "reason": "spawned in web runtime"})
            else:
                failed += 1
                diagnostics.append({"command": cmd, "status": "failed", "reason": payload.get("error") or "Unknown web runtime error"})
        except urllib.error.HTTPError as e:
            failed += 1
            err_text = ""
            try:
                err_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_text = str(e)
            diagnostics.append({"command": cmd, "status": "failed", "reason": f"HTTP {e.code}: {err_text[:180] or e.reason}"})
        except Exception as e:
            logger.warning("Web runtime startup-terminal failed for '%s': %s", cmd, e)
            failed += 1
            diagnostics.append({"command": cmd, "status": "failed", "reason": str(e)})
    return started, failed, diagnostics


def _start_inapp_terminals(project_id: int, folder: str, commands: list[str]) -> tuple[int, int, list[dict[str, str]]]:
    """Start terminals in web runtime; fallback to queue if needed."""
    started, failed, diagnostics = _start_via_web_runtime(project_id=project_id, folder=folder, commands=commands)
    if started > 0 and failed == 0:
        return started, failed, diagnostics

    from distr.core.terminal import queue_startup_terminal_launch

    queue_candidates = [d["command"] for d in diagnostics if d.get("status") == "failed" and d.get("command")]
    queued = queue_startup_terminal_launch(project_id=project_id, cwd=folder, commands=queue_candidates)
    queue_failed = max(0, len(queue_candidates) - queued)
    if queued or queue_failed:
        logger.info("Startup fallback queue for project %s: queued=%s failed=%s", project_id, queued, queue_failed)
    remaining_queued = queued
    for d in diagnostics:
        if d.get("status") == "failed" and remaining_queued > 0:
            d["status"] = "queued"
            d["reason"] = "queued for web runtime fallback"
            remaining_queued -= 1

    total_started = started + queued
    total_failed = max(0, len(commands) - total_started)
    return total_started, total_failed, diagnostics


def _format_startup_diagnostics(diagnostics: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(diagnostics, start=1):
        cmd = (item.get("command") or "").strip()
        status = (item.get("status") or "unknown").strip()
        reason = (item.get("reason") or "").strip()
        if len(cmd) > 100:
            cmd = cmd[:97] + "..."
        lines.append(f"{idx}. [{status}] {cmd} — {reason}")
    return "\n".join(lines)


def _parse_startup_command_lines(startup_instructions: str) -> list[str]:
    """Split startup instructions into runnable lines; skip blanks and # comments."""
    out: list[str] = []
    for line in (startup_instructions or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def _build_decisions_meta() -> str:
    """Build workflow callback metadata for .tickets files when applicable."""
    from distr.core.workflow.dispatcher import get_current_workflow_env
    _wenv = get_current_workflow_env()
    run_id = _wenv.get("run_id")
    step_id = _wenv.get("step_id")
    workflow_id = _wenv.get("workflow_id")
    if not run_id:
        return ""
    try:
        import json as _json
        api_base = os.environ.get("DECISIONS_API_BASE", "http://localhost:5555")
        wf_id = int(workflow_id) if workflow_id else 0
        r_id = int(run_id)
        meta = {
            "run_id": r_id,
            "step_id": int(step_id) if step_id else 0,
            "workflow_id": wf_id,
            "api_base": api_base,
            "context_type": "workflow",
            "callback_url": f"{api_base}/api/workflows/{wf_id}/runs/{r_id}/continue",
            "callback_payload_type": "workflow_continue",
        }
        return f"<!-- decisions-meta: {_json.dumps(meta)} -->\n"
    except (ValueError, TypeError) as e:
        logger.warning("CreateProjectTicket: Could not build decisions-meta: %s", e)
        return ""


def _applescript_escape_double_quoted(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _launch_startup_commands_in_terminal_app(folder_location: str, startup_instructions: str) -> tuple[bool, str]:
    """
    Run each configured startup command in Apple's Terminal.app (or separate windows on fallback).

    On macOS, prefers one window with tabs (Cmd+T between commands). If that fails, opens one window per command.
    On other OSes: best-effort separate terminal processes.
    """
    commands = _parse_startup_command_lines(startup_instructions)
    if not commands:
        return True, ""

    system = platform.system()
    if system != "Darwin":
        return _launch_startup_commands_non_macos(folder_location, commands)

    shell_lines = [f"cd {shlex.quote(folder_location)} && {c}" for c in commands]

    # Try: first tab via do script; further tabs via Cmd+T + do script in front window
    if len(shell_lines) == 1:
        osa_tab = "\n".join(
            [
                'tell application "Terminal"',
                "activate",
                f'do script "{_applescript_escape_double_quoted(shell_lines[0])}"',
                "end tell",
            ]
        )
    else:
        chunks: list[str] = [
            'tell application "Terminal"',
            "activate",
            f'do script "{_applescript_escape_double_quoted(shell_lines[0])}"',
            "end tell",
        ]
        for line in shell_lines[1:]:
            chunks.append("delay 0.4")
            chunks.append('tell application "System Events"')
            chunks.append('keystroke "t" using command down')
            chunks.append("end tell")
            chunks.append("delay 0.3")
            chunks.append('tell application "Terminal"')
            chunks.append(f'do script "{_applescript_escape_double_quoted(line)}" in front window')
            chunks.append("end tell")
        osa_tab = "\n".join(chunks)

    try:
        r = subprocess.run(["osascript", "-e", osa_tab], capture_output=True, text=True, timeout=90)
        if r.returncode == 0:
            return True, f"Launched {len(commands)} startup command(s) in Terminal (tabs)."
        logger.warning("Terminal tab AppleScript failed: %s — trying separate windows", r.stderr.strip())
    except Exception as e:
        logger.warning(f"Terminal tab launch error: {e}", exc_info=True)

    # Fallback: one Terminal window per command (no System Events keystrokes)
    osa_windows = "\n".join(
        [
            'tell application "Terminal"',
            "activate",
            "\n".join([f'do script "{_applescript_escape_double_quoted(sl)}"' for sl in shell_lines]),
            "end tell",
        ]
    )
    try:
        r2 = subprocess.run(["osascript", "-e", osa_windows], capture_output=True, text=True, timeout=90)
        if r2.returncode == 0:
            return True, f"Launched {len(commands)} startup command(s) in Terminal (separate windows)."
        return False, (r2.stderr or r2.stdout or "osascript failed")[:800]
    except Exception as e:
        logger.error(f"Terminal fallback launch failed: {e}", exc_info=True)
        return False, str(e)


def _launch_startup_commands_non_macos(folder_location: str, commands: list[str]) -> tuple[bool, str]:
    """Best-effort: spawn a new terminal window per command on Linux/Windows."""
    system = platform.system()
    ok_count = 0
    if system == "Linux":
        gterm = shutil.which("gnome-terminal")
        if not gterm:
            return False, "Install gnome-terminal (or use macOS) to auto-launch startup commands."
        for c in commands:
            inner = f"cd {shlex.quote(folder_location)} && {c}; exec bash"
            try:
                subprocess.Popen([gterm, "--", "bash", "-lc", inner], start_new_session=True)
                ok_count += 1
            except Exception as e:
                logger.warning("Failed to launch gnome-terminal for command %s: %s", c, e)
        return (ok_count > 0, f"Started {ok_count}/{len(commands)} command(s) in gnome-terminal." if ok_count else "Could not launch any terminals.")
    if system == "Windows":
        for c in commands:
            try:
                subprocess.Popen(
                    ["cmd", "/c", "start", "DecisionsAI-startup", "cmd", "/k", f'cd /d "{folder_location}" && {c}'],
                    shell=False,
                )
                ok_count += 1
            except Exception as e:
                logger.warning("Failed to launch Windows console for command %s: %s", c, e)
        return (ok_count > 0, f"Started {ok_count}/{len(commands)} command(s) in new console windows." if ok_count else "Could not launch any consoles.")
    return False, f"Startup terminal launch not implemented for {system}."


class ListProjectsTool(BaseTool):
    """Tool for listing all available projects - fetches LIVE data from database."""

    name: str = "list_projects"
    description: str = """List all available projects. FETCHES LIVE DATA from the database every time.

    IMPORTANT: This tool queries the database FRESH each time - use it whenever you need current project info.

    Triggers (MUST use this tool for these):
    - "What projects do I have?"
    - "List my projects"
    - "Show all projects"
    - "Check my projects"
    - "Look at the projects"
    - "Can you see the projects?"
    - "Is the project there?"
    - "Does it have the details now?"
    - "Go look and see"
    - "Check if it's there"

    Returns: LIVE list of all projects with their names, IDs, folder locations, and status.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> List[str]:
        """Get triggers for list projects."""
        return [
            "list projects", "show projects", "what projects",
            "my projects", "available projects", "show all projects",
            "list my projects", "projects list", "check projects",
            "look at projects", "see the projects",
        ]

    def _run(self, text: str = "", **kwargs) -> str:
        """Execute list projects action - always fetches fresh data."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project

            session = get_session()
            try:
                # Always fetch fresh from database
                projects = session.query(Project).order_by(Project.modified_date.desc()).all()

                if not projects:
                    return "No projects found. You can create a new project in the Projects Manager or by dropping a folder."

                response = f"**Available Projects ({len(projects)}) - LIVE DATA:**\n\n"

                for project in projects:
                    status = "✓ Active" if project.in_use else ""
                    has_folder = "✓ Has folder" if project.folder_location else "⚠ No folder set"
                    
                    response += f"- **{project.name}** (ID: {project.id})\n"
                    response += f"  Folder: {project.folder_location if project.folder_location else 'Not set'}\n"
                    if status:
                        response += f"  Status: {status}\n"
                    if project.description:
                        response += f"  Description: {project.description}\n"

                response += "\nTo switch to a project, say 'switch to project <name>' or 'open project <name> and start it'."

                return response

            except Exception as e:
                logger.error(f"Error listing projects: {e}", exc_info=True)
                return f"Error listing projects: {str(e)}"
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error in list_projects tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class GetProjectDetailsTool(BaseTool):
    """Tool for getting FRESH details about a specific project."""

    name: str = "get_project_details"
    description: str = """Get FRESH details about a specific project by name. FETCHES LIVE DATA from database.

    IMPORTANT: Use this tool when the user asks to check, look at, or verify a project's current state.
    This ALWAYS queries the database for the latest information.

    Triggers (MUST use this tool for these):
    - "Check the project X"
    - "Does project X have a folder now?"
    - "Look at project X"
    - "What are the details of project X?"
    - "Is project X set up?"
    - "Can you see project X?"
    - "Check if X has the details"

    Returns: LIVE project details including folder location, startup instructions, etc.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> List[str]:
        """Get triggers for get project details."""
        return [
            "check project", "check the project", "project details",
            "does project have", "look at project", "is project set up",
            "can you see project", "check if project"
        ]

    def _run(self, text: str = "", project_name: str = "", **kwargs) -> str:
        """Execute get project details - always fetches fresh data."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from difflib import SequenceMatcher

            def similarity(a: str, b: str) -> float:
                return SequenceMatcher(None, a.lower(), b.lower()).ratio()

            # Extract project name from text if not provided
            search_name = project_name.strip() if project_name else ""
            
            if not search_name and text:
                # Try to extract project name from patterns
                patterns = [
                    r"check\s+(?:the\s+)?project\s+([A-Za-z0-9_\-]+)",
                    r"project\s+([A-Za-z0-9_\-]+)\s+(?:have|has|set)",
                    r"look\s+at\s+(?:the\s+)?project\s+([A-Za-z0-9_\-]+)",
                    r"see\s+(?:the\s+)?project\s+([A-Za-z0-9_\-]+)",
                    r"(?:is|does)\s+([A-Za-z0-9_\-]+)\s+(?:set|have|there)",
                ]
                for pattern in patterns:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        search_name = match.group(1).strip()
                        break

            session = get_session()
            try:
                project = None
                
                if search_name:
                    # Fuzzy match to find project
                    all_projects = session.query(Project).all()
                    best_match = None
                    best_score = 0.0
                    
                    for p in all_projects:
                        score = similarity(search_name, p.name)
                        if score > best_score:
                            best_score = score
                            best_match = p
                    
                    if best_match and best_score >= 0.5:
                        project = best_match
                        logger.info(f"GetProjectDetails: Matched '{search_name}' to '{project.name}' (score: {best_score:.2f})")
                
                # If no specific project, get the active one
                if not project:
                    project = session.query(Project).filter(Project.in_use == True).first()
                    if project:
                        logger.info(f"GetProjectDetails: Using active project '{project.name}'")
                
                if not project:
                    # List all projects instead
                    all_projects = session.query(Project).all()
                    if all_projects:
                        names = [p.name for p in all_projects]
                        return f"No matching project found. Available projects: {', '.join(names)}"
                    return "No projects found in the database."

                # Build detailed response
                response = f"**Project: {project.name}** (ID: {project.id}) - LIVE DATA\n\n"
                response += f"**Folder:** {project.folder_location if project.folder_location else '⚠ NOT SET'}\n"
                response += f"**Active:** {'Yes' if project.in_use else 'No'}\n"
                
                if project.description:
                    response += f"**Description:** {project.description}\n"
                
                if project.startup_instructions:
                    response += f"**Startup Instructions:** {len(project.startup_instructions.strip().split(chr(10)))} command(s) configured\n"
                else:
                    response += "**Startup Instructions:** None configured\n"
                
                # Check if ready to start
                if project.folder_location:
                    response += "\n✓ This project is ready to be opened and started."
                else:
                    response += "\n⚠ This project needs a folder location set in the Projects Manager before it can be started."

                return response

            except Exception as e:
                logger.error(f"Error getting project details: {e}", exc_info=True)
                return f"Error: {str(e)}"
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error in get_project_details tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class OpenAndStartProjectInput(BaseModel):
    """Input schema for OpenAndStartProjectTool."""
    project_name: str = Field(
        default="",
        description="The name of the project to open. Extract this from the user's request. If not specified, will use the currently active project."
    )


class OpenAndStartProjectTool(BaseTool):
    """Tool for switching to a project and immediately starting it."""

    name: str = "open_and_start_project"
    description: str = """Switch to a project by name and immediately start it (open in editor + run startup commands in Terminal).

    IMPORTANT: You MUST extract the project name from the user's request and pass it as project_name.
    
    Examples:
    - User says "Open project Tensology" -> project_name="Tensology"
    - User says "Open the project and start it" -> project_name="" (will use active project)
    - User says "Start the Tensology project" -> project_name="Tensology"
    - User says "Can you open Merrypak" -> project_name="Merrypak"

    The tool will:
    1. Find the project by name (with fuzzy matching for typos)
    2. Activate the project (set it as the current working project)
    3. Open the project folder in Cursor/VS Code
    4. Launch each configured startup command in a new Terminal tab or window (macOS Terminal.app)
    """
    args_schema: type[BaseModel] = OpenAndStartProjectInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> List[str]:
        """Get triggers for open and start project."""
        return [
            "open project and start", "open and start project",
            "switch to project and start", "open the project and start",
            "open the project", "start the project",
            "open project", "launch the project"
        ]

    def _run(self, project_name: str = "", **kwargs) -> str:
        """Execute open and start project action."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.agent.services.rag.project import activate_project

            # Use project_name directly - the LLM should have extracted it
            search_name = project_name.strip() if project_name else ""
            
            logger.info("OpenAndStartProject called with project_name='%s'", project_name)

            session = get_session()
            try:
                project = None
                logger.info(f"OpenAndStartProject: project_name='{project_name}', search_name='{search_name}'")

                # Search for project by name with fuzzy matching
                if search_name and not project:
                    from difflib import SequenceMatcher
                    
                    def similarity(a: str, b: str) -> float:
                        """Calculate similarity ratio between two strings."""
                        return SequenceMatcher(None, a.lower(), b.lower()).ratio()
                    
                    all_projects = session.query(Project).all()
                    logger.info("Found %s projects in database: %s", len(all_projects), [p.name for p in all_projects])
                    best_match = None
                    best_score = 0.0
                    
                    for p in all_projects:
                        # Check project name similarity
                        name_score = similarity(search_name, p.name)
                        logger.debug("Comparing '%s' to project '%s': score=%.2f", search_name, p.name, name_score)
                        logger.debug(f"  Comparing '{search_name}' to project '{p.name}': score={name_score:.2f}")
                        if name_score > best_score:
                            best_score = name_score
                            best_match = p
                        
                        # Check trigger words similarity
                        try:
                            trigger_words = json.loads(p.additional_trigger_words) if p.additional_trigger_words else []
                            for word in trigger_words:
                                word_score = similarity(search_name, word)
                                if word_score > best_score:
                                    best_score = word_score
                                    best_match = p
                                    logger.debug("Trigger '%s' (project %s): score=%.2f", word, p.name, word_score)
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass
                    
                    logger.debug("Fuzzy matching results: best_match='%s', best_score=%.2f", best_match.name if best_match else None, best_score)
                    logger.info(f"Fuzzy matching results: best_match='{best_match.name if best_match else None}', best_score={best_score:.2f}")
                    
                    # Accept match if score is above threshold (0.7 = 70% similar)
                    if best_match and best_score >= 0.7:
                        project = best_match
                        logger.info("Fuzzy matched '%s' to project '%s' (score: %.2f)", search_name, project.name, best_score)
                        logger.info(f"Fuzzy matched '{search_name}' to project '{project.name}' (score: {best_score:.2f})")
                    elif best_match and best_score >= 0.5:
                        # Lower threshold but warn - might be a poor match
                        logger.warning("Weak fuzzy match '%s' to project '%s' (score: %.2f) - using anyway", search_name, best_match.name, best_score)
                        logger.warning(f"Weak fuzzy match '{search_name}' to project '{best_match.name}' (score: {best_score:.2f}) - using anyway")
                        project = best_match
                    else:
                        logger.info("No match above threshold for '%s' (best was %s at %.2f)", search_name, best_match.name if best_match else 'none', best_score)

                # Try trigger words - but ONLY if we have a specific search_name
                # Don't match trigger words against the full text (too permissive)
                if not project and search_name:
                    all_projects = session.query(Project).all()
                    for p in all_projects:
                        try:
                            trigger_words = json.loads(p.additional_trigger_words) if p.additional_trigger_words else []
                            for word in trigger_words:
                                # Only match if search_name closely matches the trigger word
                                if search_name.lower() == word.lower() or search_name.lower() in word.lower() or word.lower() in search_name.lower():
                                    project = p
                                    logger.info(f"Matched trigger word '{word}' to project '{project.name}'")
                                    break
                            if project:
                                break
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass

                # FALLBACK: If no project name was specified (generic "open the project"),
                # use the currently active project
                if not project and not search_name:
                    # Check if user said something generic like "open the project" without a name
                    # If no project name was provided, use active project
                    if not search_name:
                        # Use the currently active project
                        project = session.query(Project).filter(Project.in_use == True).first()
                        if project:
                            logger.info(f"No project name specified, using active project: {project.name}")

                if not project:
                    # Check if there's an active project they might want
                    active_project = session.query(Project).filter(Project.in_use == True).first()
                    if active_project:
                        return f"Project not found. Did you mean the active project '{active_project.name}'? Say 'open the project and start it' to use it, or specify a project name."
                    
                    # List available projects to help the user
                    projects = session.query(Project).order_by(Project.name).all()
                    if projects:
                        project_list = ", ".join([p.name for p in projects])
                        return f"Project not found. Available projects: {project_list}\n\nSay 'open project <name> and start it' with one of these project names."
                    return "No projects found. Create a project first in the Projects Manager."

                # Check if project has required settings
                if not project.folder_location:
                    return f"Project '{project.name}' has no folder location set. Please configure it in the Projects Manager first."

                # Activate the project
                activate_result = activate_project(project.id)
                if not activate_result.get('success'):
                    return f"Error activating project: {activate_result.get('error')}"

                logger.info(f"Activated project: {project.name} (ID: {project.id})")
                folder_location = project.folder_location

                # Start in-app PTY terminals for each startup command
                startup_instructions = project.startup_instructions.strip() if project.startup_instructions else ""
                if startup_instructions:
                    commands = _parse_startup_command_lines(startup_instructions)
                    if commands:
                        started, failed, diagnostics = _start_inapp_terminals(project.id, folder_location, commands)
                        response = f"PROJECT ACTIVATED: {project.name}\n"
                        response += f"Started or queued {started} terminal(s) for the Projects panel."
                        if failed:
                            response += f" ({failed} failed.)"
                        if diagnostics:
                            response += "\n\nStartup command results:\n" + _format_startup_diagnostics(diagnostics)
                        return response

                return f"PROJECT ACTIVATED: {project.name}\nNo startup instructions configured."

            except Exception as e:
                logger.error(f"Error opening and starting project: {e}", exc_info=True)
                return f"Error: {str(e)}"
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error in open_and_start_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class SwitchProjectInput(BaseModel):
    """Input schema for SwitchProjectTool."""
    project_name: str = Field(
        default="",
        description="The name of the project to switch to. Extract this from the user's request."
    )


class SwitchProjectTool(BaseTool):
    """Tool for switching to a different project (activate it)."""

    name: str = "switch_project"
    description: str = """Switch to a different project by name. This activates the project and loads its context.

    IMPORTANT: You MUST extract the project name from the user's request and pass it as project_name.
    
    Examples:
    - User says "Switch to project Tensology" -> project_name="Tensology"
    - User says "I'm working on Merrypak" -> project_name="Merrypak"
    - User says "Work on the API project" -> project_name="API"
    """
    args_schema: type[BaseModel] = SwitchProjectInput
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        """Get triggers for switch project."""
        return [
            "switch to project", "work on project", "working on project",
            "switch project", "activate project", "use project",
            "i'm working on", "im working on"
        ]

    def _run(self, project_name: str = "", **kwargs) -> str:
        """Execute switch project action."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.agent.services.rag.project import activate_project
            from difflib import SequenceMatcher

            def similarity(a: str, b: str) -> float:
                """Calculate similarity ratio between two strings."""
                return SequenceMatcher(None, a.lower(), b.lower()).ratio()

            # Use project_name directly - LLM should have extracted it
            search_name = project_name.strip() if project_name else ""
            logger.info("SwitchProject called with project_name='%s'", project_name)

            session = get_session()
            try:
                project = None
                
                # Try to find by ID if it looks like a number
                if search_name and search_name.isdigit():
                    project_id = int(search_name)
                    project = session.query(Project).filter(Project.id == project_id).first()
                    if project:
                        logger.info("SwitchProject found by ID: %s", project.name)

                # Search for project by name with fuzzy matching
                if search_name and not project:
                    all_projects = session.query(Project).all()
                    logger.info("SwitchProject found %s projects: %s", len(all_projects), [p.name for p in all_projects])
                    best_match = None
                    best_score = 0.0
                    
                    for p in all_projects:
                        # Check project name similarity
                        name_score = similarity(search_name, p.name)
                        logger.debug("SwitchProject '%s' vs '%s': score=%.2f", search_name, p.name, name_score)
                        logger.debug(f"  Comparing '{search_name}' to project '{p.name}': score={name_score:.2f}")
                        if name_score > best_score:
                            best_score = name_score
                            best_match = p
                        
                        # Check trigger words similarity
                        try:
                            trigger_words = json.loads(p.additional_trigger_words) if p.additional_trigger_words else []
                            for word in trigger_words:
                                word_score = similarity(search_name, word)
                                if word_score > best_score:
                                    best_score = word_score
                                    best_match = p
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass

                    logger.debug("SwitchProject best match: '%s' (score: %.2f)", best_match.name if best_match else None, best_score)
                    logger.info(f"Fuzzy matching results: best_match='{best_match.name if best_match else None}', best_score={best_score:.2f}")
                    
                    # Accept match if score is above threshold
                    if best_match and best_score >= 0.7:
                        project = best_match
                        logger.info(f"Fuzzy matched '{search_name}' to project '{project.name}' (score: {best_score:.2f})")
                    elif best_match and best_score >= 0.5:
                        # Lower threshold but use it
                        project = best_match
                        logger.warning(f"Weak fuzzy match '{search_name}' to project '{best_match.name}' (score: {best_score:.2f})")

                # Try trigger words as exact/substring match (fallback)
                if not project and search_name:
                    all_projects = session.query(Project).all()
                    for p in all_projects:
                        try:
                            trigger_words = json.loads(p.additional_trigger_words) if p.additional_trigger_words else []
                            for word in trigger_words:
                                if search_name.lower() == word.lower() or search_name.lower() in word.lower() or word.lower() in search_name.lower():
                                    project = p
                                    logger.info(f"Matched trigger word '{word}' to project '{project.name}'")
                                    break
                            if project:
                                break
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass

                if not project:
                    # List available projects to help
                    all_projects = session.query(Project).all()
                    if all_projects:
                        project_list = ", ".join([p.name for p in all_projects])
                        return f"Project not found. Available projects: {project_list}"
                    return "No projects found. Create a project first in the Projects Manager."

                # Activate project
                result = activate_project(project.id)

                if result.get('success'):
                    logger.info(f"Switched to project: {project.name} (ID: {project.id})")

                    response = f"PROJECT ACTIVATED: {project.name}\n"
                    if project.folder_location:
                        response += f"Folder: {project.folder_location}\n"
                    
                    # Instructions for LLM behavior
                    response += "\n---\n"
                    response += "INSTRUCTIONS FOR RESPONSE:\n"
                    response += "- Confirm the project is now active\n"
                    response += "- Ask: 'What changes would you like to make?' or similar\n"
                    response += "- DO NOT presume specific actions\n"
                    response += "- Optionally mention: 'Say deactivate project mode to switch away'\n"

                    return response
                else:
                    return f"Error activating project: {result.get('error')}"

            except Exception as e:
                logger.error(f"Error switching project: {e}", exc_info=True)
                return f"Error switching project: {str(e)}"
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error in switch_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class QueryCurrentProjectTool(BaseTool):
    """Tool for querying information about the currently active project."""

    name: str = "query_current_project"
    description: str = """ALWAYS use this tool when the user asks what project they are working on/with.

    Triggers (MUST use this tool for these):
    - "What project am I working on?"
    - "What project am I working with?"
    - "Show me the current project"
    - "What project is active?"
    - "Which project am I on?"
    - "Tell me about the current project"
    - "What's the active project?"

    Returns: Brief response with project name, description (if available), and asks what they'd like to do with it.
    This signals that the conversation context should now listen for project instructions.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        """Get triggers for query current project."""
        return [
            "what project", "current project", "active project",
            "which project", "show me the project"
        ]

    def _run(self, text: str = "", **kwargs) -> str:
        """Execute query current project action."""
        try:
            from distr.core.agent.services.rag.project import get_active_project

            project = get_active_project()

            if not project:
                return "No project is currently active. Say 'switch to project <name>' to activate one."

            # Brief, conversational response
            response = f"You're working on **{project['name']}**"

            # Add description if available
            if project.get('description'):
                response += f" - {project['description']}"

            response += ".\n\nWhat would you like to do with it today?"

            return response

        except Exception as e:
            logger.error(f"Error in query_current_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class DeactivateProjectTool(BaseTool):
    """Tool for deactivating the current project to stop receiving project context."""

    name: str = "deactivate_project"
    description: str = """Deactivate the current project when the user says they're not working on it anymore.

    This tool:
    1. Sets the active project's in_use flag to False
    2. Clears project context from memory
    3. Stops injecting project context into the conversation

    After deactivation, the user's messages will no longer be treated as project instructions.

    Triggers (use this tool for these):
    - "I'm not working on the project"
    - "I'm not working with the project"
    - "Stop working on this project"
    - "Deactivate the project"
    - "I'm done with this project"
    - "Turn off the project"
    - "Disable the project"

    Returns: Confirmation that the project has been deactivated.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        """Get triggers for deactivate project."""
        return [
            "not working on", "not working with", "stop working on",
            "deactivate project", "done with project", "turn off project",
            "disable project", "stop project"
        ]

    def _run(self, text: str = "", **kwargs) -> str:
        """Execute deactivate project action."""
        try:
            from distr.core.agent.services.rag.project import get_active_project, deactivate_project

            # Get current project
            project = get_active_project()

            if not project:
                return "No project is currently active."

            project_id = project['id']
            project_name = project['name']

            # Deactivate the project
            result = deactivate_project(project_id)

            if result.get('success'):
                logger.info(f"Deactivated project: {project_name} (ID: {project_id})")
                return f"Okay, you're no longer working on **{project_name}**. Project context has been deactivated."
            else:
                return f"Error deactivating project: {result.get('error')}"

        except Exception as e:
            logger.error(f"Error in deactivate_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class CreateProjectFromFolderTool(BaseTool):
    """Tool for creating a new project from a dropped folder."""

    name: str = "create_project_from_folder"
    description: str = """Create a new project from a folder path. Used when user drags a folder and says it's a project.

    Usage:
    - User drops folder and says "This is the MyApp project"
    - User drops folder and says "Make this a project called API"
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        """Get triggers for create project from folder."""
        return [
            "this is a project", "this is the project", "make this a project",
            "create project from", "this folder is"
        ]

    def _run(self, folder_path: str, project_name: str = "", description: str = "", **kwargs) -> str:
        """Execute create project from folder action."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.signals import signal_manager
            from distr.core.agent.services.rag.project import activate_project

            if not os.path.exists(folder_path):
                return f"Error: Folder not found: {folder_path}"

            if not project_name:
                # Use folder name as project name
                project_name = os.path.basename(folder_path)

            session = get_session()
            try:
                # Check if project already exists
                existing = session.query(Project).filter(
                    (Project.name == project_name) | (Project.folder_location == folder_path)
                ).first()

                if existing:
                    return f"A project already exists with this name or folder: {existing.name}"

                # Create new project
                new_project = Project(
                    name=project_name,
                    description=description,
                    folder_location=folder_path,
                    additional_trigger_words="[]",
                    in_use=False,
                    created_date=datetime.utcnow(),
                    modified_date=datetime.utcnow()
                )
                session.add(new_project)
                session.commit()
                project_id = new_project.id

                logger.info(f"Created project from folder: {project_name} (ID: {project_id})")

                # Activate the project
                activate_result = activate_project(project_id)

                if activate_result.get('success'):
                    response = f"Created and activated project: {project_name}"
                    response += f"\nFolder: {folder_path}"
                    response += f"\n\nProject is now active. Any work instructions will create tickets in:"
                    response += f"\n{folder_path}/.tickets/"
                    return response
                else:
                    return f"Project created but activation failed: {activate_result.get('error')}"

            except Exception as e:
                session.rollback()
                logger.error(f"Error creating project from folder: {e}", exc_info=True)
                return f"Error creating project: {str(e)}"
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error in create_project_from_folder tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class AddFilesToProjectTool(BaseTool):
    """Tool for adding dropped files to the active project."""

    name: str = "add_files_to_project"
    description: str = """Add files to the currently active project. Used when user drops files and says they're for the project.

    Usage:
    - User drops files and says "These are for the project"
    - User drops files and says "Add these to the current project"
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        """Get triggers for add files to project."""
        return [
            "for the project", "to the project", "add to project",
            "these are for", "add these to"
        ]

    def _run(self, file_paths: list, description: str = "", **kwargs) -> str:
        """Execute add files to project action."""
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import ProjectFile
            from distr.core.signals import signal_manager
            from distr.core.agent.services.rag.project import get_active_project, reindex_project

            # Get active project
            project = get_active_project()
            if not project:
                return "No project is currently active. Switch to a project first with 'switch to project <name>'"

            project_id = project['id']

            # Ensure file_paths is a list
            if isinstance(file_paths, str):
                file_paths = [file_paths]

            session = get_session()
            try:
                added_files = []

                for file_path in file_paths:
                    if not os.path.exists(file_path):
                        logger.warning(f"File not found: {file_path}")
                        continue

                    # Check if file already in project
                    existing = session.query(ProjectFile).filter(
                        ProjectFile.project_id == project_id,
                        ProjectFile.file_path == file_path
                    ).first()

                    if existing:
                        logger.info(f"File already in project: {file_path}")
                        continue

                    # Add file to project
                    filename = os.path.basename(file_path)
                    project_file = ProjectFile(
                        project_id=project_id,
                        filename=filename,
                        description=description,
                        file_path=file_path,
                        created_date=datetime.utcnow(),
                        modified_date=datetime.utcnow()
                    )
                    session.add(project_file)
                    added_files.append(filename)

                session.commit()

                if not added_files:
                    return "No new files were added (they may already be in the project)."

                logger.info(f"Added {len(added_files)} files to project {project['name']}")

                # Re-index project
                reindex_result = reindex_project(project_id)

                response = f"Added {len(added_files)} file(s) to project {project['name']}:"
                for filename in added_files:
                    response += f"\n- {filename}"

                if reindex_result.get('success'):
                    response += f"\n\nRe-indexed project with {reindex_result.get('files_indexed', 0)} total files."

                return response

            except Exception as e:
                session.rollback()
                logger.error(f"Error adding files to project: {e}", exc_info=True)
                return f"Error adding files: {str(e)}"
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error in add_files_to_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class CreateProjectTicketTool(BaseTool):
    """Tool for creating a work ticket in the active project's .tickets folder."""

    name: str = "create_project_ticket"
    description: str = """Create a work ticket/task in the active project. ALWAYS pass the user's instruction.

    REQUIRED PARAMETERS:
    - instruction: The full text of what the user wants done (string). Pass the user's exact words or a clear summary.

    OPTIONAL PARAMETERS:
    - title: Short title for the ticket (string). Auto-generated if not provided.
    - context: Additional context (string).

    IMPORTANT: When user says "create a ticket for X" or describes work they want done, call this tool with:
    instruction="<the full description of the work>"

    Example: User says "change the search background from green to dark blue"
    Call: create_project_ticket(instruction="change the search background from green to dark blue")

    DO NOT ask clarifying questions. Just create the ticket with whatever information is provided.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        """Get triggers for create project ticket.
        
        Only 'tell cursor' is a strong trigger. Other triggers are for
        code-change instructions when a project is active — but the word
        'ticket' on its own should route to the ticket board tool, not here.
        """
        return [
            "tell cursor",
        ]

    def _run(self, text: str = "", instruction: str = "", title: str = "", context: str = "", **kwargs) -> str:
        """Execute create project ticket action."""
        try:
            from distr.core.agent.services.rag.project import get_active_project

            # Use text or instruction (whichever is provided)
            instruction_text = instruction or text

            if not instruction_text:
                return "Error: No instruction provided for the ticket."

            # Get active project
            project = get_active_project()
            if not project:
                return "No project is currently active. Switch to a project first to create tickets."

            if not project.get('folder_location'):
                return f"Project {project['name']} has no folder location set. Cannot create ticket."

            # Ensure .tickets folder exists
            tickets_folder = os.path.join(project['folder_location'], '.tickets')
            os.makedirs(tickets_folder, exist_ok=True)

            # Generate ticket filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ticket_filename = f"ticket_{timestamp}.md"
            ticket_path = os.path.join(tickets_folder, ticket_filename)

            # Extract title if not provided
            if not title:
                # Try to extract from instruction
                if instruction_text.lower().startswith("tell cursor"):
                    title = instruction_text.replace("tell cursor", "").strip()
                    title = title.lstrip("to ")
                else:
                    # Take first sentence or first 50 chars
                    title = instruction_text.split('.')[0][:50]

            # Clean up title
            title = title.strip().capitalize()
            if not title.endswith('.'):
                title = title.rstrip('.')

            # Build ticket content
            ticket_content = f"""---
id: ticket_{timestamp}
title: {title}
project: {project['name']}
created: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
status: open
---

## Description
{instruction_text}

## Requirements
<!-- Extract specific requirements from the instruction -->

## Context
- **Project:** {project['name']} (ID: {project['id']})
- **Folder:** {project['folder_location']}
{f"- **Additional Context:** {context}" if context else ""}

## Related Files
<!-- List any relevant files mentioned or discovered -->

## Conversation Context
<!-- Relevant excerpts from the conversation -->

---
*Auto-generated by DecisionsAI*
"""

            # Write ticket to file
            try:
                meta_header = _build_decisions_meta()
                if meta_header:
                    ticket_content = meta_header + ticket_content
                with open(ticket_path, 'w', encoding='utf-8') as f:
                    f.write(ticket_content)

                logger.info(f"Created ticket: {ticket_path}")

                response = f"Created work ticket in project {project['name']}:"
                response += f"\n\n**File:** {ticket_path}"
                response += f"\n**Title:** {title}"
                response += f"\n\nYou can now use this ticket with Cursor or other editors to implement the feature."

                return response

            except Exception as e:
                logger.error(f"Error writing ticket file: {e}", exc_info=True)
                return f"Error writing ticket: {str(e)}"

        except Exception as e:
            logger.error(f"Error in create_project_ticket tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class OpenProjectTool(BaseTool):
    """Tool for opening a project folder in Cursor or VS Code."""

    name: str = "open_project"
    description: str = """Open the current project folder in Cursor or VS Code.

    This tool opens the active project's folder in Cursor (or VS Code if Cursor is not available).
    It does NOT create any startup files - it only opens the editor.

    Triggers (use this tool for these):
    - "Open the project"
    - "Open this project"
    - "Open the project folder"
    - "Open project in Cursor"
    - "Open project in VS Code"

    Returns: Confirmation of which editor was opened.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        """Get triggers for open project."""
        return [
            "open the project", "open this project", "open project",
            "open project folder", "open in cursor", "open in vscode",
            "open in vs code"
        ]

    def _run(self, text: str = "", **kwargs) -> str:
        """Execute open project action."""
        try:
            from distr.core.agent.services.rag.project import get_active_project

            project = get_active_project()

            if not project:
                return "Error: No project is currently active. Say 'switch to project <name>' to activate one first."

            # Check if project has a folder location
            if not project.get('folder_location'):
                return f"Error: Project '{project['name']}' does not have a folder location set. Please set the folder location in the Projects Manager first."

            folder_location = project['folder_location']

            # Open project folder in Cursor or VS Code
            editor_opened = False
            editor_used = None

            # Try Cursor first
            if shutil.which('cursor'):
                try:
                    subprocess.run(['cursor', folder_location], check=False)
                    editor_opened = True
                    editor_used = "Cursor"
                    logger.info(f"Opened project folder in Cursor: {folder_location}")
                except Exception as e:
                    logger.warning(f"Failed to open Cursor: {e}")

            # Fall back to VS Code if Cursor not available
            if not editor_opened and shutil.which('code'):
                try:
                    subprocess.run(['code', folder_location], check=False)
                    editor_opened = True
                    editor_used = "Visual Studio Code"
                    logger.info(f"Opened project folder in VS Code: {folder_location}")
                except Exception as e:
                    logger.warning(f"Failed to open VS Code: {e}")

            if editor_opened:
                return f"Opened project '{project['name']}' in {editor_used}\n\nFolder: {folder_location}"
            else:
                return f"Error: Could not find Cursor or VS Code in system PATH.\n\nPlease install Cursor or VS Code and make sure the command is available in your PATH.\n\nProject folder: {folder_location}"

        except Exception as e:
            logger.error(f"Error in open_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class CreateTicketAndOpenProjectTool(BaseTool):
    """Create a project ticket and open the project in Cursor/VS Code."""

    name: str = "create_ticket_and_open_project"
    description: str = """Create a work ticket in the active project's `.tickets` folder, then open that project in Cursor.

    Use this when the user wants one command to both:
    1) capture work as a ticket file, and
    2) jump into the project in Cursor/VS Code.

    REQUIRED PARAMETERS:
    - instruction: Work request to put in the ticket.

    OPTIONAL PARAMETERS:
    - title: Optional ticket title override.
    - context: Extra context to include in ticket body.
    - open_editor: Defaults to true. Set false to only create the ticket.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        return [
            "create ticket and open project",
            "tell cursor and open project",
            "make a ticket and open cursor",
            "create ticket then open cursor",
        ]

    def _run(
        self,
        text: str = "",
        instruction: str = "",
        title: str = "",
        context: str = "",
        open_editor: bool = True,
        **kwargs,
    ) -> str:
        try:
            instruction_text = (instruction or text or "").strip()
            if not instruction_text:
                return "Error: No instruction provided."

            ticket_tool = CreateProjectTicketTool(event_queue=self.event_queue)
            ticket_result = ticket_tool._run(
                instruction=instruction_text,
                title=title,
                context=context,
            )
            if isinstance(ticket_result, str) and (
                ticket_result.startswith("Error:")
                or ticket_result.startswith("No project")
            ):
                return ticket_result

            if not open_editor:
                return ticket_result

            open_tool = OpenProjectTool(event_queue=self.event_queue)
            open_result = open_tool._run()
            return f"{ticket_result}\n\n{open_result}"
        except Exception as e:
            logger.error(f"Error in create_ticket_and_open_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"


class SelfUpdateViaCursorTool(BaseTool):
    """Developer-only utility to let Decisions self-update through Cursor tickets."""

    name: str = "self_update_via_cursor"
    description: str = """Developer utility: create a self-update ticket and open the Decisions project in Cursor.

    Safety gates (all required):
    - Active project exists with folder_location
    - Project root contains `.env`
    - `.env` has `DEBUG=True`
    - Cursor CLI is available in PATH

    If any gate fails, returns a safe no-op message and does nothing.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    @staticmethod
    def _is_debug_enabled(project_root: str) -> bool:
        env_path = os.path.join(project_root, ".env")
        if not os.path.isfile(env_path):
            return False
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip().upper() == "DEBUG":
                        value = v.strip().strip('"').strip("'").upper()
                        return value in {"TRUE", "1", "YES", "ON"}
        except Exception:
            return False
        return False

    def _run(self, instruction: str = "", text: str = "", append_mode: bool = True, **kwargs) -> str:
        try:
            from distr.core.agent.services.rag.project import get_active_project

            request_text = (instruction or text or "").strip()
            if not request_text:
                return "Error: No self-update instruction provided."

            project = get_active_project()
            if not project:
                return "Self-update disabled: no active project."

            project_root = (project.get("folder_location") or "").strip()
            if not project_root or not os.path.isdir(project_root):
                return "Self-update disabled: active project folder is missing."

            env_path = os.path.join(project_root, ".env")
            if not os.path.isfile(env_path):
                return "Self-update disabled: `.env` not found at project root."

            if not self._is_debug_enabled(project_root):
                return "Self-update disabled: `.env` must contain `DEBUG=True`."

            if not shutil.which("cursor"):
                return "Self-update disabled: Cursor CLI (`cursor`) is not available in PATH."

            payload = request_text
            if append_mode:
                payload = (
                    "Continue the CURRENT running ticket/session if one exists. "
                    "Do NOT start a brand-new session unless necessary.\n\n"
                    + request_text
                )

            create_tool = CreateProjectTicketTool(event_queue=self.event_queue)
            ticket_result = create_tool._run(instruction=payload, context="Self-update via developer utility")
            if ticket_result.startswith("Error:") or ticket_result.startswith("No project"):
                return ticket_result

            open_tool = OpenProjectTool(event_queue=self.event_queue)
            open_result = open_tool._run()
            return (
                "Self-update utility executed.\n\n"
                + ticket_result
                + "\n\n"
                + open_result
            )
        except Exception as e:
            logger.error("Error in self_update_via_cursor tool: %s", e, exc_info=True)
            return f"Error: {str(e)}"


class StartProjectTool(BaseTool):
    """Tool for starting a project by opening it in an editor and running startup commands in Terminal."""

    name: str = "start_project"
    description: str = """Start the current project by opening it in Cursor/VS Code and launching startup commands in Terminal.

    This tool:
    1. Opens the project folder in Cursor (or VS Code if Cursor not available)
    2. Reads the startup_instructions from the active project (one shell command per non-empty line)
    3. Runs each command in a new Terminal tab or window (macOS Terminal.app; see implementation for Linux/Windows)

    Triggers (use this tool for these):
    - "Start the project"
    - "Start this project"
    - "Open and start the project"
    - "Launch the project"
    - "Boot up the project"
    - "Initialize the project"

    Returns: Confirmation of editor opened and terminals launched.
    """
    event_queue: Any = Field(default=None, exclude=True)

    def __init__(self, event_queue=None, **data):
        super().__init__(**data)
        if event_queue:
            self.event_queue = event_queue

    def get_triggers(self) -> list[str]:
        """Get triggers for start project."""
        return [
            "start the project", "start this project", "start project",
            "launch the project", "launch project", "boot up the project",
            "initialize the project", "run the project"
        ]

    def _run(self, text: str = "", **kwargs) -> str:
        """Execute start project action."""
        try:
            from distr.core.agent.services.rag.project import get_active_project

            # Extract project name from text (e.g. "start project auctionnow" → "auctionnow")
            search_name = ""
            if text:
                import re
                text_lower = text.lower().strip().rstrip('.')
                # Strip common prefixes to get the project name
                for prefix in ['start project', 'start the project', 'open project', 'open and start project',
                               'launch project', 'run project', 'start']:
                    if text_lower.startswith(prefix):
                        search_name = text_lower[len(prefix):].strip()
                        break
                if not search_name:
                    search_name = text_lower

            # If a project name was specified, find it by fuzzy matching
            if search_name:
                from distr.core.db import get_session as get_db_session
                from distr.core.db.projects import Project
                from difflib import SequenceMatcher

                with get_db_session() as session:
                    all_projects = session.query(Project).all()
                    best_match = None
                    best_score = 0.0
                    for p in all_projects:
                        score = SequenceMatcher(None, search_name, p.name.lower()).ratio()
                        if score > best_score:
                            best_score = score
                            best_match = p
                        # Also check trigger words
                        try:
                            triggers = json.loads(p.additional_trigger_words) if p.additional_trigger_words else []
                            for tw in triggers:
                                tw_score = SequenceMatcher(None, search_name, tw.lower()).ratio()
                                if tw_score > best_score:
                                    best_score = tw_score
                                    best_match = p
                        except (json.JSONDecodeError, ValueError, TypeError):
                            pass

                    if best_match and best_score >= 0.5:
                        logger.info("StartProject: fuzzy matched '%s' to project '%s' (score: %.2f)", search_name, best_match.name, best_score)
                        # Delegate to OpenAndStartProjectTool with the matched name
                        from distr.core.agent.tools.system.project_tools import OpenAndStartProjectTool
                        opener = OpenAndStartProjectTool()
                        return opener._run(project_name=best_match.name)

            project = get_active_project()

            if not project:
                return "Error: No project is currently active. Say 'switch to project <name>' to activate one first."

            # Check if project has a folder location
            if not project.get('folder_location'):
                return f"Error: Project '{project['name']}' does not have a folder location set. Please set the folder location in the Projects Manager first."

            # Check if project has startup instructions
            startup_instructions = project.get('startup_instructions', '').strip()
            if not startup_instructions:
                return f"Project '{project['name']}' does not have any startup instructions configured.\n\nPlease add startup instructions in the Projects Manager (Advanced tab) first."
            if not _parse_startup_command_lines(startup_instructions):
                return f"Project '{project['name']}' startup instructions have no runnable commands (add one shell command per line, or remove # comments only)."

            folder_location = project['folder_location']
            commands = _parse_startup_command_lines(startup_instructions)
            started, failed, diagnostics = _start_inapp_terminals(project_id=project['id'],
                                                                  folder=folder_location,
                                                                  commands=commands)
            response = f"PROJECT STARTED: {project['name']}\n"
            response += f"Started or queued {started} terminal(s) for the Projects panel."
            if failed:
                response += f" ({failed} failed.)"
            if diagnostics:
                response += "\n\nStartup command results:\n" + _format_startup_diagnostics(diagnostics)
            return response

        except Exception as e:
            logger.error(f"Error in start_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"
