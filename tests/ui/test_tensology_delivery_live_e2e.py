"""Live Tensology approval-to-evidence delivery certification."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.workflow_ticket_loop_e2e import DEFAULT_BASE_URL, WorkflowTicketLoopHarness


pytestmark = pytest.mark.live_cli_matrix


class _TensologyState:
    def __init__(self, project_name: str) -> None:
        self.ticket = {
            "id": 17,
            "project_id": 91,
            "project_name": project_name,
            "reference": "E2E-91-001",
            "title": "Write and verify the approved delivery marker",
            "description": "Create delivery-proof.txt containing exactly TENSOLOGY_LOOP_GREEN and verify it.",
            "implementation_plan": "Make the smallest file change, read it back, and run a deterministic verification command.",
            "tasks": ["Create delivery-proof.txt", "Verify its exact contents"],
            "success_criteria": ["delivery-proof.txt contains exactly TENSOLOGY_LOOP_GREEN"],
            "execution_complexity": "low",
            "execution_status": "awaiting_decisions",
            "decisions_thread_id": "",
            "repository_ready": True,
        }
        self.requests: list[dict] = []


def _service(state: _TensologyState) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, data: object, status: int = 200) -> None:
            raw = json.dumps({"data": data, "error": None}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path.endswith("/tickets"):
                self._reply([dict(state.ticket)])
            elif path.endswith("/discussion-assessments") or path.endswith("/discussion-context"):
                self._reply([])
            else:
                self._reply({})

        def do_POST(self) -> None:  # noqa: N802
            self._record("POST")

        def do_PATCH(self) -> None:  # noqa: N802
            self._record("PATCH")

        def _record(self, method: str) -> None:
            body = self._body()
            path = urlparse(self.path).path
            state.requests.append({
                "method": method,
                "path": path,
                "body": body,
                "idempotency_key": self.headers.get("Idempotency-Key") or "",
                "authorization": self.headers.get("Authorization") or "",
            })
            if path.endswith("/tickets/17/execution"):
                if body.get("thread_id"):
                    state.ticket["decisions_thread_id"] = str(body["thread_id"])
                if body.get("status"):
                    state.ticket["execution_status"] = str(body["status"])
                for source, target in (
                    ("backend", "decisions_backend"),
                    ("provider", "decisions_provider"),
                    ("model", "decisions_model"),
                ):
                    if body.get(source):
                        state.ticket[target] = body[source]
            self._reply({"ok": True})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name="fake-tensology", daemon=True)
    thread.start()
    return server, thread


def _wait_until(predicate, timeout: float, message: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(1)
    raise AssertionError(message)


def test_live_tensology_approval_executes_once_and_returns_redacted_evidence(tmp_path: Path) -> None:
    if os.environ.get("DECISIONS_RUN_LIVE_TENSOLOGY_E2E") != "1":
        pytest.skip("Set DECISIONS_RUN_LIVE_TENSOLOGY_E2E=1 to run live Tensology certification.")
    harness = WorkflowTicketLoopHarness(DEFAULT_BASE_URL)
    if not harness.server_reachable():
        pytest.skip(f"Web server not reachable at {DEFAULT_BASE_URL}")

    project_name = f"[e2e] Tensology delivery {int(time.time())}"
    project_dir = tmp_path / "tensology-delivery"
    project_dir.mkdir()
    (project_dir / "README.md").write_text("Disposable Tensology delivery fixture.\n", encoding="utf-8")
    project = harness.api_request(
        "/projects",
        method="POST",
        data={
            "name": project_name,
            "folder_location": str(project_dir),
            "coding_backend": "codex",
            "coding_backend_model": "gpt-5.6-luna",
        },
    )
    project_id = int(project["id"])
    project_board = harness.api_request(f"/projects/{project_id}/kanban-board").get("board") or {}
    runtime = harness.api_request("/diagnostics/runtime")
    db_path = Path(str(runtime["db_root"])) / "settings.db"
    state = _TensologyState(project_name)
    server, thread = _service(state)
    original: tuple | None = None
    chat_id = 0
    try:
        with sqlite3.connect(db_path) as db:
            original = db.execute(
                "SELECT tensology_enabled, tensology_url, tensology_key, "
                "project_cli_low_backend, project_cli_low_model, project_cli_low_model_provider, "
                "project_cli_low_codex_intelligence, project_cli_low_codex_speed FROM settings LIMIT 1"
            ).fetchone()
            assert original is not None
            db.execute(
                "UPDATE settings SET tensology_enabled = 1, tensology_url = ?, tensology_key = ?, "
                "project_cli_low_backend = 'codex', project_cli_low_model = 'gpt-5.6-luna', "
                "project_cli_low_model_provider = 'openai', project_cli_low_codex_intelligence = 'low', "
                "project_cli_low_codex_speed = 'standard'",
                (f"http://127.0.0.1:{server.server_port}", "tns_decisionsai_e2e-only"),
            )
            db.commit()

        _wait_until(
            lambda: bool(state.ticket["decisions_thread_id"]),
            30,
            f"Tensology approval was not claimed: {state.requests}",
        )
        chat_id = int(state.ticket["decisions_thread_id"])
        _wait_until(
            lambda: str(harness.api_request(f"/workflows/studio/tasks/{chat_id}/execution").get("status") or "")
            in {"completed", "failed"},
            1200,
            "The approved Tensology execution did not reach a terminal state.",
        )
        execution = harness.api_request(f"/workflows/studio/tasks/{chat_id}/execution")
        assert execution.get("status") == "completed", execution
        assert (project_dir / "delivery-proof.txt").read_text(encoding="utf-8").strip() == "TENSOLOGY_LOOP_GREEN"

        _wait_until(
            lambda: state.ticket["execution_status"] in {"done", "failed"},
            30,
            f"Decisions did not return terminal evidence: {state.requests}",
        )
        assert state.ticket["execution_status"] == "done", state.requests
        time.sleep(5)

        claims = [row for row in state.requests if row["method"] == "POST" and row["path"].endswith("/tickets/17/execution")]
        terminal = [row for row in state.requests if row["method"] == "PATCH" and row["body"].get("status") == "done"]
        assert len(claims) == 1, claims
        assert len({row["body"].get("thread_id") for row in claims}) == 1
        assert terminal, state.requests
        assert all(row["authorization"] == "Bearer tns_decisionsai_e2e-only" for row in state.requests)
        assert all(row["idempotency_key"] for row in claims + terminal)
        returned = json.dumps(terminal[-1]["body"].get("evidence") or {})
        assert "tns_decisionsai_e2e-only" not in returned
        assert str(project_dir) not in returned
        assert terminal[-1]["body"]["evidence"]["route"]["selected"]["model"] == "gpt-5.6-luna"
    finally:
        if original is not None:
            with sqlite3.connect(db_path) as db:
                db.execute(
                    "UPDATE settings SET tensology_enabled = ?, tensology_url = ?, tensology_key = ?, "
                    "project_cli_low_backend = ?, project_cli_low_model = ?, project_cli_low_model_provider = ?, "
                    "project_cli_low_codex_intelligence = ?, project_cli_low_codex_speed = ?",
                    original,
                )
                db.commit()
        time.sleep(2)
        server.shutdown()
        thread.join(timeout=5)
        if chat_id:
            try:
                harness.api_request(f"/workflows/studio/tasks/{chat_id}/execution/stop", method="POST")
            except Exception:
                pass
            harness._best_effort_api_delete(f"/workflows/studio/tasks/{chat_id}?delete_linked_ticket=true")
        if project_board.get("id"):
            harness._best_effort_api_delete(f"/tickets/boards/{int(project_board['id'])}")
        harness._best_effort_api_delete(f"/projects/{project_id}")
