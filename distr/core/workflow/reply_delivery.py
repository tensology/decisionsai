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
    r"^[ \t]*(?:\*\*)?reply_text(?:\*\*)?[ \t]*:[ \t]*(?P<body>.*)$", re.IGNORECASE,
)
_PACKET_FIELD_RE = re.compile(r'^[ \t]*(?:\*\*)?[a-z_]+(?:\*\*)?[ \t]*:(?!//)', re.IGNORECASE)
_REPLY_FENCE_RE = re.compile(r'`{3}(?:text)?', re.IGNORECASE)


def reply_key(ticket_id: int, channel: str, target: str, body: str) -> str:
    """Stable idempotency key for one reply to one target."""
    digest = hashlib.sha256(
        f"{int(ticket_id)}|{channel}|{target}|{body}".encode("utf-8")
    ).hexdigest()
    return f"ticket-reply:{digest[:40]}"


def extract_reply_text(result_text: str | list[str]) -> str:
    """Read the latest step with a reply field, keeping step boundaries intact."""
    packets = [result_text] if isinstance(result_text, str) else result_text
    for packet in reversed(packets):
        reply = _parse_reply_packet(packet)
        if reply is not None:
            return reply
    return ''


def _parse_reply_packet(result_text: str) -> str | None:
    """Return None for no reply field, or an empty string for an unusable reply."""
    # Fenced bodies may contain lines that look like packet fields. Keep the
    # legacy field parser for results created with the old completion contract.
    replies, current = [], None
    fenced = False
    for line in str(result_text or '').splitlines():
        if fenced:
            if line.strip() == '```':
                replies.append('\n'.join(current).strip())
                current, fenced = None, False
            else:
                current.append(line)
            continue
        match = _REPLY_TEXT_RE.match(line)
        if match or (current is not None and _PACKET_FIELD_RE.match(line)):
            if current is not None:
                replies.append('\n'.join(current).strip().strip('`').strip())
            current = [match.group('body')] if match else None
            if match and _REPLY_FENCE_RE.fullmatch(match.group('body').strip()):
                current, fenced = [], True
        elif current is not None:
            current.append(line)
    if fenced:
        return ''
    if current is not None:
        replies.append('\n'.join(current).strip().strip('`').strip())
    return next((reply for reply in reversed(replies) if reply), '') if replies else None


# ---------------------------------------------------------------------------
# Channel adapters
# ---------------------------------------------------------------------------


def _send_whatsapp(*, target: str, body: str, subject: str = "") -> dict[str, Any]:
    from distr.core.integrations.whatsapp.relay_client import send_message_via_relay

    return dict(send_message_via_relay(jid=target, text=body) or {})


def _send_email(*, target: dict, body: str, subject: str = '', key: str = '') -> dict[str, Any]:
    from distr.core.incoming.source_reply import send_reply
    receipt = send_reply(target, body, key)
    return {'success': True, 'provider_receipt': receipt}


ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "whatsapp": _send_whatsapp,
    "email": _send_email,
    'mailshot': _send_email,
}


# ---------------------------------------------------------------------------
# Idempotency ledger
# ---------------------------------------------------------------------------


