"""Approvals for destructive Development-agent tool actions (app process only).

Agent subprocesses (Pi / Claude / Codex / Cursor tool bridge) may only:
  POST /api/dev-approvals/request        create a pending request (+ thread card)
  GET  /api/dev-approvals/{id}           poll status; returns approval_id once approved
  POST /api/dev-approvals/consume        use an approval exactly once
Only a user click in the thread UI may:
  POST /api/dev-approvals/{id}/approve   (token + same-origin browser fetch + click header)
  POST /api/dev-approvals/{id}/deny

Approvals are signed with an HMAC key generated per app boot and held only in
this process's memory. A subprocess that edits the DB or imports this module
gets a different key, so its forged approvals never verify in the app.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/dev-approvals", tags=["dev-approvals"])
_NO_STORE = {"Cache-Control": "no-store"}
_BOOT_KEY = secrets.token_bytes(32)  # never persisted, never returned
APPROVAL_TTL = timedelta(minutes=15)
USER_ACTION_HEADER = "X-Decisions-User-Action"


def _now():
    from distr.core.db import utc_now_naive
    return utc_now_naive()


def _sign(row: Any, approval_hash: str) -> str:
    msg = "|".join(str(x) for x in (row.request_id, row.chat_id, row.turn_id, row.tool, row.action,
                                     row.args_hash, approval_hash))
    return hmac.new(_BOOT_KEY, msg.encode(), hashlib.sha256).hexdigest()


def _resolve_turn(chat_id: Any, turn_id: Any) -> tuple[Optional[int], Optional[int]]:
    """Bind to the thread's active turn when the agent did not pass one."""
    chat = int(chat_id) if chat_id else None
    turn = int(turn_id) if turn_id else None
    if chat and not turn:
        try:
            from distr.core.chat_turns import latest_active_turn_id
            turn = latest_active_turn_id(chat) or None
        except Exception:
            turn = None
    return chat, turn


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _require_token(request: Request) -> None:
    from distr.gui.web.security import require_internal_token_request
    require_internal_token_request(request)


def _require_user_click(request: Request) -> None:
    """Approve/deny must come from the thread UI in a browser, not a script."""
    from distr.gui.web.security import is_allowed_local_origin
    _require_token(request)
    origin = (request.headers.get("origin") or "").rstrip("/")
    same = f"{request.url.scheme}://{request.headers.get('host') or ''}".rstrip("/")
    if not is_allowed_local_origin(origin) or origin != same:
        raise HTTPException(status_code=403, detail="approval must come from the Decisions UI")
    if request.headers.get("sec-fetch-site") != "same-origin" or request.headers.get("sec-fetch-mode") != "cors":
        raise HTTPException(status_code=403, detail="approval must come from the Decisions UI")
    if request.headers.get(USER_ACTION_HEADER) != "click":
        raise HTTPException(status_code=403, detail="approval must come from a user click")


def _view(row: Any, *, with_secret: Optional[str] = None) -> dict[str, Any]:
    try:
        details = json.loads(row.details_json or "{}")
    except Exception:
        details = {}
    out = {"request_id": row.request_id, "chat_id": row.chat_id, "turn_id": row.turn_id, "tool": row.tool,
           "action": row.action, "summary": row.summary, "details": details, "status": row.status,
           "expires_at": row.expires_at.isoformat() + "Z"}
    if with_secret:
        out["approval_id"] = with_secret
    return out


def _expire(session, row) -> None:
    if row.status in ("pending", "approved") and row.expires_at < _now():
        row.status = "expired"
        session.commit()
        _update_card(row)


def _post_card(row: Any) -> Optional[str]:
    if not row.chat_id:
        return None
    try:
        from distr.core.chat_turns import create_event
        ev = create_event(int(row.chat_id), "tool_started", turn_id=row.turn_id, status="pending",
                          title=f"Approval needed: {row.tool} {row.action}", summary=row.summary[:1000],
                          metadata={"tool_name": row.tool, "approval_request": _view(row)})
        return (ev or {}).get("event_id")
    except Exception:
        return None


def _update_card(row: Any) -> None:
    if not row.event_id:
        return
    try:
        from distr.core.chat_turns import update_event
        done = row.status in ("approved", "consumed")
        update_event(row.event_id, event_type="tool_completed" if done else "tool_failed" if row.status != "pending" else None,
                     status={"approved": "completed", "consumed": "completed", "denied": "cancelled",
                             "expired": "skipped"}.get(row.status, "pending"),
                     metadata={"approval_request": _view(row)})
    except Exception:
        pass


