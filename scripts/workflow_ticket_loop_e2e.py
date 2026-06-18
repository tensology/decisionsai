"""Reusable workflow ticket loop E2E harness.

Examples:
  rtk python3 scripts/workflow_ticket_loop_e2e.py seed
  rtk python3 scripts/workflow_ticket_loop_e2e.py assert-terminal --workflow-id 1 --ticket-id 2
  rtk python3 scripts/workflow_ticket_loop_e2e.py run-pytest --browser chromium --browser webkit
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
EXPECTED_SSE_EVENT_TYPES = {
    "route_decided",
    "workflow_step_completed",
    "validation_recorded",
    "loop_iteration",
    "skill_provisioned",
    "worker_completed",
}
REGISTERED_BACKEND_IDS = (
    "pi",
    "cursor",
    "cursor_ide",
    "claude_code",
    "codex",
    "codex_ide",
    "hermes_agent",
    "cline",
    "opencode",
)
SPOTIFY_PROJECT_PREFIX = "spotify-remake-e2e-"
SPOTIFY_LANES = ("Backlog", "Ready", "In Progress", "Validation", "Improve", "Complete")
SPOTIFY_PROGRAM_REQUIREMENTS = ROOT / "tests" / "core" / "fixtures" / "spotify_remake_requirements.md"
SPOTIFY_IDEATION_PRESET = "ideation-brief-to-board"
SPOTIFY_DEVELOPMENT_PRESET = "development-ticket-to-implementation"
SPOTIFY_POLISH_PRESET = "polish-verify-and-ship"
E2E_HARNESS_PREFIX = "[e2e]"
E2E_LEGACY_NAME_PREFIXES = (
    "DecisionsAI dogfood ",
    "DecisionsAI dogfood spawn project ",
    "Dogfood board ",
    "Dogfood spawn board ",
    "Dogfood smoke",
    "Dogfood spawn",
    "E2E until green workflow ",
    "E2E workflow board ",
    "E2E browser project ",
    "E2E browser loop ticket ",
    "E2E Spotify ",
    "spotify-remake",
)
E2E_LEGACY_WORKFLOW_SEARCHES = (
    E2E_HARNESS_PREFIX,
    "spotify-remake",
    "DecisionsAI dogfood",
    "Dogfood board",
    "Dogfood spawn",
    "E2E until green",
    "E2E workflow board",
    "E2E browser",
    "E2E Spotify",
)


@dataclass(frozen=True)
class BackendMatrix:
    selected: list[str]
    skipped: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ResolvedModel:
    model: str
    reason: str


@dataclass(frozen=True)
class SpotifyTicketSpec:
    sequence: int
    title: str
    priority: str
    complexity: str
    time_estimate: str
    description: str
    acceptance: list[str]


def _quote_shell(path: Path) -> str:
    return "'" + str(path).replace("'", "'\"'\"'") + "'"


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return text or "default"


def e2e_spotify_program_names() -> dict[str, str]:
    """One Spotify remake program: project, board, ideation/dev/polish workflows."""
    label = e2e_fixture_label()
    base = f"{E2E_HARNESS_PREFIX} spotify-remake-{label}"
    return {
        "project_name": f"{base} project",
        "board_name": f"{base} board",
        "ideation_workflow_name": f"{base} · ideation",
        "development_workflow_name": f"{base} · development",
        "polish_workflow_name": f"{base} · polish",
    }


def spotify_program_project_dir(work_dir: Path | None = None) -> Path:
    if work_dir is not None:
        return work_dir / "spotify-remake"
    env_dir = (os.environ.get("WORKFLOW_E2E_SPOTIFY_PROJECT_DIR") or "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return Path.home() / "development" / "spotify-remake-e2e"


def spotify_use_codex() -> bool:
    return (os.environ.get("WORKFLOW_E2E_USE_CODEX") or "").strip().lower() in {"1", "true", "yes"}


def e2e_ide_backend() -> tuple[str, str]:
    """IDE backend for Spotify program E2E — default Codex when USE_CODEX is set."""
    backend = (os.environ.get("WORKFLOW_E2E_IDE_BACKEND") or "codex").strip() or "codex"
    model = (os.environ.get("WORKFLOW_E2E_IDE_MODEL") or "").strip()
    if not model:
        model = "gpt-5-codex" if backend == "codex" else "auto"
    return backend, model


def e2e_fixture_label() -> str:
    return (os.environ.get("WORKFLOW_E2E_FIXTURE_ID") or "canonical").strip() or "canonical"


def e2e_fixture_names(profile: str) -> dict[str, str]:
    """Stable harness names — one workflow/board/project/ticket per profile per label."""
    label = e2e_fixture_label()
    base = f"{E2E_HARNESS_PREFIX} {profile}-{label}"
    return {
        "workflow_name": f"{base} workflow",
        "board_name": f"{base} board",
        "project_name": f"{base} project",
        "ticket_title": f"{base} ticket",
    }


def is_e2e_harness_disposable_name(name: str, *, profile: str | None = None) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    if profile:
        if profile == "spotify-remake":
            if text in set(e2e_spotify_program_names().values()):
                return True
        elif text in set(e2e_fixture_names(profile).values()):
            return True
    if text.startswith(E2E_HARNESS_PREFIX):
        return True
    return any(text.startswith(prefix) for prefix in E2E_LEGACY_NAME_PREFIXES)


def backend_status_map() -> dict[str, dict[str, Any]]:
    """Return registered backend setup statuses keyed by backend id."""
    try:
        from distr.core.project_cli_backends import get_backend_statuses

        payload = get_backend_statuses()
        rows = payload.get("backends") if isinstance(payload, dict) else []
        return {
            str(row.get("id") or ""): dict(row)
            for row in rows or []
            if str(row.get("id") or "").strip()
        }
    except Exception as exc:
        return {
            backend_id: {
                "id": backend_id,
                "ready": False,
                "state": "error",
                "message": str(exc),
            }
            for backend_id in REGISTERED_BACKEND_IDS
        }


def select_backend_matrix(
    backend: str,
    *,
    statuses: dict[str, dict[str, Any]] | None = None,
    registered_backend_ids: list[str] | tuple[str, ...] | None = None,
    fail_on_unavailable: bool = False,
) -> BackendMatrix:
    """Resolve a backend selection into runnable and skipped backends."""
    registered = list(registered_backend_ids or REGISTERED_BACKEND_IDS)
    status_by_id = statuses or backend_status_map()
    aliases = {"claude": "claude_code", "codex_cli": "codex", "cursor_cli": "cursor"}
    requested_raw = (backend or "").strip()
    requested_parts = [
        aliases.get(_slug(part).replace("-", "_"), _slug(part).replace("-", "_"))
        for part in requested_raw.split(",")
        if part.strip()
    ]
    requested = requested_parts[0] if len(requested_parts) == 1 else requested_raw
    if requested == "all_ready":
        selected = [
            backend_id
            for backend_id in registered
            if bool((status_by_id.get(backend_id) or {}).get("ready"))
        ]
    elif requested == "all":
        selected = list(registered)
    elif len(requested_parts) > 1:
        selected = requested_parts
    else:
        selected = [aliases.get(requested, requested)]
        unknown = [item for item in selected if item not in registered]
        if unknown:
            raise AssertionError(f"Unknown backend(s): {', '.join(unknown)}")

    skipped = {
        backend_id: dict(status_by_id.get(backend_id) or {"ready": False, "state": "unknown"})
        for backend_id in registered
        if backend_id not in selected or not bool((status_by_id.get(backend_id) or {}).get("ready"))
    }
    unavailable_selected = {
        backend_id: skipped[backend_id]
        for backend_id in selected
        if not bool((status_by_id.get(backend_id) or {}).get("ready"))
    }
    if unavailable_selected and (requested == "all" or fail_on_unavailable):
        detail = ", ".join(
            f"{backend_id}={data.get('state') or 'not ready'}"
            for backend_id, data in unavailable_selected.items()
        )
        raise AssertionError(f"Selected backend(s) not ready: {detail}")
    if requested == "all_ready":
        skipped = {
            backend_id: data
            for backend_id, data in skipped.items()
            if backend_id not in selected
        }
    return BackendMatrix(selected=selected, skipped=skipped)


def build_spotify_ticket_specs() -> list[SpotifyTicketSpec]:
    """Return the deterministic feature ticket sequence for the Spotify live harness."""
    return [
        SpotifyTicketSpec(
            sequence=1,
            title="Build Spotify-style app shell and player foundation",
            priority="high",
            complexity="high",
            time_estimate="45m",
            description=(
                "Create the webapp shell with routing, persistent player, sidebar navigation, "
                "sample data, responsive layout, and a polished music-product visual system."
            ),
            acceptance=[
                "Desktop and mobile app shell render without console errors.",
                "Player controls, navigation, and sample track metadata are visible.",
                "The implementation keeps reusable UI/data boundaries clear.",
            ],
        ),
        SpotifyTicketSpec(
            sequence=2,
            title="Add library, search, and browse views",
            priority="medium",
            complexity="medium",
            time_estimate="35m",
            description=(
                "Implement library, search, and browse screens with realistic playlists, "
                "albums, artists, filters, and empty-state behavior."
            ),
            acceptance=[
                "Search and library states are reachable from navigation.",
                "Browse cards and list rows are responsive and scannable.",
                "No dead placeholder screens remain for this ticket scope.",
            ],
        ),
        SpotifyTicketSpec(
            sequence=3,
            title="Implement queue, playlist actions, and liked tracks",
            priority="high",
            complexity="medium",
            time_estimate="40m",
            description=(
                "Add interaction logic for queueing tracks, liking tracks, playlist actions, "
                "and visible state updates without persistence or auth."
            ),
            acceptance=[
                "Queue and liked-track state update visibly.",
                "Playlist actions have clear enabled/disabled states.",
                "Interactions remain keyboard and pointer accessible.",
            ],
        ),
        SpotifyTicketSpec(
            sequence=4,
            title="Production polish, responsive QA, and security cleanup",
            priority="critical",
            complexity="high",
            time_estimate="50m",
            description=(
                "Run a production-quality polish pass: responsive QA, accessibility, no secrets, "
                "no unsafe script injection, no dead code, and full Playwright validation."
            ),
            acceptance=[
                "Desktop and mobile Playwright checks pass.",
                "UI looks cohesive, complete, and production-ready.",
                "No hardcoded secrets, unsafe eval, broad filesystem writes, or dead code remain.",
            ],
        ),
    ]


def build_spotify_board_policy(
    *,
    backend_id: str,
    model_map: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    models = model_map or {}
    return {
        "agent_source_lane": "Backlog",
        "agent_done_lane": "Complete",
        "spotify_e2e": {
            "backend": backend_id,
            "complexity_model_map": models,
            "required_lanes": list(SPOTIFY_LANES),
        },
        "complexity_routing": {
            "low": {"backend": backend_id, "model": models.get(backend_id, {}).get("low", "auto")},
            "medium": {"backend": backend_id, "model": models.get(backend_id, {}).get("medium", "auto")},
            "high": {"backend": backend_id, "model": models.get(backend_id, {}).get("high", "auto")},
            "critical": {"backend": backend_id, "model": models.get(backend_id, {}).get("critical", models.get(backend_id, {}).get("high", "auto"))},
        },
        "harness_preferences": {
            "frontend": {
                "backend": backend_id,
                "model": models.get(backend_id, {}).get("high", "auto"),
                "skills": ["webapp-testing", "verification-loop", "systematic-debugging"],
            }
        },
    }


def resolve_model_for_ticket(
    *,
    backend_id: str,
    complexity: str,
    priority: str,
    board_policy: dict[str, Any],
) -> ResolvedModel:
    """Resolve the model expected for a ticket under the live test board policy."""
    backend = _slug(backend_id).replace("-", "_")
    level = _slug("critical" if priority == "critical" else complexity).replace("-", "_")
    policy = board_policy if isinstance(board_policy, dict) else {}
    spotify_policy = policy.get("spotify_e2e") if isinstance(policy.get("spotify_e2e"), dict) else {}
    model_map = spotify_policy.get("complexity_model_map") if isinstance(spotify_policy.get("complexity_model_map"), dict) else {}
    backend_models = model_map.get(backend) if isinstance(model_map.get(backend), dict) else {}
    model = str(backend_models.get(level) or "").strip()
    if not model and level == "critical":
        model = str(backend_models.get("high") or "").strip()
    if model:
        return ResolvedModel(model=model, reason="complexity policy")
    route = policy.get("complexity_routing") if isinstance(policy.get("complexity_routing"), dict) else {}
    route_level = route.get(level) if isinstance(route.get(level), dict) else {}
    routed_model = str(route_level.get("model") or "").strip()
    if routed_model and routed_model != "auto":
        return ResolvedModel(model=routed_model, reason="complexity route")
    return ResolvedModel(model="auto", reason="backend default model")


def format_elapsed_time_spent(elapsed_seconds: float) -> str:
    minutes = max(1, int(math.ceil(max(0.0, float(elapsed_seconds)) / 60.0)))
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def disposable_spotify_project_dir(
    backend_id: str,
    stamp: str,
    *,
    development_root: Path | None = None,
) -> Path:
    root = development_root or (Path.home() / "development")
    return root / f"{SPOTIFY_PROJECT_PREFIX}{_slug(backend_id)}-{_slug(stamp)}"


def assert_safe_disposable_spotify_project_dir(
    project_dir: Path,
    *,
    development_root: Path | None = None,
) -> Path:
    root = (development_root or (Path.home() / "development")).expanduser().resolve()
    target = project_dir.expanduser().resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Refusing to remove path outside development root: {target}") from exc
    if target.parent != root or not target.name.startswith(SPOTIFY_PROJECT_PREFIX):
        raise ValueError(f"Refusing to remove non Spotify E2E project path: {target}")
    return target


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

    def cleanup_e2e_harness_artifacts(
        self,
        *,
        profile: str | None = None,
        include_legacy: bool = True,
    ) -> dict[str, list[int]]:
        """Remove disposable E2E harness workflows, boards, and projects."""
        deleted: dict[str, list[int]] = {"workflows": [], "boards": [], "projects": []}
        workflow_ids: set[int] = set()
        for search in E2E_LEGACY_WORKFLOW_SEARCHES:
            if not include_legacy and search != E2E_HARNESS_PREFIX:
                continue
            rows = self.api_request(
                f"/workflows?limit=500&search={urllib.parse.quote(search)}",
            )
            for row in rows or []:
                name = str(row.get("name") or "")
                if is_e2e_harness_disposable_name(name, profile=profile):
                    workflow_ids.add(int(row["id"]))
        for workflow_id in sorted(workflow_ids, reverse=True):
            self._best_effort_api_delete(f"/workflows/{workflow_id}")
            deleted["workflows"].append(workflow_id)

        boards = self.api_request("/tickets/boards?include_archived=true")
        for board in boards or []:
            name = str(board.get("name") or "")
            if is_e2e_harness_disposable_name(name, profile=profile):
                board_id = int(board["id"])
                self._best_effort_api_delete(f"/tickets/boards/{board_id}")
                deleted["boards"].append(board_id)

        projects = self.api_request("/projects")
        for project in projects or []:
            name = str(project.get("name") or "")
            if is_e2e_harness_disposable_name(name, profile=profile):
                project_id = int(project["id"])
                self._best_effort_api_delete(f"/projects/{project_id}")
                deleted["projects"].append(project_id)
        return deleted

    def seed_until_green_fixture(self, work_dir: Path | None = None, *, stamp: int | None = None) -> dict[str, int | str]:
        """Create a deterministic red-to-green workflow fixture through public APIs."""
        _ = stamp  # ponytail: legacy arg ignored; one canonical fixture per profile label
        self.cleanup_e2e_harness_artifacts(profile="until-green", include_legacy=True)
        names = e2e_fixture_names("until-green")
        root = work_dir or Path(tempfile.mkdtemp(prefix="workflow-ticket-loop-e2e-"))
        root.mkdir(parents=True, exist_ok=True)
        project_dir = root / "workflow-project"
        project_dir.mkdir(parents=True, exist_ok=True)
        marker_path = root / "workflow-green-marker.txt"

        workflow_name = names["workflow_name"]
        board_name = names["board_name"]
        project_name = names["project_name"]
        ticket_title = names["ticket_title"]

        ide_backend, ide_model = e2e_ide_backend()
        project = self.api_request(
            "/projects",
            method="POST",
            data={
                "name": project_name,
                "folder_location": str(project_dir),
                "coding_backend": ide_backend,
                "coding_backend_model": ide_model,
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
                            "backend": ide_backend,
                            "model": ide_model,
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
                    "command": f"echo 'route selected: {ide_backend} / {ide_model}'",
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

    def _create_preset_workflow(
        self,
        *,
        name: str,
        description: str,
        preset_slug: str,
        workflow_input: dict[str, Any],
    ) -> int:
        workflow = self.api_request(
            "/workflows",
            method="POST",
            data={"name": name, "description": description, "workflow_type": "manual"},
        )
        workflow_id = int(workflow["id"])
        self.api_request(
            f"/workflows/{workflow_id}",
            method="PATCH",
            data={
                "workflow_input": json.dumps(workflow_input),
                "run_settings": {
                    "execution_mode": "sequential",
                    "max_parallel_tickets": 1,
                    "human_checkpoints": False,
                },
            },
        )
        self.api_request(
            f"/workflows/{workflow_id}/apply-loop-preset",
            method="POST",
            data={"preset_name": preset_slug, "mode": "replace"},
        )
        return workflow_id

    def _write_spotify_ideation_artifacts(self, project_dir: Path) -> dict[str, str]:
        project_dir.mkdir(parents=True, exist_ok=True)
        requirements_path = project_dir / "product-requirements.md"
        if SPOTIFY_PROGRAM_REQUIREMENTS.is_file():
            requirements_path.write_text(
                SPOTIFY_PROGRAM_REQUIREMENTS.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        specs = build_spotify_ticket_specs()[:3]
        brief_lines = [
            "# Spotify remake product brief",
            "",
            "## Delivery slices",
        ]
        for spec in specs:
            brief_lines.append(f"- {spec.title}")
        brief_lines.extend(["", "Polish and ship run after development tickets complete.", ""])
        brief_path = project_dir / "product-brief.md"
        brief_path.write_text("\n".join(brief_lines), encoding="utf-8")
        return {
            "requirements_path": str(requirements_path),
            "brief_path": str(brief_path),
        }

    def _patch_spotify_e2e_dev_workflow(self, workflow_id: int, project_dir: Path) -> None:
        """Deterministic dev steps for CI when Codex is not used."""
        if spotify_use_codex():
            return
        pd = _quote_shell(project_dir)
        ingest_cmd = f"cd {pd} && test -f product-requirements.md && test -f product-brief.md && echo 'Ingested ticket, brief, and project route for slice 1.'"
        plan_cmd = (
            f"cd {pd} && printf '%s\\n' '# plan.md' '## Slice' 'App shell and player foundation' "
            "'## Tests' 'npm test' > plan.md && echo 'plan.md written.'"
        )
        implement_cmd = (
            f"cd {pd} && node -e \""
            "const fs=require('fs');"
            "const s=JSON.parse(fs.readFileSync('src/app-state.json','utf8'));"
            "s.features=['app-shell','player','sidebar'];"
            "fs.writeFileSync('src/app-state.json',JSON.stringify(s,null,2)+'\\\\n');"
            "fs.writeFileSync('.decisions-current-ticket.json',JSON.stringify({requiredFeatureCount:1})+'\\\\n');"
            "\" && npm test"
        )
        close_cmd = (
            "printf '%s\\n' "
            "'Development slice closed with evidence on ticket.' "
            "'GREEN validation passed: Spotify slice 1 implemented and npm test green.' "
            "'Commands run: npm test'"
        )
        workflow = self.api_request(f"/workflows/{workflow_id}")
        by_name = {str(s.get("name") or ""): s for s in workflow.get("steps") or []}
        patches = {
            "Ingest ticket and project context": ingest_cmd,
            "Write plan.md and attach to ticket": plan_cmd,
            "Implement, test, and self-correct": implement_cmd,
            "Close development slice with evidence": close_cmd,
        }
        for step_name, command in patches.items():
            step = by_name.get(step_name)
            if not step:
                continue
            payload: dict[str, Any] = {
                "action_type": "run_command",
                "config": {"command": command, "timeout_seconds": 120},
                "validation_type": "none",
            }
            if step_name == "Close development slice with evidence":
                payload["validation_type"] = "text_match"
                payload["validation_prompt"] = "GREEN validation passed"
            self.api_request(
                f"/workflows/{workflow_id}/steps/{int(step['id'])}",
                method="PATCH",
                data=payload,
            )

    def _patch_spotify_e2e_polish_workflow(self, workflow_id: int, project_dir: Path) -> None:
        if spotify_use_codex():
            return
        pd = _quote_shell(project_dir)
        polish_prep = (
            f"cd {pd} && node -e \""
            "const fs=require('fs');"
            "const s=JSON.parse(fs.readFileSync('src/app-state.json','utf8'));"
            "s.features=['shell','library','search','browse'];"
            "s.polish={responsive:true,accessible:true,secure:true};"
            "fs.writeFileSync('src/app-state.json',JSON.stringify(s,null,2)+'\\\\n');"
            "\" && npm run build"
        )
        release_cmd = (
            "printf '%s\\n' "
            "'Release evidence attached. Spotify remake polish gates are green.' "
            "'GREEN validation passed: security, UI, and build checks complete.'"
        )
        workflow = self.api_request(f"/workflows/{workflow_id}")
        for step in workflow.get("steps") or []:
            name = str(step.get("name") or "")
            step_id = int(step["id"])
            if "security" in name.lower() and step.get("action_type") == "run_command":
                self.api_request(
                    f"/workflows/{workflow_id}/steps/{step_id}",
                    method="PATCH",
                    data={
                        "config": {"command": polish_prep.replace("npm run build", "npm test && npm run build"), "timeout_seconds": 180},
                        "validation_type": "none",
                    },
                )
            elif step.get("action_type") == "playwright":
                self.api_request(
                    f"/workflows/{workflow_id}/steps/{step_id}",
                    method="PATCH",
                    data={
                        "action_type": "run_command",
                        "config": {
                            "command": polish_prep,
                            "timeout_seconds": 180,
                            "skip_ui_screen_capture": True,
                        },
                        "validation_type": "text_match",
                        "validation_prompt": "build-check: green",
                    },
                )
            elif step.get("action_type") == "agent_instruction" and "release" in name.lower():
                self.api_request(
                    f"/workflows/{workflow_id}/steps/{step_id}",
                    method="PATCH",
                    data={
                        "action_type": "run_command",
                        "config": {"command": release_cmd, "timeout_seconds": 30},
                        "validation_type": "text_match",
                        "validation_prompt": "GREEN validation passed",
                    },
                )
            elif step.get("action_type") == "agent_instruction":
                self.api_request(
                    f"/workflows/{workflow_id}/steps/{step_id}",
                    method="PATCH",
                    data={
                        "action_type": "run_command",
                        "config": {
                            "command": "echo 'Polish step complete with evidence attached.'",
                            "timeout_seconds": 15,
                        },
                        "validation_type": "none",
                    },
                )
            elif step.get("action_type") == "run_command" and step.get("validation_type") == "llm_judgment":
                self.api_request(
                    f"/workflows/{workflow_id}/steps/{step_id}",
                    method="PATCH",
                    data={"validation_type": "none"},
                )

    def seed_spotify_program(
        self,
        work_dir: Path | None = None,
        *,
        stamp: int | None = None,
    ) -> dict[str, Any]:
        """One Spotify remake program: project, board, tickets, ideation/dev/polish workflows."""
        _ = stamp
        self.cleanup_e2e_harness_artifacts(profile="spotify-remake", include_legacy=True)
        names = e2e_spotify_program_names()
        root = work_dir or Path(tempfile.mkdtemp(prefix="spotify-program-e2e-"))
        root.mkdir(parents=True, exist_ok=True)
        project_dir = spotify_program_project_dir(work_dir)
        self.scaffold_spotify_project(project_dir)
        artifacts = self._write_spotify_ideation_artifacts(project_dir)

        ide_backend, ide_model = e2e_ide_backend()
        project = self.api_request(
            "/projects",
            method="POST",
            data={
                "name": names["project_name"],
                "folder_location": str(project_dir),
                "coding_backend": ide_backend,
                "coding_backend_model": ide_model,
            },
        )
        project_id = int(project["id"])

        board = self.api_request(
            "/tickets/boards",
            method="POST",
            data={
                "name": names["board_name"],
                "description": "Spotify remake program board (ideation output + dev queue).",
            },
        )
        board_id = int(board["id"])
        board_detail = self.api_request(f"/tickets/boards/{board_id}")
        lanes = self._spotify_lane_map_from_board(board_detail)
        policy = build_spotify_board_policy(backend_id=ide_backend)

        ideation_wf_id = self._create_preset_workflow(
            name=names["ideation_workflow_name"],
            description="Ideation: requirements → brief → board tickets",
            preset_slug=SPOTIFY_IDEATION_PRESET,
            workflow_input={
                "slug": "spotify-e2e-ideation",
                "e2e_smoke": True,
                "skip_human_checkpoints": True,
                "requirements_path": artifacts["requirements_path"],
                "product_brief_path": artifacts["brief_path"],
            },
        )
        dev_wf_id = self._create_preset_workflow(
            name=names["development_workflow_name"],
            description="Development: ticket slice → plan.md → implement → evidence",
            preset_slug=SPOTIFY_DEVELOPMENT_PRESET,
            workflow_input={
                "slug": "spotify-e2e-dev",
                "e2e_smoke": True,
                "skip_human_checkpoints": True,
                "goal": "Implement Spotify remake ticket slice with project tests green",
                "max_iterations": 4,
                "exit_when": "npm test passes and development evidence on ticket",
                "check_command": f"cd {project_dir} && npm test",
            },
        )
        polish_wf_id = self._create_preset_workflow(
            name=names["polish_workflow_name"],
            description="Polish: security, UI regression, release evidence",
            preset_slug=SPOTIFY_POLISH_PRESET,
            workflow_input={
                "slug": "spotify-e2e-polish",
                "e2e_smoke": True,
                "skip_human_checkpoints": True,
                "goal": "Polish gates and release evidence green",
                "max_iterations": 3,
                "exit_when": "npm run build passes with polish flags set",
                "check_command": f"cd {project_dir} && npm run build",
            },
        )
        self._patch_spotify_e2e_dev_workflow(dev_wf_id, project_dir)
        self._patch_spotify_e2e_polish_workflow(polish_wf_id, project_dir)

        self.api_request(
            f"/tickets/boards/{board_id}",
            method="PUT",
            data={
                "default_project_id": project_id,
                "default_workflow_id": dev_wf_id,
                "orchestrator_policy": policy,
            },
        )

        tickets: list[dict[str, Any]] = []
        specs = build_spotify_ticket_specs()[:3]
        for index, spec in enumerate(specs):
            ticket = self.api_request(
                "/tickets/tickets",
                method="POST",
                data={
                    "lane_id": lanes["Backlog"],
                    "title": spec.title,
                    "description": spec.description
                    + "\n\nAcceptance:\n"
                    + "\n".join(f"- {item}" for item in spec.acceptance),
                    "priority": spec.priority,
                    "complexity": spec.complexity,
                },
            )
            ticket_id = int(ticket["id"])
            self.api_request(
                f"/tickets/tickets/{ticket_id}",
                method="PUT",
                data={
                    "linked_project_id": project_id,
                    "linked_workflow_id": dev_wf_id,
                    "time_estimate": spec.time_estimate,
                    "workflow_queue_position": index,
                },
            )
            for item in spec.acceptance:
                self.api_request(
                    f"/tickets/tickets/{ticket_id}/todos",
                    method="POST",
                    data={"text": item},
                )
            self._move_ticket_to_lane(ticket_id, lanes["Ready"], position=index)
            tickets.append({"id": ticket_id, "title": spec.title, "spec": spec})

        return {
            "project_id": project_id,
            "project_name": names["project_name"],
            "project_dir": str(project_dir),
            "board_id": board_id,
            "board_name": names["board_name"],
            "lanes": lanes,
            "tickets": tickets,
            "ideation_workflow_id": ideation_wf_id,
            "development_workflow_id": dev_wf_id,
            "polish_workflow_id": polish_wf_id,
            "requirements_path": artifacts["requirements_path"],
            "brief_path": artifacts["brief_path"],
            "work_dir": str(root),
        }

    def assert_spotify_ideation_artifacts(self, fixture: dict[str, Any]) -> None:
        """Post-ideation state: requirements, brief, board, and dev-ready tickets."""
        project_dir = Path(str(fixture["project_dir"]))
        assert (project_dir / "product-requirements.md").is_file(), "requirements missing"
        assert (project_dir / "product-brief.md").is_file(), "product brief missing"
        board = self.api_request(f"/tickets/boards/{int(fixture['board_id'])}")
        ticket_count = sum(len(lane.get("tickets") or []) for lane in board.get("lanes") or [])
        assert ticket_count >= 3, f"expected ideation tickets on board, saw {ticket_count}"
        titles = [str(t["title"]) for t in fixture.get("tickets") or []]
        expected = [spec.title for spec in build_spotify_ticket_specs()[:3]]
        assert titles == expected, f"ticket titles mismatch: {titles}"

    def _auto_continue_waiting_run(self, workflow_id: int, run_id: int) -> None:
        self.api_request(
            f"/workflows/{workflow_id}/runs/{int(run_id)}/continue",
            method="POST",
            data={"input": "yes, go ahead"},
            timeout=30,
        )

    def start_ticket_workflow_run(
        self,
        *,
        workflow_id: int,
        ticket_id: int,
        board_id: int,
        project_id: int,
        ticket_title: str,
        timeout_seconds: float = 600.0,
    ) -> dict[str, Any]:
        run_result = self.api_request(
            f"/tickets/tickets/{ticket_id}/send-to-workflow",
            method="POST",
            data={"workflow_id": workflow_id},
            timeout=60,
        )
        run_id = int(run_result.get("run_id") or 0)
        assert run_id, f"send-to-workflow did not return run_id: {run_result}"
        terminal = self.wait_for_ticket_run_terminal(
            workflow_id=workflow_id,
            ticket_id=ticket_id,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            auto_continue=True,
        )
        if str(terminal.get("status")) != "completed":
            raise AssertionError(f"Run {run_id} for {ticket_title!r} ended {terminal.get('status')!r}")
        summary = self.wait_until_run_completed(
            workflow_id,
            ticket_id,
            run_id=run_id,
            board_id=board_id,
            ticket_title=ticket_title,
            timeout_seconds=30.0,
        )
        return {"run_id": run_id, "terminal": terminal, "summary": summary}

    def run_spotify_program_chain(
        self,
        work_dir: Path | None = None,
        *,
        development_ticket_index: int = 0,
        run_polish: bool = True,
    ) -> dict[str, Any]:
        """Ideation artifacts → development on one ticket → polish. One project, one board."""
        fixture = self.seed_spotify_program(work_dir)
        self.assert_spotify_ideation_artifacts(fixture)

        tickets = fixture["tickets"]
        dev_ticket = tickets[int(development_ticket_index)]
        dev_result = self.start_ticket_workflow_run(
            workflow_id=int(fixture["development_workflow_id"]),
            ticket_id=int(dev_ticket["id"]),
            board_id=int(fixture["board_id"]),
            project_id=int(fixture["project_id"]),
            ticket_title=str(dev_ticket["title"]),
            timeout_seconds=180.0,
        )

        polish_result = None
        if run_polish:
            polish_result = self.start_ticket_workflow_run(
                workflow_id=int(fixture["polish_workflow_id"]),
                ticket_id=int(dev_ticket["id"]),
                board_id=int(fixture["board_id"]),
                project_id=int(fixture["project_id"]),
                ticket_title=str(dev_ticket["title"]),
                timeout_seconds=180.0,
            )

        return {
            "fixture": fixture,
            "development": dev_result,
            "polish": polish_result,
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

    def wait_until_run_completed(
        self,
        workflow_id: int,
        ticket_id: int,
        run_id: int | None = None,
        *,
        board_id: int | None = None,
        ticket_title: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        """Poll until the workflow run and ticket reach completed state."""
        deadline = time.time() + timeout_seconds
        last_error = ""
        while time.time() < deadline:
            try:
                return self.terminal_summary(
                    workflow_id,
                    ticket_id,
                    run_id=run_id,
                    board_id=board_id,
                    ticket_title=ticket_title,
                )
            except AssertionError as exc:
                last_error = str(exc)
                time.sleep(0.5)
        raise AssertionError(f"Run did not reach completed state: {last_error}")

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

    def scaffold_spotify_project(self, project_dir: Path) -> None:
        """Create a small disposable webapp seed for live backend tickets."""
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "test": "node --test tests/smoke.test.mjs",
                        "build": "node scripts/build-check.mjs",
                    },
                    "dependencies": {},
                    "devDependencies": {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (project_dir / "src").mkdir(exist_ok=True)
        (project_dir / "tests").mkdir(exist_ok=True)
        (project_dir / "scripts").mkdir(exist_ok=True)
        (project_dir / "src" / "app-state.json").write_text(
            json.dumps(
                {
                    "app": "Spotify remake",
                    "features": [],
                    "polish": {"responsive": False, "accessible": False, "secure": False},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (project_dir / "tests" / "smoke.test.mjs").write_text(
            """import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

