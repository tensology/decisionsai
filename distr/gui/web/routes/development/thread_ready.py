"""Acknowledge that a completed Development thread was opened."""
import asyncio
import logging

from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def register_routes(router, templates):
    @router.post("/chats/{chat_id}/acknowledge-ready")
    async def acknowledge_ready(chat_id: int):
        from distr.core.workflow.thread_ready_ack import acknowledge_ready_thread

        try:
            return JSONResponse(await asyncio.to_thread(acknowledge_ready_thread, int(chat_id)))
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except Exception as exc:
            logger.error("acknowledge-ready failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)