def _get(session, request_id: str):
    from distr.core.db import DevApprovalRequest
    row = session.query(DevApprovalRequest).filter(DevApprovalRequest.request_id == str(request_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="unknown approval request")
    return row


# ---------------- agent-side (token) ----------------
@router.post("/request")
async def create_request(request: Request):
    _require_token(request)
    body = await request.json()
    from distr.core.db import DevApprovalRequest, get_session
    for key in ("tool", "action", "args_hash"):
        if not str(body.get(key) or "").strip():
            raise HTTPException(status_code=400, detail=f"{key} required")
    chat_id, turn_id = _resolve_turn(body.get("chat_id"), body.get("turn_id"))
    with get_session() as session:
        row = DevApprovalRequest(
            request_id="req_" + secrets.token_hex(10), chat_id=chat_id,
            turn_id=turn_id, tool=str(body["tool"])[:64], action=str(body["action"])[:64],
            args_hash=str(body["args_hash"])[:64], summary=str(body.get("summary") or "")[:2000],
            details_json=json.dumps(body.get("details") or {}, default=str)[:20000], status="pending",
            expires_at=_now() + APPROVAL_TTL)
        session.add(row)
        session.commit()
        row.event_id = _post_card(row)
        session.commit()
        return JSONResponse(_view(row), headers=_NO_STORE)


@router.get("/{request_id}")
async def get_request(request_id: str, request: Request):
    _require_token(request)
    from distr.core.db import get_session
    with get_session() as session:
        row = _get(session, request_id)
        _expire(session, row)
        secret = None
        if row.status == "approved":
            # Re-mint is impossible: the id is only stored hashed. Hand it out once.
            secret = _PENDING_HANDOUT.pop(row.request_id, None)
        return JSONResponse(_view(row, with_secret=secret), headers=_NO_STORE)


_PENDING_HANDOUT: dict[str, str] = {}  # request_id -> approval_id, in app memory only


@router.post("/consume")
async def consume(request: Request):
    _require_token(request)
    body = await request.json()
    approval_id = str(body.get("approval_id") or "")
    chat_id, turn_id = _resolve_turn(body.get("chat_id"), body.get("turn_id"))
    from distr.core.db import DevApprovalRequest, get_session
    with get_session() as session:
        row = (session.query(DevApprovalRequest)
               .filter(DevApprovalRequest.approval_id_hash == _sha(approval_id)).first()) if approval_id else None
        if row is not None:
            _expire(session, row)
        ok = bool(
            row is not None and row.status == "approved"
            and hmac.compare_digest(row.signature or "", _sign(row, row.approval_id_hash or ""))
            and row.tool == body.get("tool") and row.action == body.get("action")
            and row.args_hash == body.get("args_hash")
            and (row.chat_id or None) == chat_id
            and (row.turn_id or None) == turn_id
        )
        if ok:
            row.status = "consumed"
            session.commit()
            _update_card(row)
        return JSONResponse({"ok": ok}, headers=_NO_STORE)


# ---------------- user-only (browser click) ----------------
@router.post("/{request_id}/approve")
async def approve(request_id: str, request: Request):
    _require_user_click(request)
    from distr.core.db import get_session
    with get_session() as session:
        row = _get(session, request_id)
        _expire(session, row)
        if row.status != "pending":
            raise HTTPException(status_code=409, detail=f"request is {row.status}")
        approval_id = "apr_" + secrets.token_urlsafe(18)
        row.approval_id_hash = _sha(approval_id)
        row.signature = _sign(row, row.approval_id_hash)
        row.status = "approved"
        row.decided_date = _now()
        row.expires_at = _now() + APPROVAL_TTL
        session.commit()
        _PENDING_HANDOUT[row.request_id] = approval_id
        _update_card(row)
        return JSONResponse(_view(row), headers=_NO_STORE)


@router.post("/{request_id}/deny")
async def deny(request_id: str, request: Request):
    _require_user_click(request)
    from distr.core.db import get_session
    with get_session() as session:
        row = _get(session, request_id)
        if row.status == "pending":
            row.status = "denied"
            row.decided_date = _now()
            session.commit()
            _update_card(row)
        return JSONResponse(_view(row), headers=_NO_STORE)
