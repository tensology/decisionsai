"""Bootstrap companion workspaces for org, board, project, workflow, run, ticket."""

from __future__ import annotations

import json
import logging
from typing import Any

from distr.core.db import get_session

from .paths import AGENTS_FILE, companion_root, org_companion_root
from .pickup_handoff import write_active, write_handoff
from .references import sync_entity_references
from .router import org_router_path, parent_router_for_board, parent_router_for_project, parent_router_for_workflow
from .stages import build_step_routing_from_stages, sync_workflow_stages
from .template import (
    agents_md,
    board_context_md,
    board_router_md,
    default_step_routing_table,
    org_router_md,
    project_context_md,
    project_router_md,
    ticket_context_md,
    ticket_router_md,
    workflow_context_md,
    workflow_router_md,
    workflow_agents_md,
    write_entity_files,
)

logger = logging.getLogger(__name__)


def _scaffold_exists(entity_type: str, entity_id: int | str) -> bool:
    return (companion_root(entity_type, entity_id) / AGENTS_FILE).is_file()  # type: ignore[arg-type]


def _org_registry() -> tuple[list[dict], list[dict], list[dict]]:
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    from distr.core.db.workflow import AutoWorkflow

    boards: list[dict] = []
    projects: list[dict] = []
    workflows: list[dict] = []
    try:
        with get_session() as session:
            boards = [{"id": b.id, "name": b.name or f"Board {b.id}"} for b in session.query(KanbanBoard).limit(40).all()]
            projects = [{"id": p.id, "name": p.name or f"Project {p.id}"} for p in session.query(Project).limit(40).all()]
            workflows = [{"id": w.id, "name": w.name or f"Workflow {w.id}"} for w in session.query(AutoWorkflow).limit(40).all()]
    except Exception:
        logger.debug("_org_registry failed", exc_info=True)
    return boards, projects, workflows


def ensure_workspace(
    entity_type: str,
    entity_id: int | str,
    *,
    force: bool = False,
    reason: str = "",
    run_kwargs: dict[str, Any] | None = None,
) -> str:
    """Idempotent companion workspace provision."""
    if reason:
        logger.debug("ensure_workspace %s/%s force=%s reason=%s", entity_type, entity_id, force, reason)
    if entity_type == "org":
        return bootstrap_org(force=force)
    if entity_type == "runs":
        kwargs = run_kwargs or {}
        return bootstrap_run(int(entity_id), force=force, **kwargs)
    if not force and _scaffold_exists(entity_type, entity_id):
        return str(companion_root(entity_type, entity_id))  # type: ignore[arg-type]
    handlers = {
        "boards": lambda: bootstrap_board(int(entity_id), force=True),
        "projects": lambda: bootstrap_project(int(entity_id), force=True),
        "workflows": lambda: bootstrap_workflow(int(entity_id), force=True),
        "tickets": lambda: bootstrap_ticket(int(entity_id), force=True),
    }
    handler = handlers.get(entity_type)
    if not handler:
        raise ValueError(f"unsupported entity_type: {entity_type}")
    return handler()


def bootstrap_org(*, force: bool = False) -> str:
    """Ensure org-level workspace exists."""
    root = org_companion_root()
    if root.is_dir() and not force and (root / "agents.md").is_file():
        return str(root)

    board_count = project_count = workflow_count = 0
    board_registry: list[dict] = []
    project_registry: list[dict] = []
    workflow_registry: list[dict] = []
    try:
        from distr.core.db.kanban import KanbanBoard
        from distr.core.db.projects import Project
        from distr.core.db.workflow import AutoWorkflow

        with get_session() as session:
            board_count = session.query(KanbanBoard).count()
            project_count = session.query(Project).count()
            workflow_count = session.query(AutoWorkflow).count()
            board_registry, project_registry, workflow_registry = _org_registry()
    except Exception:
        logger.debug("bootstrap_org: entity counts failed", exc_info=True)

    mission = "Coordinate DecisionsAI projects, boards, and workflows via filesystem routing."
    return write_entity_files(
        "org",
        "decisionsai",
        agents_content=agents_md(entity_type="org", name="DecisionsAI", mission=mission),
        router_content=org_router_md(
            board_count=board_count,
            project_count=project_count,
            workflow_count=workflow_count,
            board_registry=board_registry,
            project_registry=project_registry,
            workflow_registry=workflow_registry,
        ),
        context_content="# Org context\n\nGlobal routing lobby for DecisionsAI.\n",
        decisions={"entity_type": "org", "slug": "decisionsai"},
    )


