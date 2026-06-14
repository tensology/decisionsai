"""Reusable workflow ticket loop E2E harness.

Examples:
  rtk python3 scripts/workflow_ticket_loop_e2e.py seed
  rtk python3 scripts/workflow_ticket_loop_e2e.py assert-terminal --workflow-id 1 --ticket-id 2
  rtk python3 scripts/workflow_ticket_loop_e2e.py run-pytest --browser chromium --browser webkit
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
EXPECTED_SSE_EVENT_TYPES = {
    "route_decided",
    "workflow_step_completed",
    "validation_recorded",
    "loop_iteration",
    "skill_provisioned",
    "worker_completed",
}


def _quote_shell(path: Path) -> str:
    return "'" + str(path).replace("'", "'\"'\"'") + "'"


@dataclass
class SseCapture:
    events: list[dict[str, Any]]
    stop: Callable[[], None]
    state: dict[str, Any]


class WorkflowTicketLoopHarness:
    """HTTP/SSE helpers for the explicit until-green workflow E2E."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._internal_token: str | None = None

    def server_reachable(self, timeout: float = 3.0) -> bool:
        try:
            urllib.request.urlopen(f"{self.base_url}/workflows/", timeout=timeout)
            return True
        except Exception:
            return False

    def internal_api_token(self) -> str:
        if self._internal_token is not None:
            return self._internal_token
        with urllib.request.urlopen(f"{self.base_url}/workflows/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        match = re.search(r'<meta\s+name="decisionsai-internal-api-token"\s+content="([^"]*)"', html)
        self._internal_token = unescape(match.group(1)) if match else ""
        if not self._internal_token:
            raise AssertionError("Workflows page did not expose an internal API token")
        return self._internal_token

    def api_request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        timeout: int = 20,
    ) -> Any:
        body = None
        headers = {"X-DecisionsAI-Internal-Token": self.internal_api_token()}
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}/api{path}",
            method=method,
            data=body,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {"raw": raw}
            raise AssertionError(f"{method} /api{path} failed with {exc.code}: {payload}") from exc

    def seed_until_green_fixture(self, work_dir: Path | None = None, *, stamp: int | None = None) -> dict[str, int | str]:
        """Create a deterministic red-to-green workflow fixture through public APIs."""
        root = work_dir or Path(tempfile.mkdtemp(prefix="workflow-ticket-loop-e2e-"))
        root.mkdir(parents=True, exist_ok=True)
        stamp = int(stamp or time.time() * 1000)
        project_dir = root / f"workflow-project-{stamp}"
        project_dir.mkdir(parents=True, exist_ok=True)
        marker_path = root / f"workflow-green-{stamp}.txt"

        workflow_name = f"E2E until green workflow {stamp}"
        board_name = f"E2E workflow board {stamp}"
        project_name = f"E2E browser project {stamp}"
        ticket_title = f"E2E browser loop ticket {stamp}"

        project = self.api_request(
            "/projects",
            method="POST",
            data={
                "name": project_name,
                "folder_location": str(project_dir),
                "coding_backend": "codex",
                "coding_backend_model": "gpt-5-codex",
            },
        )
        project_id = int(project["id"])

        board = self.api_request(
            "/tickets/boards",
            method="POST",
            data={"name": board_name, "description": "Workflow loop browser E2E board."},
        )
        board_id = int(board["id"])
        self.api_request(
            f"/tickets/boards/{board_id}",
            method="PUT",
            data={
                "default_project_id": project_id,
                "orchestrator_policy": {
                    "harness_preferences": {
                        "frontend": {
                            "backend": "codex",
                            "model": "gpt-5-codex",
                            "skills": [
                                "executing-plans",
                                "tdd-workflow",
                                "webapp-testing",
                                "verification-loop",
                            ],
                        }
                    }
                },
            },
        )
        board_detail = self.api_request(f"/tickets/boards/{board_id}")
        lane_id = int((board_detail["lanes"] or [])[0]["id"])

        ticket = self.api_request(
            "/tickets/tickets",
            method="POST",
            data={
                "lane_id": lane_id,
                "title": ticket_title,
                "priority": "high",
                "complexity": "high",
                "description": (
                    "Frontend UI workflow loop ticket. Validate route skills, Playwright/browser-use "
                    "activity, computer-use evidence labels, context transfer, and green exit."
                ),
            },
        )
        ticket_id = int(ticket["id"])
        self.api_request(
            f"/tickets/tickets/{ticket_id}/todos",
            method="POST",
            data={"text": "Acceptance: final check must be green before exit."},
        )
        self.api_request(
            f"/tickets/tickets/{ticket_id}/links",
            method="POST",
            data={"title": "Local workflow fixture", "url": "https://example.test/workflow-loop"},
        )

        workflow = self.api_request(
            "/workflows",
            method="POST",
            data={
                "name": workflow_name,
                "description": "E2E workflow that fails red once, loops through fix, then exits green.",
                "workflow_type": "manual",
            },
        )
        workflow_id = int(workflow["id"])
        self.api_request(
            f"/workflows/{workflow_id}",
            method="PATCH",
            data={
                "workflow_input": json.dumps({
                    "goal": "Exit only when the Playwright/browser-use check is green.",
                    "max_iterations": 3,
                    "exit_when": "validation output is GREEN",
                    "check_command": f"test -f {_quote_shell(marker_path)}",
                }),
                "run_settings": {
                    "execution_mode": "sequential",
                    "concurrency_scope": "project",
                    "max_parallel_tickets": 1,
                    "branch_per_ticket": True,
                },
                "pre_chain": ["executing-plans", "tdd-workflow"],
                "post_chain": ["verification-loop"],
            },
        )
        self.api_request(
            f"/workflows/{workflow_id}/runs/0/ui-feedback",
            method="POST",
            data={
                "label": "approved",
                "reason": "Dense workflow panels with clear queue, loop, run, route, and activity hierarchy.",
                "board_id": board_id,
                "project_id": project_id,
                "screenshot_paths": [],
            },
        )

        route_step = self._add_step(
            workflow_id,
            {
                "position": 0,
                "name": "Route ticket with skills",
                "action_type": "run_command",
                "instruction": "Validate the route decision and skill handoff before running the loop.",
                "config": {
                    "command": "echo 'route selected: codex / gpt-5-codex'",
                    "timeout_seconds": 10,
                    "skills": ["executing-plans", "tdd-workflow"],
                    "tools": ["cli"],
                },
            },
        )
        fix_step = self._add_step(
            workflow_id,
            {
                "position": 1,
                "name": "Fix and rerun green check",
                "action_type": "run_command",
                "instruction": "Apply a deterministic fix and pass context to the validation step.",
                "config": {
                    "command": (
                        f"sleep 12; mkdir -p {_quote_shell(root)}; "
                        f"printf green > {_quote_shell(marker_path)}; "
                        "echo 'fixed: result packet now green-ready'"
                    ),
                    "timeout_seconds": 20,
                    "skills": ["verification-loop"],
                    "tools": ["other"],
                    "context": ["ticket_workflow_brief", "prior_step_result", "result_packet", "route_decision"],
                },
            },
        )
        check_step = self._add_step(
            workflow_id,
            {
                "position": 2,
                "name": "Validate browser with Playwright",
                "action_type": "playwright",
                "instruction": "Open the workflow validation fixture and fail red until the marker is present.",
                "config": {
                    "headless": True,
                    "timeout_seconds": 20,
                    "skills": ["webapp-testing"],
                    "tools": ["playwright", "browser_use"],
                    "context": ["ticket_workflow_brief", "prior_step_result", "result_packet", "route_decision"],
                    "code": (
                        "from pathlib import Path\n"
                        f"marker = Path({str(marker_path)!r})\n"
                        "if marker.exists():\n"
                        "    print('GREEN validation passed: ticket brief, prior result, and route decision transferred.')\n"
                        "    print('flow summary: Selected the workflow, queued the ticket, started the run, watched the loop return red to fix, and confirmed green exit.')\n"
                        "    print('layout hierarchy notes: Queue, loop, active run, route, and activity panels stayed distinct with the active step and loop iteration visible.')\n"
                        "    print('1. [click] Select until-green workflow -> workflow detail loaded')\n"
                        "    print('2. [click] Add ticket to workflow queue -> queued ticket row visible')\n"
                        "    print('3. [click] Start run -> active run and loop activity visible')\n"
                        "else:\n"
                        "    print('RED validation failed: marker missing, loop back to fix step.')\n"
                        "    raise SystemExit(1)\n"
                    ),
                },
                "validation_type": "browser_ui",
                "validation_prompt": "The workflow must exit only after the UI check is green.",
            },
        )
        browser_step = self._add_step(
            workflow_id,
            {
                "position": 3,
                "name": "Inspect with Browser Use",
                "action_type": "browser_use",
                "instruction": "Browser-use smoke coverage step for visible tool selection and activity evidence.",
                "config": {"skills": ["browser-qa"], "tools": ["browser_use"]},
            },
        )
        computer_step = self._add_step(
            workflow_id,
            {
                "position": 4,
                "name": "Inspect with Computer Use",
                "action_type": "computer_use",
                "instruction": "Computer-use smoke coverage step for route/tool selection and screenshot evidence labels.",
                "config": {"skills": ["browser-qa"], "tools": ["computer_use"]},
            },
        )

        self.api_request(
            f"/workflows/{workflow_id}/steps/{route_step['id']}",
            method="PATCH",
            data={"on_pass_goto": int(check_step["id"])},
        )
        self.api_request(
            f"/workflows/{workflow_id}/steps/{check_step['id']}",
            method="PATCH",
            data={"on_fail_goto": int(fix_step["id"]), "on_pass_goto": -1},
        )
        self.api_request(
            f"/workflows/{workflow_id}/steps/{fix_step['id']}",
            method="PATCH",
            data={"on_pass_goto": int(check_step["id"])},
        )

        return {
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "board_id": board_id,
            "board_name": board_name,
            "project_id": project_id,
            "project_name": project_name,
            "ticket_id": ticket_id,
            "ticket_title": ticket_title,
            "route_step_id": int(route_step["id"]),
            "fix_step_id": int(fix_step["id"]),
            "check_step_id": int(check_step["id"]),
            "browser_step_id": int(browser_step["id"]),
            "computer_step_id": int(computer_step["id"]),
            "marker_path": str(marker_path),
            "work_dir": str(root),
        }

    def _workflow_step_by_name(self, workflow: dict[str, Any], name: str) -> dict[str, Any]:
        for step in workflow.get("steps") or []:
            if step.get("name") == name:
                return step
        raise AssertionError(f"Workflow step not found: {name}")

    def _add_step(self, workflow_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        workflow = self.api_request(f"/workflows/{workflow_id}/steps", method="POST", data=payload)
        return self._workflow_step_by_name(workflow, payload["name"])

    def start_sse_capture(self, client_id: str) -> SseCapture:
        events: list[dict[str, Any]] = []
        ready = threading.Event()
        stop = threading.Event()
        state: dict[str, Any] = {"error": "", "response": None}

        def _reader() -> None:
            req = urllib.request.Request(
                f"{self.base_url}/api/events/stream?client_id={client_id}",
                headers={"X-DecisionsAI-Internal-Token": self.internal_api_token()},
            )
            current_event = ""
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    state["response"] = resp
                    for raw in resp:
                        if stop.is_set():
                            break
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        if line.startswith("event:"):
                            current_event = line.split(":", 1)[1].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line.split(":", 1)[1].strip()
                        if current_event == "ready":
                            ready.set()
                            continue
                        if current_event == "app":
                            try:
                                events.append(json.loads(data))
                            except Exception:
                                events.append({"raw": data})
            except Exception as exc:
                if not stop.is_set():
                    state["error"] = repr(exc)
            finally:
                ready.set()

        thread = threading.Thread(target=_reader, name=f"workflow-sse-{client_id}", daemon=True)
        thread.start()
        if not ready.wait(5):
            raise AssertionError("SSE stream did not become ready")

        def _stop() -> None:
            stop.set()
            resp = state.get("response")
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            thread.join(timeout=2)

        return SseCapture(events=events, stop=_stop, state=state)

    def wait_for_sse_events(
        self,
        events: list[dict[str, Any]],
        workflow_id: int,
        expected_types: set[str] | None = None,
        *,
        timeout_seconds: float = 15.0,
    ) -> list[dict[str, Any]]:
        expected = expected_types or EXPECTED_SSE_EVENT_TYPES
        deadline = time.time() + timeout_seconds
        matching: list[dict[str, Any]] = []
        while time.time() < deadline:
            matching = [
                event.get("data") or {}
                for event in events
                if event.get("type") == "orchestration.event"
                and int((event.get("data") or {}).get("workflow_id") or 0) == int(workflow_id)
            ]
            seen = {str(event.get("event_type") or "") for event in matching}
            if expected.issubset(seen):
                return matching
            time.sleep(0.2)
        seen = {str(event.get("event_type") or "") for event in matching}
        raise AssertionError(f"SSE orchestration events missing {sorted(expected - seen)}; saw {sorted(seen)}")

    def wait_until_ticket_not_active(self, ticket_title: str, *, timeout_seconds: float = 60.0) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            runs = self.api_request("/workflows/active-runs?limit=50")
            if not any(str(run.get("ticket_title") or "").find(ticket_title) >= 0 for run in runs):
                return
            time.sleep(0.5)
        raise AssertionError(f"Ticket still has an active workflow run: {ticket_title}")

    def terminal_summary(
        self,
        workflow_id: int,
        ticket_id: int,
        run_id: int | None = None,
        *,
        board_id: int | None = None,
        ticket_title: str | None = None,
    ) -> dict[str, Any]:
        workflow = self.api_request(f"/workflows/{workflow_id}")
        runs = workflow.get("runs") or []
        completed_runs = [run for run in runs if run.get("status") == "completed"]
        if run_id is not None:
            completed_runs = [run for run in completed_runs if int(run.get("id") or 0) == int(run_id)]
        if not completed_runs:
            raise AssertionError("Completed run history missing")
        latest = completed_runs[0]
        ticket_status = None
        ticket_lookup_source = ""
        ticket_lookup_error = ""
        try:
            ticket = self.api_request(f"/tickets/tickets/{ticket_id}")
            ticket_status = ticket.get("workflow_status")
            ticket_lookup_source = "ticket"
        except AssertionError as exc:
            ticket_lookup_error = str(exc)
        if ticket_status != "completed" and board_id is not None:
            board = self.api_request(f"/tickets/boards/{board_id}")
            for lane in board.get("lanes") or []:
                for ticket in lane.get("tickets") or []:
                    same_id = int(ticket.get("id") or 0) == int(ticket_id)
                    same_title = ticket_title and ticket.get("title") == ticket_title
                    if same_id or same_title:
                        ticket_status = ticket.get("workflow_status")
                        ticket_lookup_source = "board"
                        break
                if ticket_lookup_source == "board":
                    break
        if ticket_status != "completed":
            suffix = f" ({ticket_lookup_error})" if ticket_lookup_error else ""
            raise AssertionError(f"Ticket workflow_status is {ticket_status!r}, expected completed{suffix}")
        events = self.api_request(f"/workflows/{workflow_id}/orchestrator-events?limit=120")
        return {
            "workflow_id": workflow_id,
            "ticket_id": ticket_id,
            "run_id": int(latest.get("id")),
            "run_status": latest.get("status"),
            "ticket_workflow_status": ticket_status,
            "ticket_lookup_source": ticket_lookup_source,
            "event_types": sorted({str(event.get("event_type") or "") for event in events}),
            "green_seen": any("GREEN validation passed" in json.dumps(event, default=str) for event in events),
            "completed_seen": any(str(event.get("event_type") or "") == "worker_completed" for event in events),
        }


def workflow_ws_bootstrap_script() -> str:
    return """() => {
        window.__workflowWsMessages = [];
        window.__workflowWsErrors = [];
        window.__workflowWsOpen = false;
        const ws = new WebSocket(`ws://${location.host}/api/ws/workflows`);
        window.__workflowWs = ws;
        ws.onopen = () => { window.__workflowWsOpen = true; };
        ws.onmessage = (event) => { window.__workflowWsMessages.push(event.data); };
        ws.onerror = () => { window.__workflowWsErrors.push('error'); };
    }"""


def workflow_ws_messages_script() -> str:
    return """() => (window.__workflowWsMessages || []).map((raw) => {
        try { return JSON.parse(raw); } catch (_) { return { raw }; }
    })"""


def workflow_ws_close_script() -> str:
    return "() => window.__workflowWs && window.__workflowWs.close()"


def wait_until_workflow_ws_open(page: Any, *, timeout_seconds: float = 5.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if page.evaluate("() => Boolean(window.__workflowWsOpen)"):
            return
        page.wait_for_timeout(100)
    raise AssertionError("Workflow WebSocket did not open")


def _cmd_seed(args: argparse.Namespace) -> int:
    harness = WorkflowTicketLoopHarness(args.base_url)
    if not harness.server_reachable():
        raise SystemExit(f"Web server not reachable at {args.base_url}")
    work_dir = Path(args.work_dir) if args.work_dir else None
    ids = harness.seed_until_green_fixture(work_dir)
    print(json.dumps(ids, indent=2))
    return 0


def _cmd_assert_terminal(args: argparse.Namespace) -> int:
    harness = WorkflowTicketLoopHarness(args.base_url)
    summary = harness.terminal_summary(args.workflow_id, args.ticket_id, args.run_id)
    print(json.dumps(summary, indent=2))
    return 0


def _cmd_run_pytest(args: argparse.Namespace) -> int:
    browsers = args.browser or ["chromium", "webkit"]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "e2e_playwright",
        "tests/ui/test_workflow_ticket_loop_browser_playwright_e2e.py",
    ]
    for browser in browsers:
        cmd.extend(["--browser", browser])
    extra = list(args.extra or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    if extra:
        cmd.extend(extra)
    else:
        cmd.append("-q")
    return subprocess.call(cmd)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable DecisionsAI workflow ticket loop E2E harness")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="DecisionsAI web base URL")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Create the deterministic until-green workflow fixture")
    seed.add_argument("--work-dir", default="", help="Optional project/marker root directory")
    seed.set_defaults(func=_cmd_seed)

    terminal = sub.add_parser("assert-terminal", help="Assert completed run/ticket/event terminal state")
    terminal.add_argument("--workflow-id", type=int, required=True)
    terminal.add_argument("--ticket-id", type=int, required=True)
    terminal.add_argument("--run-id", type=int)
    terminal.set_defaults(func=_cmd_assert_terminal)

    run_pytest = sub.add_parser("run-pytest", help="Run the reusable Playwright E2E pytest")
    run_pytest.add_argument("--browser", action="append", choices=["chromium", "webkit", "firefox"])
    run_pytest.add_argument("extra", nargs=argparse.REMAINDER, help="Extra pytest args after --")
    run_pytest.set_defaults(func=_cmd_run_pytest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
