"""Normalize process environment for GUI and detached macOS launches."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def _path_looks_minimal(path: str) -> bool:
    parts = [p for p in path.split(os.pathsep) if p]
    if len(parts) < 6:
        return True
    joined = path.lower()
    return "/opt/homebrew" not in joined and "/usr/local/bin" not in joined


def bootstrap_gui_launch_environment() -> None:
    """Ensure PATH, HOME, and SHELL match an interactive login shell when thin."""
    if sys.platform != "darwin":
        return

    path = os.environ.get("PATH", "")
    shell = (os.environ.get("SHELL") or "").strip()
    if shell and not _path_looks_minimal(path):
        return

    shell_bin = shell or "/bin/zsh"
    if not os.path.isfile(shell_bin):
        shell_bin = "/bin/zsh" if os.path.isfile("/bin/zsh") else "/bin/bash"

    try:
        proc = subprocess.run(
            [shell_bin, "-ilc", "env -0"],
            capture_output=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:
        logger.debug("Could not load login shell environment: %s", exc)
        return

    if proc.returncode != 0:
        logger.debug("Login shell env export failed (rc=%s)", proc.returncode)
        return

    for entry in proc.stdout.split(b"\0"):
        if not entry or b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        name = key.decode("utf-8", errors="replace")
        if not name or name.startswith("_"):
            continue
        if name in ("_", "SHLVL", "PWD", "OLDPWD"):
            continue
        os.environ[name] = value.decode("utf-8", errors="replace")

    logger.info(
        "Bootstrapped GUI launch environment (shell=%s, path_entries=%s)",
        shell_bin,
        len(os.environ.get("PATH", "").split(os.pathsep)),
    )