def bootstrap_board(board_id: int, *, force: bool = False) -> str:
    bootstrap_org()
    if not force and _scaffold_exists("boards", board_id):
        return str(companion_root("boards", board_id))
    from distr.core.db.kanban import KanbanBoard, KanbanLane

    with get_session() as session:
        board = session.query(KanbanBoard).filter(KanbanBoard.id == int(board_id)).first()
        if not board:
            raise ValueError(f"board not found: {board_id}")
        lanes = (
            session.query(KanbanLane)
            .filter(KanbanLane.board_id == board.id)
            .order_by(KanbanLane.position)
            .all()
        )
        lane_names = [lane.name for lane in lanes]
        name = board.name or f"Board {board_id}"
        description = board.description or ""
        default_project_id = board.default_project_id
        default_workflow_id = board.default_workflow_id
        policy: dict[str, Any] = {}
        if board.orchestrator_policy:
            try:
                policy = json.loads(board.orchestrator_policy) or {}
            except Exception:
                policy = {}

    parent = parent_router_for_board(board_id)
    path = write_entity_files(
        "boards",
        board_id,
        agents_content=agents_md(entity_type="board", name=name, mission=f"Manage tickets on {name}."),
        router_content=board_router_md(
            board_id=board_id,
            board_name=name,
            default_project_id=default_project_id,
            default_workflow_id=default_workflow_id,
            lane_names=lane_names,
            parent_path=parent,
        ),
        context_content=board_context_md(description=description, orchestrator_policy=policy),
        decisions={
            "entity_type": "board",
            "board_id": board_id,
            "default_project_id": default_project_id,
            "default_workflow_id": default_workflow_id,
        },
    )
    _migrate_board_notes_to_active(board_id)
    try:
        sync_entity_references("boards", board_id)
    except Exception:
        logger.debug("bootstrap_board: references sync failed", exc_info=True)
    return path


def bootstrap_project(project_id: int, *, force: bool = False) -> str:
    bootstrap_org()
    if not force and _scaffold_exists("projects", project_id):
        return str(companion_root("projects", project_id))
    from distr.core.db.projects import Project, ProjectContextItem

    with get_session() as session:
        project = session.query(Project).filter(Project.id == int(project_id)).first()
        if not project:
            raise ValueError(f"project not found: {project_id}")
        items = (
            session.query(ProjectContextItem)
            .filter(ProjectContextItem.project_id == project.id)
            .order_by(ProjectContextItem.id)
            .all()
        )
        context_items = [{"title": i.title, "content": i.content} for i in items]
        name = project.name or f"Project {project_id}"
        board_id = project.kanban_board_id
        startup_instructions = project.startup_instructions or ""
        folder_location = project.folder_location or ""
        coding_backend = project.coding_backend or ""
        project_in_use = bool(project.in_use)
        if board_id:
            try:
                bootstrap_board(int(board_id))
            except Exception:
                logger.debug("bootstrap_project: board bootstrap failed", exc_info=True)

    parent = parent_router_for_project(board_id)
    path = write_entity_files(
        "projects",
        project_id,
        agents_content=agents_md(
            entity_type="project",
            name=name,
            mission=startup_instructions.strip() or f"Develop in {name}.",
        ),
        router_content=project_router_md(
            project_id=project_id,
            project_name=name,
            folder_location=folder_location,
            kanban_board_id=board_id,
            coding_backend=coding_backend,
            parent_path=parent,
        ),
        context_content=project_context_md(
            startup_instructions=startup_instructions,
            context_items=context_items,
        ),
        decisions={
            "entity_type": "project",
            "project_id": project_id,
            "kanban_board_id": board_id,
            "folder_location": folder_location,
            "coding_backend": coding_backend,
        },
    )
    if project_in_use and folder_location:
        try:
            from .sync import sync_projection_for_project

            sync_projection_for_project(project_id)
        except Exception:
            logger.debug("bootstrap_project: projection sync failed", exc_info=True)
    try:
        sync_entity_references("projects", project_id)
    except Exception:
        logger.debug("bootstrap_project: references sync failed", exc_info=True)
    return path


