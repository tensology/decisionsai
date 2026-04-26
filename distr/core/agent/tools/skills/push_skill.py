"""
Push Skill Tool — push a skill to a project's CLI.

For pi: copies into .pi/skills/<skill_id>/SKILL.md (Agent Skills spec — auto-loaded).
For others: copies as flat .md command files.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.pi_skill_push_files import USER_INTENT_FILENAME, write_pi_skill_user_intent

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
_SKILLS_DIR = _PROJECT_ROOT / "skills"

# Supported CLI targets and their skill directories
CLI_TARGETS = {
    "pi": ".pi/skills",
    "claude": ".claude/commands",
    "cursor": ".cursor/commands",
    "gemini": ".gemini/commands",
    "codex": ".codex/commands",
}


class PushSkillInput(BaseModel):
    skill_id: str = Field(description="The skill ID to push. Use the ID from find_skill or list_skills. Examples: 'brainstorming', 'ln-621-security-auditor', 'pdf'.")
    project_path: str = Field(
        default=".",
        description=(
            "Project root directory. If '.' or omitted, uses the **active project's folder** when one is set and has a folder path; "
            "otherwise the process working directory."
        ),
    )
    target: str = Field(default="pi", description="CLI target to push to: 'pi', 'claude', 'cursor', 'gemini', or 'codex'. Defaults to 'pi'.")
    instructions: Optional[str] = Field(
        default="",
        description=(
            "For pi: how the user wants to use this skill in this project — quote or summarize what they said in chat. "
            "Saved as USER_INTENT.md next to SKILL.md (CLI reads it on cold start). "
            "If they asked you to push/install but have not described usage yet, ask one brief question first, then pass their answer here."
        ),
    )


class PushSkillTool(BaseTool):
    """Tool for pushing a skill to a project's CLI."""

    name: str = "push_skill"
    description: str = """Push a skill into a project's CLI so Pi or other targets can load it.

Typical conversation: user asks for a skill like X → use find_skill → user says push it to project Y → call push_skill.

For **pi** (default): copies `.pi/skills/<skill_id>/SKILL.md`. Pass **instructions** with what the user wants Pi to do with this skill — same idea as the Skills UI "Use this skill to:" box. That text is stored as USER_INTENT.md beside SKILL.md so it is on disk even when the CLI was not running.

If the user already explained how they want to use it (same turn or earlier), put that into **instructions**. If they said "push it" but never described usage, ask one short clarifying question, wait for their reply, then call push_skill including **instructions** with their wording.

Other targets (claude, cursor, gemini, codex): flat command file; **instructions** only applies to pi.

Examples: "push brainstorming to ~/myapp", "install the security auditor here".
"""
    args_schema: Type[BaseModel] = PushSkillInput

    def _resolve_project_directory(self, project_path: str) -> tuple[Path, str]:
        """
        Resolve where to push. Empty or '.' prefers the DB active project's folder_location
        so pushes match "this project" without relying on CWD (often wrong for GUI apps).
        """
        raw = (project_path or "").strip()
        if raw not in ("", "."):
            return Path(raw).expanduser().resolve(), ""

        try:
            from distr.core.agent.services.rag.project import get_active_project

            ap = get_active_project()
            if ap and ap.get("folder_location"):
                loc = Path(ap["folder_location"]).expanduser().resolve()
                if loc.exists() and loc.is_dir():
                    name = ap.get("name") or ""
                    hint = f" (resolved from active project: {name})" if name else " (resolved from active project folder)"
                    return loc, hint
        except Exception:
            logger.debug("push_skill: could not resolve active project folder", exc_info=True)

        return Path(".").resolve(), ""

    def _resolve_skill(self, skill_id: str) -> Optional[Path]:
        """Resolve a skill ID to its directory, with fuzzy matching."""
        skill_dir = _SKILLS_DIR / skill_id
        if skill_dir.exists() and skill_dir.is_dir():
            return skill_dir
        matches = [
            d for d in os.listdir(_SKILLS_DIR)
            if d.lower().startswith(skill_id.lower()) or skill_id.lower() in d.lower()
        ]
        if len(matches) == 1:
            return _SKILLS_DIR / matches[0]
        return None

    def _push_to_target(self, skill_dir: Path, skill_id: str, project_path: Path, target: str) -> str:
        """Push skill files to the target CLI directory."""
        target_dir_name = CLI_TARGETS.get(target.lower())
        if not target_dir_name:
            available = ", ".join(f"'{k}'" for k in CLI_TARGETS.keys())
            return f"Unknown target '{target}'. Supported targets: {available}"

        target_dir = project_path / target_dir_name
        target_dir.mkdir(parents=True, exist_ok=True)

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            skill_md = skill_dir / "skill.md"
        if not skill_md.exists():
            return f"Skill '{skill_id}' has no SKILL.md file to push."

        if target.lower() == "pi":
            # Pi follows the Agent Skills spec: <skill_id>/SKILL.md
            dest_skill_dir = target_dir / skill_id
            dest_skill_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_skill_dir / "SKILL.md"
            shutil.copy2(skill_md, dest_file)
            pushed_files = [f"{target_dir_name}/{skill_id}/SKILL.md"]

            # Copy supporting files
            for subdir_name in ["scripts", "references", "reference"]:
                subdir = skill_dir / subdir_name
                if subdir.exists() and subdir.is_dir():
                    dest_subdir = dest_skill_dir / subdir_name
                    if dest_subdir.exists():
                        shutil.rmtree(dest_subdir)
                    shutil.copytree(subdir, dest_subdir)
                    count = sum(1 for _ in dest_subdir.rglob("*") if _.is_file())
                    pushed_files.append(f"{target_dir_name}/{skill_id}/{subdir_name}/ ({count} files)")
        else:
            # Claude/Cursor/Gemini/Codex: flat .md command file
            dest_file = target_dir / f"{skill_id}.md"
            shutil.copy2(skill_md, dest_file)
            pushed_files = [f"{target_dir_name}/{skill_id}.md"]

            for subdir_name in ["scripts", "references", "reference"]:
                subdir = skill_dir / subdir_name
                if subdir.exists() and subdir.is_dir():
                    dest_subdir = target_dir / skill_id / subdir_name
                    if dest_subdir.exists():
                        shutil.rmtree(dest_subdir)
                    shutil.copytree(subdir, dest_subdir)
                    count = sum(1 for _ in dest_subdir.rglob("*") if _.is_file())
                    pushed_files.append(f"{target_dir_name}/{skill_id}/{subdir_name}/ ({count} files)")

        return str(dest_file)

    def _run(
        self,
        skill_id: str = "",
        project_path: str = ".",
        target: str = "pi",
        instructions: Optional[str] = None,
        **kwargs,
    ) -> str:
        try:
            skill_id = skill_id.strip()
            if not skill_id:
                return "Please provide a skill ID. Use find_skill or list_skills to discover available skills."

            project, project_resolve_hint = self._resolve_project_directory(project_path)
            if not project.exists():
                return f"Project path '{project_path}' does not exist."
            if not project.is_dir():
                return f"Project path '{project_path}' is not a directory."

            skill_dir = self._resolve_skill(skill_id)
            if skill_dir is None:
                return f"Skill '{skill_id}' not found. Use find_skill or list_skills to discover available skills."

            actual_id = skill_dir.name
            dest_file = self._push_to_target(skill_dir, actual_id, project, target)

            intent_note = ""
            if target.lower() == "pi":
                dest_skill_dir = project / CLI_TARGETS["pi"] / actual_id
                intent_path = write_pi_skill_user_intent(dest_skill_dir, actual_id, instructions or "")
                if intent_path:
                    intent_note = f"\n\nAlso wrote `{USER_INTENT_FILENAME}` with your usage notes for cold CLI starts."

            target_dir_name = CLI_TARGETS.get(target.lower(), target)
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                skill_md = skill_dir / "skill.md"

            skill_name = actual_id
            try:
                content = skill_md.read_text(encoding="utf-8")
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end > 0:
                        for line in content[3:end].split("\n"):
                            if line.strip().startswith("name:"):
                                skill_name = line.split(":", 1)[1].strip().strip('"').strip("'")
                                break
            except Exception:
                pass

            slash_hint = f"/skill:{actual_id}" if target.lower() == "pi" else f"/{actual_id}"
            return (
                f"✅ Pushed skill '{skill_name}' ({actual_id}) to {target}!\n\n"
                f"Destination: {dest_file}\n"
                f"Project: {project}{project_resolve_hint}\n"
                f"Target: {target} ({target_dir_name}/)\n\n"
                f"Use as command: {slash_hint}"
                f"{intent_note}"
            )

        except Exception as e:
            logger.error("Error in push_skill: %s", e, exc_info=True)
            return f"Error pushing skill: {str(e)}"

    async def _arun(
        self,
        skill_id: str = "",
        project_path: str = ".",
        target: str = "pi",
        instructions: Optional[str] = None,
        **kwargs,
    ) -> str:
        return self._run(
            skill_id=skill_id,
            project_path=project_path,
            target=target,
            instructions=instructions,
        )