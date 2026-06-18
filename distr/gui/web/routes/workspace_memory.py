"""HTTP API for agent workspace memory (filesystem source of truth)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/projects/{project_id}/workspace-memory")
    async def get_project_workspace_memory(project_id: int):
        from distr.core.workspace_memory.pickup_handoff import read_handoff_preview, read_ledger_tail
        from distr.core.workspace_memory.provision import bootstrap_project
        from distr.core.workspace_memory.router import workspace_summary
        from distr.core.workspace_memory.sync import sync_projection_for_project
        from distr.core.db import get_session
        from distr.core.db.projects import Project

        with get_session() as session:
            project = session.query(Project).filter(Project.id == int(project_id)).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            folder = project.folder_location or ""
            board_id = project.kanban_board_id

        try:
            bootstrap_project(project_id)
        except Exception:
            logger.debug("workspace-memory: bootstrap project failed", exc_info=True)

        summary = workspace_summary(
            project_id=project_id,
            board_id=board_id,
            folder_location=folder,
        )
        sync_result = sync_projection_for_project(project_id) if folder else {"ok": False}
        return JSONResponse(
            {
                "project_id": project_id,
                "workspace": summary,
                "handoff_preview": read_handoff_preview("projects", project_id),
                "ledger_tail": read_ledger_tail("projects", project_id, limit=10),
                "projection": sync_result,
            }
        )

    @router.post("/projects/{project_id}/workspace-memory/sync")
    async def sync_project_workspace_memory(project_id: int):
        from distr.core.workspace_memory.sync import sync_projection_for_project

        result = sync_projection_for_project(project_id, force=True)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "sync failed")
        return JSONResponse(result)

    @router.get("/tickets/boards/{board_id}/workspace-memory")
    async def get_board_workspace_memory(board_id: int):
        from distr.core.workspace_memory.pickup_handoff import read_handoff_preview, read_ledger_tail
        from distr.core.workspace_memory.provision import bootstrap_board
        from distr.core.workspace_memory.router import workspace_summary

        try:
            bootstrap_board(board_id)
        except Exception:
            logger.debug("workspace-memory: bootstrap board failed", exc_info=True)
        summary = workspace_summary(board_id=board_id)
        return JSONResponse(
            {
                "board_id": board_id,
                "workspace": summary,
                "handoff_preview": read_handoff_preview("boards", board_id),
                "ledger_tail": read_ledger_tail("boards", board_id, limit=10),
            }
        )

    @router.get("/workflows/{workflow_id}/harness-handoff")
    async def get_workflow_harness_handoff(workflow_id: int, run_id: int | None = None):
        from distr.core.db import get_session
        from distr.core.db.workflow import AutoWorkflow
        from distr.core.workflow.workflow_resolve import validate_run_belongs_to_workflow
        from distr.core.workspace_memory.harness_handoff import build_workflow_harness_handoff

        with get_session() as session:
            wf = session.query(AutoWorkflow).filter(AutoWorkflow.id == int(workflow_id)).first()
            if not wf:
                raise HTTPException(status_code=404, detail="Workflow not found")

        if run_id is not None and not validate_run_belongs_to_workflow(int(run_id), workflow_id):
            raise HTTPException(status_code=404, detail="Run not found for this workflow")

        try:
            payload = build_workflow_harness_handoff(int(workflow_id), run_id=run_id, refresh=True)
        except Exception as exc:
            logger.exception("harness-handoff failed for workflow %s", workflow_id)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse(payload)

    @router.get("/workflows/{workflow_id}/workspace-memory")
    async def get_workflow_workspace_memory(workflow_id: int):
        from distr.core.workspace_memory.pickup_handoff import read_handoff_preview
        from distr.core.workspace_memory.provision import bootstrap_workflow, build_step_routing_table
        from distr.core.workspace_memory.router import workspace_summary

        try:
            bootstrap_workflow(workflow_id)
        except Exception:
            logger.debug("workspace-memory: bootstrap workflow failed", exc_info=True)
        summary = workspace_summary(workflow_id=workflow_id)
        return JSONResponse(
            {
                "workflow_id": workflow_id,
                "workspace": summary,
                "handoff_preview": read_handoff_preview("workflows", workflow_id),
                "step_routing_table": build_step_routing_table(workflow_id),
            }
        )

    @router.get("/workflows/{workflow_id}/runs/{run_id}/workspace-memory")
    async def get_run_workspace_memory(workflow_id: int, run_id: int):
        from distr.core.workflow.workflow_resolve import validate_run_belongs_to_workflow
        from distr.core.workspace_memory.pickup_handoff import read_handoff_preview, read_ledger_tail
        from distr.core.workspace_memory.provision import bootstrap_run
        from distr.core.workspace_memory.router import workspace_summary

        if not validate_run_belongs_to_workflow(run_id, workflow_id):
            raise HTTPException(status_code=404, detail="Run not found for this workflow")

        try:
            bootstrap_run(run_id, workflow_id=workflow_id)
        except Exception:
            logger.debug("workspace-memory: bootstrap run failed", exc_info=True)
        summary = workspace_summary(workflow_id=workflow_id, run_id=run_id)
        return JSONResponse(
            {
                "workflow_id": workflow_id,
                "run_id": run_id,
                "workspace": summary,
                "handoff_preview": read_handoff_preview("runs", run_id),
                "ledger_tail": read_ledger_tail("runs", run_id, limit=20),
            }
        )

    @router.post("/workspace-memory/migrate")
    async def migrate_workspace_memory():
        from distr.core.workspace_memory.provision import migrate_db_context_to_filesystem

        return JSONResponse(migrate_db_context_to_filesystem())

    @router.get("/tickets/tickets/{ticket_id}/workspace-memory")
    async def get_ticket_workspace_memory(ticket_id: int):
        from distr.core.workspace_memory.lifecycle import hook_ensure_workspace
        from distr.core.workspace_memory.pickup_handoff import read_handoff_preview, read_ledger_tail
        from distr.core.workspace_memory.reader import load_workspace_context
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanLane, KanbanTicket

        with get_session() as session:
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found")
            lane = session.query(KanbanLane).filter(KanbanLane.id == ticket.lane_id).first()
            board_id = lane.board_id if lane else None
            project_id = ticket.linked_project_id

        hook_ensure_workspace("tickets", ticket_id, reason="api_get")
        ctx = load_workspace_context(
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=project_id,
            ensure=False,
        )
        return JSONResponse(
            {
                "ticket_id": ticket_id,
                "workspace": {
                    "companion_paths": ctx.companion_paths,
                    "router_chain": ctx.router_chain,
                    "handoff_preview": ctx.handoff_preview,
                    "references_index": ctx.references_index,
                },
                "handoff_preview": read_handoff_preview("tickets", ticket_id),
                "ledger_tail": read_ledger_tail("tickets", ticket_id, limit=20),
            }
        )

    @router.post("/tickets/tickets/{ticket_id}/workspace-memory/handoff")
    async def post_ticket_workspace_handoff(ticket_id: int, request: Request):
        from distr.core.workspace_memory.lifecycle import hook_ensure_workspace
        from distr.core.workspace_memory.pickup_handoff import perform_handoff
        from distr.core.db import get_session
        from distr.core.db.kanban import KanbanTicket

        payload: dict[str, Any] = {}
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}

        with get_session() as session:
            ticket = session.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
            if not ticket:
                raise HTTPException(status_code=404, detail="Ticket not found")

        body = (payload.get("summary") or "").strip() or "Ticket handoff."
        hook_ensure_workspace("tickets", ticket_id, reason="api_handoff")
        result = perform_handoff("tickets", ticket_id, summary=body, source="api_handoff")
        return JSONResponse({"ticket_id": ticket_id, **result})

    return router