def build_step_routing_table(workflow_id: int) -> str:
    try:
        return build_step_routing_from_stages(workflow_id)
    except Exception:
        logger.debug("build_step_routing_table: stage routing failed", exc_info=True)
    from distr.core.db.workflow import AutoWorkflowStep

    with get_session() as session:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowStep.position)
            .all()
        )
        step_rows = [
            {
                "position": int(step.position or 0),
                "id": step.id,
                "name": step.name or "",
                "action_type": (step.action_type or "agent_instruction").strip(),
            }
            for step in steps
        ]
    if not step_rows:
        return default_step_routing_table()

    lines = [
        "## Step routing",
        "",
        "| Step | Action | Read | Skip | Skills |",
        "|------|--------|------|------|--------|",
    ]
    for row in step_rows:
        action = row["action_type"]
        read = "memory/handoff.md, memory/active.md"
        skip = "pipeline/output/"
        skills = "—"
        step_name = row["name"].lower()
        if action in {"send_to_project_cli", "run_command"}:
            read = "memory/handoff.md, active ticket, project router"
            skills = "decisions-cursor-worker / decisions-codex-worker"
        elif "validation" in step_name or action == "http_request":
            read = "board learned rules, run steering log"
            skip = "pipeline/brief/"
            skills = "browser-qa"
        elif "plan" in step_name:
            read = "pipeline/brief/, board router"
            skip = "pipeline/output/"
            skills = "planning"
        lines.append(f"| {row['name']} | {action} | {read} | {skip} | {skills} |")
    return "\n".join(lines) + "\n"


def _workflow_step_lines(workflow_id: int) -> list[str]:
    from distr.core.db.workflow import AutoWorkflowStep

    with get_session() as session:
        steps = (
            session.query(AutoWorkflowStep)
            .filter(AutoWorkflowStep.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowStep.position)
            .all()
        )
        step_rows = [
            {
                "position": int(step.position or 0),
                "id": step.id,
                "name": step.name or "",
                "linked_project_id": step.linked_project_id,
                "action_type": step.action_type or "agent_instruction",
            }
            for step in steps
        ]
    lines = []
    for row in step_rows:
        linked = f", project={row['linked_project_id']}" if row["linked_project_id"] else ""
        lines.append(f"- #{row['position']} {row['name']} ({row['action_type']}{linked})")
    return lines


def _workflow_agent_context_sections(workflow_id: int) -> list[tuple[str, str]]:
    from distr.core.db.workflow import AutoWorkflowVariable

    with get_session() as session:
        rows = (
            session.query(AutoWorkflowVariable)
            .filter(AutoWorkflowVariable.workflow_id == int(workflow_id))
            .order_by(AutoWorkflowVariable.id)
            .all()
        )
        # Materialize scalar values before SessionContext commits/closes. The
        # commit expires ORM attributes, so reading row.name afterwards raises
        # DetachedInstanceError and silently removes workflow memory from the
        # developer context assembled for every step.
        return [(row.name or "Context", row.default_value or "") for row in rows]


