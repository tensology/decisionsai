import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core import software_update
from distr.gui.web.routes.settings import software_update as routes


def git(root, *args, input=None):
    return subprocess.run(
        ["git", *args], cwd=root, input=input, capture_output=True, check=True, text=True,
    ).stdout.strip()


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "installation"
    origin = tmp_path / "origin"
    root.mkdir()
    origin.mkdir()
    git(origin, "init", "--bare", "-b", "main")
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Installer Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "remote", "add", "origin", str(origin))
    (root / "version.txt").write_text("old")
    (root / ".gitignore").write_text("installer/.requirements_installed_profile\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial version")
    current = git(root, "rev-parse", "HEAD")
    git(root, "push", "origin", "HEAD:refs/heads/main")
    blob = git(root, "hash-object", "-w", "--stdin", input="new")
    ignore = git(root, "rev-parse", "HEAD:.gitignore")
    tree = git(root, "mktree", input=f"100644 blob {ignore}\t.gitignore\n100644 blob {blob}\tversion.txt\n")
    latest = git(root, "commit-tree", tree, "-p", current, "-m", "New version")
    git(root, "push", "origin", f"{latest}:refs/heads/main")
    return root, current, latest


def test_check_detects_update_and_preserves_checkout(installation):
    root, current, latest = installation
    assert software_update.check_update(root)["target"] == latest
    assert git(root, "rev-parse", "HEAD") == current
    (root / "draft.txt").write_text("untracked work")
    assert software_update.check_update(root)["status"] == "blocked"
    assert (root / "draft.txt").read_text() == "untracked work"


def test_failed_precondition_keeps_app_open(installation, monkeypatch, tmp_path):
    root, current, _ = installation
    monkeypatch.setenv("DECISIONS_DATA_DIR", str(tmp_path / "data"))
    def cannot_close(_pid):
        raise TimeoutError("The existing process did not close.")

    monkeypatch.setattr(software_update, "_wait_for_exit", cannot_close)
    monkeypatch.setattr(software_update, "_relaunch", lambda *args: pytest.fail("Do not start a second app"))
    assert software_update.run_update(root, 10, 8765) == 1
    assert software_update.read_state()["phase"] == "failed"
    assert git(root, "rev-parse", "HEAD") == current


def test_legacy_setup_installs_only_changed_requirements(tmp_path, monkeypatch):
    previous = "pywhispercpp @ git+https://example.invalid/whisper\nplaywright==1.63.0\npathspec>=0.11.0\n"
    (tmp_path / software_update.REQUIREMENTS).write_text(
        previous.replace("pathspec>=0.11.0", "pathspec>=1.1.1") + "# Comment only\n",
        encoding="utf-8",
    )
    installs = []
    monkeypatch.setattr(
        software_update, "_install_requirements",
        lambda root, python, requirements: installs.append(requirements.read_text(encoding="utf-8")),
    )
    software_update._prepare(tmp_path, previous)
    assert installs == ["pathspec>=1.1.1\n"]


def test_update_requires_local_browser_action(monkeypatch):
    app = FastAPI()
    router = APIRouter()
    routes.register_routes(router, None)
    app.include_router(router)
    monkeypatch.setattr(routes, "_begin", lambda *args: pytest.fail("Unexpected update"))
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    assert client.post("/software-update").status_code == 403
    response = client.post("/software-update", headers={
        "origin": "https://example.invalid", "sec-fetch-site": "cross-site",
        "sec-fetch-mode": "cors", "x-decisions-update": "click",
    })
    assert response.status_code == 403


def test_update_starts_worker_before_requesting_app_exit(monkeypatch, tmp_path):
    from PyQt6.QtCore import QCoreApplication

    app = FastAPI()
    router = APIRouter()
    routes.register_routes(router, None)
    app.include_router(router)
    calls = []
    monkeypatch.setattr(QCoreApplication, "instance", staticmethod(lambda: object()))
    monkeypatch.setattr(routes, "resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr(routes, "_current_check", lambda *_: {"status": "available"})
    monkeypatch.setattr(software_update, "read_state", lambda: {})
    monkeypatch.setattr(
        software_update, "start_update",
        lambda root, pid, port: calls.append(("worker", root, pid, port)),
    )
    monkeypatch.setattr(routes, "_quit_for_update", lambda: calls.append(("quit",)))
    client = TestClient(app, base_url="http://127.0.0.1:8767")
    response = client.post("/software-update", headers={
        "origin": "http://127.0.0.1:8767", "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors", "x-decisions-update": "click",
    })
    assert response.status_code == 202
    assert calls == [("worker", tmp_path, os.getpid(), 8767), ("quit",)]


def test_detached_worker_waits_fast_forwards_prepares_and_relaunches(installation, tmp_path):
    root, current, latest = installation
    # Add launcher fixtures to both versions without changing the working checkout.
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "first_run.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:] == ['--non-interactive']\nPath('prepared').write_text('yes')\n"
    )
    (bin_dir / "start.py").write_text(
        "import json, os, time\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self):\n"
        "  self.send_response(200)\n  self.end_headers()\n"
        "  self.wfile.write(json.dumps({'status':'ok','pid':os.getpid()}).encode())\n"
        "  if self.path == '/stop': self.server.done = True\n"
        "server = HTTPServer(('127.0.0.1',int(os.environ['DECISIONS_WEB_PORT'])),Handler)\n"
        "server.timeout = 2\nserver.done = False\ndeadline = time.monotonic()+15\n"
        "while not server.done and time.monotonic() < deadline:\n"
        " server.handle_request()\n"
        " if not server.done and not os.path.exists('prepared'): break\n"
    )
    git(root, "add", "bin")
    git(root, "commit", "-m", "Fixture launcher")
    current = git(root, "rev-parse", "HEAD")
    original_tree = git(root, "rev-parse", "HEAD^{tree}")
    entries = [line for line in git(root, "ls-tree", original_tree).splitlines() if not line.endswith("\tversion.txt")]
    blob = git(root, "rev-parse", f"{latest}:version.txt")
    tree = git(root, "mktree", input="\n".join(entries + [f"100644 blob {blob}\tversion.txt"]) + "\n")
    latest = git(root, "commit-tree", tree, "-p", current, "-p", latest, "-m", "Update with fixtures")
    git(root, "push", "origin", f"{latest}:refs/heads/main")
    marker = root / software_update.PROFILE_MARKER
    marker.parent.mkdir()
    marker.touch()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    data = tmp_path / "data"
    with (tmp_path / "worker.log").open("w+") as log, subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    ) as parent:
        try:
            with subprocess.Popen(
                [sys.executable, str(Path(software_update.__file__)), str(root), str(parent.pid), str(port)],
                env={**os.environ, "DECISIONS_DATA_DIR": str(data)}, stdout=log, stderr=log,
            ) as updater:
                try:
                    with pytest.raises(subprocess.TimeoutExpired):
                        updater.wait(timeout=1)
                    assert git(root, "rev-parse", "HEAD") == current
                    parent.terminate()
                    parent.wait(timeout=5)
                    exit_code = updater.wait(timeout=20)
                    log.seek(0)
                    assert exit_code == 0, log.read()
                    assert git(root, "rev-parse", "HEAD") == latest
                    assert (root / "prepared").read_text() == "yes"
                    status = json.loads((data / "run/software-update.json").read_text())
                    assert status["phase"] == "complete"
                finally:
                    if updater.poll() is None:
                        updater.kill()
        finally:
            try:
                with urlopen(f"http://127.0.0.1:{port}/stop", timeout=2):
                    pass
            except OSError:
                pass
            if parent.poll() is None:
                parent.kill()