def recover_prior_reply(db, run, packets: list[str], reply: str, *, workflow: dict) -> dict | None:
    """Recover an attempt made with older workflow output selection rules."""
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent

    previous = {extract_reply_text(packets), extract_reply_text('\n\n'.join(packets))} - {'', reply}
    if not previous:
        return None
    attempts = db.query(OrchestratorEvent).filter_by(
        ticket_id=run.ticket_id, source='reply_delivery', event_type=REPLY_EVENT_TYPE,
    ).filter(OrchestratorEvent.status.in_({'sent', 'sending', 'unconfirmed', 'failed'}))
    for attempt in attempts.order_by(OrchestratorEvent.id.desc()):
        try:
            payload = json.loads(attempt.payload or '{}')
            stored_workflow = payload.get('workflow')
            if stored_workflow is not None:
                if stored_workflow.get('run_id') != run.id or stored_workflow.get('workflow_id') != run.workflow_id:
                    continue
            else:
                earliest = run.completed_at or run.started_at
                if earliest is None or attempt.created_at is None or attempt.created_at < earliest:
                    continue
            envelope = payload['envelope']
            channel = envelope['channel']
            body = payload['body']
            if not any(body == shape_reply(text, channel, contact=envelope.get('source_contact'))
                       for text in previous):
                continue
            ticket = db.get(KanbanTicket, run.ticket_id)
            if (ticket is None or not _matching_receipt(attempt, ticket_id=run.ticket_id,
                    envelope=envelope, body=body, workflow=workflow)
                    or _delivery_envelope(db, ticket, resolve_reply_target(ticket), envelope['subject'],
                                          envelope.get('resolved_target')) != envelope):
                return _receipt_conflict(attempt.status)
            sent = attempt.status == 'sent'
            return {'delivered': sent, 'unconfirmed': not sent, 'channel': channel,
                    'target': envelope['recipient'], 'body': body, 'result': payload.get('detail')}
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def _delivery_envelope(db, ticket, target, subject, prepared=None):
    from distr.core.db.kanban import KanbanBoard, KanbanLane
    lane = db.get(KanbanLane, ticket.lane_id) if ticket.lane_id else None
    board = db.get(KanbanBoard, lane.board_id) if lane else None
    return {
        'ticket_id': int(ticket.id), 'channel': target.channel,
        'recipient': ', '.join(prepared['to']) if prepared else target.contact if target.channel in {'email', 'mailshot'} else target.thread_id,
        'thread_id': target.thread_id, 'subject': subject,
        'project_id': ticket.linked_project_id if ticket.linked_project_id is not None else (board.default_project_id if board else None),
        # Normal In Progress -> QA movement does not change the reply scope.
        'board_id': board.id if board else None,
        'source_provider': ticket.source_provider, 'source_external_id': ticket.source_external_id,
        'source_contact': ticket.source_contact, 'source_url': ticket.source_url,
        'whatsapp_message_id': ticket.whatsapp_message_id,
        'review_context': hashlib.sha256(f'{ticket.title or ""}\n{ticket.description or ""}'.encode()).hexdigest(),
        'resolved_target': prepared,
    }


def _prepare_mail_target(target, message_id, adapter, subject):
    """Use the same exact-source/account/Reply-all contract as reviewed replies."""
    if adapter is not _send_email:
        return None
    from distr.core.incoming.source_reply import resolve_target
    prepared = resolve_target({'channel': 'gmail' if target.channel == 'email' else 'mailshot',
        'thread_id': target.thread_id, 'message_ids': [str(message_id)] if message_id else [],
        'scoped_ids': True, 'label': target.label})
    if subject:
        prepared['subject'] = subject
    return prepared


def _validate_workflow_context(db, workflow, envelope, ticket_id):
    from distr.core.db import Chat
    from distr.core.db.workflow import AutoWorkflowRun
    run = db.get(AutoWorkflowRun, int(workflow['run_id']))
    chat_id = workflow.get('chat_id')
    if (run is None or str(run.status).lower() != 'completed' or run.ticket_id != ticket_id
            or run.chat_id != chat_id or run.workflow_id != int(workflow['workflow_id'])
            or envelope['ticket_id'] != ticket_id
            or (run.board_id is not None and run.board_id != envelope['board_id'])):
        raise ValueError('The completed workflow, thread or ticket no longer matches this review.')
    run_data = json.loads(run.run_data or '{}')
    if not isinstance(run_data, dict):
        raise ValueError('The completed workflow scope is unavailable.')
    if run_data.get('project_id') is not None and str(run_data['project_id']) != str(envelope['project_id']):
        raise ValueError('The workflow project no longer matches this review.')
    if chat_id is not None:
        root = db.get(Chat, int(chat_id))
        if root is None or root.parent_id is not None or root.project_id != envelope['project_id']:
            raise ValueError('The Development thread project no longer matches this reply.')
    turn_id = workflow.get('turn_id')
    if turn_id is not None:
        turn = db.get(Chat, int(turn_id))
        if chat_id is None or turn is None or (turn.id != chat_id and turn.parent_id != chat_id):
            raise ValueError('The reviewed workflow turn belongs to another thread.')


def _is_delivery_row(row, ticket_id):
    return (row.source == 'reply_delivery' and row.event_type == REPLY_EVENT_TYPE
            and row.ticket_id == ticket_id)


def _matching_receipt(row, *, ticket_id, envelope, body, workflow):
    if (not _is_delivery_row(row, ticket_id) or row.project_id != envelope['project_id']
            or row.board_id != envelope['board_id']):
        return None
    try:
        payload = json.loads(row.payload or '{}')
    except (ValueError, TypeError):
        return None
    if (not isinstance(payload, dict) or payload.get('envelope') != envelope
            or payload.get('body') != body or payload.get('workflow') != workflow):
        return None
    return payload


