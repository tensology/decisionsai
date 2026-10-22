"""Single source of truth for which CLI harnesses are installed on this machine (2026-10-22, harnesssrc).

Detection is a plain binary lookup, the same approach as ``catalog_probe.resolve_codex_binary``:
``shutil.which`` first (server PATH), then known install folders, because a GUI-launched
server often lacks Homebrew / npm-global / per-user bins on PATH. No subprocess, no auth probe,
so it is fast and safe to call from every dropdown.

Exposed at ``GET /api/harnesses/installed`` and in JS via ``static/shared/harnesses.js``.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

# id -> (display name, binaries to look for). Ids match project_cli_backends registry ids.
HARNESSES: dict[str, tuple[str, tuple[str, ...]]] = {
    "pi": ("Pi", ("pi",)),
    "cursor": ("Cursor", ("cursor-agent",)),
    "codex": ("Codex", ("codex",)),
    "claude_code": ("Claude Code", ("claude",)),
    "kilocode": ("Kilo", ("kilo", "kilocode")),
    "opencode": ("OpenCode", ("opencode",)),
    "cline": ("Cline", ("cline",)),
}


def _known_folders() -> list[str]:
    home = os.path.expanduser("~")
    return [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        os.path.join(home, ".local/bin"),
        os.path.join(home, ".npm-global/bin"),
        os.path.join(home, ".opencode/bin"),
        os.path.join(home, ".bun/bin"),
        os.path.join(home, ".cargo/bin"),
        os.path.join(home, "bin"),
    ]


def resolve_harness_binary(harness_id: str) -> str | None:
    entry = HARNESSES.get(str(harness_id or "").strip().lower())
    if not entry:
        return None
    for name in entry[1]:
        found = shutil.which(name)
        if found:
            return found
    for folder in _known_folders():
        for name in entry[1]:
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def detect_harnesses() -> list[dict[str, Any]]:
    """Every known harness with its detection result (installed or not)."""
    rows = []
    for harness_id, (name, binaries) in HARNESSES.items():
        path = resolve_harness_binary(harness_id)
        rows.append({"id": harness_id, "name": name, "installed": bool(path), "path": path or "", "binaries": list(binaries)})
    return rows


def installed_harnesses() -> list[dict[str, Any]]:
    return [row for row in detect_harnesses() if row["installed"]]
