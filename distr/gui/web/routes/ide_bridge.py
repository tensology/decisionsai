"""IDE-first bridge routes for Cursor/Codex project sessions."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class IdeSessionRequest(BaseModel):
    source: str = Field(default="ide")
    cwd: str = ""
    project_id: Optional[int] = None
    session_id: Optional[int] = None
    chat_id: Optional[int] = None


class IdeEventRequest(IdeSessionRequest):
    event_type: str = "ide_event"
    status: str = ""
    message: str = ""
    input: str = ""
    output: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.post("/ide/sessions")
    async def create_or_resume_ide_session(request: IdeSessionRequest):
        try:
            from distr.core.ide_bridge import ensure_ide_session

            return JSONResponse({
                "success": True,
                **ensure_ide_session(
                    source=request.source,
                    cwd=request.cwd,
                    project_id=request.project_id,
                    session_id=request.session_id,
                    chat_id=request.chat_id,
                ),
            })
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=404)

    @router.post("/ide/sessions/event")
    async def record_ide_session_event(request: IdeEventRequest):
        try:
            from distr.core.ide_bridge import record_ide_event

            return JSONResponse({
                "success": True,
                **record_ide_event(
                    source=request.source,
                    cwd=request.cwd,
                    project_id=request.project_id,
                    session_id=request.session_id,
                    chat_id=request.chat_id,
                    event_type=request.event_type,
                    status=request.status,
                    message=request.message,
                    input_text=request.input,
                    output_text=request.output,
                    payload=request.payload,
                    evidence=request.evidence,
                ),
            })
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=404)

    @router.get("/ide/sessions/progress")
    async def get_ide_session_progress(
        source: str = "",
        cwd: str = "",
        project_id: Optional[int] = None,
        session_id: Optional[int] = None,
    ):
        try:
            from distr.core.ide_bridge import get_ide_progress

            return JSONResponse({
                "success": True,
                **get_ide_progress(
                    source=source,
                    cwd=cwd,
                    project_id=project_id,
                    session_id=session_id,
                ),
            })
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=404)

    return router
