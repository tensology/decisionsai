"""Agent-side client for Development-agent approvals (runs in tool subprocesses).

This module can only CREATE requests, POLL them and CONSUME approved ones, all
through the running app's HTTP API (routes/dev_approvals.py). There is no grant
here: approving needs a user click in the thread UI, and approvals are signed
with a key that exists only in the app process. Never imports the app DB.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

_IGNORED_ARG_KEYS = {"approval_id"}
INLINE_WAIT_S = float(os.environ.get("DECISIONS_DEV_APPROVAL_INLINE_WAIT_S", "40"))
POLL_S = 1.5

# Transport hook: (method, path, body|None) -> (status, json). Tests swap this.
Transport = Callable[[str, str, Optional[dict]], tuple[int, dict]]


def args_hash(args: dict[str, Any]) -> str:
    clean = {k: v for k, v in sorted((args or {}).items()) if k not in _IGNORED_ARG_KEYS and v is not None}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()


def run_context() -> tuple[Optional[int], Optional[int]]:
    def _int(name: str) -> Optional[int]:
        try:
            return int(os.environ.get(name) or 0) or None
        except ValueError:
            return None
    return _int("DECISIONS_CHAT_ID"), _int("DECISIONS_TURN_ID")


def _token() -> str:
    env = (os.environ.get("DECISIONSAI_INTERNAL_API_TOKEN") or "").strip()
    if env:
        return env
    path = Path((os.environ.get("DECISIONSAI_INTERNAL_TOKEN_FILE") or "").strip()
                or Path.home() / ".decisionsai" / "internal_api_token").expanduser()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _base() -> str:
    base = (os.environ.get("DECISIONS_API_BASE") or "").strip()
    if not base:
        try:
            from distr.core.web_runtime import get_local_web_base_url
            base = get_local_web_base_url()
        except Exception:
            base = "http://127.0.0.1:8765"
    return base.rstrip("/")


def _http(method: str, path: str, body: Optional[dict] = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(_base() + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-DecisionsAI-Internal-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except Exception:
            return exc.code, {}
    except Exception as exc:
        return 0, {"detail": f"Decisions app not reachable: {type(exc).__name__}"}


transport: Transport = _http


MANAGED_REPLY_SENT_SUMMARY = 'The approved closing reply was sent.'


def confirmed_managed_reply(session, row, details):
    from distr.core.workflow.reply_delivery import recover_sent_workflow_reply

    workflow = details.get('workflow') if isinstance(details, dict) else None
    if (row.tool != 'reply_delivery' or not isinstance(workflow, dict)
            or row.action != details.get('channel') or row.args_hash != args_hash(details)
            or row.chat_id != workflow.get('chat_id') or row.turn_id != workflow.get('turn_id')):
        return None
    receipt = recover_sent_workflow_reply(session, details)
    return {**receipt, 'approval_request_id': row.request_id} if receipt else None


def request_view(row: Any, *, with_secret: Optional[str] = None, receipt_session=None) -> dict[str, Any]:
    """Full local review view shared by HTTP and authorized Chat projection."""
    try:
        details = json.loads(row.details_json or '{}')
    except (TypeError, ValueError):
        details = {}
    out = {'request_id': row.request_id, 'chat_id': row.chat_id, 'turn_id': row.turn_id,
           'tool': row.tool, 'action': row.action, 'summary': row.summary, 'details': details,
           'status': row.status, 'expires_at': row.expires_at.isoformat() + 'Z'}
    if row.status == 'sending' and receipt_session is not None:
        receipt = confirmed_managed_reply(receipt_session, row, details)
        if receipt:
            out.update(status='sent', summary=MANAGED_REPLY_SENT_SUMMARY,
                       details={**details, 'delivery_outcome': receipt})
    if with_secret:
        out['approval_id'] = with_secret
    return out


def request_approval(tool: str, action: str, args: dict[str, Any], summary: str,
                     details: Optional[dict[str, Any]] = None,
                     context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    chat_id, turn_id = run_context() if context is None else (context.get('chat_id'), context.get('turn_id'))
    status, data = transport("POST", "/api/dev-approvals/request", {
        "tool": tool, "action": action, "args_hash": args_hash(args), "chat_id": chat_id, "turn_id": turn_id,
        "summary": summary, "details": details or {}})
    if status != 200:
        return {"error": data.get("detail") or f"approval request failed (HTTP {status})"}
    return data


def status(request_id: str) -> dict[str, Any]:
    code, data = transport("GET", f"/api/dev-approvals/{request_id}", None)
    return data if code == 200 else {"status": "error", "detail": data.get("detail") or f"HTTP {code}"}


def wait(request_id: str, timeout_s: float) -> dict[str, Any]:
    """Poll until the user decides or timeout. Returns the status dict (approval_id once approved)."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        st = status(request_id)
        if st.get("status") != "pending" or time.monotonic() >= deadline:
            return st
        time.sleep(POLL_S)


