"""
Project RAG Service - Manages project-specific RAG indexes

Each project gets its own persistent RAG index at:
~/.decisionsai/project_indexes/project_{id}_{slugified_name}/

When switching projects:
- Previous project RAG is cleared from memory (but persisted)
- New project RAG is loaded into memory
- Chat history remains intact
"""

import os
import re
import logging
import pathspec
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Global state
_project_rag_services = {}  # project_id → RAG service
_active_project_id = None


def slugify(text: str) -> str:
    """Convert text to URL-safe slug"""
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text.strip('-')


def get_project_index_path(project_id: int, project_name: str) -> str:
    """Get the index path for a project"""
    home_dir = os.path.expanduser("~")
    slug = slugify(project_name)
    return os.path.join(home_dir, ".decisionsai", "project_indexes", f"project_{project_id}_{slug}")


def parse_gitignore(folder_path: str) -> Optional[pathspec.PathSpec]:
    """
    Parse .gitignore file and return a PathSpec for matching.

    Args:
        folder_path: Root folder to look for .gitignore

    Returns:
        PathSpec object or None if no .gitignore found
    """
    gitignore_path = os.path.join(folder_path, ".gitignore")
    if not os.path.exists(gitignore_path):
        return None

    try:
        with open(gitignore_path, 'r', encoding='utf-8') as f:
            patterns = f.readlines()
        # Filter out comments and empty lines
        patterns = [p.strip() for p in patterns if p.strip() and not p.startswith('#')]
        return pathspec.PathSpec.from_lines('gitwildmatch', patterns)
    except Exception as e:
        logger.warning(f"Failed to parse .gitignore at {gitignore_path}: {e}")
        return None


def should_index_file(file_path: str, folder_root: str, gitignore_spec: Optional[pathspec.PathSpec]) -> bool:
    """
    Check if a file should be indexed based on gitignore rules.

    Args:
        file_path: Full path to file
        folder_root: Root folder (for relative path calculation)
        gitignore_spec: PathSpec from .gitignore

    Returns:
        True if file should be indexed, False otherwise
    """
    # Always exclude these patterns
    ALWAYS_EXCLUDE = [
        '__pycache__', '.git', '.svn', '.hg', 'node_modules',
        'venv', '.venv', 'env', '.env', 'dist', 'build',
        '.pytest_cache', '.mypy_cache', '.tox', 'coverage',
        '.DS_Store', 'Thumbs.db'
    ]

    # Check always exclude patterns
    for pattern in ALWAYS_EXCLUDE:
        if pattern in file_path:
            return False

    # Check gitignore
    if gitignore_spec:
        try:
            # Get relative path from root
            rel_path = os.path.relpath(file_path, folder_root)
            if gitignore_spec.match_file(rel_path):
                return False
        except Exception as e:
            logger.debug(f"Error checking gitignore for {file_path}: {e}")

    return True


def get_indexable_extensions() -> List[str]:
    """Get list of file extensions that should be indexed"""
    return [
        # Code
        '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.h', '.hpp',
        '.go', '.rs', '.rb', '.php', '.swift', '.kt', '.scala', '.r', '.m',
        '.cs', '.vb', '.sh', '.bash', '.zsh', '.fish',
        # Markup/Config
        '.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.xml', '.html', '.css',
        '.scss', '.sass', '.less', '.ini', '.cfg', '.conf',
        # Docs
        '.pdf', '.docx', '.doc', '.rtf',
        # Data
        '.csv', '.sql',
        # Other
        '.gitignore', '.dockerignore', 'Dockerfile', 'Makefile', 'README'
    ]


def scan_project_folder(folder_path: str, gitignore_spec: Optional[pathspec.PathSpec] = None) -> List[str]:
    """
    Recursively scan project folder for indexable files.

    Args:
        folder_path: Root folder to scan
        gitignore_spec: Optional PathSpec from .gitignore

    Returns:
        List of file paths to index
    """
    indexable_files = []
    indexable_exts = get_indexable_extensions()

    try:
        for root, dirs, files in os.walk(folder_path):
            # Filter directories in-place to avoid walking excluded dirs
            dirs[:] = [d for d in dirs if should_index_file(os.path.join(root, d), folder_path, gitignore_spec)]

            for file in files:
                file_path = os.path.join(root, file)

                # Check if file should be indexed
                if not should_index_file(file_path, folder_path, gitignore_spec):
                    continue

                # Check extension
                _, ext = os.path.splitext(file)
                if ext.lower() in indexable_exts or file in ['README', 'Makefile', 'Dockerfile']:
                    indexable_files.append(file_path)

        logger.info(f"Found {len(indexable_files)} indexable files in {folder_path}")
        return indexable_files

    except Exception as e:
        logger.error(f"Error scanning folder {folder_path}: {e}")
        return []


