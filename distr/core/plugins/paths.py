"""Canonical paths for DecisionsAI IDE plugins and vendored harness packs."""

from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

PLUGINS_ROOT = _PROJECT_ROOT / "plugins"
CODEX_IDE_DIR = PLUGINS_ROOT / "codex-ide"
CURSOR_IDE_DIR = PLUGINS_ROOT / "cursor-ide"
ECC_VENDOR_DIR = PLUGINS_ROOT / "ecc"
COMPETITION_PACK_DIR = PLUGINS_ROOT / "competition-pack"
AGENT_REACH_PACK_DIR = PLUGINS_ROOT / "agent-reach-pack"
COMMUNITY_SKILLS_PACK_DIR = PLUGINS_ROOT / "community-skills-pack"

# Installed plugin folder names (under the user's home directory).
CODEX_PLUGIN_NAME = "decisions-codex"
CURSOR_PLUGIN_NAME = "decisions-cursor"


def project_root() -> Path:
    """Return the DecisionsAI repository root."""
    return _PROJECT_ROOT


def codex_ide_source() -> Path:
    """Return the repo-local Codex IDE plugin source directory."""
    return CODEX_IDE_DIR


def cursor_ide_source() -> Path:
    """Return the repo-local Cursor IDE plugin source directory."""
    return CURSOR_IDE_DIR


def ecc_vendor_dir() -> Path:
    """Return the vendored ECC harness pack directory."""
    return ECC_VENDOR_DIR


def competition_vendor_dir() -> Path:
    """Return the vendored Ponytail/Fallow competition pack directory."""
    return COMPETITION_PACK_DIR


def competition_ponytail_skills_dir() -> Path:
    return COMPETITION_PACK_DIR / "ponytail" / "skills"


def competition_fallow_skills_dir() -> Path:
    return COMPETITION_PACK_DIR / "fallow" / "skills"


def agent_reach_vendor_dir() -> Path:
    return AGENT_REACH_PACK_DIR


def agent_reach_skill_dir() -> Path:
    return AGENT_REACH_PACK_DIR / "skills" / "agent-reach"


def agent_reach_skills_root() -> Path:
    return AGENT_REACH_PACK_DIR / "skills"


def agent_reach_reference_dir() -> Path:
    return _PROJECT_ROOT.parent / "reference" / "agent-reach"


def yt_dlp_reference_dir() -> Path:
    return _PROJECT_ROOT.parent / "reference" / "yt-dlp"


def community_skills_dir() -> Path:
    return COMMUNITY_SKILLS_PACK_DIR / "skills"
