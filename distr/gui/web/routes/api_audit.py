"""API audit, part 2 (22 Oct 2026): token-gated client endpoints.

Every route in this module requires the internal token (header
``X-DecisionsAI-Internal-Token`` or ``?internal_token=``), including GETs, the
same check ``/api/thirdparty`` uses.

* ``GET  /api/chat/replies/{reply_id}?wait=<sec>`` reply status + content (long-poll, cap 60 s)
* ``GET  /api/chat/current`` / ``PUT /api/chat/current {"chat_id": N}``
* ``POST /api/skills/{skill_id}/run`` one-shot skill run, returns a ``reply_id``
* ``GET  /api/settings/all`` every settings section, secrets masked
* ``GET|PATCH|DELETE /api/chat/{chat_id}/messages/{message_id}`` and
  ``POST /api/chat/{chat_id}/messages/{message_id}/truncate-after?confirm=true``
  (aliases of the ``/api/chats/...`` routes in ``api_extensions``)

``reply_id`` format: ``"<chat_id>.<after_row_id>.<sha8>"``. It is stateless (no
registry, survives restarts): the reply is the first thread row newer than
``after_row_id`` whose input hashes to ``sha8`` (falls back to the first newer
user row). A bare integer is accepted as a turn (chats row) id.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

REPLY_WAIT_CAP_SECONDS = 60.0
REPLY_POLL_SECONDS = 0.5
SKILL_BODY_MAX_CHARS = 20000


def require_token(request: Request) -> None:
    from distr.gui.web.security import require_internal_token_request

    require_internal_token_request(request)


# ---------------------------------------------------------------- reply ids
def _sha8(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:8]


def make_reply_id(chat_id: int, after_row_id: Optional[int], message: str) -> str:
    return f"{int(chat_id)}.{int(after_row_id or 0)}.{_sha8(message)}"


def parse_reply_id(reply_id: str) -> Tuple[Optional[int], Optional[int], Optional[str], Optional[int]]:
    """Return (chat_id, after_row_id, sha8, turn_id)."""
    raw = (reply_id or "").strip()
    if raw.isdigit():
        return None, None, None, int(raw)
    parts = raw.split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit() or len(parts[2]) != 8:
        raise HTTPException(status_code=400, detail="Malformed reply_id")
    return int(parts[0]), int(parts[1]), parts[2].lower(), None


def resolve_reply_turn(chat_id: int, after_row_id: int, sha8: str) -> Optional[int]:
    from distr.core.db import get_session
    from distr.gui.web.routes.api_extensions import thread_root_id, thread_rows

    with get_session() as session:
        root_id = thread_root_id(session, chat_id)
        candidates = [r for r in thread_rows(session, root_id)
                      if int(r.id) > int(after_row_id) and (r.input or "").strip()]
        for row in candidates:
            if _sha8(row.input or "") == sha8:
                return int(row.id)
        return int(candidates[0].id) if candidates else None


def _chat_of_turn(turn_id: int) -> Optional[int]:
    from distr.core.db import Chat, get_session
    from distr.gui.web.routes.api_extensions import thread_root_id

    with get_session() as session:
        if session.get(Chat, int(turn_id)) is None:
            return None
        return thread_root_id(session, int(turn_id))


def _reply_snapshot(reply_id: str) -> Dict[str, Any]:
    from distr.gui.web.routes.api_extensions import turn_status

    chat_id, after_row_id, sha8, turn_id = parse_reply_id(reply_id)
    if turn_id is not None:
        chat_id = _chat_of_turn(turn_id)
        if chat_id is None:
            raise HTTPException(status_code=404, detail="Reply not found")
    else:
        turn_id = resolve_reply_turn(chat_id, after_row_id, sha8)
    base: Dict[str, Any] = {"reply_id": reply_id, "chat_id": chat_id, "turn_id": turn_id}
    if turn_id is None:
        return {**base, "status": "queued", "done": False, "content": None}
    status = turn_status(chat_id, turn_id) or {}
    done = bool(status.get("done"))
    return {
        **base,
        "status": status.get("status", "pending"),
        "done": done,
        "content": status.get("assistant_text") if done else None,
        "partial_content": None if done else (status.get("assistant_text") or None),
        "error": status.get("error"),
        "input": status.get("input"),
        "created_at": status.get("created_at"),
        "completed_at": status.get("completed_at"),
    }


async def wait_for_reply(reply_id: str, wait: float) -> Dict[str, Any]:
    budget = max(0.0, min(float(wait or 0), REPLY_WAIT_CAP_SECONDS))
    deadline = time.monotonic() + budget
    snap = await asyncio.to_thread(_reply_snapshot, reply_id)
    while not snap.get("done") and time.monotonic() < deadline:
        await asyncio.sleep(REPLY_POLL_SECONDS)
        snap = await asyncio.to_thread(_reply_snapshot, reply_id)
    snap["timed_out"] = bool(budget) and not snap.get("done")
    snap["wait_cap_seconds"] = REPLY_WAIT_CAP_SECONDS
    return snap


# ---------------------------------------------------------------- current chat
def _current_chat_payload() -> Dict[str, Any]:
    from distr.core.chat import ChatService
    from distr.core.db import Chat, get_session

    chat_id = ChatService.get_current_chat_id()
    info = None
    if chat_id is not None:
        with get_session() as session:
            chat = session.get(Chat, int(chat_id))
            if chat is not None:
                info = {"id": int(chat.id), "title": chat.title, "provider": chat.provider,
                        "model_name": chat.model_name,
                        "modified_date": chat.modified_date.isoformat() if chat.modified_date else None}
    return {"chat_id": int(chat_id) if chat_id is not None else None, "chat": info}


# ---------------------------------------------------------------- settings guard
PROJECT_CLI_PREFIX = "project_cli_"


def _raw_settings() -> Dict[str, Any]:
    from distr.core.settings import load_settings_from_db

    return dict(load_settings_from_db() or {})


def project_cli_snapshot() -> Dict[str, Any]:
    return {k: v for k, v in _raw_settings().items() if str(k).startswith(PROJECT_CLI_PREFIX)}


def validate_project_cli_patch(patch: Dict[str, Any]) -> None:
    """Reject attempts to blank a tier's backend/model (that is the 'wipe' failure mode)."""
    bad = sorted(k for k, v in patch.items()
                 if str(k).startswith(PROJECT_CLI_PREFIX)
                 and str(k).endswith(("_backend", "_model"))
                 and (v is None or not str(v).strip()))
    if bad:
        raise HTTPException(status_code=422, detail={"error": "project_cli tier backend/model cannot be blank", "keys": bad})


