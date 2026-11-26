"""Planning HTTP boundary. Existing URLs remain compatible."""
from fastapi import Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
import asyncio
import logging
from distr.gui.web.security import require_internal_token_dependency
from .models import PlanBuildRequest, PlanChatActionRequest, PlanInterfaceShotRequest, PlanInterfaceLayoutRequest, PlanMemoryRequest, PlanRestoreRequest, PlanChatCreateRequest, PlanChatUpdateRequest, PlanMessageRequest, PlanFileReconcileRequest, PlanInstructionRequest, PlanItemCreateRequest, PlanItemUpdateRequest, PlanWorkspaceEnsureRequest, PlanPlaceRequest, PlanTicketsRequest

logger = logging.getLogger(__name__)


class PlanOutlineSource(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


class PlanOutlineEdit(PlanOutlineSource):
    expected_revision: int = Field(ge=1)


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


async def _outline_call(function, *args, **kwargs):
    from distr.core.planning.service import PlanBoardScopeChanged, PlanRevisionConflict

    try:
        return JSONResponse(await asyncio.to_thread(function, *args, **kwargs))
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    except (PlanBoardScopeChanged, PlanRevisionConflict) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)


def _read_scoped_plan(reader, workspace_id, board_id, project_id, *args):
    from distr.core.planning.service import validate_board_plan_read

    if board_id is not None or project_id is not None:
        validate_board_plan_read(workspace_id, board_id, project_id)
    result = reader(workspace_id, *args)
    if result is None:
        raise LookupError("Plan workspace not found.")
    if board_id is not None or project_id is not None:
        validate_board_plan_read(workspace_id, board_id, project_id)
    return result


