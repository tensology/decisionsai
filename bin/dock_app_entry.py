"""Dock .app entry — run inside decisions.app/Contents/MacOS/decisions (not bare Python)."""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _project_paths() -> tuple[Path, Path]:
    env_root = os.environ.get("DECISIONS_PROJECT_ROOT", "").strip()
    env_bundle = os.environ.get("DECISIONS_APP_BUNDLE", "").strip()
    if env_root:
        project_root = Path(env_root)
        app_root = Path(env_bundle) if env_bundle else project_root / "decisions.app"
        return app_root, project_root

    executable = Path(__file__).resolve()
    if executable.parent.name == "MacOS" and executable.parent.parent.name == "Contents":
        app_root = executable.parent.parent.parent
        return app_root, app_root.parent

    project_root = executable.parent.parent
    app_root = project_root / "decisions.app"
    return app_root, project_root


def _append_launcher_log(project_root: Path, message: str) -> None:
    log_dir = Path.home() / ".decisions" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "launcher.log"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def _is_already_running(project_root: Path) -> bool:
    marker = f"{project_root}/bin/start.py"
    try:
        output = subprocess.check_output(["ps", "aux"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return False
    for line in output.splitlines():
        if marker not in line:
            continue
        parts = line.split()
        if len(parts) > 1:
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            if pid != os.getpid():
                return True
    return False


def _request_activation() -> None:
    run_dir = Path.home() / ".decisions" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "activate.request").touch()


def _start_sidecar(project_root: Path) -> None:
    sidecar_script = project_root / "bin" / "decisions-sidecar.sh"
    if not sidecar_script.is_file():
        return
    subprocess.run(
        ["bash", "-c", f"source {sidecar_script!s} && decisions_start_sidecar {project_root!s}"],
        check=False,
    )


def _bootstrap_env(app_root: Path, project_root: Path) -> None:
    os.environ.setdefault("DECISIONS_DOCK_APP", "1")
    os.environ.setdefault("DECISIONS_APP_BUNDLE", str(app_root))
    os.environ.setdefault("DECISIONS_PROJECT_ROOT", str(project_root))
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)


def main() -> int:
    app_root, project_root = _project_paths()
    _bootstrap_env(app_root, project_root)

    _append_launcher_log(project_root, f"===== DecisionsAI dock launch {datetime.now()} =====")
    _append_launcher_log(project_root, f"Project root: {project_root}")
    _append_launcher_log(project_root, f"Bundle executable: {Path(__file__).resolve()}")

    if _is_already_running(project_root):
        _append_launcher_log(project_root, "DecisionsAI already running — requesting activation.")
        _request_activation()
        return 0

    venv_python = Path(
        os.environ.get("DECISIONS_PYTHON", Path.home() / ".virtualenvs/decisions/bin/python")
    )
    start_script = project_root / "bin" / "start.py"
    if not start_script.is_file():
        _append_launcher_log(project_root, "Missing bin/start.py")
        return 1
    if not venv_python.is_file():
        _append_launcher_log(project_root, "First launch — run bin/decisions.sh setup.")
        setup = project_root / "bin" / "decisions.sh"
        if setup.is_file():
            os.execv("/bin/bash", ["/bin/bash", str(setup), "--foreground"])
        return 1

    run_dir = Path.home() / ".decisions" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "decisions-run.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    _append_launcher_log(project_root, "Starting DecisionsAI (dock bundle entry).")
    _start_sidecar(project_root)
    time.sleep(0.1)

    sys.argv = [str(start_script)]
    runpy.run_path(str(start_script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