def _workflow_board_id(workflow_id: int) -> int | None:
    from distr.core.db.kanban import KanbanBoard

    with get_session() as session:
        board = (
            session.query(KanbanBoard)
            .filter(KanbanBoard.default_workflow_id == int(workflow_id))
            .first()
        )
        return board.id if board else None


def bootstrap_workflow(workflow_id: int, *, force: bool = False) -> str:
    bootstrap_org()
    if not force and _scaffold_exists("workflows", workflow_id):
        return str(companion_root("workflows", workflow_id))
    from distr.core.db.workflow import AutoWorkflow

    with get_session() as session:
        wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
        if not wf:
            raise ValueError(f"workflow not found: {workflow_id}")
        name = wf.name or f"Workflow {workflow_id}"
        context_rules = wf.context_rules or ""

    board_id = _workflow_board_id(workflow_id)
    if board_id:
        try:
            bootstrap_board(board_id)
        except Exception:
            logger.debug("bootstrap_workflow: board bootstrap failed", exc_info=True)

    parent = parent_router_for_workflow(board_id)
    path = write_entity_files(
        "workflows",
        workflow_id,
        agents_content=workflow_agents_md(
            workflow_id=workflow_id,
            name=name,
            mission=f"Execute workflow {name}.",
        ),
        router_content=workflow_router_md(
            workflow_id=workflow_id,
            workflow_name=name,
            board_id=board_id,
            step_lines=_workflow_step_lines(workflow_id),
            parent_path=parent,
        ),
        context_content=workflow_context_md(
            context_rules=context_rules,
            agent_context_sections=_workflow_agent_context_sections(workflow_id),
            step_routing_table=build_step_routing_table(workflow_id),
        ),
        decisions={
            "entity_type": "workflow",
            "workflow_id": workflow_id,
            "board_id": board_id,
        },
    )
    try:
        sync_entity_references("workflows", workflow_id)
        sync_workflow_stages(workflow_id)
    except Exception:
        logger.debug("bootstrap_workflow: references/stages sync failed", exc_info=True)
    return path


def bootstrap_run(
    run_id: int,
    *,
    workflow_id: int | None = None,
    board_id: int | None = None,
    ticket_id: int | None = None,
    project_id: int | None = None,
    step_id: int | None = None,
    force: bool = False,
) -> str:
    bootstrap_org()
    if not force and _scaffold_exists("runs", run_id):
        return str(companion_root("runs", run_id))
    if workflow_id:
        try:
            bootstrap_workflow(int(workflow_id))
        except Exception:
            logger.debug("bootstrap_run: workflow bootstrap failed", exc_info=True)
    if project_id:
        try:
            bootstrap_project(int(project_id))
        except Exception:
            logger.debug("bootstrap_run: project bootstrap failed", exc_info=True)
    if ticket_id:
        try:
            bootstrap_ticket(int(ticket_id))
        except Exception:
            logger.debug("bootstrap_run: ticket bootstrap failed", exc_info=True)

    from .router import parent_router_for_workflow

    parent = parent_router_for_workflow(board_id) if board_id else org_router_path()
    mission = f"Workflow run #{run_id}"
    path = write_entity_files(
        "runs",
        run_id,
        agents_content=agents_md(entity_type="run", name=f"Run {run_id}", mission=mission),
        router_content=f"# Run router — #{run_id}\n\nparent: {parent}\n",
        context_content=f"# Run context\n\n- run_id: {run_id}\n- workflow_id: {workflow_id}\n- step_id: {step_id}\n",
        decisions={
            "entity_type": "run",
            "run_id": run_id,
            "workflow_id": workflow_id,
            "board_id": board_id,
            "ticket_id": ticket_id,
            "project_id": project_id,
            "step_id": step_id,
        },
    )
    if project_id:
        try:
            from .sync import sync_projection_for_project

            sync_projection_for_project(int(project_id))
        except Exception:
            logger.debug("bootstrap_run: projection sync failed", exc_info=True)
    return path