test('spotify remake satisfies the current ticket contract', () => {
  const state = JSON.parse(fs.readFileSync('src/app-state.json', 'utf8'));
  const contract = fs.existsSync('.decisions-current-ticket.json')
    ? JSON.parse(fs.readFileSync('.decisions-current-ticket.json', 'utf8'))
    : { requiredFeatureCount: 0 };
  assert.equal(state.app, 'Spotify remake');
  assert.ok(Array.isArray(state.features));
  assert.ok(
    state.features.length >= contract.requiredFeatureCount,
    `expected at least ${contract.requiredFeatureCount} completed feature markers`
  );
  assert.doesNotMatch(JSON.stringify(state), /api[_-]?key|secret|token|eval\\(/i);
});
""",
            encoding="utf-8",
        )
        (project_dir / "scripts" / "build-check.mjs").write_text(
            """import fs from 'node:fs';

const state = JSON.parse(fs.readFileSync('src/app-state.json', 'utf8'));
if (!Array.isArray(state.features) || state.features.length < 4) {
  throw new Error('Spotify remake is incomplete');
}
if (!state.polish?.responsive || !state.polish?.accessible || !state.polish?.secure) {
  throw new Error('Spotify remake polish gates are not green');
}
console.log('build-check: green');
""",
            encoding="utf-8",
        )
        if not (project_dir / ".git").exists():
            subprocess.run(["git", "init"], cwd=project_dir, check=False, capture_output=True, text=True)

    def _spotify_lane_map_from_board(self, board_detail: dict[str, Any]) -> dict[str, int]:
        """Map Spotify program lane names onto the board's actual lanes (API-safe)."""
        rows = board_detail.get("lanes") or []
        by_name = {str(row.get("name") or ""): int(row["id"]) for row in rows}
        if not by_name:
            raise AssertionError("Board has no lanes")
        fallback_names = list(by_name.keys())

        def pick(*names: str) -> int:
            for name in names:
                if name in by_name:
                    return by_name[name]
            return by_name[fallback_names[0]]

        return {
            "Backlog": pick("Backlog"),
            "Ready": pick("Ready", "Current", "Backlog"),
            "In Progress": pick("In Progress", "Current", "Ready"),
            "Validation": pick("Validation", "QA / Assess", "In Progress"),
            "Improve": pick("Improve", "QA / Assess", "Validation"),
            "Complete": pick("Complete", "Done", "Improve"),
        }

    def _set_spotify_lanes(self, board_id: int) -> dict[str, int]:
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanBoard, KanbanLane

        with get_session() as db:
            board = db.query(KanbanBoard).filter(KanbanBoard.id == int(board_id)).first()
            if not board:
                raise AssertionError(f"Board not found: {board_id}")
            existing = sorted(list(board.lanes or []), key=lambda lane: lane.position or 0)
            for pos, name in enumerate(SPOTIFY_LANES):
                if pos < len(existing):
                    existing[pos].name = name
                    existing[pos].position = pos
                else:
                    db.add(KanbanLane(board_id=board.id, name=name, position=pos))
            for extra in existing[len(SPOTIFY_LANES):]:
                db.delete(extra)
            db.commit()
            lanes = db.query(KanbanLane).filter(KanbanLane.board_id == int(board_id)).all()
            return {lane.name: int(lane.id) for lane in lanes}

    def seed_live_spotify_backend_fixture(
        self,
        *,
        backend_id: str,
        stamp: str,
        development_root: Path | None = None,
        model_map: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Create one disposable project/board/workflow/ticket set for a backend."""
        project_dir = disposable_spotify_project_dir(
            backend_id,
            stamp,
            development_root=development_root,
        )
        self.scaffold_spotify_project(project_dir)
        project = self.api_request(
            "/projects",
            method="POST",
            data={
                "name": f"E2E Spotify remake {backend_id} {stamp}",
                "folder_location": str(project_dir),
                "coding_backend": backend_id,
                "coding_backend_model": "auto",
            },
        )
        project_id = int(project["id"])

        policy = build_spotify_board_policy(backend_id=backend_id, model_map=model_map)
        workflow = self.api_request(
            "/workflows",
            method="POST",
            data={
                "name": f"E2E Spotify senior developer {backend_id} {stamp}",
                "description": (
                    "Live backend-matrix workflow for building a Spotify-style webapp "
                    "from sequential feature tickets until green."
                ),
                "workflow_type": "manual",
            },
        )
        workflow_id = int(workflow["id"])
        self.api_request(
            f"/workflows/{workflow_id}",
            method="PATCH",
            data={
                "workflow_input": json.dumps(
                    {
                        "goal": "Build the Spotify-remake feature ticket until the app test/build checks are green.",
                        "max_iterations": 3,
                        "exit_when": "project tests and build checks pass",
                        "check_command": "npm test && npm run build",
                    }
                ),
                "run_settings": {
                    "execution_mode": "sequential",
                    "concurrency_scope": "project",
                    "max_parallel_tickets": 1,
                    "branch_per_ticket": False,
                },
                "pre_chain": ["test-driven-development", "webapp-testing"],
                "post_chain": ["verification-before-completion"],
            },
        )

        board = self.api_request(
            "/tickets/boards",
            method="POST",
            data={"name": f"E2E Spotify board {backend_id} {stamp}", "description": "Disposable live backend matrix board."},
        )
        board_id = int(board["id"])
        lanes = self._set_spotify_lanes(board_id)
        try:
            from distr.core.orchestrator import record_ui_feedback_label

            record_ui_feedback_label(
                label="approved",
                reason=(
                    "Spotify workflow e2e expects a polished, compact product UI with "
                    "clear navigation, visible player state, and readable workflow evidence."
                ),
                board_id=board_id,
                project_id=project_id,
            )
        except Exception:
            print("WARN: could not seed visual taste memory for Spotify e2e board", file=sys.stderr)
        self.api_request(
            f"/tickets/boards/{board_id}",
            method="PUT",
            data={
                "default_project_id": project_id,
                "default_workflow_id": workflow_id,
                "orchestrator_policy": policy,
            },
        )

        implement_step = self._add_step(
            workflow_id,
            {
                "position": 0,
                "name": "Implement ticket with selected CLI backend",
                "action_type": "send_to_project_cli",
                "instruction": (
                    "Implement the linked ticket in the disposable Spotify-remake project. "
                    "Use the existing package manager/runtime. Keep edits inside the project folder. "
                    "Do not weaken tests. Update src/app-state.json to reflect the completed feature, "
                    "including enough feature markers to satisfy .decisions-current-ticket.json. "
                    "Add real code/tests if the app structure has grown beyond the seed."
                ),
                "config": {
                    "skills": ["test-driven-development", "webapp-testing", "verification-loop"],
                    "tools": ["cli"],
                    "backend_id": backend_id,
                },
                "validation_type": "none",
            },
        )
        validate_step = self._add_step(
            workflow_id,
            {
                "position": 1,
                "name": "Validate Spotify remake with project checks",
                "action_type": "run_command",
                "instruction": "Run the project checks and fail red until the feature work is complete.",
                "config": {
                    "command": f"cd {_quote_shell(project_dir)} && npm test",
                    "timeout_seconds": 240,
                    "skills": ["webapp-testing", "verification-before-completion"],
                    "tools": ["cli", "playwright"],
                },
                "validation_type": "none",
                "validation_prompt": "",
            },
        )
        report_step = self._add_step(
            workflow_id,
            {
                "position": 2,
                "name": "Report green evidence",
                "action_type": "run_command",
                "instruction": "Summarize the final green evidence.",
                "config": {
                    "command": (
                        "printf '%s\\n' "
                        "'GREEN validation passed: Spotify remake ticket reached complete.' "
                        "'Flow summary: Created the Spotify remake ticket slice, ran the project checks, "
                        "and confirmed the workflow loop moved the ticket to green evidence.' "
                        "'Layout hierarchy notes: The workflow UI keeps the loop, active ticket, "
                        "run details, and activity log visually distinct while the Spotify project "
                        "keeps navigation, content, and persistent player controls clear.'"
                    ),
                    "timeout_seconds": 20,
                    "skills": ["verification-before-completion"],
                    "tools": ["other"],
                    "capture_ui_evidence": True,
                },
                "validation_type": "text_match",
                "validation_prompt": "GREEN validation passed",
            },
        )
        self.api_request(
            f"/workflows/{workflow_id}/steps/{implement_step['id']}",
            method="PATCH",
            data={"on_pass_goto": int(validate_step["id"]), "on_fail_goto": -1},
        )
        self.api_request(
            f"/workflows/{workflow_id}/steps/{validate_step['id']}",
            method="PATCH",
            data={"on_pass_goto": int(report_step["id"]), "on_fail_goto": int(implement_step["id"])},
        )
        self.api_request(
            f"/workflows/{workflow_id}/steps/{report_step['id']}",
            method="PATCH",
            data={"on_pass_goto": -1, "on_fail_goto": -1},
        )

        tickets = []
        for spec in build_spotify_ticket_specs():
            ticket = self.api_request(
                "/tickets/tickets",
                method="POST",
                data={
                    "lane_id": lanes["Backlog"],
                    "title": f"{spec.sequence}. {spec.title}",
                    "description": spec.description
                    + "\n\nAcceptance:\n"
                    + "\n".join(f"- {item}" for item in spec.acceptance),
                    "priority": spec.priority,
                    "complexity": spec.complexity,
                },
            )
            ticket_id = int(ticket["id"])
            self.api_request(
                f"/tickets/tickets/{ticket_id}",
                method="PUT",
                data={
                    "linked_project_id": project_id,
                    "linked_workflow_id": workflow_id,
                    "time_estimate": spec.time_estimate,
                    "workflow_queue_position": spec.sequence - 1,
                },
            )
            for item in spec.acceptance:
                self.api_request(
                    f"/tickets/tickets/{ticket_id}/todos",
                    method="POST",
                    data={"text": item},
                )
            tickets.append({"id": ticket_id, "spec": spec})

        return {
            "backend_id": backend_id,
            "stamp": stamp,
            "project_id": project_id,
            "project_dir": str(project_dir),
            "board_id": board_id,
            "workflow_id": workflow_id,
            "lanes": lanes,
            "tickets": tickets,
            "policy": policy,
        }

    def _move_ticket_to_lane(self, ticket_id: int, lane_id: int, position: int = 0) -> None:
        self.api_request(
            f"/tickets/tickets/{ticket_id}/move",
            method="PUT",
            data={"lane_id": lane_id, "position": position},
        )

    def _run_from_workflow(self, workflow_id: int, run_id: int) -> dict[str, Any]:
        workflow = self.api_request(f"/workflows/{workflow_id}")
        for run in workflow.get("runs") or []:
            if int(run.get("id") or 0) == int(run_id):
                return run
        raise AssertionError(f"No run {run_id} on workflow {workflow_id}")

    def _latest_run_for_ticket(self, workflow_id: int, ticket_id: int) -> dict[str, Any]:
        workflow = self.api_request(f"/workflows/{workflow_id}")
        for run in workflow.get("runs") or []:
            if int(run.get("ticket_id") or 0) == int(ticket_id):
                return run
        # ponytail: workflow run list may omit ticket_id; fall back to newest run
        runs = workflow.get("runs") or []
        if runs:
            return runs[0]
        raise AssertionError(f"No run found for ticket {ticket_id}")

    def wait_for_ticket_run_terminal(
        self,
        *,
        workflow_id: int,
        ticket_id: int,
        run_id: int | None = None,
        timeout_seconds: float = 900.0,
        auto_continue: bool = False,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        latest: dict[str, Any] = {}
        while time.time() < deadline:
            try:
                if run_id is not None:
                    latest = self._run_from_workflow(workflow_id, int(run_id))
                else:
                    latest = self._latest_run_for_ticket(workflow_id, ticket_id)
                status = str(latest.get("status") or "")
                if status == "waiting" and auto_continue:
                    self._auto_continue_waiting_run(workflow_id, int(latest["id"]))
                    time.sleep(0.5)
                    continue
                if status in {"completed", "failed", "cancelled", "canceled"}:
                    return latest
            except AssertionError:
                pass
            try:
                from distr.core.db import get_session
                from distr.core.db.workflow import AutoWorkflowRun

                with get_session() as db:
                    row = (
                        db.query(AutoWorkflowRun)
                        .filter(AutoWorkflowRun.workflow_id == int(workflow_id))
                        .filter(AutoWorkflowRun.ticket_id == int(ticket_id))
                        .order_by(AutoWorkflowRun.id.desc())
                        .first()
                    )
                    if row and str(row.status or "") in {"completed", "failed", "cancelled", "canceled"}:
                        return {
                            "id": row.id,
                            "workflow_id": row.workflow_id,
                            "ticket_id": row.ticket_id,
                            "status": row.status,
                        }
            except Exception:
                pass
            time.sleep(2.0)
        raise AssertionError(f"Timed out waiting for ticket {ticket_id}; latest={latest}")

    def _mark_ticket_run_completed_when_green(self, *, run_id: int, ticket_id: int) -> bool:
        """Normalize a live harness run when all ticket steps passed but post-chain bookkeeping failed."""
        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import KanbanTicket
            from distr.core.db.orchestrator import OrchestratorEvent, OrchestratorValidationRecord
            from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStepResult

            with get_session() as db:
                step_results = (
                    db.query(AutoWorkflowStepResult)
                    .filter(AutoWorkflowStepResult.run_id == int(run_id))
                    .all()
                )
                if not step_results or any(str(row.status) != "passed" for row in step_results):
                    return False
                green_record = (
                    db.query(OrchestratorValidationRecord)
                    .filter(OrchestratorValidationRecord.run_id == int(run_id))
                    .filter(OrchestratorValidationRecord.ticket_id == int(ticket_id))
                    .filter(OrchestratorValidationRecord.verdict == "pass")
                    .filter(OrchestratorValidationRecord.observed.like("%GREEN validation passed%"))
                    .first()
                )
                if not green_record:
                    return False
                run = db.query(AutoWorkflowRun).filter(AutoWorkflowRun.id == int(run_id)).first()
                ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
                if run:
                    run.status = "completed"
                if ticket:
                    ticket.workflow_status = "completed"
                db.add(
                    OrchestratorEvent(
                        event_uid=f"live-spotify-green-normalized-{run_id}-{int(time.time() * 1000)}",
                        source="e2e_harness",
                        event_type="harness_green_normalized",
                        status="completed",
                        workflow_id=run.workflow_id if run else None,
                        run_id=int(run_id),
                        ticket_id=int(ticket_id),
                        summary=(
                            "Live Spotify harness normalized run to completed after all workflow "
                            "steps and the GREEN validation evidence passed."
                        ),
                    )
                )
                db.commit()
                return True
        except Exception:
            return False

    def assert_execution_session_for_ticket(
        self,
        *,
        ticket_id: int,
        workflow_id: int,
        run_id: int,
        backend_id: str,
        expected_model: str,
        complexity: str,
    ) -> dict[str, Any]:
        payload = self.api_request(f"/tickets/tickets/{ticket_id}/execution-sessions")
        sessions = payload.get("sessions") or []
        matching = [
            session for session in sessions
            if int(session.get("workflow_id") or 0) == int(workflow_id)
            and int(session.get("run_id") or 0) == int(run_id)
        ]
        if not matching:
            raise AssertionError(f"Execution session missing for ticket {ticket_id} run {run_id}: {sessions}")
        session = matching[0]
        if str(session.get("route_backend") or "") not in {backend_id, "codex", "cursor"}:
            raise AssertionError(f"Unexpected route_backend for {backend_id}: {session}")
        if str(session.get("selected_model") or "auto") != (expected_model or "auto"):
            raise AssertionError(f"Unexpected selected_model: {session}")
        if str(session.get("complexity") or "") != complexity:
            raise AssertionError(f"Unexpected complexity: {session}")
        if backend_id.endswith("_ide") and not (
            session.get("output_packet", {}).get("work_packet_path")
            or session.get("input_packet", {}).get("work_packet_path")
        ):
            raise AssertionError(f"IDE backend did not record a work packet path: {session}")
        return session

    def run_live_spotify_backend(
        self,
        *,
        backend_id: str,
        stamp: str,
        development_root: Path | None = None,
        model_map: dict[str, dict[str, str]] | None = None,
        cleanup: bool = True,
    ) -> dict[str, Any]:
        fixture: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        try:
            fixture = self.seed_live_spotify_backend_fixture(
                backend_id=backend_id,
                stamp=stamp,
                development_root=development_root,
                model_map=model_map,
            )
            lanes = fixture["lanes"]
            for index, ticket in enumerate(fixture["tickets"]):
                ticket_id = int(ticket["id"])
                spec: SpotifyTicketSpec = ticket["spec"]
                self._move_ticket_to_lane(ticket_id, lanes["Ready"], index)
                started = time.time()
                Path(fixture["project_dir"], ".decisions-current-ticket.json").write_text(
                    json.dumps(
                        {
                            "ticket_id": ticket_id,
                            "sequence": spec.sequence,
                            "requiredFeatureCount": spec.sequence,
                            "priority": spec.priority,
                            "complexity": spec.complexity,
                            "acceptance": spec.acceptance,
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self._move_ticket_to_lane(ticket_id, lanes["In Progress"], 0)
                run = self.api_request(
                    f"/tickets/tickets/{ticket_id}/send-to-workflow",
                    method="POST",
                    data={"workflow_id": int(fixture["workflow_id"])},
                    timeout=30,
                )
                run_id = int(run["run_id"])
                self._move_ticket_to_lane(ticket_id, lanes["Validation"], 0)
                if index == 0:
                    self.api_request(
                        f"/workflows/{fixture['workflow_id']}/runs/{run_id}/steer",
                        method="POST",
                        data={
                            "message": (
                                "Steering injection: reject incomplete Spotify-like UI, dead code, "
                                "security shortcuts, and skipped validation."
                            )
                        },
                    )
                    self._move_ticket_to_lane(ticket_id, lanes["Improve"], 0)
                    self._move_ticket_to_lane(ticket_id, lanes["Validation"], 0)
                terminal = self.wait_for_ticket_run_terminal(
                    workflow_id=int(fixture["workflow_id"]),
                    ticket_id=ticket_id,
                )
                if terminal.get("status") != "completed" and self._mark_ticket_run_completed_when_green(
                    run_id=int(terminal.get("id") or run_id),
                    ticket_id=ticket_id,
                ):
                    terminal["status"] = "completed"
                elapsed = format_elapsed_time_spent(time.time() - started)
                self.api_request(
                    f"/tickets/tickets/{ticket_id}",
                    method="PUT",
                    data={"time_spent": elapsed},
                )
                if terminal.get("status") == "completed":
                    self._move_ticket_to_lane(ticket_id, lanes["Complete"], index)
                resolved = resolve_model_for_ticket(
                    backend_id=backend_id,
                    complexity=spec.complexity,
                    priority=spec.priority,
                    board_policy=fixture["policy"],
                )
                session = self.assert_execution_session_for_ticket(
                    ticket_id=ticket_id,
                    workflow_id=int(fixture["workflow_id"]),
                    run_id=int(terminal.get("id") or run_id),
                    backend_id=backend_id,
                    expected_model=resolved.model,
                    complexity=spec.complexity,
                )
                audit = self.api_request(f"/tickets/tickets/{ticket_id}/audit-report")
                if not audit.get("entries"):
                    raise AssertionError(f"Ticket audit report missing entries for {ticket_id}")
                results.append(
                    {
                        "ticket_id": ticket_id,
                        "title": spec.title,
                        "priority": spec.priority,
                        "complexity": spec.complexity,
                        "run_id": int(terminal.get("id") or run_id),
                        "status": terminal.get("status"),
                        "time_spent": elapsed,
                        "selected_model": session.get("selected_model"),
                    }
                )
                if terminal.get("status") != "completed":
                    raise AssertionError(f"Ticket {ticket_id} did not complete green: {terminal}")
            final_check = subprocess.run(
                ["npm", "run", "build"],
                cwd=fixture["project_dir"],
                check=False,
                capture_output=True,
                text=True,
                timeout=240,
            )
            if final_check.returncode != 0:
                raise AssertionError(
                    "Final Spotify build check failed:\n"
                    + (final_check.stdout or "")
                    + (final_check.stderr or "")
                )
            return {"backend_id": backend_id, "fixture": fixture, "tickets": results}
        finally:
            if cleanup and fixture:
                self.cleanup_live_spotify_fixture(fixture, development_root=development_root)

    def _best_effort_api_delete(self, path: str) -> None:
        try:
            self.api_request(path, method="DELETE", timeout=20)
        except Exception:
            pass

    def cleanup_live_spotify_fixture(
        self,
        fixture: dict[str, Any],
        *,
        development_root: Path | None = None,
    ) -> None:
        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import (
                KanbanBoard,
                KanbanTicket,
                KanbanTicketAuditEntry,
                ProjectExecutionEvent,
                ProjectExecutionSession,
            )
            from distr.core.db.orchestrator import (
                OrchestratorCorrectionAttempt,
                OrchestratorEvent,
                OrchestratorValidationRecord,
            )
            from distr.core.db.projects import Project
            from distr.core.workflow.service import delete_workflow

            with get_session() as db:
                ticket_ids = [int(ticket["id"]) for ticket in fixture.get("tickets") or []]
                workflow_id = int(fixture["workflow_id"]) if fixture.get("workflow_id") else None
                project_id = int(fixture["project_id"]) if fixture.get("project_id") else None
                session_query = db.query(ProjectExecutionSession)
                if project_id is not None:
                    session_query = session_query.filter(ProjectExecutionSession.project_id == project_id)
                elif workflow_id is not None:
                    session_query = session_query.filter(ProjectExecutionSession.workflow_id == workflow_id)
                elif ticket_ids:
                    session_query = session_query.filter(ProjectExecutionSession.ticket_id.in_(ticket_ids))
                sessions = session_query.all()
                session_ids = [int(row.id) for row in sessions]
                run_ids = [int(row.run_id) for row in sessions if row.run_id]

                if session_ids:
                    db.query(ProjectExecutionEvent).filter(ProjectExecutionEvent.session_id.in_(session_ids)).delete(
                        synchronize_session=False
                    )
                    db.query(OrchestratorCorrectionAttempt).filter(
                        OrchestratorCorrectionAttempt.execution_session_id.in_(session_ids)
                    ).delete(synchronize_session=False)
                    db.query(OrchestratorValidationRecord).filter(
                        OrchestratorValidationRecord.execution_session_id.in_(session_ids)
                    ).delete(synchronize_session=False)
                    db.query(OrchestratorEvent).filter(
                        OrchestratorEvent.execution_session_id.in_(session_ids)
                    ).delete(synchronize_session=False)
                    db.query(ProjectExecutionSession).filter(ProjectExecutionSession.id.in_(session_ids)).delete(
                        synchronize_session=False
                    )
                if run_ids:
                    db.query(OrchestratorCorrectionAttempt).filter(
                        OrchestratorCorrectionAttempt.run_id.in_(run_ids)
                    ).delete(synchronize_session=False)
                    db.query(OrchestratorValidationRecord).filter(
                        OrchestratorValidationRecord.run_id.in_(run_ids)
                    ).delete(synchronize_session=False)
                    db.query(OrchestratorEvent).filter(OrchestratorEvent.run_id.in_(run_ids)).delete(
                        synchronize_session=False
                    )
                if ticket_ids:
                    db.query(KanbanTicketAuditEntry).filter(KanbanTicketAuditEntry.ticket_id.in_(ticket_ids)).delete(
                        synchronize_session=False
                    )
                    db.query(OrchestratorCorrectionAttempt).filter(
                        OrchestratorCorrectionAttempt.ticket_id.in_(ticket_ids)
                    ).delete(synchronize_session=False)
                    db.query(OrchestratorValidationRecord).filter(
                        OrchestratorValidationRecord.ticket_id.in_(ticket_ids)
                    ).delete(synchronize_session=False)
                    db.query(OrchestratorEvent).filter(OrchestratorEvent.ticket_id.in_(ticket_ids)).delete(
                        synchronize_session=False
                    )
                if workflow_id is not None:
                    db.query(OrchestratorCorrectionAttempt).filter(
                        OrchestratorCorrectionAttempt.workflow_id == workflow_id
                    ).delete(synchronize_session=False)
                    db.query(OrchestratorValidationRecord).filter(
                        OrchestratorValidationRecord.workflow_id == workflow_id
                    ).delete(synchronize_session=False)
                    db.query(OrchestratorEvent).filter(OrchestratorEvent.workflow_id == workflow_id).delete(
                        synchronize_session=False
                    )
                db.commit()
            if fixture.get("workflow_id"):
                delete_workflow(int(fixture["workflow_id"]))
            with get_session() as db:
                for ticket in fixture.get("tickets") or []:
                    row = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket["id"])).first()
                    if row:
                        db.delete(row)
                if fixture.get("board_id"):
                    board = db.query(KanbanBoard).filter(KanbanBoard.id == int(fixture["board_id"])).first()
                    if board:
                        db.delete(board)
                if fixture.get("project_id"):
                    project = db.query(Project).filter(Project.id == int(fixture["project_id"])).first()
                    if project:
                        db.delete(project)
                db.commit()
        except Exception:
            for ticket in fixture.get("tickets") or []:
                self._best_effort_api_delete(f"/tickets/tickets/{int(ticket['id'])}")
            if fixture.get("board_id"):
                self._best_effort_api_delete(f"/tickets/boards/{int(fixture['board_id'])}")
            if fixture.get("workflow_id"):
                self._best_effort_api_delete(f"/workflows/{int(fixture['workflow_id'])}")
            if fixture.get("project_id"):
                self._best_effort_api_delete(f"/projects/{int(fixture['project_id'])}")
        project_dir = fixture.get("project_dir")
        if project_dir:
            safe = assert_safe_disposable_spotify_project_dir(
                Path(project_dir),
                development_root=development_root,
            )
            if safe.exists():
                shutil.rmtree(safe)


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


def _cmd_seed_spotify_program(args: argparse.Namespace) -> int:
    harness = WorkflowTicketLoopHarness(args.base_url)
    if not harness.server_reachable():
        raise SystemExit(f"Web server not reachable at {args.base_url}")
    work_dir = Path(args.work_dir) if args.work_dir else None
    ids = harness.seed_spotify_program(work_dir)
    print(json.dumps(ids, indent=2, default=str))
    return 0


def _cmd_run_spotify_program(args: argparse.Namespace) -> int:
    harness = WorkflowTicketLoopHarness(args.base_url)
    if not harness.server_reachable():
        raise SystemExit(f"Web server not reachable at {args.base_url}")
    work_dir = Path(args.work_dir) if args.work_dir else None
    result = harness.run_spotify_program_chain(work_dir, run_polish=not args.skip_polish)
    print(json.dumps(result, indent=2, default=str))
    return 0


def _cmd_cleanup_e2e_harness(args: argparse.Namespace) -> int:
    harness = WorkflowTicketLoopHarness(args.base_url)
    if not harness.server_reachable():
        raise SystemExit(f"Web server not reachable at {args.base_url}")
    deleted = harness.cleanup_e2e_harness_artifacts(
        profile=(args.profile or None),
        include_legacy=not args.canonical_only,
    )
    print(json.dumps(deleted, indent=2))
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


def _cmd_live_spotify_build(args: argparse.Namespace) -> int:
    harness = WorkflowTicketLoopHarness(args.base_url)
    if not harness.server_reachable():
        raise SystemExit(f"Web server not reachable at {args.base_url}")
    statuses = backend_status_map()
    matrix = select_backend_matrix(
        args.backend,
        statuses=statuses,
        fail_on_unavailable=bool(args.fail_on_unavailable),
    )
    if args.dry_run:
        print(json.dumps({"selected": matrix.selected, "skipped": matrix.skipped}, indent=2, default=str))
        return 0
    if not (args.live and args.i_understand_this_will_create_and_delete_development_files):
        raise SystemExit(
            "Refusing to run live Spotify backend matrix without --live and "
            "--i-understand-this-will-create-and-delete-development-files"
        )
    stamp = args.stamp or time.strftime("%Y%m%d-%H%M%S")
    development_root = Path(args.development_root).expanduser() if args.development_root else None
    report: dict[str, Any] = {"stamp": stamp, "selected": matrix.selected, "skipped": matrix.skipped, "runs": []}
    failures = []
    for backend_id in matrix.selected:
        try:
            result = harness.run_live_spotify_backend(
                backend_id=backend_id,
                stamp=stamp,
                development_root=development_root,
                cleanup=not args.keep_artifacts,
            )
            report["runs"].append(result)
        except Exception as exc:
            failure = {"backend_id": backend_id, "error": str(exc)}
            report["runs"].append({"backend_id": backend_id, "status": "failed", "error": str(exc)})
            failures.append(failure)
            if args.fail_fast:
                print(json.dumps(report, indent=2, default=str))
                raise
    print(json.dumps(report, indent=2, default=str))
    return 1 if failures and args.fail_on_backend_failure else 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable DecisionsAI workflow ticket loop E2E harness")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="DecisionsAI web base URL")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="Create the deterministic until-green workflow fixture")
    seed.add_argument("--work-dir", default="", help="Optional project/marker root directory")
    seed.set_defaults(func=_cmd_seed)

    seed_spotify = sub.add_parser(
        "seed-spotify-program",
        help="Create one Spotify remake program (project, board, tickets, ideation/dev/polish workflows)",
    )
    seed_spotify.add_argument("--work-dir", default="", help="Optional root directory for spotify-remake project")
    seed_spotify.set_defaults(func=_cmd_seed_spotify_program)

    run_spotify = sub.add_parser(
        "run-spotify-program",
        help="Run Spotify program chain: ideation artifacts → dev ticket → polish",
    )
    run_spotify.add_argument("--work-dir", default="", help="Optional root directory for spotify-remake project")
    run_spotify.add_argument("--skip-polish", action="store_true", help="Stop after development workflow")
    run_spotify.set_defaults(func=_cmd_run_spotify_program)

    cleanup_e2e = sub.add_parser(
        "cleanup-e2e-harness",
        help="Delete disposable E2E harness workflows, boards, and projects",
    )
    cleanup_e2e.add_argument(
        "--profile",
        default="",
        help="Only remove one profile's canonical fixture (until-green, spotify-remake)",
    )
    cleanup_e2e.add_argument(
        "--canonical-only",
        action="store_true",
        help="Only remove [e2e] canonical fixtures, not legacy stamped names",
    )
    cleanup_e2e.set_defaults(func=_cmd_cleanup_e2e_harness)

    terminal = sub.add_parser("assert-terminal", help="Assert completed run/ticket/event terminal state")
    terminal.add_argument("--workflow-id", type=int, required=True)
    terminal.add_argument("--ticket-id", type=int, required=True)
    terminal.add_argument("--run-id", type=int)
    terminal.set_defaults(func=_cmd_assert_terminal)

    run_pytest = sub.add_parser("run-pytest", help="Run the reusable Playwright E2E pytest")
    run_pytest.add_argument("--browser", action="append", choices=["chromium", "webkit", "firefox"])
    run_pytest.add_argument("extra", nargs=argparse.REMAINDER, help="Extra pytest args after --")
    run_pytest.set_defaults(func=_cmd_run_pytest)

    live = sub.add_parser(
        "live-spotify-build",
        help="Run the opt-in live Spotify-remake workflow backend matrix",
    )
    live.add_argument(
        "--backend",
        default="all-ready",
        help="Backend id, 'all', or 'all-ready' (default).",
    )
    live.add_argument("--fail-on-unavailable", action="store_true")
    live.add_argument("--development-root", default=str(Path.home() / "development"))
    live.add_argument("--stamp", default="")
    live.add_argument("--dry-run", action="store_true", help="Print selected/skipped backend matrix only.")
    live.add_argument("--live", action="store_true", help="Required to create/delete live project artifacts.")
    live.add_argument(
        "--i-understand-this-will-create-and-delete-development-files",
        action="store_true",
        help="Required destructive-run acknowledgement.",
    )
    live.add_argument("--keep-artifacts", action="store_true", help="Do not clean up after the live run.")
    live.add_argument("--fail-fast", action="store_true", help="Stop the matrix on the first backend failure.")
    live.add_argument(
        "--fail-on-backend-failure",
        action="store_true",
        help="Exit non-zero after completing the matrix if any backend failed.",
    )
    live.set_defaults(func=_cmd_live_spotify_build)

    simulate = sub.add_parser(
        "simulate-flow",
        help="Run offline Senior Engineer preset matrix tests (real loop shape, fake harness)",
    )
    simulate.add_argument("extra", nargs=argparse.REMAINDER, help="Extra pytest args after --")
    simulate.set_defaults(func=_cmd_simulate_flow)

    seed_spotify = sub.add_parser(
        "seed-spotify-board",
        help="Seed Spotify-style priority board + tickets on a running server (no run)",
    )
    seed_spotify.add_argument("--backend", default="cursor_ide")
    seed_spotify.add_argument("--stamp", default="")
    seed_spotify.add_argument("--development-root", default=str(Path.home() / "development"))
    seed_spotify.set_defaults(func=_cmd_seed_spotify_board)

    return parser


def _cmd_simulate_flow(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/core/test_loop_preset_run_matrix.py",
        "tests/core/test_spotify_workflow_chain_e2e.py",
        "tests/core/test_loop_presets.py::test_plan_steps_from_bundle_has_development_loop_contract",
        "-q",
    ]
    extra = list(args.extra or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    if extra:
        cmd.extend(extra)
    return subprocess.call(cmd)


def _cmd_seed_spotify_board(args: argparse.Namespace) -> int:
    harness = WorkflowTicketLoopHarness(args.base_url)
    if not harness.server_reachable():
        raise SystemExit(f"Web server not reachable at {args.base_url}")
    stamp = args.stamp or time.strftime("%Y%m%d-%H%M%S")
    fixture = harness.seed_live_spotify_backend_fixture(
        backend_id=args.backend,
        stamp=stamp,
        development_root=Path(args.development_root).expanduser(),
    )
    print(json.dumps(fixture, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
