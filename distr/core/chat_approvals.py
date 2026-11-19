"""In-chat approval cards for Chat agents (browser actions and deletes).

Reuses the Development approval machinery end to end: the agent asks the app
over HTTP (/api/dev-approvals/request), the app posts an Approve/Deny card into
the Chat or group thread, only a real same-origin click can approve, the app
hands out a one-shot HMAC-signed approval_id, and the agent consumes it once.

Everything here fails CLOSED: no thread to show the card in, the app not
reachable, any error, a deny, or no answer within 15 minutes all mean "no",
and the agent is told not to retry. There is no Qt dialog path.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

APPROVAL_TTL_S = 15 * 60          # matches routes/dev_approvals.APPROVAL_TTL
WAIT_GRACE_S = 20                 # let the server mark it expired before we give up
TOOL_TIMEOUT_S = APPROVAL_TTL_S + 60.0


class Decision(tuple):
    """(approved: bool, message: str) — message is what the agent should be told."""

    def __new__(cls, approved: bool, message: str = ""):
        return super().__new__(cls, (bool(approved), message))

    @property
    def approved(self) -> bool:
        return self[0]

    @property
    def message(self) -> str:
        return self[1]

    def __bool__(self) -> bool:  # `if request_in_chat(...)` reads naturally
        return self[0]


def sensitive_ask_enabled() -> bool:
    """Settings › Initiative › 'Ask before sensitive actions'. Unknown => ask."""
    try:
        from distr.core.settings import load_settings_from_db

        value = load_settings_from_db().get("initiative_ask_sensitive", True)
        return value is None or bool(value)
    except Exception:
        logger.warning("could not read initiative_ask_sensitive; asking", exc_info=True)
        return True


def current_chat_id() -> Optional[int]:
    try:
        from distr.core.chat import ChatService

        value = ChatService.get_current_chat_id()
        return int(value) if value else None
    except Exception:
        logger.warning("could not resolve the current chat for an approval", exc_info=True)
        return None


def request_in_chat(tool: str, action: str, args: dict[str, Any], summary: str,
                    details: Optional[dict[str, Any]] = None, *, chat_id: Optional[int] = None,
                    timeout_s: float = APPROVAL_TTL_S + WAIT_GRACE_S) -> Decision:
    """Show an Approve/Deny card in the Chat thread and wait for Paul's click."""
    from distr.core.workflow import dev_approvals

    chat_id = chat_id or current_chat_id()
    label = summary.strip() or f"{tool} {action}"
    if not chat_id:
        return Decision(False, f"Not done: there is no Chat thread to ask Paul in ({label}). Do not retry.")
    context = {"chat_id": int(chat_id), "turn_id": None}
    try:
        req = dev_approvals.request_approval(tool, action, args or {}, label, details or {}, context=context)
        if req.get("error") or not req.get("request_id"):
            return Decision(False, f"Not done: the approval card could not be shown ({req.get('error') or 'no id'}). Do not retry.")
        rid = req["request_id"]
        st = dev_approvals.wait(rid, timeout_s)
        status = st.get("status")
        if status == "approved" and st.get("approval_id") and dev_approvals.consume(
                st["approval_id"], tool, action, args or {}, context=context):
            return Decision(True, "Approved by Paul.")
        if status == "denied":
            return Decision(False, f"Paul denied this: {label}. Do not retry.")
        if status in ("expired", "pending"):
            return Decision(False, f"No answer within 15 minutes, treated as denied: {label}. Do not retry unless Paul asks.")
        return Decision(False, f"Not done: approval failed ({st.get('detail') or status}). Do not retry.")
    except Exception:
        logger.warning("in-chat approval failed; refusing", exc_info=True)
        return Decision(False, f"Not done: the approval could not be completed ({label}). Do not retry.")