def bootstrap_ticket(ticket_id: int, *, force: bool = False) -> str:
    bootstrap_org()
    if not force and _scaffold_exists("tickets", ticket_id):
        return str(companion_root("tickets", ticket_id))
    from distr.core.db.kanban import KanbanLane, KanbanTicket

    with get_session() as session:
        ticket = session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        if not ticket:
            raise ValueError(f"ticket not found: {ticket_id}")
        lane = session.query(KanbanLane).filter(KanbanLane.id == ticket.lane_id).first()
        board_id = lane.board_id if lane else None
        title = ticket.title or f"Ticket {ticket_id}"
        linked_project_id = ticket.linked_project_id
        linked_workflow_id = ticket.linked_workflow_id
        description = ticket.description or ""
        context_notes = ticket.context_notes or ""

    if board_id:
        try:
            bootstrap_board(int(board_id))
        except Exception:
            logger.debug("bootstrap_ticket: board bootstrap failed", exc_info=True)

    parent = parent_router_for_board(board_id) if board_id else org_router_path()
    path = write_entity_files(
        "tickets",
        ticket_id,
        agents_content=agents_md(entity_type="ticket", name=title, mission=f"Work ticket: {title}."),
        router_content=ticket_router_md(
            ticket_id=ticket_id,
            title=title,
            board_id=board_id,
            linked_project_id=linked_project_id,
            linked_workflow_id=linked_workflow_id,
            parent_path=parent,
        ),
        context_content=ticket_context_md(
            title=title,
            description=description,
            context_notes=context_notes,
        ),
        decisions={
            "entity_type": "ticket",
            "ticket_id": ticket_id,
            "board_id": board_id,
            "linked_project_id": linked_project_id,
            "linked_workflow_id": linked_workflow_id,
        },
    )
    return path


def _migrate_board_notes_to_active(board_id: int) -> None:
    """One-time import of legacy global board notes into per-board active.md."""
    try:
        from distr.core.kanban.board_notes import load_board_notes

        notes = load_board_notes()
        if not notes:
            return
        lines = ["# Board notes", ""]
        for note in notes:
            title = note.get("title") or "Untitled"
            body = (note.get("content") or "").strip()
            lines.append(f"## {title}")
            lines.append(body or "(empty)")
            lines.append("")
        write_active("boards", board_id, "\n".join(lines).strip())
    except Exception:
        logger.debug("migrate board notes failed", exc_info=True)


def migrate_db_context_to_filesystem(*, project_id: int | None = None, workflow_id: int | None = None) -> dict[str, Any]:
    """One-time migration helper: regenerate companion files from DB."""
    migrated: list[str] = []
    bootstrap_org()
    if project_id:
        bootstrap_project(int(project_id), force=True)
        migrated.append(f"project:{project_id}")
    else:
        from distr.core.db.projects import Project

        with get_session() as session:
            for row in session.query(Project).all():
                bootstrap_project(row.id, force=True)
                migrated.append(f"project:{row.id}")
    if workflow_id:
        bootstrap_workflow(int(workflow_id), force=True)
        migrated.append(f"workflow:{workflow_id}")
    else:
        from distr.core.db.workflow import AutoWorkflow

        with get_session() as session:
            for row in session.query(AutoWorkflow).all():
                bootstrap_workflow(row.id, force=True)
                migrated.append(f"workflow:{row.id}")
    from distr.core.db.kanban import KanbanBoard

    with get_session() as session:
        for row in session.query(KanbanBoard).all():
            bootstrap_board(row.id, force=True)
            migrated.append(f"board:{row.id}")
    from distr.core.db.kanban import KanbanTicket

    with get_session() as session:
        for row in session.query(KanbanTicket).all():
            bootstrap_ticket(row.id, force=True)
            migrated.append(f"ticket:{row.id}")
    return {"migrated": migrated}
