"""
process_tracker.py — Track spawned child PIDs so they can be killed on restart/crash.

Writes all child PIDs to a lock file. On startup, any PIDs in that file are
killed before the app starts. On clean shutdown, the file is cleared.
This prevents orphaned Python worker processes from accumulating across restarts.
"""

import atexit
import logging
import os
import signal
import time
import subprocess

logger = logging.getLogger(__name__)

# PID file location — same dir as the DB
_PID_FILE: str = ""
_tracked_pids: set = set()
_registered: bool = False


def _get_pid_file() -> str:
    global _PID_FILE
    if not _PID_FILE:
        try:
            from distr.core.paths import DB_DIR
            _PID_FILE = os.path.join(DB_DIR, "worker_pids.txt")
        except Exception:
            _PID_FILE = os.path.expanduser("~/.decisions/worker_pids.txt")
    return _PID_FILE


def _write_pids():
    """Persist current tracked PIDs to disk."""
    try:
        path = _get_pid_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for pid in _tracked_pids:
                f.write(f"{pid}\n")
    except Exception as e:
        logger.debug("process_tracker: could not write PID file: %s", e)


def register_child_pid(pid: int):
    """Call this after spawning any child process."""
    if pid and pid > 0:
        _tracked_pids.add(pid)
        _write_pids()
        logger.debug("process_tracker: registered PID %d (%d total)", pid, len(_tracked_pids))


def unregister_child_pid(pid: int):
    """Call this after a child process has been cleanly terminated."""
    _tracked_pids.discard(pid)
    _write_pids()


def kill_tracked_pids(pids: set = None, timeout: float = 3.0):
    """Kill a set of PIDs (defaults to all tracked). SIGTERM then SIGKILL."""
    targets = pids if pids is not None else set(_tracked_pids)
    if not targets:
        return

    logger.info("process_tracker: terminating %d worker PID(s): %s", len(targets), sorted(targets))

    # SIGTERM pass
    alive = set()
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            alive.add(pid)
        except ProcessLookupError:
            pass  # already gone
        except Exception as e:
            logger.debug("process_tracker: SIGTERM %d failed: %s", pid, e)

    if alive:
        time.sleep(min(timeout, 2.0))

    # SIGKILL any survivors
    for pid in alive:
        try:
            os.kill(pid, 0)  # check still alive
            os.kill(pid, signal.SIGKILL)
            logger.info("process_tracker: SIGKILL sent to PID %d", pid)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.debug("process_tracker: SIGKILL %d failed: %s", pid, e)


def kill_stale_pids_from_file():
    """Read the PID file and kill any processes still running from a previous session."""
    path = _get_pid_file()
    if not os.path.isfile(path):
        return

    stale = set()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.isdigit():
                    stale.add(int(line))
    except Exception as e:
        logger.debug("process_tracker: could not read PID file: %s", e)
        return

    if not stale:
        return

    # Filter to only PIDs that are actually still running
    running = set()
    for pid in stale:
        try:
            os.kill(pid, 0)
            running.add(pid)
        except ProcessLookupError:
            pass
        except Exception:
            pass

    if running:
        logger.info("process_tracker: killing %d stale worker PID(s) from previous session: %s",
                    len(running), sorted(running))
        kill_tracked_pids(running, timeout=3.0)

    # Clear the file
    try:
        os.remove(path)
    except Exception:
        pass


def kill_rogue_decisions_processes(protected_pids: set | None = None, timeout: float = 2.0):
    """Kill lingering DecisionsAI processes that are not part of this runtime.

    This catches detached launcher/worker leftovers that are not tracked in
    worker_pids.txt (for example, stale start.py/decisions.sh/sidecar processes).
    """
    protected = set(protected_pids or set())
    try:
        protected.add(os.getpid())
    except Exception:
        pass
    try:
        ppid = os.getppid()
        if ppid > 1:
            protected.add(ppid)
    except Exception:
        pass

    rogue_pids = set()

    # Prefer psutil for reliable cmdline inspection.
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["pid", "cmdline", "name"]):
            try:
                pid = int(proc.info.get("pid") or 0)
                if pid <= 1 or pid in protected:
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                name = str(proc.info.get("name") or "").lower()
                haystack = f"{name} {cmdline}"
                if "decisionsai" not in haystack:
                    continue
                if any(marker in haystack for marker in (
                    "bin/start.py",
                    "bin/decisions.sh",
                    " sidecar",
                    "uvicorn",
                    "decisions ",
                )):
                    rogue_pids.add(pid)
            except Exception:
                continue
    except Exception:
        # Fallback: pgrep over command line (best-effort).
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", "DecisionsAI"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.splitlines():
                if line.strip().isdigit():
                    pid = int(line.strip())
                    if pid > 1 and pid not in protected:
                        rogue_pids.add(pid)
        except Exception:
            pass

    if not rogue_pids:
        return

    logger.info(
        "process_tracker: killing %d rogue DecisionsAI process(es): %s",
        len(rogue_pids), sorted(rogue_pids)
    )
    kill_tracked_pids(rogue_pids, timeout=timeout)


def _atexit_cleanup():
    """Kill all tracked children on any normal Python exit."""
    if _tracked_pids:
        logger.info("process_tracker: atexit — killing %d worker(s)", len(_tracked_pids))
        kill_tracked_pids()
    try:
        path = _get_pid_file()
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


def _signal_handler(signum, frame):
    """SIGTERM/SIGINT handler — clean up children then re-raise."""
    logger.info("process_tracker: caught signal %d — cleaning up workers", signum)
    kill_tracked_pids()
    try:
        path = _get_pid_file()
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass
    # Re-raise as KeyboardInterrupt so Qt's event loop exits normally
    raise KeyboardInterrupt()


def setup(main_pid: int = None):
    """Call once from the main process at startup.

    1. Kills any stale PIDs from a previous (crashed) session.
    2. Registers atexit + signal handlers to kill children on exit.
    """
    global _registered
    if _registered:
        return
    _registered = True

    # Step 1: kill orphans from last session
    kill_stale_pids_from_file()

    # Step 2: register cleanup hooks
    atexit.register(_atexit_cleanup)

    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (OSError, ValueError):
        pass  # can't set signal in non-main thread

    logger.info("process_tracker: initialized (main PID=%d)", os.getpid())
