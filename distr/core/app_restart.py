"""Detached desktop-app restart helpers.

The restart child must survive the current Qt process exiting. A short-lived
``bash -c 'sleep && python …'`` child can be torn down with the parent on macOS,
so we write a small script under ~/.decisions/logs and launch it with nohup.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

RESTART_SLEEP_SECONDS = 3
RESTART_LOG_NAME = "restart.log"


def resolve_project_root() -> Path:
    """Return the repository / bundle project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # distr/core/app_restart.py -> repo root is three levels up from distr/
    return Path(__file__).resolve().parents[2]


def resolve_restart_python(project_root: Path) -> str:
    """Pick the Python interpreter that should relaunch the app."""
    candidates = [
        sys.executable,
        Path.home() / ".virtualenvs" / "decisions" / "bin" / "python",
        project_root / "venv" / "bin" / "python",
        project_root / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        path = str(candidate)
        if path and os.path.isfile(path):
            return path
    return sys.executable


def restart_log_path() -> Path:
    path = Path.home() / ".decisions" / "logs" / RESTART_LOG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def restart_script_path() -> Path:
    path = Path.home() / ".decisions" / "logs" / "restart_decisions.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build_restart_shell_script(*, project_root: Path, python_path: str) -> str:
    """Build a bash script that relaunches ``bin/start.py`` after the parent exits."""
    start_script = project_root / "bin" / "start.py"
    log_path = restart_log_path()
    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        f"cd {shlex.quote(str(project_root))}",
        f"sleep {RESTART_SLEEP_SECONDS}",
        "export DECISIONS_RESTARTING=1",
    ]
    if start_script.is_file():
        cmd = f"{shlex.quote(python_path)} {shlex.quote(str(start_script))} --skip-kill-existing"
    else:
        cmd = shlex.quote(python_path)
    lines.append(f"exec {cmd} >> {shlex.quote(str(log_path))} 2>&1")
    return "\n".join(lines) + "\n"


def _frozen_macos_app_bundle() -> str | None:
    if not getattr(sys, "frozen", False) or sys.platform != "darwin":
        return None
    exe = Path(sys.executable).resolve()
    for parent in [exe, *exe.parents]:
        if parent.suffix == ".app":
            return str(parent)
    return None


def spawn_restart_process(*, project_root: Path | None = None, python_path: str | None = None) -> None:
    """Spawn a detached process that relaunches Decisions after the current app quits."""
    root = project_root or resolve_project_root()
    py = python_path or resolve_restart_python(root)

    app_bundle = _frozen_macos_app_bundle()
    if app_bundle:
        subprocess.Popen(
            ["/usr/bin/open", "-n", app_bundle],
            start_new_session=True,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[RESTART] Spawned new app bundle via open -n: %s", app_bundle)
        return

    if sys.platform == "win32":
        start_script = root / "bin" / "start.py"
        cmd = [py, str(start_script), "--skip-kill-existing"] if start_script.is_file() else [py]
        script = root / "_restart.bat"
        quoted_cmd = " ".join(f'"{part}"' for part in cmd)
        script.write_text(
            "@echo off\n"
            "timeout /t 3 /nobreak >nul\n"
            f"{quoted_cmd}\n"
            f'del "{script}"\n',
            encoding="utf-8",
        )
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        subprocess.Popen(
            ["cmd", "/c", str(script)],
            cwd=str(root),
            creationflags=creationflags,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("[RESTART] Spawned Windows restart batch: %s", script)
        return

    script_path = restart_script_path()
    script_path.write_text(
        build_restart_shell_script(project_root=root, python_path=py),
        encoding="utf-8",
    )
    os.chmod(script_path, 0o755)
    subprocess.Popen(
        ["nohup", "/bin/bash", str(script_path)],
        cwd=str(root),
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "DECISIONS_RESTARTING": "1"},
    )
    logger.info(
        "[RESTART] Spawned detached restart script %s (python=%s, log=%s)",
        script_path,
        py,
        restart_log_path(),
    )
