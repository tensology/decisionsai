"""Spawn detached OS cleanup when the app exits quickly."""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def _project_root() -> str:
    from distr.core.paths import CORE_DIR

    return os.path.abspath(CORE_DIR)


def spawn_detached_cleanup(
    *,
    agent_pid: int | None = None,
    main_pid: int | None = None,
) -> bool:
    """Start ``bin/decisions-cleanup.sh`` in a new session (survives app exit)."""
    root = _project_root()
    script = os.path.join(root, "bin", "decisions-cleanup.sh")
    if not os.path.isfile(script):
        logger.warning("external_cleanup: missing %s", script)
        return False

    cmd = ["/bin/bash", script, "--detach", "--project-root", root]
    if main_pid:
        cmd.extend(["--main-pid", str(main_pid)])
    if agent_pid:
        cmd.extend(["--agent-pid", str(agent_pid)])

    try:
        subprocess.Popen(
            cmd,
            cwd=root,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        logger.info("external_cleanup: spawned detached cleanup")
        return True
    except Exception as exc:
        logger.warning("external_cleanup: spawn failed: %s", exc)
        return False
