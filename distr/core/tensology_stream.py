"""Outbound websocket from DecisionsAI to Tensology for pushed ticket events.

DecisionsAI runs on a workstation bound to 127.0.0.1:8765 behind NAT, so Tensology
cannot call it. Instead DecisionsAI opens one authenticated outbound websocket
(wss://<tensology>/ws/decisions, served by Tensology's existing Node relay) and
Tensology pushes lightweight ``{"type": "decisions_ticket", "ticket_id": ...}``
notifications. Each notification is handled by fetching the full ticket through the
existing ``GET /api/integrations/v1/tickets`` API and the shared per-ticket processing.

Replaces the 3s/15s ticket poll. A full catch-up fetch runs on every (re)connect, and
at most once per OFFLINE_CATCHUP_SECONDS while the stream cannot connect, so tickets
sent while DecisionsAI was offline (or before the relay is deployed) are not lost.

Pause: the ``tensology_push_paused`` setting (or the DECISIONS_TENSOLOGY_PUSH_PAUSED env
override) stops the ticket push only: no connect, no catch-up fetch, no ticket handling
and no ticket PATCHes. It is re-read live, so an open connection closes within
PAUSE_CHECK_SECONDS. Presence, discussions and Mailshot intake (``tensology_enabled``)
are unaffected.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import time
import urllib.parse
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

STREAM_PATH = "/ws/decisions"
TICKET_EVENT_TYPE = "decisions_ticket"
WORK_REQUEST_EVENT_TYPE = "decisions_work_request"
PING_INTERVAL_SECONDS = 20
PING_TIMEOUT_SECONDS = 20
BACKOFF_INITIAL_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0
STABLE_CONNECTION_SECONDS = 30.0
OFFLINE_CATCHUP_SECONDS = 60.0
NOT_CONFIGURED_RETRY_SECONDS = 30.0
EVENT_DEBOUNCE_SECONDS = 0.25
PAUSE_CHECK_SECONDS = 5.0
PUSH_PAUSED_ENV = "DECISIONS_TENSOLOGY_PUSH_PAUSED"
_TRUTHY = {"1", "true", "yes", "on"}

STREAM_STATE: dict[str, Any] = {
    "connected": False,
    "url": "",
    "connected_at": None,
    "last_event_at": None,
    "last_error": "",
    "events": 0,
    "reconnects": 0,
}


_URGENT_WORK_TERMS = (
    "urgent", "asap", "immediate", "critical", "outage", "down", "offline",
    "error", "exception", "traceback", "failing", "failed", "broken", "production",
    "prod", "5xx", "500",
)


def work_request_summary(event: dict[str, Any]) -> str:
    """Turn a Tensology work-request signal into one descriptive, human sentence."""
    project = " ".join(str(event.get("project") or "").split())
    title = " ".join(str(event.get("title") or "").split())
    priority = str(event.get("priority") or "").strip().lower()
    rationale = " ".join(str(event.get("rationale") or "").split())
    request_id = event.get("request_id")

    haystack = f"{title} {rationale}".lower()
    urgent = priority in {"urgent", "high", "critical", "immediate"} or any(
        term in haystack for term in _URGENT_WORK_TERMS
    )

    lead = "Urgent — needs your attention" if urgent else "A Tensology proposal needs your decision"
    subject = (title or "a new work proposal").rstrip(". ")
    head = f"{lead}: for {project}, {subject}" if project else f"{lead}: {subject}"

    parts: list[str] = [head + "."]
    if rationale and rationale.lower() not in subject.lower():
        parts.append(f"Why: {rationale[:240].rstrip('. ')}.")
    if request_id:
        parts.append(f"(request {request_id}).")
    parts.append("Say run it, queue it as a ticket, or dismiss.")
    return " ".join(parts)


def handle_work_request_signal(event: Any) -> None:
    """A Tensology work proposal arrived: describe it and put it in front of the user.

    Only the signal and a short summary travel from Tensology; the full proposal stays
    there. This is enough to say *what* is waiting, how urgent it looks, and why.
    """
    if not isinstance(event, dict):
        event = {"request_id": event}
    summary = work_request_summary(event)
    try:
        from distr.core.orchestration_events import emit_orchestration_event

        emit_orchestration_event(
            source="tensology",
            event_type="needs_input",
            status="waiting",
            summary=summary,
            payload={
                "surface": "tensology",
                "subtype": "work_request",
                "request_id": event.get("request_id"),
                "urgent": "Urgent" in summary,
                "project": event.get("project"),
                "title": event.get("title"),
            },
            notify_channels=["chat"],
        )
    except Exception:
        logger.debug("Tensology work-request orchestration event failed", exc_info=True)
    try:
        from distr.core.chat import ChatService

        chat_id = ChatService.get_current_chat_id()
        if chat_id:
            ChatService.append_assistant_notice(int(chat_id), summary, hidden=True)
    except Exception:
        logger.debug("Tensology work-request chat notice failed", exc_info=True)


def tensology_stream_url(settings: dict[str, Any]) -> str:
    explicit = str(settings.get("tensology_ws_url") or "").strip()
    if explicit:
        return explicit
    base = urllib.parse.urlsplit(str(settings.get("tensology_url") or "https://www.tensology.com").strip())
    scheme = "ws" if base.scheme == "http" else "wss"
    return urllib.parse.urlunsplit((scheme, base.netloc, STREAM_PATH, "", ""))


def tensology_push_paused(settings: dict[str, Any] | None = None) -> bool:
    """True when the Tensology ticket push is paused (env override wins over the setting)."""
    raw = os.environ.get(PUSH_PAUSED_ENV)
    if raw is not None and raw.strip():
        return raw.strip().lower() in _TRUTHY
    if settings is None:
        try:
            from distr.core.settings import load_settings_from_db

            settings = load_settings_from_db() or {}
        except Exception:
            return False
    value = settings.get("tensology_push_paused")
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return bool(value)


async def _close_when_paused(websocket: Any, paused: Callable[[], bool]) -> None:
    """Close an open stream as soon as the push is paused (checked every PAUSE_CHECK_SECONDS)."""
    while True:
        await asyncio.sleep(PAUSE_CHECK_SECONDS)
        try:
            is_paused = await asyncio.to_thread(paused)
        except Exception:
            continue
        if is_paused:
            logger.info("Tensology ticket stream paused (tensology_push_paused); closing connection")
            STREAM_STATE["last_error"] = "paused"
            await websocket.close()
            return


def _stream_config() -> tuple[str, str] | None:
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    if not settings.get("tensology_enabled"):
        return None
    key = str(settings.get("tensology_key") or "").strip()
    if not key:
        return None
    return tensology_stream_url(settings), key


def next_backoff(previous: float) -> float:
    if previous <= 0:
        return BACKOFF_INITIAL_SECONDS
    return min(BACKOFF_MAX_SECONDS, previous * 2)


def _jittered(delay: float) -> float:
    return delay * random.uniform(0.8, 1.2)


def _default_connect(url: str, key: str):
    import websockets

    return websockets.connect(
        url,
        additional_headers={
            "Authorization": f"Bearer {key}",
            "X-Tensology-Source": "decisionsai-ticket-stream",
        },
        ping_interval=PING_INTERVAL_SECONDS,
        ping_timeout=PING_TIMEOUT_SECONDS,
        open_timeout=15,
        close_timeout=5,
        max_size=1_048_576,
    )


async def _default_catch_up() -> Any:
    from distr.core.tensology_client import process_tensology_approved_tickets

    return await asyncio.to_thread(process_tensology_approved_tickets, force=True)


async def _default_handle_events(ticket_ids: list[int]) -> Any:
    from distr.core.tensology_client import process_tensology_ticket_events

    return await asyncio.to_thread(process_tensology_ticket_events, ticket_ids)


def parse_event(raw: Any) -> dict[str, Any] | None:
    try:
        event = json.loads(raw if isinstance(raw, str) else bytes(raw).decode("utf-8"))
    except Exception:
        return None
    return event if isinstance(event, dict) else None


class TicketEventDispatcher:
    """Serialises catch-ups and pushed events, coalescing bursts into one fetch."""

    def __init__(self, catch_up: Callable[[], Awaitable[Any]], handle_events: Callable[[list[int]], Awaitable[Any]]):
        self._catch_up = catch_up
        self._handle_events = handle_events
        self._wake = asyncio.Event()
        self._ticket_ids: set[int] = set()
        self._full = False

    def request_catch_up(self) -> None:
        self._full = True
        self._wake.set()

    def notify(self, ticket_id: Any) -> None:
        try:
            self._ticket_ids.add(int(ticket_id))
        except (TypeError, ValueError):
            self._full = True  # an event without a usable id falls back to a full fetch
        self._wake.set()

    async def run(self) -> None:
        while True:
            await self._wake.wait()
            if EVENT_DEBOUNCE_SECONDS:
                await asyncio.sleep(EVENT_DEBOUNCE_SECONDS)
            self._wake.clear()
            full, ids = self._full, sorted(self._ticket_ids)
            self._full, self._ticket_ids = False, set()
            try:
                if full:
                    logger.info("Tensology ticket stream: catch-up fetch")
                    await self._catch_up()
                elif ids:
                    logger.info("Tensology ticket stream: fetching pushed tickets %s", ids)
                    await self._handle_events(ids)
            except Exception as exc:
                logger.warning("Tensology ticket stream processing failed safely: %s", exc)


async def run_tensology_ticket_stream(
    *,
    connect: Callable[[str, str], Any] | None = None,
    catch_up: Callable[[], Awaitable[Any]] | None = None,
    handle_events: Callable[[list[int]], Awaitable[Any]] | None = None,
    handle_work_request: Callable[[Any], Any] | None = None,
    config: Callable[[], tuple[str, str] | None] | None = None,
    paused: Callable[[], bool] | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
    max_attempts: int | None = None,
) -> None:
    """Keep one outbound authenticated websocket to Tensology open, reconnecting with backoff."""
    connect = connect or _default_connect
    catch_up = catch_up or _default_catch_up
    handle_events = handle_events or _default_handle_events
    handle_work_request = handle_work_request or handle_work_request_signal
    config = config or _stream_config
    paused = paused or tensology_push_paused

    async def _is_paused() -> bool:
        try:
            return bool(await asyncio.to_thread(paused))
        except Exception as exc:
            logger.debug("Tensology ticket stream pause check failed: %s", exc)
            return False

    backoff = 0.0
    pause_logged = False
    last_catch_up = float("-inf")
    last_logged_error = ""
    attempts = 0
    while max_attempts is None or attempts < max_attempts:
        attempts += 1
        if await _is_paused():
            STREAM_STATE.update(connected=False, last_error="paused")
            if not pause_logged:
                logger.info("Tensology ticket stream paused (tensology_push_paused); not connecting")
                pause_logged = True
            await sleep(NOT_CONFIGURED_RETRY_SECONDS)
            continue
        if pause_logged:
            logger.info("Tensology ticket stream resumed (tensology_push_paused off)")
            pause_logged = False
        try:
            cfg = await asyncio.to_thread(config)
        except Exception as exc:
            cfg = None
            logger.debug("Tensology ticket stream config unavailable: %s", exc)
        if not cfg:
            STREAM_STATE.update(connected=False, last_error="not_configured")
            await sleep(NOT_CONFIGURED_RETRY_SECONDS)
            continue
        url, key = cfg
        connected_at: float | None = None
        try:
            async with connect(url, key) as websocket:
                connected_at = clock()
                STREAM_STATE.update(connected=True, url=url, connected_at=time.time(), last_error="")
                last_logged_error = ""
                logger.info("Tensology ticket stream connected: %s", url)
                dispatcher = TicketEventDispatcher(catch_up, handle_events)
                worker = asyncio.create_task(dispatcher.run())
                pause_watch = asyncio.create_task(_close_when_paused(websocket, paused))
                try:
                    # Catch up on anything sent while DecisionsAI was offline.
                    last_catch_up = clock()
                    dispatcher.request_catch_up()
                    async for raw in websocket:
                        event = parse_event(raw)
                        if not event:
                            continue
                        kind = str(event.get("type") or "")
                        if kind == TICKET_EVENT_TYPE:
                            STREAM_STATE["last_event_at"] = time.time()
                            STREAM_STATE["events"] = int(STREAM_STATE.get("events") or 0) + 1
                            logger.info(
                                "Tensology ticket stream event received: ticket %s (%s)",
                                event.get("ticket_id"), event.get("event") or event.get("reason") or "update",
                            )
                            dispatcher.notify(event.get("ticket_id"))
                        elif kind == WORK_REQUEST_EVENT_TYPE:
                            STREAM_STATE["last_event_at"] = time.time()
                            STREAM_STATE["events"] = int(STREAM_STATE.get("events") or 0) + 1
                            logger.info(
                                "Tensology work-request stream event received: request %s (%s)",
                                event.get("request_id"), event.get("event") or "update",
                            )
                            try:
                                import inspect as _inspect

                                if _inspect.iscoroutinefunction(handle_work_request):
                                    await handle_work_request(event)
                                else:
                                    await asyncio.to_thread(handle_work_request, event)
                            except Exception:
                                logger.debug("Tensology work-request handler failed", exc_info=True)
                        elif kind == "ping":
                            await websocket.send(json.dumps({"type": "pong", "timestamp": event.get("timestamp")}))
                    # Let an event that arrived just before a clean close finish.
                    await asyncio.sleep(EVENT_DEBOUNCE_SECONDS + 0.05)
                finally:
                    for task in (pause_watch, worker):
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
        except asyncio.CancelledError:
            STREAM_STATE["connected"] = False
            raise
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:300]
            STREAM_STATE["last_error"] = message
            if message != last_logged_error:
                logger.warning("Tensology ticket stream unavailable: %s", message)
                last_logged_error = message
        STREAM_STATE["connected"] = False
        if connected_at is not None:
            logger.info("Tensology ticket stream disconnected")
            if clock() - connected_at >= STABLE_CONNECTION_SECONDS:
                backoff = 0.0
        backoff = next_backoff(backoff)
        STREAM_STATE["reconnects"] = int(STREAM_STATE.get("reconnects") or 0) + 1
        if clock() - last_catch_up >= OFFLINE_CATCHUP_SECONDS and not await _is_paused():
            # Offline fallback: one catch-up per minute at most while push is down.
            last_catch_up = clock()
            try:
                await catch_up()
            except Exception as exc:
                logger.debug("Tensology offline catch-up failed: %s", exc)
        await sleep(_jittered(backoff))
