"""Check and update a Git installation after its desktop process exits."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener


ACTIVE_STATES = {"waiting", "updating", "preparing", "restarting"}
PROFILE_MARKER = "installer/.requirements_installed_profile"
REQUIREMENTS = "requirements.txt"


def state_path():
    base = os.environ.get("DECISIONS_DATA_DIR")
    return (Path(base) if base else Path.home() / ".decisions") / "run" / "software-update.json"


def read_state():
    try:
        state = json.loads(state_path().read_text(encoding="utf-8"))
        if state["phase"] in ACTIVE_STATES and time.time() - state["timestamp"] > 15 * 60:
            return {"phase": "failed", "message": "The updater stopped before completing. See software-update.log."}
        return state
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _write_state(phase, message):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as output:
        json.dump({"phase": phase, "message": message, "timestamp": time.time()}, output)
    try:
        Path(output.name).replace(path)
    finally:
        Path(output.name).unlink(missing_ok=True)


def _git(root, *args, timeout=30):
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, timeout=timeout, check=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return result.stdout.strip()


def check_update(root, *, fetch=True):
    if not (root / ".git").exists():
        return {"status": "unsupported", "message": "This installation uses release downloads."}
    if _git(root, "branch", "--show-current") != "main":
        return {"status": "blocked", "message": "Switch to main before updating DecisionsAI."}
    if fetch:
        _git(root, "fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
    current = _git(root, "rev-parse", "HEAD")
    latest = _git(root, "rev-parse", "refs/remotes/origin/main")
    result = {"current": current[:8], "latest": latest[:8]}
    ahead, behind = map(int, _git(root, "rev-list", "--left-right", "--count", f"{current}...{latest}").split())
    if ahead:
        return {**result, "status": "blocked", "message": "Local commits are ahead of origin/main. Resolve them before updating."}
    if not behind:
        return {**result, "status": "current", "message": "DecisionsAI is up to date."}
    if _git(root, "status", "--porcelain", "--untracked-files=normal"):
        return {**result, "status": "blocked", "message": "The checkout has local changes. Commit or stash them before updating."}
    return {**result, "status": "available", "target": latest, "message": f"Update {latest[:8]} is available."}


def _console_python():
    executable = Path(sys.executable)
    console = executable.with_name("python.exe")
    return str(console if executable.name.lower() == "pythonw.exe" and console.is_file() else executable)


def _detach():
    if os.name == "nt":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def start_update(root, parent_pid, port):
    path = state_path()
    log = path.parent / "software-update.log"
    _write_state("waiting", "Waiting for DecisionsAI to close.")
    try:
        with log.open("a", encoding="utf-8") as output:
            subprocess.Popen(
                [_console_python(), str(Path(__file__).resolve()), str(root), str(parent_pid), str(port)],
                cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                close_fds=True, **_detach(),
            )
    except OSError:
        _write_state("failed", "Could not start the update process.")
        raise


def _wait_for_exit(pid):
    deadline = time.monotonic() + 90
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            if ctypes.get_last_error() == 87:  # Process no longer exists.
                return
            raise OSError("Could not wait for the DecisionsAI process.")
        try:
            if kernel.WaitForSingleObject(handle, 90_000) == 0:
                return
        finally:
            kernel.CloseHandle(handle)
    else:
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.25)
    raise TimeoutError("DecisionsAI did not close in time.")


def _prepare(root, changed_requirements):
    python = _console_python()
    setup = root / "bin" / "first_run.py"
    if (root / PROFILE_MARKER).is_file():
        subprocess.run([python, str(setup), "--non-interactive"], cwd=root,
                       check=True, stdin=subprocess.DEVNULL, timeout=600)
    elif changed_requirements:
        requirements = root / REQUIREMENTS
        if os.name == "nt":
            with tempfile.TemporaryDirectory() as temp:
                filtered = Path(temp) / REQUIREMENTS
                filtered.write_text("\n".join(
                    line for line in requirements.read_text(encoding="utf-8").splitlines()
                    if not line.startswith("pywhispercpp")
                ) + "\n", encoding="utf-8")
                _install_requirements(root, python, filtered)
        else:
            _install_requirements(root, python, requirements)


def _install_requirements(root, python, requirements):
    subprocess.run([python, "-m", "pip", "install", "-r", str(requirements)], cwd=root,
                   check=True, stdin=subprocess.DEVNULL, timeout=600, env={**os.environ, "DEBUG": "0"})


def _relaunch(root, port):
    cmd = [_console_python(), str(root / "bin" / "start.py"), "--skip-kill-existing"]
    if os.name != "nt" and (root / "bin" / "decisions-run.sh").is_file():
        cmd = ["/bin/bash", str(root / "bin" / "decisions-run.sh")]
    return subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, close_fds=True,
                            env={**os.environ, "DECISIONS_RESTARTING": "1",
                                 "DECISIONS_WEB_PORT": str(port), "VENV_DIR": sys.prefix},
                            **_detach())


def _wait_for_health(process, port, parent_pid):
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise OSError("The relaunched DecisionsAI process exited during startup.")
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                health = json.loads(response.read(4096))
                if health.get("status") == "ok" and health.get("pid") not in (None, parent_pid):
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.5)
    raise TimeoutError("The relaunched app did not become reachable.")


def run_update(root, parent_pid, port):
    exited = False
    relaunched = False
    try:
        _wait_for_exit(parent_pid)
        exited = True
        _write_state("updating", "Updating DecisionsAI from origin/main.")
        update = check_update(root)
        if update["status"] == "available":
            changed = bool(_git(root, "diff", "--name-only", "HEAD", update["target"], "--", REQUIREMENTS))
            _git(root, "merge", "--ff-only", "--no-overwrite-ignore", update["target"])
            _write_state("preparing", "Checking Python dependencies.")
            _prepare(root, changed)
        elif update["status"] != "current":
            raise ValueError(update["message"])
        _write_state("restarting", "Starting the updated app.")
        process = _relaunch(root, port)
        relaunched = True
        _wait_for_health(process, port, parent_pid)
        _write_state("complete", "DecisionsAI updated and restarted.")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Update failed: {type(exc).__name__}: {exc}", flush=True)
        _write_state("failed", "The update failed. See software-update.log for details.")
        if exited and not relaunched:
            try:
                _relaunch(root, port)
            except OSError as restart_error:
                print(f"Could not relaunch DecisionsAI: {restart_error}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run_update(Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])))
