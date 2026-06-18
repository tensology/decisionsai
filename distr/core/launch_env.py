"""Normalize process environment for GUI and detached macOS launches."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def _sanitize_dyld_library_path_for_apple_silicon() -> None:
    """Drop Intel Homebrew paths from DYLD on ARM Macs (breaks psycopg2/torch wheels)."""
    if sys.platform != "darwin":
        return
    try:
        import platform

        if platform.machine() != "arm64":
            return
    except Exception:
        return
    dyld = (os.environ.get("DYLD_LIBRARY_PATH") or "").strip()
    if not dyld or "/usr/local/lib" not in dyld.split(":"):
        return
    kept = [part for part in dyld.split(":") if part and part != "/usr/local/lib"]
    if kept:
        os.environ["DYLD_LIBRARY_PATH"] = ":".join(kept)
    else:
        os.environ.pop("DYLD_LIBRARY_PATH", None)
    logger.info("Removed /usr/local/lib from DYLD_LIBRARY_PATH on arm64")


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
        if name.startswith("DECISIONS_"):
            continue
        os.environ[name] = value.decode("utf-8", errors="replace")

    _sanitize_dyld_library_path_for_apple_silicon()

    logger.info(
        "Bootstrapped GUI launch environment (shell=%s, path_entries=%s)",
        shell_bin,
        len(os.environ.get("PATH", "").split(os.pathsep)),
    )
