"""Shared helpers for projecting Decisions harness skills into IDE/CLI homes."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from distr.core.plugins import CODEX_PLUGIN_NAME


def detected_harnesses() -> dict[str, bool]:
    return {
        "codex": bool(shutil.which("codex")),
        "claude": bool(shutil.which("claude")),
        "cursor": bool(shutil.which("cursor") or shutil.which("cursor-agent")),
        "pi": bool(shutil.which("pi")),
        "cline": bool(shutil.which("cline")),
    }


def harness_skill_bases(home: Path, detected: dict[str, bool]) -> dict[str, Path]:
    """Return per-harness directories where SKILL.md trees should be copied."""
    bases: dict[str, Path] = {}
    if detected.get("codex"):
        bases["codex"] = home / "plugins" / CODEX_PLUGIN_NAME / "skills"
    if detected.get("claude"):
        bases["claude"] = home / ".claude" / "skills"
    if detected.get("pi"):
        bases["pi"] = home / ".pi" / "skills"
    if detected.get("cursor"):
        bases["cursor"] = home / ".cursor" / "skills"
    if detected.get("cline"):
        bases["cline"] = home / ".cline" / "skills"
    if detected.get("gemini"):
        bases["gemini"] = home / ".gemini" / "skills"
    return bases


def harness_command_bases(home: Path, detected: dict[str, bool]) -> dict[str, Path]:
    """Cursor/Codex slash-command style surfaces (workflow provision target)."""
    bases: dict[str, Path] = {}
    if detected.get("cursor"):
        bases["cursor"] = home / ".cursor" / "commands"
    if detected.get("codex"):
        bases["codex"] = home / ".codex" / "commands"
    if detected.get("claude"):
        bases["claude"] = home / ".claude" / "commands"
    if detected.get("pi"):
        bases["pi"] = home / ".pi" / "commands"
    if detected.get("gemini"):
        bases["gemini"] = home / ".gemini" / "commands"
    return bases


def copy_skill_tree(src: Path, dest: Path) -> bool:
    if not src.is_dir():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".git", "node_modules"),
    )
    return True


def install_skills_to_harnesses(
    *,
    home: Path,
    detected: dict[str, bool],
    skill_sources: dict[str, Path],
    also_commands: bool = True,
    overwrite: bool = True,
    overwrite_by_skill: dict[str, bool] | None = None,
) -> list[str]:
    """Copy skill_id -> source_dir into each detected harness. Returns dest paths."""
    written: list[str] = []
    skill_bases = harness_skill_bases(home, detected)
    command_bases = harness_command_bases(home, detected) if also_commands else {}
    per_skill = overwrite_by_skill or {}

    for skill_id, src in skill_sources.items():
        if not src.is_dir():
            continue
        skill_md = src / "SKILL.md"
        if not skill_md.is_file():
            skill_md = src / "skill.md"
        if not skill_md.is_file():
            continue
        do_overwrite = per_skill.get(skill_id, overwrite)
        for harness, base in skill_bases.items():
            dest_dir = base / skill_id
            if dest_dir.exists() and not do_overwrite:
                continue
            if copy_skill_tree(src, dest_dir):
                written.append(str(dest_dir))
        if also_commands:
            for harness, base in command_bases.items():
                dest_file = base / f"{skill_id}.md"
                if dest_file.exists() and not do_overwrite:
                    continue
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(skill_md, dest_file)
                written.append(str(dest_file))
    return written


def write_projection_skill(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def projection_paths(home: Path, detected: dict[str, bool], skill_name: str) -> dict[str, Path]:
    """Standard Decisions projection filenames per harness."""
    mapping = {
        "codex": home / "plugins" / CODEX_PLUGIN_NAME / "skills" / skill_name / "SKILL.md",
        "claude": home / ".claude" / "skills" / skill_name / "SKILL.md",
        "cursor": home / ".cursor" / f"decisions-{skill_name}.md",
        "pi": home / ".pi" / "skills" / skill_name / "SKILL.md",
        "cline": home / ".cline" / "skills" / skill_name / "SKILL.md",
    }
    return {key: path for key, path in mapping.items() if detected.get(key)}
