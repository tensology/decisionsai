"""First-run bootstrap: make the shared-memory stack default-on for fresh installs.

Called once from Decisions startup (non-blocking). If the stack was never
installed for this user, or the pinned MemPalace version changed, it starts
``python -m distr.core.mempalace.installer install`` as a detached background
process (log: ~/Library/Logs/mempalace/firstrun.log) and returns at once.
It does nothing when every component is switched off, on non-macOS hosts, or
while a previous bootstrap is still running.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from distr.core.mempalace import installer

LOCK_MAX_AGE_S = 30 * 60


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def state_path() -> Path:
    return _home() / ".decisions" / "mempalace" / "install-state.json"


def needs_install() -> bool:
    try:
        st = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return st.get("mempalace_version") != installer.PINNED_VERSION


def maybe_bootstrap(*, python: str | None = None) -> str:
    """Start the installer in the background when needed. Returns a short status string."""
    if sys.platform != "darwin":
        return "skipped: not macOS"
    if not (installer.hub_enabled() or installer.harness_wiring_enabled() or installer.capture_enabled()):
        return "skipped: all MemPalace components switched off"
    if not needs_install():
        return "already installed"
    lock = state_path().with_name("firstrun.lock")
    try:
        if time.time() - lock.stat().st_mtime < LOCK_MAX_AGE_S:
            return "bootstrap already running"
    except OSError:
        pass
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    logdir = _home() / "Library" / "Logs" / "mempalace"
    logdir.mkdir(parents=True, exist_ok=True)
    log = open(logdir / "firstrun.log", "a", encoding="utf-8")  # noqa: SIM115 - handed to the child
    log.write(f"\n=== first-run bootstrap {time.strftime('%Y-%m-%d %H:%M:%S %z')} ===\n")
    log.flush()
    subprocess.Popen(
        [python or sys.executable, "-m", "distr.core.mempalace.installer", "install"],
        cwd=str(installer.REPO_ROOT), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, env={**os.environ, "PYTHONPATH": str(installer.REPO_ROOT)},
    )
    return "bootstrap started"
