"""Deliver a ticket's closing reply on its own channel, exactly once.

The reply phase used to depend on the model remembering to send. This module makes
the send deterministic: resolve provenance, shape for the channel, gate anything
non-trivial behind a human, and refuse to send the same reply twice.

It fails closed. If approval cannot be obtained, nothing is sent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Callable, Optional

from distr.core.workflow.reply_channel import (
    find_promise_claims,
    resolve_reply_target,
    shape_reply,
)

logger = logging.getLogger(__name__)

# Replies longer than this, on a gated class, or claiming something shipped need
# a human before they reach a customer.
APPROVAL_CHAR_THRESHOLD = 400
GATED_CLASSES = frozenset({"heavy", "critical"})
REPLY_EVENT_TYPE = "ticket_reply_sent"

_REPLY_TEXT_RE = re.compile(
    r"(?im)^\s*(?:\*\*)?reply_text(?:\*\*)?\s*:\s*(?P<body>.+?)(?=\n\s*\n|\n\s*(?:\*\*)?[a-z_]+\s*:|\Z)",
    re.DOTALL,
)


def reply_key(ticket_id: int, channel: str, target: str, body: str) -> str:
    """Stable idempotency key for one reply to one target."""
    digest = hashlib.sha256(
        f"{int(ticket_id)}|{channel}|{target}|{body}".encode("utf-8")
    ).hexdigest()
    return f"ticket-reply:{digest[:40]}"


def extract_reply_text(result_text: str) -> str:
    """Pull the customer-facing reply out of a worker's result packet.

    The reply phase is required to return a ``reply_text:`` field. Nothing is
    inferred from prose: a missing field means there is no reply to send.
    """
    # Take the last non-empty match: when several steps return a packet, the
    # final step's reply is the one the customer should receive.
    for match in reversed(list(_REPLY_TEXT_RE.finditer(str(result_text or "")))):
        text = match.group("body").strip().strip("`").strip()
        if text:
            return text
    return ""


# ---------------------------------------------------------------------------
# Channel adapters
# ---------------------------------------------------------------------------


def _send_whatsapp(*, target: str, body: str, subject: str = "") -> dict[str, Any]:
    from distr.core.integrations.whatsapp.relay_client import send_message_via_relay

    return dict(send_message_via_relay(jid=target, text=body) or {})


def _send_email(*, target: str, body: str, subject: str = "") -> dict[str, Any]:
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

    connector = GoogleWorkspaceConnector()
    if hasattr(connector, "is_connected") and not connector.is_connected():
        return {"success": False, "error": "email account is not connected"}
    ok = connector.send_email(target, subject or "Your request", body, "plain")
    return {"success": bool(ok), "target": target}


ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "whatsapp": _send_whatsapp,
    "email": _send_email,
}


# ---------------------------------------------------------------------------
# Idempotency ledger
# ---------------------------------------------------------------------------


def _already_delivered(key: str) -> bool:
    from distr.core.db import get_session
    from distr.core.db.orchestrator import OrchestratorEvent

    with get_session() as db:
        row = (
            db.query(OrchestratorEvent)
            .filter(OrchestratorEvent.event_uid == key)
            .first()
        )
        return row is not None


def _record(
    key: str,
    *,
    ticket_id: int,
    channel: str,
    target: str,
    body: str,
    status: str,
    detail: Any = None,
) -> None:
    """Record a delivery attempt under its unique key. Duplicate keys are ignored."""
    from sqlalchemy.exc import IntegrityError

    from distr.core.db import get_session
    from distr.core.db.orchestrator import OrchestratorEvent

    with get_session() as db:
        db.add(
            OrchestratorEvent(
                event_uid=key,
                source="reply_delivery",
                event_type=REPLY_EVENT_TYPE,
                status=status,
                ticket_id=int(ticket_id),
                summary=f"{channel} reply {status}",
                payload=json.dumps(
                    {
                        "channel": channel,
                        "target": target,
                        "body": str(body or "")[:2000],
                        "detail": detail,
                    },
                    default=str,
                ),
            )
        )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        except Exception:
            logger.debug("Could not record reply delivery", exc_info=True)


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


def _approval_reason(*, promises: list[str], shaped: str, class_key: str) -> str:
    bits: list[str] = []
    if promises:
        bits.append(f"it claims work is live ({promises[0]})")
    if len(shaped) > APPROVAL_CHAR_THRESHOLD:
        bits.append("it is a long reply")
    if class_key in GATED_CLASSES:
        bits.append(f"the ticket is {class_key}")
    return " and ".join(bits)


def _gate_reply(
    ticket_id: int,
    target: Any,
    shaped: str,
    approval_id: Optional[str],
    *,
    reason: str,
) -> Optional[dict[str, Any]]:
    """None means approved. Anything else is a fail-closed result to return."""
    from distr.core.workflow import dev_approvals

    summary = f"Send this reply on {target.label}? ({reason})"
    args = {"ticket_id": int(ticket_id), "channel": target.channel, "body": shaped}
    try:
        outcome = dev_approvals.gate(
            "reply_delivery", target.channel, args, approval_id, summary
        )
    except Exception as exc:
        logger.warning("Reply approval unavailable; not sending", exc_info=True)
        return {
            "delivered": False,
            "pending_approval": True,
            "reason": f"approval unavailable: {exc}",
            "body": shaped,
        }
    if outcome is None:
        return None
    return {
        "delivered": False,
        "pending_approval": True,
        "reason": summary,
        "detail": outcome,
        "body": shaped,
    }


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def deliver_ticket_reply(
    ticket_id: int,
    body: str,
    *,
    approval_id: Optional[str] = None,
    subject: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Send one reply for one ticket, at most once.

    ``force`` skips the human gate. Use it only for a reply a person has already
    read and approved.
    """
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket

    text = str(body or "").strip()
    if not text:
        return {"delivered": False, "reason": "empty reply"}

    with get_session() as db:
        ticket = db.query(KanbanTicket).filter(KanbanTicket.id == int(ticket_id)).first()
        if ticket is None:
            return {"delivered": False, "reason": "ticket not found"}
        ticket_text = f"{ticket.title or ''}\n\n{ticket.description or ''}"
        has_repo = ticket.linked_project_id is not None
        # Resolve provenance while the row is still attached. Reading a detached
        # ORM instance raises DetachedInstanceError, which callers swallow, so the
        # reply was silently skipped in production.
        target = resolve_reply_target(ticket)

    if not target.can_send:
        return {
            "delivered": False,
            "reason": "incomplete provenance",
            "missing": list(target.missing),
        }

    shaped = shape_reply(text, target.channel, contact=target.contact)
    # Email must go to the address. A thread id is not a recipient, and picking it
    # first produced "Invalid To header" from Gmail.
    if target.channel in {"email", "mailshot"}:
        send_to = target.contact or target.thread_id
    else:
        send_to = target.thread_id or target.contact
    promises = find_promise_claims(shaped)

    from distr.core.workflow.phases import classify

    class_key = classify(ticket_text, has_repo_target=has_repo)
    reason = _approval_reason(promises=promises, shaped=shaped, class_key=class_key)

    if reason and not force:
        gated = _gate_reply(int(ticket_id), target, shaped, approval_id, reason=reason)
        if gated is not None:
            return gated

    key = reply_key(int(ticket_id), target.channel, send_to, shaped)
    if _already_delivered(key):
        return {
            "delivered": False,
            "duplicate": True,
            "channel": target.channel,
            "target": send_to,
        }

    adapter = ADAPTERS.get(target.channel)
    if adapter is None:
        _record(
            key,
            ticket_id=int(ticket_id),
            channel=target.channel,
            target=send_to,
            body=shaped,
            status="unsupported",
            detail="no adapter for this channel",
        )
        return {
            "delivered": False,
            "unsupported": True,
            "channel": target.channel,
            "body": shaped,
        }

    try:
        result = adapter(target=send_to, body=shaped, subject=subject)
    except Exception as exc:
        logger.warning("Reply delivery failed on %s", target.channel, exc_info=True)
        _record(
            key,
            ticket_id=int(ticket_id),
            channel=target.channel,
            target=send_to,
            body=shaped,
            status="failed",
            detail=str(exc),
        )
        return {"delivered": False, "error": str(exc), "channel": target.channel}

    ok = bool((result or {}).get("success"))
    _record(
        key,
        ticket_id=int(ticket_id),
        channel=target.channel,
        target=send_to,
        body=shaped,
        status="sent" if ok else "failed",
        detail=result,
    )
    return {
        "delivered": ok,
        "channel": target.channel,
        "target": send_to,
        "body": shaped,
        "result": result,
    }
