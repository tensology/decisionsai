#!/usr/bin/env python3
"""Create a DecisionsAI workflow-loop smoke-test fixture.

The fixture is intentionally named with the same prefix used by
cleanup_workflow_loop_smoke.py so it can be deleted safely afterward.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


SMOKE_MARKER = "[dai-smoke-loop-fixture]"
DEFAULT_PROJECT_NAME = "Bean & Byte Coffee Co"
DEFAULT_DOMAIN = "beanandbyte.test"
DEFAULT_FOLDER = "/Users/paul/development/WORK/SMOKE/beanandbyte.test"
DEFAULT_MODELS = [
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/nemotron-3-super-120b-a12b",
]
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create one realistic workflow-loop smoke-test project with scoped tickets.")
    parser.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    parser.add_argument("--domain", default=DEFAULT_DOMAIN)
    parser.add_argument("--folder", default=DEFAULT_FOLDER)
    parser.add_argument("--model", action="append", default=[], help="NVIDIA model id. Can be passed twice.")
    parser.add_argument("--replace", action="store_true", help="Delete the existing smoke fixture before creating a fresh one.")
    args = parser.parse_args()

    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
    from distr.core.workflow.loop_preset_loader import normalize_bundle_steps

    project_name = str(args.project_name or DEFAULT_PROJECT_NAME).strip()
    domain = str(args.domain or DEFAULT_DOMAIN).strip()
    folder = Path(args.folder).expanduser()
    models = (args.model or DEFAULT_MODELS)[:2] or DEFAULT_MODELS
    while len(models) < 2:
        models.append(models[0])

    if args.replace:
        cleanup_path = REPO_ROOT / "scripts/cleanup_workflow_loop_smoke.py"
        subprocess.run(
            [sys.executable, str(cleanup_path), "--marker", SMOKE_MARKER, "--yes"],
            check=True,
        )

    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text(
        f"# {project_name}\n\n"
        f"Domain: {domain}\n\n"
        f"{SMOKE_MARKER}\n\n"
        "Temporary DecisionsAI workflow-loop smoke fixture. Safe to delete with "
        "`python3 scripts/cleanup_workflow_loop_smoke.py --yes`.\n",
        encoding="utf-8",
    )

    preset_path = REPO_ROOT / "distr/core/workflow/loop_preset_bundles/bundles/development-ticket-to-implementation.json"
    bundle = json.loads(preset_path.read_text(encoding="utf-8"))
    steps = normalize_bundle_steps(bundle)

    run_settings = {
        "execution_mode": "serial",
        "concurrency_scope": "workflow",
        "max_parallel_tickets": 1,
        "branch_per_ticket": True,
        "auto_route_models": True,
        "free_only": True,
        "prefer_local": False,
        "complexity_routes": {
            "low": {"backend": "pi", "provider": "nvidia", "model": models[1]},
            "medium": {"backend": "pi", "provider": "nvidia", "model": models[0]},
            "high": {"backend": "pi", "provider": "nvidia", "model": models[0]},
        },
    }

    with get_session() as db:
        project = Project(
            name=project_name,
            description=(
                f"{SMOKE_MARKER} Test project for a realistic coffee-shop product at {domain}. "
                "The project and board stay product-focused; implementation scope lives in tickets."
            ),
            folder_location=str(folder),
            coding_backend="pi",
            coding_backend_model=models[0],
        )
        db.add(project)
        db.flush()

        workflow = AutoWorkflow(
            name="Development: Ticket to Implementation",
            description=(
                f"{SMOKE_MARKER} Reusable implementation loop for scoped tickets. "
                "Uses NVIDIA Nemotron scoped/free models during smoke runs."
            ),
            status="active",
            workflow_type="manual",
            run_settings=json.dumps(run_settings, sort_keys=True),
        )
        db.add(workflow)
        db.flush()

        for idx, step in enumerate(steps):
            cfg = dict(step.get("config") or {})
            cfg["backend_id"] = "pi"
            cfg["model"] = models[idx % 2]
            cfg["model_provider"] = "nvidia"
            cfg["model_policy"] = {
                "free_only": True,
                "prefer_local": False,
                "provider": "nvidia",
            }
            db.add(AutoWorkflowStep(
                workflow_id=workflow.id,
                position=idx,
                name=step.get("title") or f"Step {idx + 1}",
                description=step.get("instruction") or "",
                action_type=step.get("action_type") or "send_to_project_cli",
                instruction=step.get("instruction") or "",
                step_type=step.get("action_type") or "send_to_project_cli",
                config=json.dumps(cfg, sort_keys=True),
                verification=step.get("verification") or "",
                validation_type=step.get("validation_type") or "llm_judgment",
                validation_prompt=step.get("validation_prompt") or step.get("verification") or "",
                on_pass_goto=step.get("on_pass_goto_position"),
                on_fail_goto=step.get("on_fail_goto_position"),
                wait_for_continue=bool(step.get("wait_for_continue")),
                timeout_seconds=int(step.get("timeout_seconds") or 300),
            ))

        board = KanbanBoard(
            name=project_name,
            description=f"{SMOKE_MARKER} Board for {project_name} ({domain}).",
            source="database",
            default_workflow_id=workflow.id,
            default_project_id=project.id,
            send_to_cli=False,
            color="#f97316",
        )
        db.add(board)
        db.flush()

        backlog = KanbanLane(board_id=board.id, name="Backlog", position=0)
        running = KanbanLane(board_id=board.id, name="Running", position=1)
        done = KanbanLane(board_id=board.id, name="Done", position=2)
        db.add_all([backlog, running, done])
        db.flush()

        ticket_specs = [
            {
                "title": "Set up React frontend and Django backend infrastructure",
                "description": (
                    f"{SMOKE_MARKER}\n"
                    f"Project: {project_name} ({domain})\n"
                    "Create the initial application scaffold in the project folder. "
                    "Add a React frontend, a Django backend, and a dedicated Python virtual environment "
                    "inside the project folder. Include startup commands, a backend health/API endpoint, "
                    "and a minimal frontend page that calls or documents the backend endpoint. "
                    "Keep the setup reversible and avoid global installs."
                ),
                "priority": "high",
                "complexity": "medium",
                "queue": 0,
            },
            {
                "title": "Add coffee-shop landing page and menu data shell",
                "description": (
                    f"{SMOKE_MARKER}\n"
                    "Build a small product-shaped slice after infrastructure exists: a branded landing page, "
                    "sample menu/category data, and a simple backend endpoint or fixture that the frontend can consume."
                ),
                "priority": "medium",
                "complexity": "medium",
                "queue": 1,
            },
            {
                "title": "Run browser smoke check and capture evidence",
                "description": (
                    f"{SMOKE_MARKER}\n"
                    "Start the app if possible, run a browser smoke check, capture evidence, and report whether the "
                    "frontend/backend scaffold is usable. If blocked, record the exact command or dependency that failed."
                ),
                "priority": "medium",
                "complexity": "low",
                "queue": 2,
            },
        ]
        tickets = []
        for spec in ticket_specs:
            ticket = KanbanTicket(
                lane_id=backlog.id,
                title=spec["title"],
                description=spec["description"],
                priority=spec["priority"],
                complexity=spec["complexity"],
                linked_workflow_id=workflow.id,
                linked_project_id=project.id,
                workflow_queue_position=spec["queue"],
                source_provider="manual",
                source_label="smoke-test",
            )
            db.add(ticket)
            db.flush()
            tickets.append(ticket)

        board.default_project_id = project.id
        board.default_workflow_id = workflow.id
        project.kanban_board_id = board.id
        db.commit()

        print(json.dumps({
            "project_id": project.id,
            "project_name": project.name,
            "project_folder": str(folder),
            "board_id": board.id,
            "board_name": board.name,
            "workflow_id": workflow.id,
            "ticket_ids": [ticket.id for ticket in tickets],
            "first_ticket_id": tickets[0].id if tickets else None,
            "models": models,
            "cleanup": "python3 scripts/cleanup_workflow_loop_smoke.py --yes",
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
