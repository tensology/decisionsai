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


# ---------------------------------------------------------------- select_thread (2026-10-22)
# The select_thread / open_thread tools POST here; open Chat/Development tabs receive
# ui.thread.select over the SSE stream above, select + highlight the row, and POST an ack.
_pending_acks: dict[str, int] = {}
_ack_lock = threading.Lock()


@router.post("/ui/select-thread")
async def ui_select_thread(request: Request) -> dict[str, Any]:
    require_internal_token_request(request)
    import uuid

    from distr.core.events.types import UI_THREAD_SELECT

    body = await request.json()
    surface = "chat" if str(body.get("surface") or "").lower() == "chat" else "development"
    try:
        thread_id = int(body.get("thread_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="thread_id must be an integer")
    timeout = min(max(float(body.get("timeout") or 1.5), 0.2), 3.0)
    request_id = uuid.uuid4().hex
    with _ack_lock:
        _pending_acks[request_id] = 0
    try:
        get_event_bus().publish(UI_THREAD_SELECT, {
            "request_id": request_id, "surface": surface, "thread_id": thread_id,
            "title": str(body.get("title") or "")[:200], "focus": bool(body.get("focus")),
        })
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
            with _ack_lock:
                if _pending_acks.get(request_id):
                    break
        await asyncio.sleep(0.15)  # let other tabs ack too
        with _ack_lock:
            acked = _pending_acks.get(request_id, 0)
    finally:
        with _ack_lock:
            _pending_acks.pop(request_id, None)
    return {"ok": True, "request_id": request_id, "acked": acked, "surface": surface, "thread_id": thread_id}


@router.post("/ui/ack")
async def ui_ack(request: Request) -> dict[str, Any]:
    require_internal_token_request(request)
    body = await request.json()
    rid = str(body.get("request_id") or "")
    with _ack_lock:
        if rid in _pending_acks:
            _pending_acks[rid] += 1
            return {"ok": True}
    return {"ok": False}
