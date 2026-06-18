"""Build hierarchical router tables and workspace summaries."""

from __future__ import annotations

from typing import Any

from .paths import (
    AGENTS_FILE,
    HANDOFF_FILE,
    ROUTER_FILE,
    companion_memory_file,
    companion_root,
    org_companion_root,
    projection_root,
)
def org_router_path() -> str:
    return str(org_companion_root() / ROUTER_FILE)


def board_router_path(board_id: int) -> str:
    return str(companion_root("boards", board_id) / ROUTER_FILE)


def project_router_path(project_id: int) -> str:
    return str(companion_root("projects", project_id) / ROUTER_FILE)


def workflow_router_path(workflow_id: int) -> str:
    return str(companion_root("workflows", workflow_id) / ROUTER_FILE)


def run_companion_path(run_id: int) -> str:
    return str(companion_root("runs", run_id))


def ticket_companion_path(ticket_id: int) -> str:
    return str(companion_root("tickets", ticket_id))


def parent_router_for_board(board_id: int | None) -> str:
    return org_router_path()


def parent_router_for_project(kanban_board_id: int | None) -> str:
    if kanban_board_id:
        return board_router_path(kanban_board_id)
    return org_router_path()


def parent_router_for_workflow(board_id: int | None) -> str:
    if board_id:
        return board_router_path(board_id)
    return org_router_path()


def router_chain(
    *,
    project_id: int | None = None,
    board_id: int | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    ticket_id: int | None = None,
) -> list[dict[str, str]]:
    """Ordered parent chain from most specific to org."""
    chain: list[dict[str, str]] = []
    if run_id:
        chain.append({"entity": "run", "id": str(run_id), "router": str(companion_root("runs", run_id) / ROUTER_FILE)})
    if ticket_id:
        chain.append({"entity": "ticket", "id": str(ticket_id), "router": str(companion_root("tickets", ticket_id) / ROUTER_FILE)})
    if workflow_id:
        chain.append({"entity": "workflow", "id": str(workflow_id), "router": workflow_router_path(workflow_id)})
    if project_id:
        chain.append({"entity": "project", "id": str(project_id), "router": project_router_path(project_id)})
    if board_id:
        chain.append({"entity": "board", "id": str(board_id), "router": board_router_path(board_id)})
    chain.append({"entity": "org", "id": "decisionsai", "router": org_router_path()})
    return chain


def workspace_summary(
    *,
    project_id: int | None = None,
    board_id: int | None = None,
    workflow_id: int | None = None,
    run_id: int | None = None,
    ticket_id: int | None = None,
    folder_location: str = "",
) -> dict[str, Any]:
    """Compact workspace block for developer_context."""
    from .pickup_handoff import read_handoff_preview

    companion_paths: dict[str, str] = {}
    if project_id:
        companion_paths["project"] = str(companion_root("projects", project_id))
    if board_id:
        companion_paths["board"] = str(companion_root("boards", board_id))
    if workflow_id:
        companion_paths["workflow"] = str(companion_root("workflows", workflow_id))
    if run_id:
        companion_paths["run"] = run_companion_path(run_id)
    if ticket_id:
        companion_paths["ticket"] = ticket_companion_path(ticket_id)

    projection = str(projection_root(folder_location)) if folder_location else ""
    handoff_preview = ""
    for entity_type, entity_id in (
        ("runs", run_id),
        ("projects", project_id),
        ("boards", board_id),
        ("workflows", workflow_id),
    ):
        if entity_id:
            handoff_preview = read_handoff_preview(entity_type, int(entity_id))  # type: ignore[arg-type]
            if handoff_preview:
                break

    return {
        "companion_paths": companion_paths,
        "projection_path": projection,
        "router_chain": router_chain(
            project_id=project_id,
            board_id=board_id,
            workflow_id=workflow_id,
            run_id=run_id,
            ticket_id=ticket_id,
        ),
        "handoff_preview": handoff_preview,
        "org_router": org_router_path(),
    }
