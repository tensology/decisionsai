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
import asyncio
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
    from distr.core.workflow.dev_approvals import request_view
    return request_view(row, with_secret=with_secret)


def _managed_details(row: Any) -> Optional[dict[str, Any]]:
    if row.tool != 'reply_delivery':
        return None
    try:
        details = json.loads(row.details_json or '{}')
    except (ValueError, TypeError):
        return None
    if not isinstance(details, dict) or not isinstance(details.get('workflow'), dict):
        return None
    return {key: value for key, value in details.items() if key != 'delivery_outcome'}


def _validate_managed_request(session, row: Any, details: dict[str, Any]) -> None:
    from distr.core.db import Chat
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.dev_approvals import args_hash
    workflow = details['workflow']
    try:
        run = session.get(AutoWorkflowRun, int(workflow['run_id']))
        turn = session.get(Chat, int(row.turn_id)) if row.turn_id is not None else None
        valid = (run is not None and run.status == 'completed' and row.chat_id is not None
                 and turn is not None and (turn.id == row.chat_id or turn.parent_id == row.chat_id)
                 and run.ticket_id == int(details['ticket_id'])
                 and run.workflow_id == int(workflow['workflow_id'])
                 and run.chat_id == workflow.get('chat_id') == row.chat_id
                 and workflow.get('turn_id') == row.turn_id
                 and row.action == details.get('channel')
                 and row.args_hash == args_hash(details))
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(status_code=409, detail='This closing reply no longer matches its workflow review. Nothing was sent.')


def _save_managed_outcome(session, row: Any, details: dict[str, Any], outcome: dict[str, Any]) -> None:
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.dev_approvals import MANAGED_REPLY_SENT_SUMMARY, args_hash
    outcome = {**outcome, 'approval_request_id': row.request_id}
    if outcome.get('duplicate') and outcome.get('receipt_matches') and outcome.get('delivery_status') == 'sent':
        outcome['delivered'] = True
    if outcome.get('delivered'):
        row.status = 'sent'
        row.summary = MANAGED_REPLY_SENT_SUMMARY
    elif outcome.get('unconfirmed') or outcome.get('delivery_status') in {'sending', 'unconfirmed'}:
        row.status = 'unconfirmed'
        row.summary = ('The closing reply was not confirmed.' if outcome.get('reason') else
                       'The closing reply was not confirmed. Check the original conversation before sending again.')
    else:
        row.status = 'rejected'
        row.summary = 'The closing reply was not sent.'
        if outcome.get('delivery_status') != 'scope_conflict':
            row.summary += ' ' + str(outcome.get('reason') or 'Review its current source and scope.')
    row.details_json = json.dumps({**details, 'delivery_outcome': outcome}, default=str)
    workflow = details['workflow']
    if row.args_hash != args_hash(details) or workflow.get('chat_id') != row.chat_id or workflow.get('turn_id') != row.turn_id:
        return
    try:
        run = session.get(AutoWorkflowRun, int(workflow['run_id']))
    except (KeyError, TypeError, ValueError):
        return
    if (run is not None and run.chat_id == row.chat_id
            and str(run.ticket_id) == str(details.get('ticket_id'))
            and str(run.workflow_id) == str(workflow.get('workflow_id'))):
        try:
            data = json.loads(run.run_data or '{}')
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data['reply_delivery'] = outcome
        if isinstance(data.get('terminal_receipt'), dict):
            data['terminal_receipt']['reply_delivery'] = outcome
        run.run_data = json.dumps(data, default=str)


def _expire(session, row) -> None:
    if row.status == 'sending':
        from distr.core.db import DevApprovalRequest
        from distr.core.workflow.dev_approvals import confirmed_managed_reply

        details = _managed_details(row)
        if details is None:
            return
        receipt = confirmed_managed_reply(session, row, details)
        if receipt or not hmac.compare_digest(row.signature or '', _sign(row, row.approval_id_hash or '')):
            outcome = receipt or {
                'delivered': False, 'unconfirmed': True, 'delivery_status': 'unconfirmed',
                'reason': 'The app restarted before reply delivery was confirmed. Check the original conversation before sending again.',
            }
            changed = session.query(DevApprovalRequest).filter_by(
                request_id=row.request_id, status='sending', signature=row.signature,
                args_hash=row.args_hash, details_json=row.details_json,
            ).update({DevApprovalRequest.status: 'sent' if receipt else 'unconfirmed'}, synchronize_session=False)
            if not changed:
                session.refresh(row)
                return
            _save_managed_outcome(session, row, details, outcome)
            session.commit()
            _update_card(row)
        return
    if row.status in ("pending", "approved") and row.expires_at < _now():
        details = _managed_details(row)
        if details is not None:
            _save_managed_outcome(session, row, details,
                {'delivered': False, 'delivery_status': 'expired', 'reason': 'The closing reply approval expired.'})
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
                          metadata={"tool_name": row.tool, "approval_request": _view(row)},
                          event_id=f'closing-review:{row.request_id}' if _managed_details(row) is not None else None)
        return (ev or {}).get("event_id")
    except Exception:
        return None


