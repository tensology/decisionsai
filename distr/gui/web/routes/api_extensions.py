"""Programmatic API extensions (22 Oct 2026 API audit).

Adds the client-facing pieces the GUI never needed:

* turn status / reply retrieval for Oracle chats and Development threads
  (``GET /api/chats/{chat_id}/turns[/{turn_id}]``, ``?wait=true&timeout=N``)
* message-level history editing (``/api/chats/{chat_id}/messages/{message_id}``)
* ``GET /api/chats/current``
* aggregated settings (``GET /api/settings``, ``GET /api/settings/schema``,
  ``PATCH /api/settings/{section}``) that delegate to the existing section routes
* ``POST /api/skills/{skill_id}/invoke`` (Development threads only)

Mounted under ``/api`` *before* the chat router so ``/chats/current`` is not
captured by ``/chats/{chat_id}``. Every route (GETs included) requires the
internal token via a router dependency (apiaudit 22 Oct 2026).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

MAX_WAIT_SECONDS = 300.0
DEFAULT_WAIT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.5
TERMINAL_EVENT_TYPES = {"turn_completed", "turn_failed", "turn_cancelled"}


# ---------------------------------------------------------------------------
# Helpers (module level so tests and other routes can reuse them)
# ---------------------------------------------------------------------------

def clamp_timeout(timeout: Optional[float], default: float = DEFAULT_WAIT_SECONDS) -> float:
    try:
        value = float(timeout if timeout is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(value, MAX_WAIT_SECONDS))


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _params(raw: Any) -> Dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def thread_root_id(session, chat_id: int) -> int:
    from distr.core.chat import _thread_root_chat_id

    return int(_thread_root_chat_id(session, int(chat_id)))


def thread_rows(session, root_id: int) -> list:
    """Root row plus every descendant, in transcript order (created_date, id)."""
    from distr.core.db import Chat

    rows = []
    frontier = [int(root_id)]
    root = session.get(Chat, int(root_id))
    if root is None:
        return []
    rows.append(root)
    guard = 0
    while frontier and guard < 10000:
        guard += 1
        children = session.query(Chat).filter(Chat.parent_id.in_(frontier)).all()
        frontier = [int(c.id) for c in children]
        rows.extend(children)
    def _key(row):
        created = row.created_date
        if created is not None and getattr(created, "tzinfo", None) is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
        return (created or datetime.min, int(row.id))
    rows[1:] = sorted(rows[1:], key=_key)
    return rows


def max_thread_row_id(chat_id: int) -> int:
    from distr.core.db import get_session

    with get_session() as session:
        root_id = thread_root_id(session, chat_id)
        rows = thread_rows(session, root_id)
        return max((int(r.id) for r in rows), default=int(root_id))


def message_payload(row, root_id: int) -> Dict[str, Any]:
    return {
        "message_id": int(row.id),
        "chat_id": int(root_id),
        "is_root": int(row.id) == int(root_id),
        "input": row.input or "",
        "response": row.response or "",
        "is_hidden": bool(row.is_hidden),
        "created_at": _iso(row.created_date),
        "updated_at": _iso(row.modified_date),
    }


def _active_turn_pointer(root) -> Optional[int]:
    value = _params(getattr(root, "params", None)).get("active_turn_chat_row_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def turn_status(chat_id: int, turn_id: int) -> Optional[Dict[str, Any]]:
    """Status + assistant text for one turn (the chats row holding the user message)."""
    from distr.core.db import Chat, ChatTurnEvent, get_session

    with get_session() as session:
        root_id = thread_root_id(session, chat_id)
        row = session.get(Chat, int(turn_id))
        if row is None or thread_root_id(session, int(row.id)) != root_id:
            return None
        root = session.get(Chat, root_id)
        events = (
            session.query(ChatTurnEvent)
            .filter(ChatTurnEvent.chat_id == root_id, ChatTurnEvent.turn_id == int(row.id))
            .order_by(ChatTurnEvent.sequence.asc())
            .all()
        )
        terminal = next((e for e in reversed(events) if e.event_type in TERMINAL_EVENT_TYPES), None)
        active_pointer = _active_turn_pointer(root)
        response = row.response or ""
        if terminal is not None:
            status = terminal.status or "completed"
            if status == "passed":
                status = "completed"
        elif response and active_pointer != int(row.id):
            status = "completed"
        elif events or response:
            status = "running"
        else:
            status = "pending"
        completed_at = _iso(terminal.completed_date or terminal.created_date) if terminal is not None else None
        error = None
        if terminal is not None and status in {"failed", "cancelled"}:
            error = terminal.summary or terminal.title or status
        return {
            "chat_id": int(root_id),
            "turn_id": int(row.id),
            "status": status,
            "done": status in {"completed", "failed", "cancelled", "skipped"},
            "input": row.input or "",
            "assistant_text": response,
            "error": error,
            "active": active_pointer == int(row.id) and terminal is None,
            "event_count": len(events),
            "created_at": _iso(row.created_date),
            "started_at": _iso(events[0].created_date) if events else _iso(row.created_date),
            "updated_at": _iso(row.modified_date),
            "completed_at": completed_at,
        }


async def wait_for_turn(chat_id: int, turn_id: int, timeout: float) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + clamp_timeout(timeout)
    status = await asyncio.to_thread(turn_status, chat_id, turn_id)
    while status is not None and not status.get("done") and time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        status = await asyncio.to_thread(turn_status, chat_id, turn_id)
    if status is not None:
        status["timed_out"] = not status.get("done")
    return status


def _find_new_turn_row(chat_id: int, after_row_id: int, message: str) -> Optional[int]:
    from distr.core.db import get_session

    wanted = (message or "").strip()
    with get_session() as session:
        root_id = thread_root_id(session, chat_id)
        candidates = [r for r in thread_rows(session, root_id) if int(r.id) > int(after_row_id) and (r.input or "").strip()]
        for row in candidates:
            if (row.input or "").strip() == wanted:
                return int(row.id)
        return int(candidates[0].id) if candidates else None


async def resolve_new_turn_id(chat_id: int, after_row_id: int, message: str, timeout: float) -> Optional[int]:
    deadline = time.monotonic() + max(0.0, min(float(timeout), MAX_WAIT_SECONDS))
    while True:
        found = await asyncio.to_thread(_find_new_turn_row, chat_id, after_row_id, message)
        if found is not None or time.monotonic() >= deadline:
            return found
        await asyncio.sleep(0.25)


async def turn_response_fields(
    chat_id: int,
    *,
    turn_id: Optional[int],
    after_row_id: Optional[int] = None,
    message: str = "",
    wait: bool = False,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Fields send endpoints merge into their JSON response."""
    budget = clamp_timeout(timeout)
    started = time.monotonic()
    if turn_id is None and after_row_id is not None:
        # Without wait the GUI must not be slowed down: one immediate lookup only.
        # Clients poll GET /api/chats/{id}/turns?after_row_id=N for the id later.
        resolve_budget = budget if wait else 0.0
        turn_id = await resolve_new_turn_id(chat_id, after_row_id, message, resolve_budget)
    out: Dict[str, Any] = {"turn_id": turn_id, "after_row_id": after_row_id}
    if turn_id is None:
        out["turn_status"] = None
        out["turn_pending"] = True
        return out
    remaining = max(0.0, budget - (time.monotonic() - started))
    if wait:
        out["turn_status"] = await wait_for_turn(chat_id, int(turn_id), remaining)
    else:
        out["turn_status"] = await asyncio.to_thread(turn_status, chat_id, int(turn_id))
    return out


