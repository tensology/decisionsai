"""OpenPencil read-side bridge HTTP boundary (isolated; no planning UI files touched).

Endpoints (internal-token guarded, read-only against design files):
  GET    /workflows/studio/open-pencil/status
  POST   /workflows/studio/plan-workspaces/{workspace_id}/open-pencil/inspect
  GET    /workflows/studio/plan-workspaces/{workspace_id}/open-pencil/links
  PUT    /workflows/studio/plan-workspaces/{workspace_id}/open-pencil/links   {page_key, path, note?}
  POST   /workflows/studio/plan-workspaces/{workspace_id}/open-pencil/links/inspect {page_key, board_id?}
  DELETE /workflows/studio/plan-workspaces/{workspace_id}/open-pencil/links?page_key=...

Gap reports may include ``ticket_drafts`` (dry run only). No ticket is created.
"""
import asyncio
import logging

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from distr.gui.web.security import require_internal_token_dependency

logger = logging.getLogger(__name__)
_GUARD = [Depends(require_internal_token_dependency)]


class OpenPencilInspectRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    board_id: int | None = None
    max_depth: int = Field(default=8, ge=1, le=64)


class OpenPencilLinkRequest(BaseModel):
    page_key: str = Field(min_length=1, max_length=300)
    path: str = Field(min_length=1, max_length=4096)
    note: str | None = Field(default=None, max_length=500)


class OpenPencilLinkInspectRequest(BaseModel):
    page_key: str = Field(min_length=1, max_length=300)
    board_id: int | None = None
    max_depth: int = Field(default=8, ge=1, le=64)


def _board_tickets(board_id: int | None) -> list[dict]:
    """Read-only ticket titles/descriptions for gap matching. Empty when no board."""
    if board_id is None:
        return []
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanLane, KanbanTicket

    with get_session() as db:
        rows = (
            db.query(KanbanTicket.id, KanbanTicket.title, KanbanTicket.description)
            .join(KanbanLane, KanbanLane.id == KanbanTicket.lane_id)
            .filter(KanbanLane.board_id == int(board_id))
            .all()
        )
    return [{"id": r[0], "title": r[1], "description": r[2]} for r in rows]


def inspect_with_gaps(path: str, board_id: int | None = None, max_depth: int = 8) -> dict:
    from distr.core.open_pencil import draft_ticket_specs, find_gaps, inspect_document

    inspection = inspect_document(path, max_depth=max_depth)
    gaps = find_gaps(inspection, _board_tickets(board_id))
    return {
        "inspection": inspection,
        "gaps": gaps,
        "ticket_drafts": draft_ticket_specs(inspection, gaps),
        "ticket_generation": "deferred: drafts are dry-run only; nothing was created",
    }


async def _call(function, *args, **kwargs):
    from distr.core.open_pencil import OpenPencilBridgeError, OpenPencilInvalidDocument

    try:
        return JSONResponse(await asyncio.to_thread(function, *args, **kwargs))
    except OpenPencilInvalidDocument as exc:
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=422)
    except OpenPencilBridgeError as exc:
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=503)
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except Exception:
        logger.exception("OpenPencil bridge operation failed")
        return JSONResponse({"detail": "The OpenPencil read failed. The design file was not modified."}, status_code=500)


def register_routes(router, templates):
    @router.get("/workflows/studio/open-pencil/status", dependencies=_GUARD)
    async def open_pencil_status():
        from distr.core.open_pencil import check_pin

        return await _call(lambda: check_pin().as_dict())

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/open-pencil/inspect", dependencies=_GUARD)
    async def open_pencil_inspect(workspace_id: int, data: OpenPencilInspectRequest):
        return await _call(inspect_with_gaps, data.path, data.board_id, data.max_depth)

    @router.get("/workflows/studio/plan-workspaces/{workspace_id}/open-pencil/links", dependencies=_GUARD)
    async def open_pencil_links(workspace_id: int):
        from distr.core.open_pencil.associations import list_associations

        return await _call(lambda: {"links": list_associations(workspace_id)})

    @router.put("/workflows/studio/plan-workspaces/{workspace_id}/open-pencil/links", dependencies=_GUARD)
    async def open_pencil_link(workspace_id: int, data: OpenPencilLinkRequest):
        from distr.core.open_pencil.associations import associate

        return await _call(associate, workspace_id, data.page_key, data.path, note=data.note)

    @router.post("/workflows/studio/plan-workspaces/{workspace_id}/open-pencil/links/inspect", dependencies=_GUARD)
    async def open_pencil_link_inspect(workspace_id: int, data: OpenPencilLinkInspectRequest):
        from distr.core.open_pencil.associations import get_association

        def run():
            link = get_association(workspace_id, data.page_key)
            if link is None:
                raise LookupError("No OpenPencil file is associated with this planning page.")
            return {"link": link, **inspect_with_gaps(link["path"], data.board_id, data.max_depth)}

        return await _call(run)

    @router.delete("/workflows/studio/plan-workspaces/{workspace_id}/open-pencil/links", dependencies=_GUARD)
    async def open_pencil_unlink(workspace_id: int, page_key: str):
        from distr.core.open_pencil.associations import remove_association

        return await _call(lambda: {"removed": remove_association(workspace_id, page_key)})