def restore_project_cli(before: Dict[str, Any], patched_keys) -> list:
    """Put back every project_cli_* value the caller did not explicitly patch."""
    from distr.core.settings import save_settings_to_db

    patched = set(patched_keys or ())
    settings = _raw_settings()
    restored = []
    for key, old in before.items():
        if key in patched:
            continue
        if settings.get(key) != old:
            settings[key] = old
            restored.append(key)
    for key in [k for k in settings if str(k).startswith(PROJECT_CLI_PREFIX) and k not in before and k not in patched]:
        settings.pop(key, None)
        restored.append(key)
    if restored:
        save_settings_to_db(settings)
        logger.warning("Settings PATCH: restored project_cli keys clobbered by a partial save: %s", restored)
    return sorted(restored)


# ---------------------------------------------------------------- models
class CurrentChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat_id: int


class SkillRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = ""
    chat_id: Optional[int] = None
    speak: bool = False


def create_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_token)])

    # ---- 1. replies --------------------------------------------------------
    @router.get("/chat/replies/{reply_id}")
    async def get_reply(reply_id: str, wait: float = 0.0):
        """Status (+ content once done). ``?wait=N`` long-polls up to N s (cap 60)."""
        return JSONResponse(await wait_for_reply(reply_id, wait))

    # ---- 2. message history aliases (singular /chat) ----------------------
    async def _proxy(request: Request, method: str, path: str, payload: Any = None) -> JSONResponse:
        from distr.gui.web.routes.api_extensions import _internal_call

        resp = await _internal_call(request, method, path, payload)
        try:
            data = resp.json()
        except Exception:
            data = {"detail": resp.text}
        return JSONResponse(data, status_code=resp.status_code)

    @router.get("/chat/{chat_id}/messages/{message_id}")
    async def alias_get_message(chat_id: int, message_id: int, request: Request):
        return await _proxy(request, "GET", f"/api/chats/{chat_id}/messages/{message_id}")

    @router.patch("/chat/{chat_id}/messages/{message_id}")
    async def alias_patch_message(chat_id: int, message_id: int, request: Request):
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Body must be JSON")
        return await _proxy(request, "PATCH", f"/api/chats/{chat_id}/messages/{message_id}", body)

    @router.delete("/chat/{chat_id}/messages/{message_id}")
    async def alias_delete_message(chat_id: int, message_id: int, request: Request):
        return await _proxy(request, "DELETE", f"/api/chats/{chat_id}/messages/{message_id}")

    @router.post("/chat/{chat_id}/messages/{message_id}/truncate-after")
    async def alias_truncate(chat_id: int, message_id: int, request: Request, confirm: bool = False):
        return await _proxy(request, "POST",
                            f"/api/chats/{chat_id}/messages/{message_id}/truncate-after?confirm={'true' if confirm else 'false'}")

    # ---- 3. current chat -------------------------------------------------
    @router.get("/chat/current")
    async def get_current():
        payload = await asyncio.to_thread(_current_chat_payload)
        payload["set_via"] = "PUT /api/chat/current"
        return JSONResponse(payload)

    @router.put("/chat/current")
    async def put_current(body: CurrentChatBody, request: Request):
        """Switch the Oracle's current chat (same path as the UI's load-in-agent)."""
        previous = (await asyncio.to_thread(_current_chat_payload)).get("chat_id")
        resp = await _proxy(request, "POST", f"/api/chats/{int(body.chat_id)}/load-in-agent")
        if resp.status_code >= 400:
            return resp
        from distr.gui.web.routes.chat import broadcast_chat_event

        event = {"type": "agent_chat_changed", "agent_current_chat_id": int(body.chat_id),
                 "previous_chat_id": previous}
        notified = 0
        for cid in {int(body.chat_id), *( [int(previous)] if previous is not None else [] )}:
            try:
                notified += await broadcast_chat_event({**event, "chat_id": cid})
            except Exception:
                logger.debug("agent_chat_changed broadcast failed", exc_info=True)
        return JSONResponse({"chat_id": int(body.chat_id), "previous_chat_id": previous,
                             "changed": previous != int(body.chat_id), "ui_clients_notified": notified})

    # ---- 4. skills -------------------------------------------------------
    @router.post("/skills/{skill_id}/run")
    async def run_skill(skill_id: str, body: SkillRunBody, request: Request, wait: float = 0.0):
        """One-shot run of a single skill.

        Oracle chat (``chat_id`` or the current chat): the SKILL.md instructions plus
        ``input`` are sent as one user turn via send-to-agent (speak defaults to
        false) and a ``reply_id`` comes back. Development thread: delegates to the
        thread message route with ``skill_ids=[skill_id]`` (the harness loads it).
        """
        from distr.core.db import Chat, get_session
        from distr.core.skills.catalog import filter_known_skill_ids, skill_file_for_id
        from distr.core.workflow.development_threads import is_development_thread

        known = filter_known_skill_ids([skill_id])
        if not known:
            raise HTTPException(status_code=404, detail="Skill not found")
        canonical = known[0]
        chat_id = body.chat_id
        if chat_id is None:
            chat_id = (await asyncio.to_thread(_current_chat_payload)).get("chat_id")
        if chat_id is None:
            raise HTTPException(status_code=409, detail="No current chat; pass chat_id")
        with get_session() as session:
            chat = session.get(Chat, int(chat_id))
            if chat is None:
                raise HTTPException(status_code=404, detail="Chat not found")
            dev = bool(is_development_thread(session, chat))
        wait_s = max(0.0, min(float(wait or 0), REPLY_WAIT_CAP_SECONDS))
        if dev:
            message = (body.input or "").strip() or f"Use the {canonical} skill."
            resp = await _proxy(request, "POST",
                                f"/api/workflows/studio/tasks/{int(chat_id)}/messages?wait={'true' if wait_s else 'false'}&timeout={wait_s}",
                                {"message": message, "skill_ids": [canonical]})
            data = json.loads(resp.body)
            if isinstance(data, dict):
                data.update({"mode": "development_thread", "skill_id": canonical, "chat_id": int(chat_id)})
                if data.get("turn_id"):
                    data.setdefault("reply_id", str(int(data["turn_id"])))
            return JSONResponse(data, status_code=resp.status_code)

        skill_file = skill_file_for_id(canonical)
        instructions = ""
        if skill_file is not None:
            try:
                instructions = skill_file.read_text(encoding="utf-8")[:SKILL_BODY_MAX_CHARS]
            except OSError:
                instructions = ""
        message = (
            f"Run the `{canonical}` skill once, as a standalone task.\n\n"
            f"Skill instructions ({skill_file or 'not on disk'}):\n<<<SKILL\n{instructions or '(no SKILL.md body available)'}\nSKILL>>>\n\n"
            f"Input:\n{(body.input or '').strip() or '(none)'}"
        )
        resp = await _proxy(request, "POST", f"/api/chats/{int(chat_id)}/send-to-agent",
                            {"message": message, "speak": bool(body.speak)})
        data = json.loads(resp.body)
        if resp.status_code >= 400 or not isinstance(data, dict):
            return JSONResponse(data, status_code=resp.status_code)
        out = {"mode": "oracle_one_shot", "skill_id": canonical, "chat_id": int(chat_id),
               "skill_file": str(skill_file) if skill_file else None,
               "sent": data.get("sent"), "reply_id": data.get("reply_id"), "turn_id": data.get("turn_id")}
        if wait_s and out["reply_id"]:
            out["reply"] = await wait_for_reply(out["reply_id"], wait_s)
        return JSONResponse(out)

    # ---- 5. settings -----------------------------------------------------
    @router.get("/settings/all")
    async def settings_all(request: Request):
        return await _proxy(request, "GET", "/api/settings")

    return router