def _update_card(row: Any) -> None:
    if not row.event_id:
        return
    try:
        from distr.core.chat_turns import update_event
        done = row.status in ("approved", "consumed", "sent")
        update_event(row.event_id, event_type="tool_completed" if done else "tool_started" if row.status == "sending" else "tool_failed" if row.status != "pending" else None,
                     status={"approved": "completed", "consumed": "completed", "sent": "completed", "sending": "running",
                             "unconfirmed": "failed", "rejected": "failed", "denied": "cancelled",
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
            details_json=json.dumps(body.get("details") or {}, default=str), status="pending",
            expires_at=_now() + APPROVAL_TTL)
        details = _managed_details(row)
        if details is not None:
            # Keep the full reviewed envelope; truncating JSON destroys scope.
            row.details_json = json.dumps(body['details'], default=str)
            if len(row.details_json) > 100000:
                raise HTTPException(status_code=413, detail='Closing reply review is too large.')
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            _validate_managed_request(session, row, details)
            existing = session.query(DevApprovalRequest).filter(
                DevApprovalRequest.tool == row.tool, DevApprovalRequest.action == row.action,
                DevApprovalRequest.args_hash == row.args_hash, DevApprovalRequest.chat_id == row.chat_id,
                DevApprovalRequest.turn_id == row.turn_id).order_by(DevApprovalRequest.id).first()
            if existing is not None:
                _expire(session, existing)
                if existing.status == 'pending' and not existing.event_id:
                    session.commit()  # Release the claim before the Chat event write.
                    existing.event_id = _post_card(existing)
                    session.commit()
                return JSONResponse(_view(existing), headers=_NO_STORE)
        else:
            row.details_json = row.details_json[:20000]
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
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = _get(session, request_id)
        _expire(session, row)
        if row.status != "pending":
            if _managed_details(row) is not None and row.status in {'sending', 'sent', 'unconfirmed', 'rejected', 'denied', 'expired'}:
                return JSONResponse(_view(row), headers=_NO_STORE)
            raise HTTPException(status_code=409, detail=f"request is {row.status}")
        details = _managed_details(row)
        if details is not None:
            _validate_managed_request(session, row, details)
            # Only this explicit browser decision claims execution. It is not
            # handed to an agent or replayed by a polling worker.
            row.approval_id_hash = _sha('managed:' + secrets.token_urlsafe(18))
            row.signature = _sign(row, row.approval_id_hash)
            row.status = 'sending'
            row.decided_date = _now()
            session.commit()
            _update_card(row)
        else:
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
    try:
        from distr.core.workflow.reply_delivery import execute_approved_workflow_reply
        outcome = await asyncio.to_thread(execute_approved_workflow_reply, details)
        if not isinstance(outcome, dict):
            raise ValueError('Invalid closing reply result')
    except Exception:
        outcome = {'delivered': False, 'unconfirmed': True, 'reason': 'Closing reply execution was not confirmed.'}
    from distr.core.db.orm_compat import retry_sqlite_locked

    def finish():
        with get_session() as session:
            session.connection().exec_driver_sql('BEGIN IMMEDIATE')
            row = _get(session, request_id)
            if not hmac.compare_digest(row.signature or '', _sign(row, row.approval_id_hash or '')):
                raise HTTPException(status_code=409, detail='Closing reply decision changed. Check the original conversation.')
            if row.status == 'sent' and _managed_details(row) == details:
                return JSONResponse(_view(row), headers=_NO_STORE)
            if row.status != 'sending':
                raise HTTPException(status_code=409, detail='Closing reply decision changed. Check the original conversation.')
            _save_managed_outcome(session, row, details, outcome)
            session.commit()
            _update_card(row)
            return JSONResponse(_view(row), headers=_NO_STORE)

    return await asyncio.to_thread(retry_sqlite_locked, finish)


@router.post("/{request_id}/deny")
async def deny(request_id: str, request: Request):
    _require_user_click(request)
    from distr.core.db import get_session
    with get_session() as session:
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = _get(session, request_id)
        if row.status == "pending":
            details = _managed_details(row)
            if details is not None:
                _save_managed_outcome(session, row, details,
                    {'delivered': False, 'delivery_status': 'denied', 'reason': 'The closing reply was denied.'})
            row.status = "denied"
            row.decided_date = _now()
            session.commit()
            _update_card(row)
        return JSONResponse(_view(row), headers=_NO_STORE)