def get_project_rag_service(project_id: int, project_name: str, model_name: str = "qwen3:8b", embedding_model: str = "nomic-embed-text") -> Optional[Any]:
    """
    Get or create a project-specific RAG service.

    Args:
        project_id: Project database ID
        project_name: Project name (for slugified path)
        model_name: LLM model name
        embedding_model: Embedding model name

    Returns:
        RAG service instance or None
    """
    global _project_rag_services

    if project_id in _project_rag_services:
        return _project_rag_services[project_id]

    # Detect if using OpenAI model
    from distr.core.llm_factory import is_openai_model as _is_openai
    _openai_model = _is_openai(model_name)

    # Get OpenAI API key if needed
    openai_api_key = None
    if _openai_model:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        openai_api_key = settings.get('openai_key') or settings.get('openai_api_key')
        if not openai_api_key:
            logger.warning("OpenAI model specified but no API key found in settings")

    try:
        from distr.core.agent.services.rag.indexing import LlamaIndexRAGService

        # Create project index directory
        index_path = get_project_index_path(project_id, project_name)
        os.makedirs(index_path, exist_ok=True)

        _project_rag_services[project_id] = LlamaIndexRAGService(
            model_name=model_name,
            embedding_model=embedding_model,
            index_path=index_path,
            persist_dir=index_path,
            openai_api_key=openai_api_key
        )

        logger.info(f"Created project RAG service for project {project_id} ({project_name}) at {index_path}")
        return _project_rag_services[project_id]

    except ImportError as e:
        logger.warning(f"LlamaIndex not available: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to create project RAG service: {e}")
        return None


