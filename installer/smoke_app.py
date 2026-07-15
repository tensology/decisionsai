#!/usr/bin/env python3
"""Launch an installed DecisionsAI.app and prove writable-state + web health."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def _available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--allow-harness-maintenance", action="store_true")
    args = parser.parse_args()
    executable = args.app.resolve() / "Contents" / "MacOS" / "DecisionsAI"
    if not executable.is_file():
        parser.error(f"missing app executable: {executable}")

    with tempfile.TemporaryDirectory(prefix="decisionsai-installed-smoke-") as temporary:
        root = Path(temporary)
        data_root = root / "data"
        web_port = _available_local_port()
        env = {
            **os.environ,
            "HOME": str(root / "home"),
            "DECISIONS_DATA_DIR": str(data_root),
            "DECISIONS_AI_SKIP_MODEL_PREFETCH": "1",
            "DECISIONS_SKIP_UI_SCREEN_CAPTURE": "1",
            "DECISIONS_WEB_PORT": str(web_port),
            "QT_QPA_PLATFORM": "offscreen",
        }
        if not args.allow_harness_maintenance:
            env["DECISIONSAI_SKIP_HARNESS_STACK_SETUP"] = "1"
        started = time.monotonic()
        maintenance_status: str | None = None
        process = subprocess.Popen(
            [str(executable), "--skip-kill-existing"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        healthy = False
        try:
            while time.monotonic() - started < args.timeout:
                if process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{web_port}/health",
                        timeout=1,
                    ) as response:
                        payload = json.loads(response.read())
                        healthy = (
                            response.status == 200
                            and payload.get("status") == "ok"
                            and payload.get("pid") == process.pid
                        )
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
                if healthy:
                    break
                time.sleep(0.5)
            if healthy and args.allow_harness_maintenance:
                maintenance_path = root / "home" / ".decisions" / "harness-maintenance.json"
                maintenance_deadline = time.monotonic() + 10
                while time.monotonic() < maintenance_deadline:
                    try:
                        maintenance_status = json.loads(maintenance_path.read_text())["status"]
                        break
                    except (OSError, KeyError, json.JSONDecodeError):
                        time.sleep(0.25)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        output = process.stdout.read() if process.stdout is not None else ""
        elapsed = round(time.monotonic() - started, 3)
        database = data_root / "db" / "settings.db"
        bundled_database = args.app.resolve() / "Contents" / "Resources" / "db" / "settings.db"
        if not healthy or not database.is_file() or bundled_database.exists():
            print(output[-12000:])
            print(
                json.dumps(
                    {
                        "healthy": healthy,
                        "elapsed_seconds": elapsed,
                        "exit_code": process.returncode,
                        "expected_pid": process.pid,
                        "web_port": web_port,
                        "database_created": database.is_file(),
                        "bundle_was_mutated": bundled_database.exists(),
                        "harness_maintenance": maintenance_status,
                    },
                    indent=2,
                )
            )
            return 1
        print(
            json.dumps(
                {
                    "healthy": True,
                    "elapsed_seconds": elapsed,
                    "database": str(database),
                    "pid": process.pid,
                    "web_port": web_port,
                    "bundle_was_mutated": False,
                    "harness_maintenance": maintenance_status,
                },
                indent=2,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