def _receipt_conflict(status):
    return {'delivered': False, 'duplicate': True, 'delivery_status': 'scope_conflict',
            'unconfirmed': status in {'sending', 'unconfirmed', 'failed'},
            'reason': 'An earlier delivery record does not match this review. Check the original conversation before sending another reply.'}


def _claim(key, *, ticket_id, envelope, body, supported, legacy_key=None, workflow=None):
    """Persist the unique claim and revalidate scope before any transport call."""
    from sqlalchemy.exc import IntegrityError
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    with get_session() as db:
        try:
            if db.get_bind().dialect.name == 'sqlite':
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            if workflow is not None:
                _validate_workflow_context(db, workflow, envelope, ticket_id)
            ticket = db.get(KanbanTicket, int(ticket_id))
            if ticket is None or _delivery_envelope(db, ticket, resolve_reply_target(ticket), envelope['subject'],
                                                  envelope.get('resolved_target')) != envelope:
                return {'delivered': False, 'scope_changed': True,
                        'reason': 'The ticket or reply destination changed while waiting. Review a new reply before sending.'}
            existing = (db.query(OrchestratorEvent).filter(OrchestratorEvent.event_uid.in_({key, legacy_key or key}))
                        .order_by((OrchestratorEvent.event_uid == key).desc()).all())
            retry_unsent = None
            for receipt in existing:
                if (receipt.event_uid == key and supported and _is_delivery_row(receipt, ticket_id)
                        and receipt.status in {'not_sent', 'unsupported'}):
                    retry_unsent = receipt
                    continue
                payload = (_matching_receipt(receipt, ticket_id=ticket_id, envelope=envelope, body=body, workflow=workflow)
                           if receipt.event_uid == key else None)
                if payload is None:
                    return _receipt_conflict(receipt.status)
                outcome = {'delivered': False, 'duplicate': True,
                           'unconfirmed': receipt.status in {'sending', 'unconfirmed', 'failed'},
                           'channel': envelope['channel'], 'target': envelope['recipient']}
                outcome.update(delivery_status=receipt.status, receipt_matches=True, body=body)
                if receipt.status == 'sent':
                    outcome['result'] = payload.get('detail')
                return outcome
            state = 'sending' if supported else 'unsupported'
            payload = {'channel': envelope['channel'], 'target': envelope['recipient'], 'body': body, 'envelope': envelope}
            if workflow is not None:
                payload['workflow'] = workflow
            if retry_unsent is not None:
                previous = json.loads(retry_unsent.payload or '{}')
                payload['previous_unsent'] = [*(previous.get('previous_unsent') or []),
                    {'status': retry_unsent.status, 'detail': previous.get('detail')}]
                retry_unsent.status, retry_unsent.payload = state, json.dumps(payload, default=str)
                retry_unsent.project_id, retry_unsent.board_id = envelope['project_id'], envelope['board_id']
                retry_unsent.summary = f"{envelope['channel']} reply {state}"
            else:
                db.add(OrchestratorEvent(event_uid=key, source='reply_delivery', event_type=REPLY_EVENT_TYPE,
                    status=state, ticket_id=int(ticket_id), project_id=envelope['project_id'], board_id=envelope['board_id'],
                    summary=f"{envelope['channel']} reply {state}", payload=json.dumps(payload, default=str)))
            db.commit()
        except IntegrityError:
            db.rollback()
            return {'delivered': False, 'duplicate': True, 'reason': 'This reply already has a delivery claim.'}
        except (ValueError, TypeError, KeyError) as exc:
            db.rollback()
            return {'delivered': False, 'scope_changed': True, 'reason': f'Nothing was sent. {exc}'}
        except Exception:
            db.rollback()
            logger.warning('Could not persist reply claim; nothing sent', exc_info=True)
            return {'delivered': False, 'reason': 'The delivery claim could not be saved. Nothing was sent.'}
    return None


