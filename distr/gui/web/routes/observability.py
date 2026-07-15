"""Browser responsiveness diagnostics."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)


def create_routes() -> APIRouter:
    router = APIRouter()

    @router.post("/diagnostics/ui-stall")
    async def record_ui_stall(request: Request):
        payload = await request.json()
        logger.warning(
            "Web UI stall detected duration_ms=%s drift_ms=%s path=%s visibility=%s",
            payload.get("duration_ms"),
            payload.get("drift_ms"),
            payload.get("path"),
            payload.get("visibility"),
        )
        return {"success": True}

    return router