async def _broadcast_chat_updated(root_id: int, **extra: Any) -> None:
    try:
        from distr.gui.web.routes.chat import broadcast_chat_event

        await broadcast_chat_event({"type": "chat_updated", "chat_id": int(root_id), **extra})
    except Exception:
        logger.debug("chat_updated broadcast failed", exc_info=True)


def _reload_oracle_if_current(root_id: int) -> bool:
    """Force the agent to reload history when the edited chat is the Oracle's current chat."""
    try:
        from distr.core.chat import ChatService

        if ChatService.get_current_chat_id() != int(root_id):
            return False
        from distr.core.signals import signal_manager

        signal_manager.web_load_chat_in_agent_requested.emit(int(root_id))
        return True
    except Exception:
        logger.debug("Oracle history reload failed", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Settings aggregation
# ---------------------------------------------------------------------------

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|(^|_)token$|secret|password|passwd|credential|cookie|authorization|bearer)",
    re.IGNORECASE,
)


def is_secret_key(key: str) -> bool:
    k = str(key or "")
    if "hotkey" in k.lower():
        return False
    if _SECRET_KEY_RE.search(k):
        return True
    return k.lower().endswith("_key")


def mask_secrets(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: mask_secrets(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v, key) for v in value]
    if key and is_secret_key(key) and isinstance(value, str):
        return "********" if value.strip() else ""
    return value