def _finish(key, *, status, detail):
    from distr.core.db import get_session
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.db.orm_compat import retry_sqlite_locked

    def save():
        with get_session() as db:
            row = db.query(OrchestratorEvent).filter(OrchestratorEvent.event_uid == key,
                                                   OrchestratorEvent.status == 'sending').first()
            if row is None:
                return False
            payload = json.loads(row.payload)
            payload['detail'] = detail
            row.payload = json.dumps(payload, default=str)
            row.status = status
            row.summary = f"{payload['channel']} reply {status}"
            db.commit()
        return True

    try:
        return retry_sqlite_locked(save)
    except Exception:
        logger.warning('Reply outcome could not be recorded; retain the original claim', exc_info=True)
        return False


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
    envelope: dict,
    approval_context: dict | None = None,
) -> Optional[dict[str, Any]]:
    """None means approved. Anything else is a fail-closed result to return."""
    from distr.core.workflow import dev_approvals

    summary = f"Send this reply on {target.label}? ({reason})"
    if approval_context is not None and (not approval_context.get('chat_id') or not approval_context.get('turn_id')):
        return {'delivered': False, 'pending_approval': False, 'body': shaped, 'delivery': envelope,
                'reason': 'The closing reply needs an attached Development turn for review. Nothing was sent.'}
    args = {"ticket_id": int(ticket_id), "channel": target.channel, "body": shaped,
            'delivery': envelope}
    if approval_context is not None:
        args['workflow'] = dict(approval_context)
    try:
        options = {'inline_wait_s': 0, 'context': approval_context} if approval_context is not None else {}
        outcome = dev_approvals.gate("reply_delivery", target.channel, args, approval_id, summary, details=args, **options)
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
    if isinstance(outcome.get('managed_outcome'), dict):
        return outcome['managed_outcome']
    if outcome.get('approval_status') in {'denied', 'expired'}:
        return {'delivered': False, 'pending_approval': False,
                'approval_status': outcome['approval_status'], 'reason': outcome.get('summary'),
                'detail': outcome, 'body': shaped}
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
    approval_context: dict | None = None,
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
        source_message_id = ticket.source_external_id or ticket.external_id
        actual_subject = subject or ('Your request' if target.channel == 'email' else '')
        envelope = _delivery_envelope(db, ticket, target, actual_subject)
        if approval_context is not None:
            try:
                _validate_workflow_context(db, approval_context, envelope, int(ticket_id))
            except (ValueError, TypeError, KeyError) as exc:
                return {'delivered': False, 'scope_changed': True, 'reason': f'Nothing was sent. {exc}'}

    if not target.can_send:
        return {
            "delivered": False,
            "reason": "incomplete provenance",
            "missing": list(target.missing),
        }

    adapter = ADAPTERS.get(target.channel)
    try:
        prepared = _prepare_mail_target(target, source_message_id, adapter, subject)
    except Exception as exc:
        return {'delivered': False, 'preflight_failed': True, 'reason': f'Nothing was sent. The original email could not be verified: {exc}'}
    if prepared is not None:
        envelope['resolved_target'] = prepared
        envelope['subject'] = prepared['subject']
        envelope['recipient'] = ', '.join(prepared['to'])

    shaped = shape_reply(text, target.channel, contact=target.contact)
    promises = find_promise_claims(shaped)

    from distr.core.workflow.phases import classify

    class_key = classify(ticket_text, has_repo_target=has_repo)
    reason = _approval_reason(promises=promises, shaped=shaped, class_key=class_key)

    if reason and not force:
        gated = _gate_reply(int(ticket_id), target, shaped, approval_id, reason=reason, envelope=envelope,
                            approval_context=approval_context)
        if gated is not None:
            return gated

    return _deliver_prepared(int(ticket_id), shaped, envelope, adapter, workflow=approval_context)


def _delivery_key(ticket_id, body, envelope):
    prepared = envelope.get('resolved_target')
    identity = json.dumps({field: prepared.get(field) for field in ('from', 'to', 'cc', 'thread_id', 'message_id', 'subject')},
                          sort_keys=True) if prepared else envelope['recipient']
    return reply_key(ticket_id, envelope['channel'], identity, body)


