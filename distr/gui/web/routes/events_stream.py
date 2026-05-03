"""
SSE stream for in-process EventBus (R22).

``GET /api/events/stream`` — requires internal token; optional ``client_id`` query
(max 5 concurrent streams per ``client_id``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from distr.core.events import ALL_EVENT_TYPES, get_event_bus
from distr.gui.web.security import require_internal_token_request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])

_active_sse_by_client: dict[str, int] = {}
_sse_lock = threading.Lock()
_MAX_SSE_PER_CLIENT = 5


def _acquire_sse_slot(client_id: str) -> bool:
    with _sse_lock:
        n = _active_sse_by_client.get(client_id, 0)
        if n >= _MAX_SSE_PER_CLIENT:
            return False
        _active_sse_by_client[client_id] = n + 1
        return True


def _release_sse_slot(client_id: str) -> None:
    with _sse_lock:
        n = _active_sse_by_client.get(client_id, 0)
        if n <= 1:
            _active_sse_by_client.pop(client_id, None)
        else:
            _active_sse_by_client[client_id] = n - 1


@router.get("/stream")
async def events_stream(request: Request) -> StreamingResponse:
    """Stream EventBus notifications as Server-Sent Events (SSE)."""
    require_internal_token_request(request)
    raw = (request.query_params.get("client_id") or "default").strip()
    client_id = (raw[:128] or "default") if raw else "default"

    if not _acquire_sse_slot(client_id):
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {_MAX_SSE_PER_CLIENT} concurrent SSE connections for this client",
        )

    async def gen():
        bus = get_event_bus()
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=256)

        def handler(event_type: str, payload: Any) -> None:
            def push() -> None:
                try:
                    q.put_nowait((event_type, payload))
                except asyncio.QueueFull:
                    logger.warning("SSE queue full, dropping event %s", event_type)

            try:
                loop.call_soon_threadsafe(push)
            except RuntimeError:
                pass

        for et in ALL_EVENT_TYPES:
            bus.subscribe(et, handler)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    et, payload = await asyncio.wait_for(q.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                body = json.dumps({"type": et, "data": payload}, default=str)
                yield f"event: app\ndata: {body}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            for et in ALL_EVENT_TYPES:
                bus.unsubscribe(et, handler)
            _release_sse_slot(client_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
