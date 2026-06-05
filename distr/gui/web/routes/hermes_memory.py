"""Hermes memory and quiet activity API routes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    content: str
    category: str = "user_preference"
    tags: list[str] = Field(default_factory=list)
    source_type: str = "manual"
    source_id: str = ""
    source_chat_id: Optional[int] = None
    project_id: Optional[int] = None


class MemoryPatchRequest(BaseModel):
    enabled: Optional[bool] = None


class ActivityCompactRequest(BaseModel):
    older_than_s: float = 7 * 24 * 60 * 60
    weekly: bool = False


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/hermes/memories")
    async def list_memories(category: str = "", limit: int = 100, include_disabled: bool = False):
        try:
            from distr.core.hermes_memory import list_user_memories

            return JSONResponse({
                "success": True,
                "memories": list_user_memories(
                    category=category or None,
                    limit=limit,
                    include_disabled=include_disabled,
                ),
            })
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @router.post("/hermes/memories")
    async def create_memory(request: MemoryCreateRequest):
        try:
            from distr.core.hermes_memory import record_user_memory

            memory_uid = record_user_memory(
                request.content,
                category=request.category,
                source_type=request.source_type,
                source_id=request.source_id,
                source_chat_id=request.source_chat_id,
                project_id=request.project_id,
                tags=request.tags,
                manually_added=True,
            )
            return JSONResponse({"success": bool(memory_uid), "memory_uid": memory_uid})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @router.patch("/hermes/memories/{memory_uid}")
    async def patch_memory(memory_uid: str, request: MemoryPatchRequest):
        try:
            from distr.core.hermes_memory import set_user_memory_enabled

            if request.enabled is None:
                return JSONResponse({"success": False, "error": "No supported fields supplied"}, status_code=400)
            updated = set_user_memory_enabled(memory_uid, request.enabled)
            if not updated:
                return JSONResponse({"success": False, "error": "Memory not found"}, status_code=404)
            return JSONResponse({"success": True})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @router.get("/hermes/activity")
    async def list_activity(surface: str = "", limit: int = 100, include_compacted: bool = True):
        try:
            from distr.core.hermes_memory import list_machine_activity

            return JSONResponse({
                "success": True,
                "activity": list_machine_activity(
                    surface=surface or None,
                    limit=limit,
                    include_compacted=include_compacted,
                ),
            })
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    @router.post("/hermes/activity/compact")
    async def compact_activity(request: ActivityCompactRequest):
        try:
            from distr.core.hermes_memory import compact_machine_activity, run_weekly_machine_activity_compaction

            result: dict[str, Any]
            if request.weekly:
                result = run_weekly_machine_activity_compaction(older_than_s=request.older_than_s)
            else:
                result = compact_machine_activity(older_than_s=request.older_than_s)
            return JSONResponse({"success": True, **result})
        except Exception as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=500)

    return router
