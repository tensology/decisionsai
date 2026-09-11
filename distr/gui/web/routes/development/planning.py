"""Planning HTTP boundary. Existing URLs remain compatible."""
from fastapi.responses import JSONResponse
import asyncio
from .models import PlanFileReconcileRequest, PlanInstructionRequest, PlanItemCreateRequest, PlanItemUpdateRequest, PlanWorkspaceEnsureRequest


def register_routes(router, templates):
    @router.get("/workflows/studio/plan-workspaces")
    async def workflow_studio_plan_workspaces():
        from distr.core.planning.service import list_workspaces

        return JSONResponse({"items": await asyncio.to_thread(list_workspaces)})


    @router.post("/workflows/studio/plan-workspaces")
    async def workflow_studio_ensure_plan_workspace(data: PlanWorkspaceEnsureRequest):
        try:
            from distr.core.planning.service import ensure_workspace

            return JSONResponse(await asyncio.to_thread(
                ensure_workspace,
                board_key=data.board_key,
                board_provider=data.board_provider,
                board_name=data.board_name,
                project_id=data.project_id,
            ))
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)


    @router.get("/workflows/studio/plan-workspaces/{workspace_id}")
    async def workflow_studio_plan_workspace(workspace_id: int):
        from distr.core.planning.service import get_workspace

        workspace = await asyncio.to_thread(get_workspace, workspace_id)
        if workspace is None:
            return JSONResponse({"detail": "Plan workspace not found."}, status_code=404)
        return JSONResponse(workspace)

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/ticket-preview")
    async def workflow_studio_plan_ticket_preview(workspace_id: int):
        try:
            from distr.core.planning.service import ticket_preview
            return JSONResponse(await asyncio.to_thread(ticket_preview, workspace_id))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/build-tickets")
    async def workflow_studio_build_plan_tickets(workspace_id: int):
        try:
            from distr.core.planning.service import build_approved_tickets
            return JSONResponse(await asyncio.to_thread(build_approved_tickets, workspace_id))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)


    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/items")
    async def workflow_studio_create_plan_item(workspace_id: int, data: PlanItemCreateRequest):
        try:
            from distr.core.planning.service import create_item

            return JSONResponse(await asyncio.to_thread(
                create_item,
                workspace_id=workspace_id,
                item_type=data.item_type,
                title=data.title,
                content=data.content,
                content_format=data.content_format,
            ))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)


    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/instructions")
    async def workflow_studio_plan_instruction(workspace_id: int, data: PlanInstructionRequest):
        try:
            from distr.core.planning.service import apply_instruction

            return JSONResponse(await asyncio.to_thread(
                apply_instruction,
                workspace_id,
                instruction=data.instruction,
                item_id=data.item_id,
            ))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)


    @router.patch("/workflows/studio/plan-items/{item_id}")
    async def workflow_studio_update_plan_item(item_id: int, data: PlanItemUpdateRequest):
        try:
            from distr.core.planning.service import update_item

            item = await asyncio.to_thread(
                update_item,
                item_id,
                title=data.title,
                content=data.content,
                status=data.status,
                expected_revision=data.expected_revision,
            )
            if item is None:
                return JSONResponse({"detail": "Plan item not found."}, status_code=404)
            return JSONResponse(item)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)


    @router.get("/workflows/studio/plan-items/{item_id}/revisions")
    async def workflow_studio_plan_item_revisions(item_id: int):
        from distr.core.planning.service import list_revisions

        return JSONResponse({"items": await asyncio.to_thread(list_revisions, item_id)})



    @router.get("/workflows/studio/plan-items/{item_id}/file-review")
    async def review_plan_file(item_id: int):
        from distr.core.planning.service import review_file
        try:
            return JSONResponse(await asyncio.to_thread(review_file, item_id))
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except (ValueError, OSError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    @router.post("/workflows/studio/plan-items/{item_id}/file-review")
    async def reconcile_plan_file(item_id: int, data: PlanFileReconcileRequest):
        from distr.core.planning.service import reconcile_file
        try:
            return JSONResponse(await asyncio.to_thread(reconcile_file, item_id, expected_revision=data.expected_revision, file_hash=data.file_hash, action=data.action))
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except (ValueError, OSError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
