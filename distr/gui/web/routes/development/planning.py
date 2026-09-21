"""Planning HTTP boundary. Existing URLs remain compatible."""
from fastapi import File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
import asyncio
import logging
from .models import PlanBuildRequest, PlanMessageRequest, PlanFileReconcileRequest, PlanInstructionRequest, PlanItemCreateRequest, PlanItemUpdateRequest, PlanWorkspaceEnsureRequest

logger = logging.getLogger(__name__)


async def _plan_call(function, *args, **kwargs):
    try:
        return JSONResponse(await asyncio.to_thread(function, *args, **kwargs))
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    except Exception:
        logger.exception("Plan operation failed")
        return JSONResponse({"detail": "The planning operation failed. Your saved artifacts are retained. Check the selected model and retry."}, status_code=500)


def register_routes(router, templates):
    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/attachments/{asset_id}/preview-info")
    async def plan_pdf_info(workspace_id: int, asset_id: int):
        from distr.core.planning.assets import pdf_preview_info
        return await _plan_call(pdf_preview_info, workspace_id, asset_id)

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/attachments/{asset_id}/preview")
    async def plan_pdf_preview(workspace_id: int, asset_id: int, page: int = 1):
        from distr.core.planning.assets import render_pdf_page
        try:
            data = await asyncio.to_thread(render_pdf_page, workspace_id, asset_id, page)
            return Response(data, media_type="image/png", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/conversation")
    async def plan_conversation(workspace_id: int):
        from distr.core.planning.conversation import get_conversation
        return await _plan_call(get_conversation, workspace_id)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/messages")
    async def plan_message(workspace_id: int, data: PlanMessageRequest):
        from distr.core.planning.conversation import send_message
        return await _plan_call(send_message, workspace_id, **data.model_dump())

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/build")
    async def plan_build(workspace_id: int, data: PlanBuildRequest):
        from distr.core.planning.conversation import generate_build
        return await _plan_call(generate_build, workspace_id, **data.model_dump())

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/attachments")
    async def plan_attach(workspace_id: int, file: UploadFile = File(...)):
        from distr.core.planning.assets import MAX_FILE_BYTES, add_asset
        try:
            data = await file.read(MAX_FILE_BYTES + 1)
            return await _plan_call(add_asset, workspace_id, name=file.filename, data=data)
        finally:
            await file.close()

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/attachments/{asset_id}")
    async def plan_attachment(workspace_id: int, asset_id: int):
        from distr.core.planning.assets import asset_path
        try:
            path, asset = await asyncio.to_thread(asset_path, workspace_id, asset_id)
            return FileResponse(path, media_type=asset["mime_type"], filename=asset["name"],
                                content_disposition_type="inline",
                                headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"})
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    @router.get("/workflows/studio/plan-workspaces")
    async def workflow_studio_plan_workspaces():
        from distr.core.planning.service import list_workspaces

        return JSONResponse({"items": await asyncio.to_thread(list_workspaces)})


    @router.post("/workflows/studio/plan-workspaces")
    async def workflow_studio_ensure_plan_workspace(data: PlanWorkspaceEnsureRequest):
        try:
            from distr.core.planning.service import open_project_plan

            return JSONResponse(await asyncio.to_thread(
                open_project_plan,
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


    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/scan")
    async def workflow_studio_scan_plan_workspace(workspace_id: int):
        try:
            from distr.core.planning.service import materialize_project_scan

            return JSONResponse(
                await asyncio.to_thread(
                    materialize_project_scan,
                    workspace_id,
                    force=True,
                    instruction="Scan project",
                )
            )
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
