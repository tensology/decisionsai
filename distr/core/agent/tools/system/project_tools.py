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
from datetime import datetime

logger = logging.getLogger(__name__)


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
            "look at projects", "see the projects", "is it there",
            "does it have", "go look", "check if"
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
    description: str = """Switch to a project by name and immediately start it (open in editor + generate startup file).

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
    4. Generate STARTUP.md with configured startup commands
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

                # Step 1: Activate the project
                activate_result = activate_project(project.id)
                if not activate_result.get('success'):
                    return f"Error activating project: {activate_result.get('error')}"

                logger.info(f"Activated project: {project.name} (ID: {project.id})")

                # Step 2: Open in editor
                folder_location = project.folder_location
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

                # Step 3: Generate STARTUP.md if startup instructions exist
                startup_instructions = project.startup_instructions.strip() if project.startup_instructions else ""
                startup_created = False
                startup_path = None

                if startup_instructions:
                    tickets_folder = os.path.join(folder_location, '.tickets')
                    try:
                        os.makedirs(tickets_folder, exist_ok=True)
                        startup_path = os.path.join(tickets_folder, "STARTUP.md")

                        with open(startup_path, 'w', encoding='utf-8') as f:
                            f.write(startup_instructions)

                        startup_created = True
                        logger.info(f"Created startup file: {startup_path}")
                    except Exception as e:
                        logger.error(f"Error creating startup file: {e}", exc_info=True)

                # Build response - guide LLM to ask about changes, not presume actions
                response = f"PROJECT ACTIVATED: {project.name}\n"
                response += f"Folder: {folder_location}\n"
                
                if editor_opened:
                    response += f"Editor: Opened in {editor_used}\n"
                
                if startup_created:
                    response += f"Startup: Created {startup_path}\n"
                
                # Instructions for LLM behavior
                response += "\n---\n"
                response += "INSTRUCTIONS FOR RESPONSE:\n"
                response += "- Tell the user the project is now open and ready\n"
                response += "- Ask: 'What changes would you like to make?' or similar\n"
                response += "- DO NOT presume specific actions like 'run the website' or 'start the server'\n"
                response += "- DO NOT ask about running/starting specific systems\n"
                response += "- Simply ask what they want to work on\n"
                response += "- Optionally mention: 'If you want to switch away from this project, just say deactivate project mode'\n"

                return response

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
        'ticket' on its own should route to the kanban tool, not here.
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


class StartProjectTool(BaseTool):
    """Tool for starting a project by opening it in an editor and generating startup instructions."""

    name: str = "start_project"
    description: str = """Start the current project by opening it in Cursor/VS Code and creating a simple STARTUP.md file.

    This tool:
    1. Opens the project folder in Cursor (or VS Code if Cursor not available)
    2. Reads the startup_instructions from the active project
    3. Creates a minimal STARTUP.md file in .tickets/ folder with ONLY the startup commands

    The STARTUP.md file contains ONLY bash commands - no extra metadata or ticket information.
    Each line in startup_instructions represents a command that should run in a new terminal tab.

    Triggers (use this tool for these):
    - "Start the project"
    - "Start this project"
    - "Open and start the project"
    - "Launch the project"
    - "Boot up the project"
    - "Initialize the project"

    Returns: Confirmation of editor opened and path to the generated STARTUP.md file.
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

            if not editor_opened:
                logger.warning("Neither Cursor nor VS Code found in system PATH")

            # Ensure .tickets folder exists
            tickets_folder = os.path.join(folder_location, '.tickets')

            try:
                os.makedirs(tickets_folder, exist_ok=True)
            except Exception as e:
                logger.error(f"Error creating .tickets folder: {e}", exc_info=True)
                return f"Error: Could not create .tickets folder at {tickets_folder}: {str(e)}"

            # Generate STARTUP.md filename
            startup_filename = "STARTUP.md"
            startup_path = os.path.join(tickets_folder, startup_filename)

            # Write STARTUP.md - exactly what's in the field, nothing else
            try:
                with open(startup_path, 'w', encoding='utf-8') as f:
                    f.write(startup_instructions)

                logger.info(f"Created startup file: {startup_path}")

                response = f"Started project '{project['name']}':"

                # Add editor info
                if editor_opened:
                    response += f"\n✓ Opened project folder in {editor_used}"
                else:
                    response += f"\n⚠ Could not find Cursor or VS Code in system PATH"

                response += f"\n✓ Created startup file: {startup_path}"

                return response

            except Exception as e:
                logger.error(f"Error writing startup file: {e}", exc_info=True)
                return f"Error writing startup file: {str(e)}"

        except Exception as e:
            logger.error(f"Error in start_project tool: {e}", exc_info=True)
            return f"Error: {str(e)}"