def index_project(project_id: int, model_name: str = "qwen3:8b") -> Dict[str, Any]:
    """
    Index all project files and context items.

    Args:
        project_id: Project database ID
        model_name: LLM model name

    Returns:
        Dict with indexing results
    """
    from distr.core.db import get_session
    from distr.core.db.projects import Project, ProjectContextItem, ProjectFile

    session = get_session()
    try:
        project = session.query(Project).get(project_id)
        if not project:
            return {"success": False, "error": f"Project {project_id} not found"}

        # Get or create RAG service
        rag_service = get_project_rag_service(project_id, project.name, model_name)
        if not rag_service:
            return {"success": False, "error": "RAG service not available"}

        total_indexed = 0
        files_to_index = []

        # 1. Index ProjectContextItems as documents
        context_items = session.query(ProjectContextItem).filter(
            ProjectContextItem.project_id == project_id
        ).all()

        for item in context_items:
            # Create a temporary file with the context content
            temp_dir = os.path.join(os.path.expanduser("~"), ".decisionsai", "temp_context")
            os.makedirs(temp_dir, exist_ok=True)
            temp_file = os.path.join(temp_dir, f"context_{item.id}.txt")

            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(f"# {item.title}\n\n{item.content}")

            files_to_index.append(temp_file)

        logger.info(f"Prepared {len(context_items)} context items for indexing")

        # 2. Index ProjectFiles
        project_files = session.query(ProjectFile).filter(
            ProjectFile.project_id == project_id
        ).all()

        for pf in project_files:
            if os.path.exists(pf.file_path):
                files_to_index.append(pf.file_path)
            else:
                logger.warning(f"ProjectFile not found: {pf.file_path}")

        logger.info(f"Found {len(project_files)} project files to index")

        # 3. Scan project folder (if set)
        if project.folder_location and os.path.exists(project.folder_location):
            # Parse gitignore
            gitignore_spec = parse_gitignore(project.folder_location)

            # Scan folder
            folder_files = scan_project_folder(project.folder_location, gitignore_spec)
            files_to_index.extend(folder_files)

            logger.info(f"Found {len(folder_files)} files in project folder")

        # Index all files
        if files_to_index:
            result = rag_service.index_files(files_to_index)
            total_indexed = result.get('files_indexed', 0)
            logger.info(f"Indexed {total_indexed} files for project {project.name}")

        return {
            "success": True,
            "project_id": project_id,
            "project_name": project.name,
            "files_indexed": total_indexed,
            "context_items": len(context_items),
            "project_files": len(project_files)
        }

    except Exception as e:
        logger.error(f"Error indexing project {project_id}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        session.close()


def activate_project(project_id: int, model_name: str = "qwen3:8b") -> Dict[str, Any]:
    """
    Activate a project:
    1. Set project.in_use = True (set all others to False)
    2. Load/create project RAG service
    3. Index all files if not already indexed
    4. Emit UI update signal

    Args:
        project_id: Project database ID
        model_name: LLM model name

    Returns:
        Dict with activation results
    """
    global _active_project_id
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.signals import signal_manager

    session = get_session()
    try:
        # Deactivate all projects
        session.query(Project).update({Project.in_use: False})

        # Activate target project
        project = session.query(Project).get(project_id)
        if not project:
            return {"success": False, "error": f"Project {project_id} not found"}

        project.in_use = True
        project.modified_date = datetime.utcnow()
        session.commit()

        # Clear previous project from memory (if different)
        if _active_project_id and _active_project_id != project_id:
            old_project_id = _active_project_id
            if old_project_id in _project_rag_services:
                logger.info(f"Clearing previous project {old_project_id} from memory")
                del _project_rag_services[old_project_id]

        # Set new active project
        _active_project_id = project_id

        # Get or create RAG service
        rag_service = get_project_rag_service(project_id, project.name, model_name)
        if not rag_service:
            return {"success": False, "error": "RAG service not available"}

        # Check if index exists, if not, index the project
        index_path = get_project_index_path(project_id, project.name)
        needs_indexing = not os.path.exists(os.path.join(index_path, "docstore.json"))

        if needs_indexing:
            logger.info(f"Project {project.name} needs indexing, starting index...")
            index_result = index_project(project_id, model_name)
            if not index_result.get('success'):
                logger.warning(f"Failed to index project: {index_result.get('error')}")

        logger.info(f"Activated project: {project.name} (ID: {project_id})")

        return {
            "success": True,
            "project_id": project_id,
            "project_name": project.name,
            "indexed": needs_indexing
        }

    except Exception as e:
        session.rollback()
        logger.error(f"Error activating project {project_id}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        session.close()


def deactivate_project(project_id: int) -> Dict[str, Any]:
    """
    Deactivate a project:
    1. Set project.in_use = False
    2. Clear from memory (but keep persisted index)

    Args:
        project_id: Project database ID

    Returns:
        Dict with deactivation results
    """
    global _active_project_id
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    session = get_session()
    try:
        project = session.query(Project).get(project_id)
        if not project:
            return {"success": False, "error": f"Project {project_id} not found"}

        project.in_use = False
        project.modified_date = datetime.utcnow()
        session.commit()

        # Clear from memory
        if project_id in _project_rag_services:
            del _project_rag_services[project_id]
            logger.info(f"Cleared project {project_id} from memory")

        if _active_project_id == project_id:
            _active_project_id = None

        logger.info(f"Deactivated project: {project.name} (ID: {project_id})")

        return {
            "success": True,
            "project_id": project_id,
            "project_name": project.name
        }

    except Exception as e:
        session.rollback()
        logger.error(f"Error deactivating project {project_id}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        session.close()


def get_active_project() -> Optional[Dict[str, Any]]:
    """
    Get the currently active project.

    Returns:
        Dict with project info or None
    """
    from distr.core.db import get_session
    from distr.core.db.projects import Project, ProjectContextItem, ProjectFile

    session = get_session()
    try:
        project = session.query(Project).filter(Project.in_use == True).first()
        if not project:
            return None

        # Count context items and files
        context_count = session.query(ProjectContextItem).filter(
            ProjectContextItem.project_id == project.id
        ).count()

        file_count = session.query(ProjectFile).filter(
            ProjectFile.project_id == project.id
        ).count()

        # Parse trigger words
        trigger_words = []
        try:
            import json
            if project.additional_trigger_words:
                trigger_words = json.loads(project.additional_trigger_words)
        except (json.JSONDecodeError, ValueError):
            pass

        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "folder_location": project.folder_location,
            "startup_instructions": project.startup_instructions,
            "trigger_words": trigger_words,
            "context_items_count": context_count,
            "files_count": file_count,
            "created_date": project.created_date.isoformat() if project.created_date else None,
            "modified_date": project.modified_date.isoformat() if project.modified_date else None
        }

    except Exception as e:
        logger.error(f"Error getting active project: {e}", exc_info=True)
        return None
    finally:
        session.close()


def get_active_project_context() -> Optional[str]:
    """
    Get formatted context string for the active project.
    Used for system prompt injection.

    Returns:
        Formatted context string or None
    """
    from distr.core.db import get_session
    from distr.core.db.projects import Project, ProjectContextItem, ProjectFile

    session = get_session()
    try:
        project = session.query(Project).filter(Project.in_use == True).first()
        if not project:
            return None

        # Build context string
        context_parts = []

        context_parts.append(f"## ACTIVE PROJECT: {project.name}")
        if project.description:
            context_parts.append(project.description)

        if project.folder_location:
            context_parts.append(f"\nProject Folder: {project.folder_location}")

        # Add trigger words
        try:
            import json
            if project.additional_trigger_words:
                trigger_words = json.loads(project.additional_trigger_words)
                if trigger_words:
                    context_parts.append(f"Trigger Words: {', '.join(trigger_words)}")
        except (json.JSONDecodeError, ValueError):
            pass

        # Add startup instructions
        if project.startup_instructions and project.startup_instructions.strip():
            context_parts.append("\n### Startup Instructions:")
            context_parts.append("When the user says 'start the project', use the StartProjectTool to generate a STARTUP.md file.")
            commands = [cmd.strip() for cmd in project.startup_instructions.split('\n') if cmd.strip()]
            context_parts.append(f"This project has {len(commands)} startup command(s) configured.")

        # Add context items
        context_items = session.query(ProjectContextItem).filter(
            ProjectContextItem.project_id == project.id
        ).all()

        if context_items:
            context_parts.append("\n### Project Context Items:")
            for item in context_items:
                context_parts.append(f"\n**{item.title}**")
                context_parts.append(item.content)

        # Add file count
        file_count = session.query(ProjectFile).filter(
            ProjectFile.project_id == project.id
        ).count()

        if file_count > 0:
            context_parts.append(f"\n### Project Files:")
            context_parts.append(f"You have access to {file_count} project files via RAG search.")

        # Add folder file count
        if project.folder_location and os.path.exists(project.folder_location):
            gitignore_spec = parse_gitignore(project.folder_location)
            folder_files = scan_project_folder(project.folder_location, gitignore_spec)
            if folder_files:
                context_parts.append(f"Project folder contains {len(folder_files)} indexable files.")

        # Add ticket folder info - CRITICAL INSTRUCTIONS FOR TOOL SELECTION
        if project.folder_location:
            tickets_folder = os.path.join(project.folder_location, ".tickets")
            context_parts.append(f"\n### ⚠️ CRITICAL - WORK INSTRUCTION HANDLING:")
            context_parts.append(f"🚫 DO NOT use execute_code for ANY code/UI changes to this project!")
            context_parts.append(f"✅ ALWAYS use create_project_ticket tool for work instructions.")
            context_parts.append(f"")
            context_parts.append(f"Work instructions include: 'change X', 'fix Y', 'add Z', 'update the...', 'make the...', ")
            context_parts.append(f"'set the background to...', 'modify...', 'implement...', 'create a feature for...'")
            context_parts.append(f"")
            context_parts.append(f"The IDE (Cursor) monitors {tickets_folder} and will implement the changes.")
            context_parts.append(f"Just call create_project_ticket(instruction='<user's exact request>').")
            context_parts.append(f"DO NOT try to write code yourself. DO NOT ask clarifying questions.")

        return "\n".join(context_parts)

    except Exception as e:
        logger.error(f"Error getting active project context: {e}", exc_info=True)
        return None
    finally:
        session.close()


def query_project_rag(query: str, project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Query the project RAG index.

    Args:
        query: Query string
        project_id: Optional project ID (uses active if not provided)

    Returns:
        Query results or None
    """
    global _active_project_id

    target_project_id = project_id or _active_project_id
    if not target_project_id:
        return None

    if target_project_id not in _project_rag_services:
        return None

    rag_service = _project_rag_services[target_project_id]
    return rag_service.query(query)


def reindex_project(project_id: int, model_name: str = "qwen3:8b") -> Dict[str, Any]:
    """
    Force re-index of a project (clears and rebuilds index).

    Args:
        project_id: Project database ID
        model_name: LLM model name

    Returns:
        Dict with re-indexing results
    """
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    session = get_session()
    try:
        project = session.query(Project).get(project_id)
        if not project:
            return {"success": False, "error": f"Project {project_id} not found"}

        # Clear index
        index_path = get_project_index_path(project_id, project.name)
        if os.path.exists(index_path):
            import shutil
            shutil.rmtree(index_path)
            logger.info(f"Cleared index for project {project.name}")

        # Clear from memory
        if project_id in _project_rag_services:
            del _project_rag_services[project_id]

        # Re-index
        result = index_project(project_id, model_name)

        logger.info(f"Re-indexed project {project.name}: {result}")
        return result

    except Exception as e:
        logger.error(f"Error re-indexing project {project_id}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    finally:
        session.close()
