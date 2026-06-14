"""Canonical paths for DecisionsAI IDE plugins and vendored harness packs."""

from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

PLUGINS_ROOT = _PROJECT_ROOT / "plugins"
CODEX_IDE_DIR = PLUGINS_ROOT / "codex-ide"
CURSOR_IDE_DIR = PLUGINS_ROOT / "cursor-ide"
ECC_VENDOR_DIR = PLUGINS_ROOT / "ecc"

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
