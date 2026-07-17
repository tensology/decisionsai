#!/usr/bin/env python3
"""Run workflow browser acceptance tests against an owned, disposable server.

The launcher deliberately owns both the state directory and the Uvicorn process
group.  This prevents local/CI acceptance runs from touching normal DecisionsAI
data or leaving background workers behind after failures and interrupts.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TARGET = "tests/ui/test_workflow_ticket_loop_browser_playwright_e2e.py"
SPOTIFY_TARGET = "tests/core/test_spotify_program_live_e2e.py"


def _available_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _profile_contract(profile: str) -> tuple[str, str]:
    if profile in {"spotify", "dogfood"}:
        return "e2e", SPOTIFY_TARGET
    return "e2e_playwright", CANONICAL_TARGET


def _pytest_command(profile: str, extra: list[str]) -> list[str]:
    marker, target = _profile_contract(profile)
    command = [sys.executable, "-m", "pytest", "-m", marker, target]
    command.extend(extra or ["-q"])
    return command


def _wait_for_server(base_url: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    health_url = f"{base_url}/workflows/"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"isolated server exited early with status {process.returncode}")
        try:
            with urllib.request.urlopen(health_url, timeout=1) as response:
                if response.status < 500:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise TimeoutError(f"isolated server did not become ready at {health_url}")


def _stop_process_group(process: subprocess.Popen[bytes], timeout: float = 10.0) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait(timeout=5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run workflow E2E tests against an isolated DecisionsAI server",
    )
    parser.add_argument("--profile", default="until-green", choices=["until-green", "spotify", "dogfood"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="Owned server port; 0 selects an available port")
    parser.add_argument("--data-dir", default="", help="Disposable state directory (temporary by default)")
    parser.add_argument("--keep-data", action="store_true", help="Keep an automatically-created state directory")
    parser.add_argument("--log-file", default="", help="Write Uvicorn output to this file")
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, pytest_args = parser.parse_known_args(argv)
    port = args.port or _available_port(args.host)
    base_url = f"http://{args.host}:{port}"

    automatic_data_dir = not bool(args.data_dir)
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else Path(
        tempfile.mkdtemp(prefix="decisions-workflow-e2e-")
    )
    data_dir.mkdir(parents=True, exist_ok=True)

    automatic_log = not bool(args.log_file)
    log_path = Path(args.log_file).expanduser().resolve() if args.log_file else data_dir / "web-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "DECISIONS_DATA_DIR": str(data_dir),
            "DECISIONS_AI_SKIP_MODEL_PREFETCH": "1",
            "DECISIONS_SKIP_UI_SCREEN_CAPTURE": "1",
            "WORKFLOW_E2E_BASE_URL": base_url,
            "PYTHONUNBUFFERED": "1",
        }
    )
    server_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "distr.gui.web.server:create_app",
        "--factory",
        "--host",
        args.host,
        "--port",
        str(port),
    ]
    process: subprocess.Popen[bytes] | None = None
    exit_code = 1
    try:
        with log_path.open("wb") as server_log:
            process = subprocess.Popen(
                server_command,
                cwd=ROOT,
                env=env,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            _wait_for_server(base_url, process, args.startup_timeout)
            print(f"Workflow E2E server ready: {base_url} (pid {process.pid}, state {data_dir})", flush=True)
            exit_code = subprocess.call(_pytest_command(args.profile, pytest_args), cwd=ROOT, env=env)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as exc:
        print(f"Workflow E2E launcher failed: {exc}", file=sys.stderr)
        if log_path.exists():
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            if tail:
                print("\n".join(tail), file=sys.stderr)
        exit_code = 1
    finally:
        if process is not None:
            _stop_process_group(process)
            if process.poll() is None:
                raise RuntimeError(f"isolated server process group {process.pid} is still running")
        if automatic_data_dir and not args.keep_data:
            shutil.rmtree(data_dir, ignore_errors=True)
        elif automatic_data_dir:
            print(f"Kept workflow E2E state: {data_dir}", flush=True)
        if automatic_log and not data_dir.exists() and exit_code:
            print("Use --log-file or --keep-data to retain the isolated server log.", file=sys.stderr)
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