def _section_models() -> Dict[str, Any]:
    from distr.gui.web.routes.settings._shared import (
        AdvancedSettings,
        AudioSettings,
        GeneralSettings,
        LLMSettings,
        ShortcutSettings,
    )
    from distr.gui.web.routes.settings.initiative import _InitiativePayload

    return {
        "general": GeneralSettings,
        "audio": AudioSettings,
        "llms": LLMSettings,
        "advanced": AdvancedSettings,
        "initiative": _InitiativePayload,
        "shortcuts": ShortcutSettings,
    }


# Sections readable through GET /api/settings. ``thirdparty`` and ``mcp`` are
# read-only here: their saves carry secrets/server definitions that must go
# through their dedicated routes.
SETTINGS_SECTIONS = ("general", "audio", "llms", "advanced", "initiative", "shortcuts", "thirdparty", "mcp")
READ_ONLY_SECTIONS = {"thirdparty": "/api/thirdparty", "mcp": "/api/mcp"}


async def _internal_call(request: Request, method: str, path: str, payload: Any = None):
    import httpx

    from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token

    transport = httpx.ASGITransport(app=request.app, client=("127.0.0.1", 0))
    headers = {INTERNAL_AUTH_HEADER: get_internal_api_token()}
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765", timeout=MAX_WAIT_SECONDS + 30) as client:
        return await client.request(method, path, json=payload, headers=headers)


async def _read_section(request: Request, section: str) -> Dict[str, Any]:
    resp = await _internal_call(request, "GET", f"/api/{section}")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Reading settings section '{section}' failed ({resp.status_code})")
    data = resp.json()
    return data if isinstance(data, dict) else {"value": data}


class MessagePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: Optional[str] = None
    response: Optional[str] = None
    is_hidden: Optional[bool] = None


class SkillInvokeRequest(BaseModel):
    chat_id: int
    message: str = ""


