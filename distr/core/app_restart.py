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
import time
from pathlib import Path

from distr.core.dock_app import load_dock_launch_preference, resolve_app_bundle_path

logger = logging.getLogger(__name__)

RESTART_SLEEP_SECONDS = 5
RESTART_LOG_NAME = "restart.log"
RESTART_PENDING_NAME = "restart_pending"


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


def restart_pending_path() -> Path:
    """Marker file read by decisions-cleanup.sh to avoid killing the respawn."""
    path = Path.home() / ".decisions" / "run" / RESTART_PENDING_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def mark_restart_pending() -> None:
    """Tell external cleanup that a detached restart child is about to launch."""
    try:
        restart_pending_path().write_text(str(time.time()), encoding="utf-8")
    except Exception as exc:
        logger.warning("[RESTART] Could not write restart pending marker: %s", exc)


def clear_restart_pending() -> None:
    try:
        restart_pending_path().unlink(missing_ok=True)
    except Exception:
        pass


def restart_script_path() -> Path:
    path = Path.home() / ".decisions" / "logs" / "restart_decisions.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build_restart_shell_script(
    *,
    project_root: Path,
    python_path: str,
    dock_app_bundle: str | None = None,
) -> str:
    """Build a bash script that relaunches Decisions after the parent exits."""
    if dock_app_bundle and Path(dock_app_bundle).is_dir():
        return "\n".join(
            [
                "#!/bin/bash",
                "set -euo pipefail",
                f"sleep {RESTART_SLEEP_SECONDS}",
                f"exec /usr/bin/open -n {shlex.quote(dock_app_bundle)}",
            ]
        ) + "\n"

    start_script = project_root / "bin" / "start.py"
    log_path = restart_log_path()
    run_script = project_root / "bin" / "decisions-run.sh"
    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        f"cd {shlex.quote(str(project_root))}",
        f"sleep {RESTART_SLEEP_SECONDS}",
        "export DECISIONS_RESTARTING=1",
    ]
    if run_script.is_file() and load_dock_launch_preference().get("dock"):
        lines.append("export DECISIONS_DOCK_APP=1")
        lines.append(f"exec /bin/bash {shlex.quote(str(run_script))} >> {shlex.quote(str(log_path))} 2>&1")
    elif start_script.is_file():
        cmd = f"{shlex.quote(python_path)} {shlex.quote(str(start_script))} --skip-kill-existing"
        lines.append(f"exec {cmd} >> {shlex.quote(str(log_path))} 2>&1")
    else:
        lines.append(f"exec {shlex.quote(python_path)} >> {shlex.quote(str(log_path))} 2>&1")
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
    mark_restart_pending()

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
        build_restart_shell_script(
            project_root=root,
            python_path=py,
            dock_app_bundle=resolve_app_bundle_path(root) or None,
        ),
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