def register_routes(router, templates):
    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/outlines",
                 dependencies=[Depends(require_internal_token_dependency)])
    async def plan_outline_create(workspace_id: int, board_id: int, project_id: int, data: PlanOutlineSource):
        from distr.core.planning.service import create_item

        return await _outline_call(
            create_item, workspace_id=workspace_id, item_type="wireframe", content_format="wire",
            content=data.content, outline_scope=(board_id, project_id),
        )

    @router.patch("/workflows/studio/plan-workspaces/{workspace_id}/outlines/{item_id}",
                  dependencies=[Depends(require_internal_token_dependency)])
    async def plan_outline_update(workspace_id: int, item_id: int, board_id: int, project_id: int, data: PlanOutlineEdit):
        from distr.core.planning.service import update_item

        def save():
            result = update_item(
                item_id, workspace_id=workspace_id, content=data.content,
                expected_revision=data.expected_revision, outline_scope=(board_id, project_id),
            )
            if result is None:
                raise LookupError("Plan item not found.")
            return result

        return await _outline_call(save)

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
    async def plan_conversation(workspace_id: int, chat_id: int | None = None,
                                board_id: int | None = None, project_id: int | None = None):
        from distr.core.planning.conversation import get_conversation
        return await _plan_call(_read_scoped_plan, get_conversation, workspace_id, board_id, project_id, chat_id)

    # Multi-chat (N3 Option A). Delete is a soft archive; stale expected_revision -> 409.
    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/chats", include_in_schema=False)
    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/conversations", summary="Plan conversations")
    async def plan_chats(workspace_id: int):
        from distr.core.planning.chats import list_chats
        from distr.core.planning.conversation import _workspace

        def run():
            _workspace(workspace_id)
            return {"chats": list_chats(workspace_id)}
        return await _plan_call(run)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/chats", include_in_schema=False)
    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/conversations", summary="Plan conversation create")
    async def plan_chat_create(workspace_id: int, data: PlanChatCreateRequest):
        from distr.core.planning.chats import create_chat
        return await _plan_call(create_chat, workspace_id, data.title)

    @router.patch("/workflows/studio/plan-workspaces/{workspace_id}/chats/{chat_id}", include_in_schema=False)
    @router.patch("/workflows/studio/plan-workspaces/{workspace_id}/conversations/{chat_id}", summary="Plan conversation rename")
    async def plan_chat_rename(workspace_id: int, chat_id: int, data: PlanChatUpdateRequest):
        from distr.core.planning.chats import rename_chat
        return await _plan_call(rename_chat, workspace_id, chat_id, title=data.title, expected_revision=data.expected_revision)

    # Chat memory (N2): view / switch on-off / forget (chat or project). Opt-in, local, redacted.
    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/chats/{chat_id}/memory", include_in_schema=False)
    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/conversations/{chat_id}/memory", summary="Plan conversation memory")
    async def plan_chat_memory(workspace_id: int, chat_id: int):
        from distr.core.planning.plan_memory import memory_view
        return await _plan_call(memory_view, workspace_id, chat_id)

    @router.put("/workflows/studio/plan-workspaces/{workspace_id}/chats/{chat_id}/memory", include_in_schema=False)
    @router.put("/workflows/studio/plan-workspaces/{workspace_id}/conversations/{chat_id}/memory", summary="Plan conversation memory set")
    async def plan_chat_memory_set(workspace_id: int, chat_id: int, data: PlanMemoryRequest):
        from distr.core.planning.plan_memory import set_enabled
        if data.enabled is None:
            return JSONResponse({"detail": "enabled is required."}, status_code=422)
        return await _plan_call(set_enabled, workspace_id, chat_id, bool(data.enabled))

    @router.delete("/workflows/studio/plan-workspaces/{workspace_id}/chats/{chat_id}/memory", include_in_schema=False)
    @router.delete("/workflows/studio/plan-workspaces/{workspace_id}/conversations/{chat_id}/memory", summary="Plan conversation memory forget")
    async def plan_chat_memory_forget(workspace_id: int, chat_id: int, scope: str = "chat"):
        from distr.core.planning.plan_memory import forget
        return await _plan_call(forget, workspace_id, chat_id, scope=scope)

    # Tool-card Undo (N1): restore a revision as a NEW revision; revision 0 archives an AI-created item.
    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/plan-items/{item_id}/restore")
    async def plan_item_restore(workspace_id: int, item_id: int, data: PlanRestoreRequest):
        from distr.core.planning.chat_tools import restore_revision
        from distr.core.planning.service import PlanItemTypeForbidden, PlanRevisionConflict
        try:
            result = await asyncio.to_thread(restore_revision, workspace_id, item_id,
                                             revision=data.revision, expected_revision=data.expected_revision)
            return JSONResponse(result)
        except PlanItemTypeForbidden as e:
            return JSONResponse({"detail": str(e)}, status_code=403)
        except PlanRevisionConflict as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)

    @router.delete("/workflows/studio/plan-workspaces/{workspace_id}/chats/{chat_id}", include_in_schema=False)
    @router.delete("/workflows/studio/plan-workspaces/{workspace_id}/conversations/{chat_id}", summary="Plan conversation delete")
    async def plan_chat_delete(workspace_id: int, chat_id: int):
        from distr.core.planning.chats import delete_chat
        return await _plan_call(delete_chat, workspace_id, chat_id)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/chats/{chat_id}/{action}", include_in_schema=False)
    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/conversations/{chat_id}/{action}", summary="Plan conversation action")
    async def plan_chat_action(workspace_id: int, chat_id: int, action: str, data: PlanChatActionRequest):
        from distr.core.planning import chats
        handler = {"archive": chats.archive_chat, "restore": chats.restore_chat,
                   "clear": chats.clear_chat, "compact": chats.compact_chat}.get(action)
        if handler is None:
            return JSONResponse({"detail": "Unknown chat action."}, status_code=404)
        return await _plan_call(handler, workspace_id, chat_id, expected_revision=data.expected_revision)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/messages")
    async def plan_message(workspace_id: int, data: PlanMessageRequest):
        from distr.core.planning.conversation import send_message
        return await _plan_call(send_message, workspace_id, **data.model_dump())

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/messages/stream")
    async def plan_message_stream(workspace_id: int, data: PlanMessageRequest, request: Request):
        """Stream the assistant reply as SSE deltas, then the finished conversation."""
        import json as json_lib
        from distr.core.planning.conversation import send_message

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(item):
            loop.call_soon_threadsafe(queue.put_nowait, item)

        def on_delta(delta: str):
            emit(("delta", delta))

        def run():
            try:
                result = send_message(workspace_id, on_delta=on_delta, **data.model_dump())
                emit(("done", result))
            except LookupError as exc:
                emit(("error", 404, str(exc)))
            except ValueError as exc:
                emit(("error", 409, str(exc)))
            except Exception:
                logger.exception("Plan stream failed")
                emit(("error", 500, "The planning operation failed. Your saved artifacts are retained. Check the selected model and retry."))

        async def events():
            worker = asyncio.create_task(asyncio.to_thread(run))
            try:
                yield "event: start\ndata: {}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    kind = item[0]
                    if kind == "delta":
                        yield f"event: delta\ndata: {json_lib.dumps({'text': item[1]})}\n\n"
                    elif kind == "done":
                        yield f"event: done\ndata: {json_lib.dumps(item[1], default=str)}\n\n"
                        break
                    else:
                        yield f"event: error\ndata: {json_lib.dumps({'detail': item[2], 'status': item[1]})}\n\n"
                        break
            finally:
                if not worker.done():
                    worker.cancel()
                try:
                    await worker
                except (asyncio.CancelledError, Exception):
                    pass

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/build")
    async def plan_build(workspace_id: int, data: PlanBuildRequest):
        from distr.core.planning.conversation import generate_build
        return await _plan_call(generate_build, workspace_id, **data.model_dump())

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/rebuild-interfaces")
    async def plan_rebuild_interfaces(workspace_id: int, data: PlanBuildRequest):
        from distr.core.planning.conversation import rebuild_interfaces
        return await _plan_call(rebuild_interfaces, workspace_id, **data.model_dump())

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/interface-shots")
    async def plan_interface_shot_status(workspace_id: int):
        from distr.core.planning.iface_shots import interface_shot_status
        return await _plan_call(interface_shot_status, workspace_id)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/interface-shots")
    async def plan_interface_shot_capture(workspace_id: int, data: PlanInterfaceShotRequest):
        from distr.core.planning.iface_shots import capture_interface_shot
        return await _plan_call(
            capture_interface_shot, workspace_id,
            screen_id=data.screen_id, route=data.route, lane=data.lane, force=data.force,
        )

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/interface-shots/rescan")
    async def plan_interface_shot_rescan(workspace_id: int):
        from distr.core.planning.iface_shots import clear_interface_shots
        return await _plan_call(clear_interface_shots, workspace_id)

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/interface-layout")
    async def plan_interface_layout(workspace_id: int):
        from distr.core.planning.iface_shots import interface_layout
        return await _plan_call(interface_layout, workspace_id)

    @router.put("/workflows/studio/plan-workspaces/{workspace_id}/interface-layout")
    async def plan_interface_layout_save(workspace_id: int, data: PlanInterfaceLayoutRequest):
        from distr.core.planning.iface_shots import save_interface_layout
        return await _plan_call(save_interface_layout, workspace_id, nodes=data.nodes)

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/interface-shots/{screen_id}")
    async def plan_interface_shot_file(workspace_id: int, screen_id: str):
        from distr.core.planning.iface_shots import interface_shot_file
        try:
            path, media = await asyncio.to_thread(interface_shot_file, workspace_id, screen_id)
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        return FileResponse(path, media_type=media, content_disposition_type="inline",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

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
        from distr.core.planning.service import PlanWorkspaceProjectConflict, open_project_plan

        try:
            return JSONResponse(await asyncio.to_thread(
                open_project_plan,
                board_key=data.board_key,
                board_provider=data.board_provider,
                board_name=data.board_name,
                project_id=data.project_id,
                defer_scan=True,
                start_new_plan=bool(data.start_new_plan),
            ))
        except PlanWorkspaceProjectConflict as exc:
            return JSONResponse({
                "detail": str(exc),
                "code": getattr(exc, "code", "plan_project_conflict"),
                "bound_project_id": getattr(exc, "bound_project_id", None),
                "bound_project_name": getattr(exc, "bound_project_name", ""),
                "project_id": getattr(exc, "project_id", None),
                "project_name": getattr(exc, "project_name", ""),
                "can_start_new": True,
            }, status_code=409)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)


    @router.get("/workflows/studio/plan-workspaces/{workspace_id}")
    async def workflow_studio_plan_workspace(workspace_id: int, board_id: int | None = None,
                                             project_id: int | None = None, include_data_model: bool = False):
        from distr.core.planning.service import get_workspace

        def read():
            workspace = _read_scoped_plan(get_workspace, workspace_id, board_id, project_id, include_data_model)
            if board_id is not None and project_id is not None:
                workspace["outline_editing"] = True
            return workspace

        return await _plan_call(read)

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/scan-status")
    async def workflow_studio_plan_scan_status(workspace_id: int):
        from distr.core.planning.service import plan_scan_status

        return JSONResponse(plan_scan_status(workspace_id))

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

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/requirements-pack")
    async def workflow_studio_plan_requirements_pack(workspace_id: int):
        def read(wid):
            from distr.core.planning.requirements_pack import PACK_TYPE, PROPOSED_PATH, ledger_from_items
            from distr.core.planning.service import get_workspace

            detail = get_workspace(wid)
            if detail is None:
                raise LookupError("Plan workspace not found.")
            items = detail.get("items") or []
            pack = next((item for item in items if item.get("item_type") == PACK_TYPE), None)
            return {"item": pack, "ledger": ledger_from_items(items), "proposed_path": PROPOSED_PATH, "written_to_repo": False}

        return await _plan_call(read, workspace_id)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/requirements-pack")
    async def workflow_studio_plan_requirements_pack_generate(workspace_id: int, request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        from distr.core.planning.requirements_pack import generate_requirements_pack

        new_instructions = str(body.get("new_instructions") or "") if isinstance(body, dict) else ""
        return await _plan_call(generate_requirements_pack, workspace_id, new_instructions=new_instructions)

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
            from distr.core.planning.service import PlanRevisionConflict
            # 409 only for a stale expected_revision; invalid content/status is a 422.
            return JSONResponse({"detail": str(e)}, status_code=409 if isinstance(e, PlanRevisionConflict) else 422)


    @router.delete("/workflows/studio/plan-workspaces/{workspace_id}/plan-items/{item_id}")
    async def workflow_studio_archive_plan_item(workspace_id: int, item_id: int, expected_revision: int | None = None):
        """Delete a user document from the plan: archived (history and moved file kept), never other types."""
        from distr.core.planning.service import PlanItemTypeForbidden, PlanRevisionConflict, archive_item

        if expected_revision is None:
            return JSONResponse({"detail": "expected_revision is required."}, status_code=422)
        try:
            return JSONResponse(await asyncio.to_thread(archive_item, workspace_id, item_id, expected_revision=expected_revision))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except PlanItemTypeForbidden as e:
            return JSONResponse({"detail": str(e)}, status_code=403)
        except PlanRevisionConflict as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except OSError:
            logger.exception("Plan item archive failed")
            return JSONResponse({"detail": "The document could not be archived. Nothing was removed."}, status_code=500)


    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/db-links/discover")
    async def workflow_studio_plan_db_discover(workspace_id: int):
        """Read-only: which local Postgres / MySQL servers are listening (no credentials, no queries)."""
        from distr.core.planning import db_discovery
        from distr.core.planning.service import workspace_exists

        def run():
            if not workspace_exists(workspace_id):
                raise LookupError("Plan workspace not found.")
            return db_discovery.discover()

        try:
            return JSONResponse(await asyncio.to_thread(run))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)


    # ---- Plan DB links (N5 phase 2): CRUD + secret. Bodies are parsed by hand so validation errors
    # can never echo a submitted value (FastAPI's 422 "input" would); no connection or introspection here.
    async def _db_link_call(function, *args, status=200):
        from distr.core.planning.db_links import LinkConflict
        try:
            return JSONResponse(await asyncio.to_thread(function, *args), status_code=status)
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except LinkConflict as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception:
            logger.exception("Plan DB link operation failed")
            return JSONResponse({"detail": "The database link operation failed. Nothing was changed."}, status_code=500)

    async def _json_body(request):
        try:
            data = await request.json()
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/db-links")
    async def workflow_studio_plan_db_links(workspace_id: int):
        from distr.core.planning.db_links import list_links
        return await _db_link_call(list_links, workspace_id)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/db-links")
    async def workflow_studio_plan_db_link_create(workspace_id: int, request: Request):
        from distr.core.planning.db_links import create_link
        data = await _json_body(request)
        if data is None:
            return JSONResponse({"detail": "Send the link as a JSON object."}, status_code=400)
        return await _db_link_call(create_link, workspace_id, data, status=201)

    @router.patch("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}")
    async def workflow_studio_plan_db_link_update(workspace_id: int, link_id: int, request: Request):
        from distr.core.planning.db_links import update_link
        data = await _json_body(request)
        if data is None:
            return JSONResponse({"detail": "Send the changes as a JSON object."}, status_code=400)
        return await _db_link_call(update_link, workspace_id, link_id, data)

    @router.delete("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}")
    async def workflow_studio_plan_db_link_archive(workspace_id: int, link_id: int):
        from distr.core.planning.db_links import archive_link
        return await _db_link_call(archive_link, workspace_id, link_id)

    @router.put("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/secret")
    async def workflow_studio_plan_db_link_secret(workspace_id: int, link_id: int, request: Request):
        from distr.core.planning.db_links import set_secret
        data = await _json_body(request)
        if data is None or set(data) != {"secret"}:
            return JSONResponse({"detail": 'Send {"secret": "..."} only.'}, status_code=400)
        return await _db_link_call(set_secret, workspace_id, link_id, data["secret"])


    # ---- Plan DB links (N5 phase 3): connection test + read-only catalog introspection. Failures carry a
    # stable code and a fixed message (never driver text, the secret or a connection string).
    async def _db_introspect_call(function, *args, **kwargs):
        from distr.core.planning.db_introspect import IntrospectError
        try:
            return JSONResponse(await asyncio.to_thread(function, *args, **kwargs))
        except IntrospectError as e:
            return JSONResponse({"ok": False, "code": e.code, "detail": str(e)}, status_code=e.http_status)
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception:
            logger.exception("Plan DB link introspection failed")
            return JSONResponse({"ok": False, "code": "failed", "detail": "Reading the database schema failed."}, status_code=500)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/test")
    async def workflow_studio_plan_db_link_test(workspace_id: int, link_id: int):
        from distr.core.planning.db_introspect import test_link
        return await _db_introspect_call(test_link, workspace_id, link_id)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/introspect")
    async def workflow_studio_plan_db_link_introspect(workspace_id: int, link_id: int, request: Request):
        from distr.core.planning.db_introspect import MAX_TABLES, introspect_link
        raw = await request.body()
        data = {} if not raw.strip() else await _json_body(request)
        if data is None or set(data) - {"filter", "limit"}:
            return JSONResponse({"detail": 'Send {"filter": "...", "limit": 300} or an empty body.'}, status_code=400)
        return await _db_introspect_call(introspect_link, workspace_id, link_id,
                                         table_filter=data.get("filter", ""), limit=data.get("limit", MAX_TABLES))

    # ---- Plan DB links (N5 phase 4): snapshot cache, ERD diff and explicit apply. The snapshot holds
    # catalog structure only; diff is read-only; apply is per selected table and revision-checked.
    async def _db_erd_call(function, *args, **kwargs):
        from distr.core.planning.db_introspect import IntrospectError
        from distr.core.planning.service import PlanRevisionConflict
        try:
            return JSONResponse(await asyncio.to_thread(function, *args, **kwargs))
        except IntrospectError as e:
            return JSONResponse({"ok": False, "code": e.code, "detail": str(e)}, status_code=e.http_status)
        except PlanRevisionConflict as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception:
            logger.exception("Plan DB link schema operation failed")
            return JSONResponse({"ok": False, "code": "failed", "detail": "The schema operation failed. Nothing was changed."}, status_code=500)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/snapshot")
    async def workflow_studio_plan_db_link_snapshot_refresh(workspace_id: int, link_id: int, request: Request):
        from distr.core.planning.db_erd import refresh_snapshot
        raw = await request.body()
        data = {} if not raw.strip() else await _json_body(request)
        if data is None or set(data) - {"filter"}:
            return JSONResponse({"detail": 'Send {"filter": "..."} or an empty body.'}, status_code=400)
        return await _db_erd_call(refresh_snapshot, workspace_id, link_id, table_filter=data.get("filter", ""))

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/snapshot")
    async def workflow_studio_plan_db_link_snapshot(workspace_id: int, link_id: int):
        from distr.core.planning.db_erd import read_snapshot
        return await _db_erd_call(read_snapshot, workspace_id, link_id)

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/diff")
    async def workflow_studio_plan_db_link_diff(workspace_id: int, link_id: int, item_id: int | None = None):
        from distr.core.planning.db_erd import diff_link
        return await _db_erd_call(diff_link, workspace_id, link_id, item_id=item_id)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/apply")
    async def workflow_studio_plan_db_link_apply(workspace_id: int, link_id: int, request: Request):
        from distr.core.planning.db_erd import apply_link
        data = await _json_body(request)
        if data is None or "tables" not in data or set(data) - {"tables", "item_id", "expected_revision"}:
            return JSONResponse({"detail": 'Send {"tables": [...], "item_id": null, "expected_revision": null}.'}, status_code=400)
        return await _db_erd_call(apply_link, workspace_id, link_id, tables=data["tables"],
                                  item_id=data.get("item_id"), expected_revision=data.get("expected_revision"))

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/columns")
    async def workflow_studio_plan_db_link_add_column(workspace_id: int, link_id: int, request: Request):
        from distr.core.planning.db_erd import add_linked_column
        data = await _json_body(request)
        if data is None or set(data) != {"schema", "table", "column", "field_type"}:
            return JSONResponse({"detail": 'Send {"schema", "table", "column", "field_type"}.'}, status_code=400)
        return await _db_erd_call(add_linked_column, workspace_id, link_id, data["schema"], data["table"], data["column"], data["field_type"])

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/db-links/{link_id}/rows")
    async def workflow_studio_plan_db_link_rows(workspace_id: int, link_id: int, schema: str, table: str, page: int = 1, page_size: int = 50):
        from distr.core.planning.db_erd import read_linked_rows
        return await _db_erd_call(read_linked_rows, workspace_id, link_id, schema, table, page=page, page_size=page_size)

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

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/library")
    async def plan_workspace_library(workspace_id: int, screen: str = "", kind: str = "", tag: str = ""):
        """The side panel's library, for the screen the operator is on.

        sitemap / flow -> page templates; inside a page -> widgets. Contents are
        screen-aware, and an entry scoped to another product is not offered here.
        """
        from distr.core.planning import library

        def _load() -> dict:
            product = ""
            try:
                from distr.core.planning.service import get_workspace

                ws = get_workspace(workspace_id) or {}
                product = str(ws.get("board_key") or ws.get("board_name") or "").strip().lower()
            except Exception:
                logger.debug("plan library: could not resolve the workspace product", exc_info=True)

            if kind:
                items = library.entries(kind=kind, product=product or None, tag=tag or None)
            else:
                items = library.panel_entries(screen or "page", product=product or None, tag=tag or None)
            return {
                "screen": (screen or "page").strip().lower(),
                "product": product,
                "entries": [entry.as_dict() for entry in items],
            }

        return JSONResponse(await asyncio.to_thread(_load))

    def _workspace_plan_root(workspace_id: int) -> str:
        """The workspace's planning folder - where a plan is persisted and read."""
        from distr.core.planning.service import get_workspace

        workspace = get_workspace(workspace_id) or {}
        return str(workspace.get("root_path") or "").strip()

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/plan")
    async def get_workspace_plan(workspace_id: int):
        """The plan as it lives in the project's planning folder."""
        from distr.core.planning import plan_store

        def _load() -> dict:
            root = _workspace_plan_root(workspace_id)
            if not root:
                return {"sitemap": None, "root": "", "error": "no planning folder for this workspace"}
            sm = plan_store.load_sitemap(root)
            wire, missing = sm.to_wire()
            return {
                "root": root,
                "sitemap": sm.as_dict(),
                "wire": wire,
                "missing": missing,
                "pages": sm.count(),
                "routes": sm.routes(),
                "tickets": plan_store.load_tickets_index(root),
            }

        return JSONResponse(await asyncio.to_thread(_load))

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/plan/place")
    async def place_plan_entry(workspace_id: int, data: PlanPlaceRequest):
        """Drop a library entry into the plan, then persist it.

        A page template at sitemap level creates a page; anything else is added as
        a block on the named page. The plan is written straight back to the
        project's planning folder, so the file stays the source of truth.
        """
        from distr.core.planning import library, plan_store
        from distr.core.planning.page import Block

        def _place() -> dict:
            root = _workspace_plan_root(workspace_id)
            if not root:
                return {"placed": False, "error": "no planning folder for this workspace"}

            entry = library.get(data.entry)
            if entry is None:
                return {"placed": False, "error": f"{data.entry!r} is not in the library"}

            sm = plan_store.load_sitemap(root)
            page_name = (data.page or "").strip().lower()

            if entry.kind == "page-template" and not page_name:
                node = sm.drop_template(
                    entry,
                    title=(data.title or "").strip() or entry.title,
                    route=(data.route or "").strip(),
                    parent=(data.parent or "").strip().lower() or None,
                )
                if node is None:
                    return {"placed": False, "error": "could not add the page"}
                created = node.name
            else:
                node = sm.find(page_name)
                if node is None:
                    return {"placed": False, "error": f"page {page_name!r} is not in the plan"}
                existing = [b.ref for b in node.page.blocks]
                if entry.name in existing:
                    return {"placed": False, "error": f"{entry.name!r} is already on {page_name!r}"}
                node.page = type(node.page)(
                    name=node.page.name,
                    title=node.page.title,
                    route=node.page.route,
                    base=node.page.base,
                    blocks=tuple(node.page.blocks) + (Block(ref=entry.name, span=data.span),),
                    overrides=dict(node.page.overrides),
                )
                created = ""

            wire, missing = sm.to_wire()
            saved = plan_store.save_sitemap(root, sm, wire=wire)
            return {
                "placed": bool(saved.get("saved")),
                "error": "" if saved.get("saved") else str(saved.get("error") or "write failed"),
                "created_page": created,
                "page": page_name or created,
                "entry": entry.as_dict(),
                "pages": sm.count(),
                "wire": wire,
                "missing": missing,
            }

        return JSONResponse(await asyncio.to_thread(_place))

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/plan/tickets")
    async def build_plan_tickets(workspace_id: int, data: PlanTicketsRequest):
        """Build component-level tickets from the plan and write them into it.

        Tickets belong in the PROJECT'S planning folder, never in downloads.
        """
        from distr.core.planning import plan_store, plan_tickets

        def _build() -> dict:
            root = _workspace_plan_root(workspace_id)
            if not root:
                return {"built": False, "error": "no planning folder for this workspace"}
            sm = plan_store.load_sitemap(root)
            tickets = plan_tickets.tickets_for_sitemap(sm, current=data.current, changed=data.changed or [])
            saved = plan_store.save_tickets(root, tickets, heading=data.heading or "Plan tickets")
            return {
                "built": bool(saved.get("saved")),
                "error": "" if saved.get("saved") else str(saved.get("error") or "write failed"),
                "tickets": tickets,
                "count": len(tickets),
                "dir": str(saved.get("dir") or ""),
                "pages": saved.get("pages") or [],
            }

        return JSONResponse(await asyncio.to_thread(_build))