def create_router() -> APIRouter:
    from distr.gui.web.routes.api_audit import require_token

    router = APIRouter(dependencies=[Depends(require_token)])

    # ---- current chat ---------------------------------------------------
    @router.get("/chats/current")
    async def get_current_chat():
        """Oracle's current chat. Set it with POST /api/chats/{chat_id}/load-in-agent."""
        from distr.core.chat import ChatService
        from distr.core.db import Chat, get_session

        chat_id = await asyncio.to_thread(ChatService.get_current_chat_id)
        if chat_id is None:
            return JSONResponse({"chat_id": None, "chat": None})
        with get_session() as session:
            chat = session.get(Chat, int(chat_id))
            info = None
            if chat is not None:
                info = {
                    "id": int(chat.id),
                    "title": chat.title,
                    "provider": chat.provider,
                    "model_name": chat.model_name,
                    "modified_date": _iso(chat.modified_date),
                }
        return JSONResponse({"chat_id": int(chat_id), "chat": info, "set_via": "POST /api/chats/{chat_id}/load-in-agent"})

    # ---- turns ----------------------------------------------------------
    @router.get("/chats/{chat_id}/turns")
    async def list_turns(chat_id: int, after_row_id: Optional[int] = None, limit: int = 20):
        from distr.core.db import get_session

        with get_session() as session:
            root_id = thread_root_id(session, chat_id)
            rows = thread_rows(session, root_id)
            if not rows:
                raise HTTPException(status_code=404, detail="Chat not found")
            ids = [int(r.id) for r in rows if (r.input or "").strip()]
        if after_row_id is not None:
            ids = [i for i in ids if i > int(after_row_id)]
        ids = ids[-max(1, min(int(limit), 200)):]
        turns = [t for t in (await asyncio.to_thread(lambda: [turn_status(root_id, i) for i in ids])) if t]
        return JSONResponse({"chat_id": root_id, "turns": turns})

    @router.get("/chats/{chat_id}/turns/{turn_id}")
    async def get_turn(chat_id: int, turn_id: int, wait: bool = False, timeout: Optional[float] = None):
        """Status, assistant text and timestamps for one turn. ``wait=true`` blocks until done (cap 300 s)."""
        if wait:
            status = await wait_for_turn(chat_id, turn_id, clamp_timeout(timeout))
        else:
            status = await asyncio.to_thread(turn_status, chat_id, turn_id)
        if status is None:
            raise HTTPException(status_code=404, detail="Turn not found in this chat")
        return JSONResponse(status)

    # ---- message-level history -------------------------------------------
    def _load_message(session, chat_id: int, message_id: int):
        from distr.core.db import Chat

        root_id = thread_root_id(session, chat_id)
        root = session.get(Chat, root_id)
        row = session.get(Chat, int(message_id))
        if root is None or row is None or thread_root_id(session, int(row.id)) != root_id:
            raise HTTPException(status_code=404, detail="Message not found in this chat")
        return root_id, root, row

    def _reject_active(root, message_ids: List[int]) -> None:
        pointer = _active_turn_pointer(root)
        if pointer is not None and pointer in set(message_ids):
            raise HTTPException(status_code=409, detail="That turn is still running; cancel it first")

    def _delete_rows(session, root_id: int, rows: list) -> int:
        from distr.core.db import Chat, ChatTurnEvent

        ids = [int(r.id) for r in rows if int(r.id) != int(root_id)]
        if not ids:
            return 0
        # Re-parent any grandchildren so the rest of the thread stays reachable.
        session.query(Chat).filter(Chat.parent_id.in_(ids)).update({Chat.parent_id: int(root_id)}, synchronize_session=False)
        session.query(ChatTurnEvent).filter(ChatTurnEvent.turn_id.in_(ids)).delete(synchronize_session=False)
        session.query(Chat).filter(Chat.id.in_(ids)).delete(synchronize_session=False)
        return len(ids)

    async def _after_history_change(root_id: int, reason: str) -> bool:
        reloaded = await asyncio.to_thread(_reload_oracle_if_current, root_id)
        await _broadcast_chat_updated(root_id, reason=reason)
        return reloaded

    @router.get("/chats/{chat_id}/messages")
    async def list_messages(chat_id: int):
        from distr.core.db import get_session

        with get_session() as session:
            root_id = thread_root_id(session, chat_id)
            rows = thread_rows(session, root_id)
            if not rows:
                raise HTTPException(status_code=404, detail="Chat not found")
            return JSONResponse({"chat_id": root_id, "messages": [message_payload(r, root_id) for r in rows]})

    @router.get("/chats/{chat_id}/messages/{message_id}")
    async def get_message(chat_id: int, message_id: int):
        from distr.core.db import get_session

        with get_session() as session:
            root_id, _root, row = _load_message(session, chat_id, message_id)
            return JSONResponse(message_payload(row, root_id))

    @router.patch("/chats/{chat_id}/messages/{message_id}")
    async def patch_message(chat_id: int, message_id: int, body: MessagePatch):
        from distr.core.db import get_session

        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(status_code=400, detail="Provide input, response or is_hidden")
        with get_session() as session:
            root_id, root, row = _load_message(session, chat_id, message_id)
            _reject_active(root, [int(row.id)])
            if "input" in changes:
                row.input = changes["input"]
            if "response" in changes:
                row.response = changes["response"]
            if "is_hidden" in changes:
                row.is_hidden = bool(changes["is_hidden"])
            row.modified_date = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            payload = message_payload(row, root_id)
        payload["oracle_reloaded"] = await _after_history_change(root_id, "message_updated")
        return JSONResponse(payload)

    @router.delete("/chats/{chat_id}/messages/{message_id}")
    async def delete_message(chat_id: int, message_id: int):
        from distr.core.db import get_session

        with get_session() as session:
            root_id, root, row = _load_message(session, chat_id, message_id)
            if int(row.id) == int(root_id):
                raise HTTPException(
                    status_code=409,
                    detail="The first row is the chat itself: PATCH it to clear text, or DELETE /api/chats/{chat_id}",
                )
            _reject_active(root, [int(row.id)])
            deleted = _delete_rows(session, root_id, [row])
            session.commit()
        reloaded = await _after_history_change(root_id, "message_deleted")
        return JSONResponse({"deleted": deleted, "chat_id": root_id, "message_id": int(message_id), "oracle_reloaded": reloaded})

    @router.post("/chats/{chat_id}/messages/{message_id}/truncate-after")
    async def truncate_after(chat_id: int, message_id: int, confirm: bool = False):
        """Delete every message after ``message_id`` (keeps ``message_id`` itself). Requires ``?confirm=true``."""
        from distr.core.db import get_session

        if not confirm:
            raise HTTPException(status_code=400, detail="Destructive: repeat with ?confirm=true to delete every message after this one")

        with get_session() as session:
            root_id, root, row = _load_message(session, chat_id, message_id)
            rows = thread_rows(session, root_id)
            ids = [int(r.id) for r in rows]
            position = ids.index(int(row.id))
            later = rows[position + 1:]
            _reject_active(root, [int(r.id) for r in later])
            deleted = _delete_rows(session, root_id, later)
            session.commit()
        reloaded = await _after_history_change(root_id, "messages_truncated")
        return JSONResponse({"deleted": deleted, "chat_id": root_id, "kept_through_message_id": int(message_id), "oracle_reloaded": reloaded})

    # ---- settings ---------------------------------------------------------
    @router.get("/settings/schema")
    async def settings_schema():
        models = _section_models()
        sections: Dict[str, Any] = {}
        for name in SETTINGS_SECTIONS:
            model = models.get(name)
            if model is None:
                sections[name] = {"writable": False, "read_path": f"/api/{name}", "write_path": f"POST /api/{name}"}
                continue
            schema = model.model_json_schema()
            sections[name] = {
                "writable": True,
                "read_path": f"/api/{name}",
                "patch_path": f"/api/settings/{name}",
                "keys": sorted(schema.get("properties", {}).keys()),
                "json_schema": schema,
            }
        return JSONResponse({"sections": sections})

    @router.get("/settings")
    async def get_all_settings(request: Request):
        """All settings sections. Secrets are always masked."""
        out: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        for name in SETTINGS_SECTIONS:
            try:
                out[name] = mask_secrets(await _read_section(request, name))
            except HTTPException as exc:
                errors[name] = str(exc.detail)
        return JSONResponse({"sections": out, "errors": errors, "secrets_masked": True})

    @router.patch("/settings/{section}")
    async def patch_settings_section(section: str, request: Request):
        models = _section_models()
        model = models.get(section)
        if model is None:
            if section in READ_ONLY_SECTIONS:
                raise HTTPException(status_code=405, detail=f"'{section}' is read-only here; use POST {READ_ONLY_SECTIONS[section]}")
            raise HTTPException(status_code=404, detail=f"Unknown settings section '{section}'")
        try:
            patch = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Body must be a JSON object")
        if not isinstance(patch, dict) or not patch:
            raise HTTPException(status_code=400, detail="Body must be a non-empty JSON object")
        allowed = set(model.model_fields.keys())
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise HTTPException(status_code=400, detail={"error": "Unknown keys for section", "section": section, "unknown_keys": unknown, "allowed_keys": sorted(allowed)})
        from distr.gui.web.routes.api_audit import project_cli_snapshot, restore_project_cli, validate_project_cli_patch

        validate_project_cli_patch(patch)
        cli_before = await asyncio.to_thread(project_cli_snapshot)
        current = await _read_section(request, section)
        merged = {k: current[k] for k in allowed if k in current}
        merged.update(patch)
        try:
            validated = model(**merged).model_dump()
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid value: {exc}")
        resp = await _internal_call(request, "POST", f"/api/{section}", validated)
        if resp.status_code >= 400:
            await asyncio.to_thread(restore_project_cli, cli_before, patch.keys())
            raise HTTPException(status_code=resp.status_code, detail=f"Saving '{section}' failed")
        restored = await asyncio.to_thread(restore_project_cli, cli_before, patch.keys())
        updated = await _read_section(request, section)
        return JSONResponse({"success": True, "section": section, "updated_keys": sorted(patch),
                             "project_cli_restored": restored, "settings": mask_secrets(updated)})

    # ---- skills -----------------------------------------------------------
    @router.post("/skills/{skill_id}/invoke")
    async def invoke_skill(skill_id: str, body: SkillInvokeRequest, request: Request, wait: bool = False, timeout: Optional[float] = None):
        """Run a skill by sending a Development-thread message with ``skill_ids=[skill_id]``.

        Oracle chats have no skill execution path, so they are rejected.
        """
        from distr.core.db import Chat, get_session
        from distr.core.skills.catalog import filter_known_skill_ids
        from distr.core.workflow.development_threads import is_development_thread

        if not filter_known_skill_ids([skill_id]):
            raise HTTPException(status_code=404, detail="Skill not found")
        with get_session() as session:
            chat = session.get(Chat, int(body.chat_id))
            if chat is None:
                raise HTTPException(status_code=404, detail="Chat not found")
            if not is_development_thread(session, chat):
                raise HTTPException(status_code=409, detail="Skills can only be invoked in a Development thread")
        message = (body.message or "").strip() or f"Use the {skill_id} skill."
        params = f"?wait={'true' if wait else 'false'}&timeout={clamp_timeout(timeout)}"
        resp = await _internal_call(
            request,
            "POST",
            f"/api/workflows/studio/tasks/{int(body.chat_id)}/messages{params}",
            {"message": message, "skill_ids": [skill_id]},
        )
        try:
            data = resp.json()
        except Exception:
            data = {"detail": resp.text}
        return JSONResponse(data, status_code=resp.status_code)

    return router
