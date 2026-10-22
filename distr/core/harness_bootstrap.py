"""Shared helpers for projecting Decisions harness skills into IDE/CLI homes."""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from distr.core.plugins import CODEX_PLUGIN_NAME

logger = logging.getLogger(__name__)

# Harnesses that natively discover the open Agent Skills directory ``~/.agents/skills``.
# They get no per-harness copy or link (that would double-load names).
NATIVE_SHARED_SKILL_READERS = frozenset({"pi", "kilo"})
# Bases that must hold real files (plugin packaging copies them at install time).
COPY_ONLY_SKILL_BASES = frozenset({"codex"})


def shared_skills_dir(home: Path) -> Path:
    """Canonical, harness-agnostic skill directory (Agent Skills standard)."""
    return home / ".agents" / "skills"


def skill_archive_root(home: Path) -> Path:
    return home / ".decisions" / "harness" / "skill-archive" / time.strftime("%Y%m%d-%H%M%S")


def detected_harnesses() -> dict[str, bool]:
    home = Path.home()
    return {
        "codex": bool(shutil.which("codex") or (home / ".codex").is_dir()),
        "claude": bool(shutil.which("claude") or (home / ".claude").is_dir()),
        "cursor": bool(shutil.which("cursor") or shutil.which("cursor-agent") or (home / ".cursor").is_dir()),
        "pi": bool(shutil.which("pi") or (home / ".pi").is_dir()),
        "kilo": bool(
            shutil.which("kilo")
            or shutil.which("kilocode")
            or (home / ".kilocode").is_dir()
            or (home / ".kilo").is_dir()
            or (home / ".config" / "kilo").is_dir()
        ),
        "cline": bool(shutil.which("cline") or (home / ".cline").is_dir()),
        "gemini": bool(shutil.which("gemini") or (home / ".gemini").is_dir()),
        "kiro": bool(shutil.which("kiro") or (home / ".kiro").is_dir()),
        # Grok Bot 0.68 uses Cursor-compatible .cursor/skills and .cursor/rules.
        "grok": bool(
            shutil.which("grok")
            or (Path("/Applications") / "Grok Bot.app").is_dir()
            or (home / "Library" / "Application Support" / "Grok Bot").is_dir()
        ),
    }


def harness_skill_bases(home: Path, detected: dict[str, bool]) -> dict[str, Path]:
    """Return per-harness directories where SKILL.md trees should be copied."""
    bases: dict[str, Path] = {}
    if (home / ".agents").is_dir():
        bases["agents_global"] = home / ".agents" / "skills"
    if detected.get("codex"):
        # Keep the Decisions plugin source populated for plugin packaging and
        # also project to Codex's canonical user-level skill directory. Local
        # marketplace plugins are cached at install time, while ~/.codex/skills
        # is the durable global surface read by every Codex session.
        bases["codex"] = home / "plugins" / CODEX_PLUGIN_NAME / "skills"
        bases["codex_global"] = home / ".codex" / "skills"
    if detected.get("claude"):
        bases["claude"] = home / ".claude" / "skills"
    # Pi (~/.pi/agent/skills + ~/.agents/skills) and Kilo (~/.kilo[code]/skills +
    # ~/.agents/skills + ~/.claude/skills) read the shared dir natively, so they get
    # no per-harness base. Note: ~/.pi/skills is a *project* dir for Pi, not global.
    if detected.get("cursor"):
        bases["cursor"] = home / ".cursor" / "skills"
    if detected.get("cline"):
        bases["cline"] = home / ".cline" / "skills"
    if detected.get("gemini"):
        bases["gemini"] = home / ".gemini" / "skills"
    if detected.get("kiro"):
        bases["kiro"] = home / ".kiro" / "skills"
    if detected.get("grok") and not detected.get("cursor"):
        bases["grok"] = home / ".cursor" / "skills"
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
    if detected.get("kiro"):
        bases["kiro"] = home / ".kiro" / "commands"
    return bases