def recover_sent_workflow_reply(db, details):
    """Read a confirmed receipt for this exact reviewed send after restart."""
    from distr.core.db.orchestrator import OrchestratorEvent

    try:
        envelope, body = details['delivery'], details['body']
        ticket_id = int(details['ticket_id'])
        key = _delivery_key(ticket_id, body, envelope)
        row = db.query(OrchestratorEvent).filter_by(
            event_uid=key, source='reply_delivery', event_type=REPLY_EVENT_TYPE, status='sent',
            ticket_id=ticket_id, project_id=envelope['project_id'], board_id=envelope['board_id'],
        ).one_or_none()
        if row is None:
            return None
        payload = _matching_receipt(row, ticket_id=ticket_id, envelope=envelope, body=body, workflow=details['workflow'])
        if payload is None:
            return None
        return {'delivered': True, 'delivery_status': 'sent', 'channel': envelope['channel'],
                'target': envelope['recipient'], 'body': body, 'result': payload.get('detail')}
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _deliver_prepared(ticket_id, shaped, envelope, adapter, *, workflow=None):
    channel, send_to = envelope['channel'], envelope['recipient']
    prepared = envelope.get('resolved_target')
    legacy_key = reply_key(ticket_id, channel, envelope['source_contact'], shaped) if prepared else None
    key = _delivery_key(ticket_id, shaped, envelope)
    claim = _claim(key, ticket_id=int(ticket_id), envelope=envelope, body=shaped,
                   supported=adapter is not None, legacy_key=legacy_key, workflow=workflow)
    if claim is not None:
        return claim
    if adapter is None:
        return {
            "delivered": False,
            "unsupported": True,
            "channel": channel,
            "body": shaped,
        }

    try:
        if adapter is _send_email:
            result = adapter(target=envelope['resolved_target'], body=shaped, subject=envelope['subject'], key=key)
        else:
            result = adapter(target=send_to, body=shaped, subject=envelope['subject'])
    except Exception as exc:
        from distr.core.incoming.source_reply import ReplyPreflightError
        if isinstance(exc, ReplyPreflightError):
            recorded = _finish(key, status='not_sent', detail=str(exc))
            reason = str(exc) if recorded else 'Nothing was sent. The outcome could not be saved. Review the existing reply before trying again.'
            return {'delivered': False, 'preflight_failed': True, 'unconfirmed': not recorded, 'reason': reason, 'channel': channel}
        logger.warning("Reply delivery failed on %s", channel, exc_info=True)
        _finish(key, status='unconfirmed', detail=str(exc))
        return {"delivered": False, 'unconfirmed': True, "error": str(exc), "channel": channel}

    ok = isinstance(result, dict) and result.get('success') is True and not result.get('dry_run')
    state = 'not_sent' if isinstance(result, dict) and result.get('dry_run') else 'sent' if ok else 'unconfirmed'
    recorded = _finish(key, status=state, detail=result)
    return {
        "delivered": ok and recorded,
        'unconfirmed': state == 'unconfirmed' or not recorded,
        "channel": channel,
        "target": send_to,
        "body": shaped,
        "result": result,
    }


def execute_approved_workflow_reply(details):
    """Execute an exact reviewed managed reply, called only after a user claim.

    The approval route owns the one-shot user decision. This boundary independently
    checks canonical run/thread/ticket ownership and the reviewed delivery envelope.
    It never regenerates or reshapes the approved body.
    """
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanTicket
    try:
        workflow, envelope = details['workflow'], details['delivery']
        body, ticket_id = details['body'], int(details['ticket_id'])
        if not isinstance(body, str) or not body.strip() or details['channel'] != envelope['channel']:
            raise ValueError('The reviewed reply is incomplete.')
        with get_session() as db:
            _validate_workflow_context(db, workflow, envelope, ticket_id)
            ticket = db.get(KanbanTicket, ticket_id)
            if ticket is None:
                raise ValueError('The source ticket is unavailable.')
            target = resolve_reply_target(ticket)
            if not target.can_send:
                raise ValueError('The original reply destination is unavailable.')
            adapter = ADAPTERS.get(target.channel)
            message_id = ticket.source_external_id or ticket.external_id
            current = _delivery_envelope(db, ticket, target, envelope['subject'], envelope.get('resolved_target'))
        if current != envelope:
            raise ValueError('The ticket or destination changed. Review a fresh reply before sending.')
        prepared = _prepare_mail_target(target, message_id, adapter, envelope['subject'])
        if prepared != envelope.get('resolved_target'):
            raise ValueError('The source email, recipients or sending account changed. Review a fresh reply before sending.')
        return _deliver_prepared(ticket_id, body, envelope, adapter, workflow=workflow)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'delivered': False, 'scope_changed': True, 'reason': f'Nothing was sent. {exc}'}
    except Exception:
        logger.warning('Approved workflow reply could not be prepared', exc_info=True)
        return {'delivered': False, 'preflight_failed': True,
                'reason': 'Nothing was sent. The approved reply could not be prepared; review its source before retrying.'}
