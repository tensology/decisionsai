"""Sync companion store to repo projection and root redirectors."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .paths import (
    AGENTS_FILE,
    CONTEXT_FILE,
    DECISIONS_FILE,
    HANDOFF_FILE,
    ACTIVE_FILE,
    ROUTER_FILE,
    ROOT_AGENTS_FILE,
    ROOT_CLAUDE_FILE,
    companion_root,
    ensure_projection_dirs,
    projection_root,
)
from .template import (
    projection_agents_md,
    root_agents_redirector,
    root_claude_redirector,
    write_json,
    write_text,
)

logger = logging.getLogger(__name__)

_PROJECTION_FILES = (AGENTS_FILE, ROUTER_FILE, DECISIONS_FILE)
_PROJECTION_MEMORY_FILES = (HANDOFF_FILE, ACTIVE_FILE)


def sync_projection_for_project(project_id: int, *, force: bool = False) -> dict[str, Any]:
    """Copy thin companion subset into project.folder_location/.decisions/."""
    from distr.core.db import get_session
    from distr.core.db.projects import Project

    with get_session() as session:
        project = session.query(Project).filter(Project.id == int(project_id)).first()
        if not project:
            return {"ok": False, "error": "project not found"}
        folder = (project.folder_location or "").strip()
        if not folder:
            return {"ok": False, "error": "project has no folder_location"}
        name = project.name or f"Project {project_id}"
        kanban_board_id = project.kanban_board_id

    folder_path = Path(folder).expanduser()
    if not folder_path.is_dir():
        return {"ok": False, "error": f"folder does not exist: {folder}"}

    companion = companion_root("projects", project_id)
    if not companion.is_dir():
        from .provision import bootstrap_project

        bootstrap_project(project_id)

    proj_root = ensure_projection_dirs(str(folder_path))
    copied: list[str] = []
    for filename in _PROJECTION_FILES:
        src = companion / filename
        if not src.is_file():
            continue
        dst = proj_root / filename
        if force or not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
            if filename == AGENTS_FILE:
                mission = ""
                ctx_path = companion / CONTEXT_FILE
                if ctx_path.is_file():
                    mission = name
                write_text(
                    dst,
                    projection_agents_md(
                        companion_path=str(companion),
                        entity_type="project",
                        name=name,
                        mission=mission,
                    ),
                )
            else:
                shutil.copy2(src, dst)
            copied.append(filename)

    comp_mem = companion / "memory"
    proj_mem = proj_root / "memory"
    proj_mem.mkdir(parents=True, exist_ok=True)
    for filename in _PROJECTION_MEMORY_FILES:
        src = comp_mem / filename
        if src.is_file():
            dst = proj_mem / filename
            if force or not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                shutil.copy2(src, dst)
                copied.append(f"memory/{filename}")

    redirectors = write_repo_redirectors(str(folder_path))
    return {
        "ok": True,
        "project_id": project_id,
        "projection_path": str(proj_root),
        "companion_path": str(companion),
        "copied": copied,
        "redirectors": redirectors,
        "kanban_board_id": kanban_board_id,
    }


def write_repo_redirectors(project_folder: str) -> list[str]:
    """Write AGENTS.md and CLAUDE.md at repo root pointing to .decisions/."""
    root = Path(project_folder).expanduser().resolve()
    if not root.is_dir():
        return []
    written: list[str] = []
    for filename, content in (
        (ROOT_AGENTS_FILE, root_agents_redirector()),
        (ROOT_CLAUDE_FILE, root_claude_redirector()),
    ):
        path = root / filename
        if not path.exists():
            write_text(path, content)
            written.append(filename)
    return written


def update_projection_decisions(project_folder: str, patch: dict[str, Any]) -> None:
    path = projection_root(project_folder) / DECISIONS_FILE
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(patch)
    write_json(path, data)
    companion_id = data.get("project_id")
    if companion_id:
        write_json(companion_root("projects", int(companion_id)) / DECISIONS_FILE, data)