def _same_target(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _archive_existing(dest: Path, archive_root: Path) -> Path:
    """Move a real directory/file out of the way instead of deleting it."""
    target = archive_root / dest.parent.name / dest.name
    if target.exists() or target.is_symlink():
        target = target.with_name(f"{target.name}.{int(time.time() * 1000)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dest), str(target))
    logger.info("Archived %s -> %s before linking", dest, target)
    return target


def link_skill_tree(src: Path, dest: Path, *, archive_root: Path | None = None) -> bool:
    """Make ``dest`` a symlink to ``src``.

    Never deletes a symlink that already points at ``src``; a real directory at
    ``dest`` is archived (under ``archive_root``) and then replaced by the link.
    Returns True when the filesystem changed.
    """
    if not src.is_dir():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        if _same_target(dest, src):
            return False
        dest.unlink()
    elif dest.exists():
        if _same_target(dest, src):
            return False
        _archive_existing(dest, archive_root or (dest.parent / ".archive"))
    os.symlink(str(src), str(dest))
    return True


def copy_skill_tree(src: Path, dest: Path) -> bool:
    """Real-file copy; only for surfaces that cannot hold symlinks (plugin packaging)."""
    if not src.is_dir():
        return False
    src = src.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink():
        dest.unlink()
    elif dest.exists():
        if _same_target(dest, src):
            return False  # never rmtree the source itself
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", ".git", "node_modules"),
    )
    return True


def publish_shared_skill(
    home: Path, skill_id: str, src: Path, *, overwrite: bool = True, archive_root: Path | None = None
) -> tuple[Path, bool]:
    """Ensure ``~/.agents/skills/<id>`` exists, as a symlink to ``src`` unless a
    real (user-owned, canonical) directory is already there, which is never replaced.
    Returns (canonical_path, changed)."""
    shared = shared_skills_dir(home) / skill_id
    if shared.exists() and not shared.is_symlink():
        if not _same_target(shared, src):
            logger.info("Keeping user-owned canonical skill %s (not replacing with %s)", shared, src)
        return shared, False
    if shared.is_symlink() and shared.exists() and not overwrite:
        return shared, False
    return shared, link_skill_tree(src, shared, archive_root=archive_root)


def install_skills_to_harnesses(
    *,
    home: Path,
    detected: dict[str, bool],
    skill_sources: dict[str, Path],
    also_commands: bool = True,
    overwrite: bool = True,
    overwrite_by_skill: dict[str, bool] | None = None,
) -> list[str]:
    """Publish skill_id -> source_dir into ``~/.agents/skills`` and link each detected
    harness skill dir to it. Returns changed dest paths."""
    written: list[str] = []
    skill_bases = harness_skill_bases(home, detected)
    command_bases = harness_command_bases(home, detected) if also_commands else {}
    per_skill = overwrite_by_skill or {}
    archive_root = skill_archive_root(home)

    for skill_id, src in skill_sources.items():
        if not src.is_dir():
            continue
        skill_md = src / "SKILL.md"
        if not skill_md.is_file():
            skill_md = src / "skill.md"
        if not skill_md.is_file():
            continue
        do_overwrite = per_skill.get(skill_id, overwrite)
        canonical, changed = publish_shared_skill(
            home, skill_id, src, overwrite=do_overwrite, archive_root=archive_root
        )
        if changed:
            written.append(str(canonical))
        for harness, base in skill_bases.items():
            if harness == "agents_global":
                continue
            dest_dir = base / skill_id
            if harness in COPY_ONLY_SKILL_BASES:
                if dest_dir.exists() and not do_overwrite:
                    continue
                if copy_skill_tree(canonical, dest_dir):
                    written.append(str(dest_dir))
                continue
            if dest_dir.exists() and not dest_dir.is_symlink() and not do_overwrite:
                continue
            if link_skill_tree(canonical, dest_dir, archive_root=archive_root):
                written.append(str(dest_dir))
            elif str(dest_dir) not in written and _same_target(dest_dir, canonical):
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
    # Projections are harness-specific text. Never write through a symlinked skill dir
    # (that would overwrite the shared ~/.agents copy or a repo source).
    if path.parent.is_symlink():
        path.parent.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def projection_paths(home: Path, detected: dict[str, bool], skill_name: str) -> dict[str, Path]:
    """Standard Decisions projection filenames per harness.

    Cursor Agent Skills require ``~/.cursor/skills/<id>/SKILL.md`` (not a flat
    ``~/.cursor/*.md`` stub — those are invisible to the agent skills loader).
    """
    mapping = {
        "codex": home / "plugins" / CODEX_PLUGIN_NAME / "skills" / skill_name / "SKILL.md",
        "claude": home / ".claude" / "skills" / skill_name / "SKILL.md",
        "cursor": home / ".cursor" / "skills" / skill_name / "SKILL.md",
        "pi": home / ".pi" / "agent" / "skills" / skill_name / "SKILL.md",
        "cline": home / ".cline" / "skills" / skill_name / "SKILL.md",
        "grok": home / ".cursor" / "skills" / skill_name / "SKILL.md",
        "gemini": home / ".gemini" / "skills" / skill_name / "SKILL.md",
        "kiro": home / ".kiro" / "skills" / skill_name / "SKILL.md",
    }
    return {key: path for key, path in mapping.items() if detected.get(key)}
