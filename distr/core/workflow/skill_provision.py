"""Provision bundled skills to project harness folders before/after workflow execution."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SKILLS_DIR = _PROJECT_ROOT / "skills"
_ECC_SKILLS_DIR = _PROJECT_ROOT / "vendor" / "ecc" / "skills"

CLI_TARGETS = {
    "pi": ".pi/skills",
    "cursor": ".cursor/commands",
    "cursor_ide": ".cursor/commands",
    "vscode_ide": ".cursor/commands",
    "claude_code": ".claude/commands",
    "claude": ".claude/commands",
    "codex": ".codex/commands",
    "gemini": ".gemini/commands",
}

_SKILL_RESOURCE_DIRS = ("scripts", "references", "reference")


def _skill_registry():
    from distr.core.skills.registry import SkillRegistry

    return SkillRegistry(local_roots=[_SKILLS_DIR], vendor_roots=[_ECC_SKILLS_DIR]).scan()


def _resolve_skill_dir(skill_id: str) -> Path | None:
    entry = _skill_registry().get(skill_id.strip())
    return entry.path if entry else None


def _backend_skill_target(backend_id: str) -> str:
    bid = _normalized_backend_id(backend_id)
    return CLI_TARGETS.get(bid, CLI_TARGETS["pi"])


def _normalized_backend_id(backend_id: str) -> str:
    from distr.core.project_cli_backends import normalize_backend_id

    return normalize_backend_id(backend_id)


def push_skill_to_project(
    *,
    skill_id: str,
    project_folder: str,
    backend_id: str,
) -> str | None:
    """Push one skill to the harness-specific folder. Returns dest path or None."""
    registry = _skill_registry()
    entry = registry.get(skill_id.strip())
    if not entry:
        return None
    skill_dir = entry.path
    project_path = Path(project_folder).expanduser().resolve()
    if not project_path.is_dir():
        return None
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md = skill_dir / "skill.md"
    if not skill_md.exists():
        return None
    normalized_backend_id = _normalized_backend_id(backend_id)
    target = CLI_TARGETS.get(normalized_backend_id, CLI_TARGETS["pi"])
    target_dir = project_path / target
    target_dir.mkdir(parents=True, exist_ok=True)
    actual_id = entry.canonical_id
    if target == CLI_TARGETS["pi"]:
        dest_skill_dir = target_dir / actual_id
        dest_skill_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_skill_dir / "SKILL.md"
        shutil.copy2(skill_md, dest_file)
        for subdir_name in _SKILL_RESOURCE_DIRS:
            subdir = skill_dir / subdir_name
            if subdir.is_dir():
                dest_subdir = dest_skill_dir / subdir_name
                if dest_subdir.exists():
                    shutil.rmtree(dest_subdir)
                shutil.copytree(subdir, dest_subdir)
    else:
        dest_file = registry.target_path(entry, normalized_backend_id, project_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_md, dest_file)
        for subdir_name in _SKILL_RESOURCE_DIRS:
            subdir = skill_dir / subdir_name
            if subdir.is_dir():
                dest_subdir = target_dir / actual_id / subdir_name
                if dest_subdir.exists():
                    shutil.rmtree(dest_subdir)
                shutil.copytree(subdir, dest_subdir)
    return str(dest_file)


def _parse_skill_chain(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(s).strip() for s in parsed if str(s).strip()]
    except Exception:
        pass
    return []


def provision_workflow_skills(
    *,
    workflow: Any,
    project_folder: str,
    backend_id: str,
    chain_type: str = "pre_chain",
    run_id: int | None = None,
    workflow_id: int | None = None,
    ticket_id: int | None = None,
    board_id: int | None = None,
    project_id: int | None = None,
) -> list[str]:
    """Push workflow pre_chain/post_chain skills to the active harness. Returns skill ids pushed."""
    field_name = "pre_chain" if chain_type == "pre_chain" else "post_chain"
    skill_ids = _parse_skill_chain(getattr(workflow, field_name, None))
    if not skill_ids or not project_folder:
        return []
    pushed: list[str] = []
    for skill_id in skill_ids:
        dest = push_skill_to_project(skill_id=skill_id, project_folder=project_folder, backend_id=backend_id)
        if dest:
            pushed.append(skill_id)
            try:
                from distr.core.orchestrator import emit_event

                emit_event(
                    source="orchestrator",
                    event_type="skill_provisioned",
                    status="ok",
                    workflow_id=workflow_id or getattr(workflow, "id", None),
                    run_id=run_id,
                    ticket_id=ticket_id,
                    board_id=board_id,
                    project_id=project_id,
                    summary=f"Provisioned skill {skill_id} for {backend_id}",
                    payload={"skill_id": skill_id, "chain": chain_type, "dest": dest, "backend_id": backend_id},
                )
            except Exception:
                logger.debug("Could not emit skill_provisioned", exc_info=True)
    return pushed