def consume(approval_id: Optional[str], tool: str, action: str, args: dict[str, Any],
            context: Optional[dict[str, Any]] = None) -> bool:
    if not approval_id:
        return False
    chat_id, turn_id = run_context() if context is None else (context.get('chat_id'), context.get('turn_id'))
    code, data = transport("POST", "/api/dev-approvals/consume", {
        "approval_id": approval_id, "tool": tool, "action": action, "args_hash": args_hash(args),
        "chat_id": chat_id, "turn_id": turn_id})
    return code == 200 and bool(data.get("ok"))


def gate(tool: str, action: str, args: dict[str, Any], approval_id: Optional[str], summary: str,
         details: Optional[dict[str, Any]] = None, inline_wait_s: Optional[float] = None,
         context: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """None => approved, go ahead. Otherwise a tool result to return to the agent."""
    if consume(approval_id, tool, action, args, context=context):
        return None
    req = request_approval(tool, action, args, summary, details, context=context)
    if req.get("error"):
        return {"ok": False, "tool": tool, "summary": f"Approval unavailable: {req['error']}", "artifacts": [],
                "needs_approval": True}
    rid = req["request_id"]
    managed = tool == 'reply_delivery' and isinstance((details or {}).get('workflow'), dict)
    st = req if managed else wait(rid, INLINE_WAIT_S if inline_wait_s is None else inline_wait_s)
    if managed and st.get('status') not in {'pending', 'approved'}:
        outcome = (st.get('details') or {}).get('delivery_outcome')
        if not isinstance(outcome, dict):
            outcome = {'delivered': False, 'unconfirmed': st.get('status') in {'sending', 'unconfirmed'},
                       'delivery_status': st.get('status'), 'reason': st.get('summary') or 'Closing reply awaits confirmation.'}
        return {'managed_outcome': {**outcome, 'approval_request_id': rid}}
    if st.get("status") == "approved" and st.get("approval_id") and consume(st["approval_id"], tool, action, args, context=context):
        return None
    if st.get("status") in ("denied", "expired"):
        return {"ok": False, "tool": tool, "summary": f"The user {st['status']} this {tool} {action}. Do not retry it.",
                "artifacts": [], "needs_approval": False, "request_id": rid, "approval_status": st["status"]}
    return {"ok": False, "tool": tool, "artifacts": [], "needs_approval": True, "request_id": rid,
            "summary": (f"Waiting for the user to approve '{summary}' in the thread (expires {req.get('expires_at')}). "
                        f"Call approval_wait with request_id={rid}; when it returns approval_id, retry the identical "
                        "call with that approval_id.")}


APPROVAL_WAIT_SCHEMA = {
    "description": ("Wait (up to 50 s per call) for the user's decision on a pending approval request. "
                    "Returns status pending|approved|denied|expired, and approval_id once approved."),
    "parameters": {"type": "object", "properties": {
        "request_id": {"type": "string"},
        "timeout_s": {"type": "integer", "minimum": 0, "maximum": 50, "default": 45}},
        "required": ["request_id"]},
}


def approval_wait(request_id: str, timeout_s: int | None = 45, **_: Any) -> dict[str, Any]:
    st = wait(request_id, min(max(int(timeout_s or 0), 0), 50))
    s = st.get("status")
    out = {"ok": s == "approved", "tool": "approval_wait", "artifacts": [], "status": s, "request_id": request_id}
    if st.get("approval_id"):
        out["approval_id"] = st["approval_id"]
        out["summary"] = "Approved. Retry the identical call with this approval_id."
    elif s == "approved":
        out["summary"] = "Approved, but the approval id was already handed out; retry with it."
    else:
        out["summary"] = {"pending": "Still waiting for the user.", "denied": "The user denied it. Do not retry.",
                          "expired": "The request expired. Do not retry unless the user asks."}.get(s, st.get("detail", "error"))
    return out
