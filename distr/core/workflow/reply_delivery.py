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
from urllib.parse import urlsplit

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


def _send_whatsapp(*, target: str, body: str, subject: str = "", before_send=None) -> dict[str, Any]:
    from distr.core.integrations.whatsapp.relay_client import send_message_via_relay

    return dict(send_message_via_relay(jid=target, text=body, before_send=before_send) or {})


def _send_email(*, target: dict, body: str, subject: str = '', key: str = '', before_send=None) -> dict[str, Any]:
    from distr.core.incoming.source_reply import send_reply
    receipt = send_reply(target, body, key, before_send=before_send)
    return {'success': True, 'provider_receipt': receipt}


ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "whatsapp": _send_whatsapp,
    "email": _send_email,
    'mailshot': _send_email,
}

_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")


def _send_telegram(*, target: str, body: str, subject: str = "", project_id=None, key="", before_send=None,
                   attempt_token=None) -> dict[str, Any]:
    """Reply in the Telegram chat the work came from.

    The paired private chat goes through the existing sender. A group goes through
    ``telegram.chat_send`` only when it is explicitly linked to a project (never a guessed
    id); any other chat is refused before sending.
    """
    from distr.core.incoming.source_reply import ReplyPreflightError
    from distr.core.incoming.telegram_topics import split_telegram_source, telegram_topic_id
    from distr.core.incoming.telegram_updates import _recipient_id
    from distr.core.integrations.telegram.chat_send import GroupSendRefused, MAX_TEXT, linked_group_projects, send_to_chat
    from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

    manager = _telegram_manager_from_app()
    if manager is None:
        raise ReplyPreflightError("Nothing was sent. Telegram is not connected right now.")
    chat, topic = split_telegram_source(str(target or "").strip())
    topic_id = telegram_topic_id(topic)
    if topic is not None and (topic_id is None or attempt_token is None
                              or getattr(manager, '_closing_reply_topics', False) is not True):
        raise ReplyPreflightError("Nothing was sent. The Telegram relay cannot verify delivery to this topic. Open the original topic in Telegram to reply.")
    private = _recipient_id(getattr(manager, "telegram_user_id", None))
    is_owner = private is not None and chat == str(private)

    def linked_to_project(value):
        projects = linked_group_projects(value)
        return project_id in projects if project_id is not None else bool(projects)

    if attempt_token is not None:
        from distr.core.integrations.telegram.sender import _audit_outbound_telegram_text

        if not is_owner and (project_id is None or not linked_to_project(chat)):
            raise ReplyPreflightError("Nothing was sent. The reviewed Telegram chat is no longer linked to this project or owner.")
        if len(body.encode('utf-16-le')) // 2 > MAX_TEXT or _audit_outbound_telegram_text(body) != body:
            raise ReplyPreflightError("Nothing was sent. Review a shorter, client-ready Telegram reply.")
        queue = getattr(manager, 'queue_closing_reply', None)
        frame = {'type': 'send_message', 'chat_id': int(chat), 'text': body, 'client_message_id': attempt_token}
        if topic_id is not None:
            frame['message_thread_id'] = topic_id
        if not callable(queue) or not queue(frame):
            raise ReplyPreflightError("Nothing was sent. Telegram could not queue the reviewed reply.")
        return {'awaiting_confirmation': True}
    if is_owner:
        if before_send is not None:
            before_send()
        ok = bool(manager.send_to_telegram(body, expected_chat_id=chat))
        return {"success": ok, "error": None if ok else "Telegram did not accept the message"}

    if linked_to_project(chat):
        try:
            return send_to_chat(manager, chat, body, link_check=linked_to_project, key=key, before_send=before_send)
        except GroupSendRefused as exc:
            raise ReplyPreflightError(f"Nothing was sent. {exc}") from None
    raise ReplyPreflightError("Nothing was sent. The work came from a Telegram chat I can't reply to.")


def _send_jira(*, target: str, body: str, subject: str = "", source_url=None, before_send=None) -> dict[str, Any]:
    """Comment on the originating Jira issue with the connected Jira account."""
    from distr.core.incoming.source_reply import ReplyPreflightError
    from distr.core.kanban.jira_intake import jira_domain_from_account, load_jira_account
    from distr.core.kanban.jira_work_lifecycle import post_jira_comment

    issue_key = str(target or "").strip().upper()
    if not _JIRA_KEY_RE.match(issue_key):
        raise ReplyPreflightError("Nothing was sent. The ticket has no Jira issue to comment on.")
    account = load_jira_account()
    if not account:
        raise ReplyPreflightError("Nothing was sent. No Jira account is connected.")
    if source_url:
        try:
            source = urlsplit(source_url)
            source_host = source.hostname
            account_host = urlsplit(f"https://{jira_domain_from_account(account)}").hostname
        except ValueError:
            source_host = account_host = None
        if not source_host or source_host != account_host:
            raise ReplyPreflightError("Nothing was sent. The connected Jira site does not match the original issue.")
        _, browse, original_key = source.path.rstrip("/").rpartition("/browse/")
        if browse and original_key.upper() != issue_key:
            raise ReplyPreflightError("Nothing was sent. The Jira issue key does not match the saved issue URL.")
    result = dict(post_jira_comment(account, issue_key, body, before_send=before_send) or {})
    if str(result.get("error") or "").startswith("HTTP 4"):
        raise ReplyPreflightError(f"Nothing was sent. Jira refused the comment ({str(result['error'])[:120]}).")
    return result


# Only used for Initiative-owned replies or explicit approval of the exact text.
POSTURE_ADAPTERS: dict[str, Callable[..., dict[str, Any]]] = {
    "telegram": _send_telegram,
    "jira": _send_jira,
}


# ---------------------------------------------------------------------------
# Idempotency ledger
# ---------------------------------------------------------------------------


def _protected_attempts(db, ticket_id, workflow):
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow.telegram_reply_receipts import workflow_outcome

    attempts = db.query(OrchestratorEvent).filter_by(
        ticket_id=ticket_id, source='reply_delivery', event_type=REPLY_EVENT_TYPE,
    ).filter(OrchestratorEvent.status.in_({'sent', 'sending', 'unconfirmed', 'failed', 'not_sent'}))
    legacy = {}
    for attempt in attempts.order_by(OrchestratorEvent.id.desc()):
        try:
            payload = json.loads(attempt.payload or '{}')
        except (ValueError, TypeError):
            payload = None
        payload = payload if isinstance(payload, dict) else {}
        if attempt.status == 'not_sent':
            managed = workflow_outcome(attempt, payload)
            if managed is None or not managed['unconfirmed']:
                continue
        scopes = (_receipt_scope(attempt), payload.get('workflow'))
        known_runs = {str(scope['run_id']) for scope in scopes
                      if isinstance(scope, dict) and scope.get('run_id') is not None}
        if not known_runs:
            legacy[attempt.event_uid] = attempt
        yield attempt, payload, str(workflow['run_id']) in known_runs
    attempt = _legacy_approved_attempt(db, ticket_id, workflow, legacy)
    if attempt is not None:
        # Approval evidence proves a possible send, but cannot reconstruct its receipt.
        yield attempt, {}, True


def _receipt_scope(attempt):
    try:
        evidence = json.loads(attempt.evidence or '{}')
    except (ValueError, TypeError):
        return {}
    return evidence if isinstance(evidence, dict) else {}


def _legacy_approved_attempt(db, ticket_id, workflow, attempts):
    """Link a legacy claim to the exact approval recorded before it."""
    from distr.core.db import DevApprovalRequest
    from distr.core.workflow.dev_approvals import args_hash

    if not attempts or not workflow.get('chat_id') or not workflow.get('turn_id'):
        return None
    approvals = db.query(DevApprovalRequest).filter_by(
        tool='reply_delivery', chat_id=workflow['chat_id'], turn_id=workflow['turn_id'],
    ).filter(DevApprovalRequest.status.in_({'sending', 'sent', 'unconfirmed'}),
             DevApprovalRequest.decided_date.is_not(None))
    for approval in approvals:
        if not approval.approval_id_hash or not approval.signature:
            continue
        try:
            details = json.loads(approval.details_json)
            outcome = details.pop('delivery_outcome', None)
            if isinstance(outcome, dict) and outcome.get('delivery_status') == 'scope_conflict':
                continue
            envelope, body = details['delivery'], details['body']
            if (details['workflow'] != workflow or details['ticket_id'] != ticket_id
                    or envelope['ticket_id'] != ticket_id or approval.action != details['channel']
                    or envelope['channel'] != approval.action or approval.args_hash != args_hash(details)):
                continue
            attempt = attempts.get(_delivery_key(ticket_id, body, envelope))
            if (attempt is not None and attempt.created_at is not None and approval.decided_date <= attempt.created_at
                    and attempt.project_id == envelope['project_id'] and attempt.board_id == envelope['board_id']):
                return attempt
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    return None


def recover_prior_reply(db, run, packets: list[str], reply: str, *, workflow: dict) -> dict | None:
    """Recover a scoped closing reply, including receipts from older output selection rules."""
    from distr.core.db.kanban import KanbanTicket

    previous = {extract_reply_text(packets), extract_reply_text('\n\n'.join(packets))} - {'', reply}
    for attempt, payload, scoped in _protected_attempts(db, run.ticket_id, workflow):
        try:
            if not scoped:
                if _receipt_scope(attempt).get('run_id') is not None or payload.get('workflow') is not None:
                    continue
                earliest = run.completed_at or run.started_at
                if not previous or earliest is None or attempt.created_at is None or attempt.created_at < earliest:
                    continue
            envelope = payload['envelope']
            channel = envelope['channel']
            body = payload['body']
            if not scoped and not any(body == shape_reply(text, channel, contact=envelope.get('source_contact'))
                                      for text in previous):
                continue
            ticket = db.get(KanbanTicket, run.ticket_id)
            if (ticket is None or not isinstance(body, str) or not body.strip()
                    or (scoped and _delivery_key(run.ticket_id, body, envelope) != attempt.event_uid)
                    or not _matching_receipt(attempt, ticket_id=run.ticket_id,
                    envelope=envelope, body=body, workflow=workflow)
                    or _delivery_envelope(db, ticket, resolve_reply_target(ticket), envelope['subject'],
                                          envelope.get('resolved_target')) != envelope):
                return _receipt_conflict(attempt.status)
            sent = attempt.status == 'sent'
            outcome = {'delivered': sent, 'unconfirmed': not sent, 'channel': channel,
                       'target': envelope['recipient'], 'body': body, 'result': payload.get('detail')}
            if scoped and channel == 'telegram':
                from distr.core.workflow.telegram_reply_receipts import workflow_outcome

                if managed := workflow_outcome(attempt, payload):
                    outcome.update(managed)
            return outcome
        except (ValueError, TypeError, KeyError, AttributeError):
            if scoped:
                from distr.core.workflow.telegram_reply_receipts import damaged_workflow_outcome

                return damaged_workflow_outcome(attempt, ticket_id=run.ticket_id, workflow=workflow) or _receipt_conflict(attempt.status)
    return None


def recover_reply_review(db, run, workflow):
    """Recover an existing review without re-evaluating the auto-send policy."""
    from distr.core.db import DevApprovalRequest
    from distr.core.db.time import utc_now_naive
    from distr.core.initiative.outcome_flow import find_workflow_reply_review
    from distr.core.workflow import dev_approvals

    if workflow.get('chat_id') is not None and workflow.get('turn_id') is not None:
        approvals = db.query(DevApprovalRequest).filter_by(
            tool='reply_delivery', chat_id=workflow['chat_id'], turn_id=workflow['turn_id'],
        ).order_by(DevApprovalRequest.id.desc())
        for row in approvals:
            review = dev_approvals.request_view(row, receipt_session=db)
            details = review['details']
            scope = details.get('workflow') if isinstance(details, dict) else None
            if isinstance(scope, dict) and scope.get('run_id') not in (None, run.id):
                continue
            if (scope != workflow or details.get('ticket_id') != run.ticket_id or details.get('channel') != row.action
                    or dev_approvals.args_hash({k: v for k, v in details.items() if k != 'delivery_outcome'}) != row.args_hash):
                return _receipt_conflict(row.status)
            status = review['status']
            if status == 'pending' and row.expires_at < utc_now_naive():
                status = 'expired'
            outcome = details.get('delivery_outcome')
            if isinstance(outcome, dict):
                return {**outcome, 'approval_request_id': row.request_id}
            return {'delivered': status == 'sent', 'unconfirmed': status in {'sending', 'unconfirmed'},
                    'pending_approval': status in {'pending', 'approved'}, 'approval_request_id': row.request_id,
                    'delivery_status': status, 'approval_status': status, 'preflight_failed': status == 'rejected',
                    'reason': 'The closing reply review expired.' if status == 'expired' else review['summary']}
    return find_workflow_reply_review(run.ticket_id, workflow)


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
        'source_contact': ticket.source_contact, 'source_url': target.url if target.channel == 'jira' and target.url else ticket.source_url,
        'whatsapp_message_id': ticket.whatsapp_message_id,
        'review_context': hashlib.sha256(f'{ticket.title or ""}\n{ticket.description or ""}'.encode()).hexdigest(),
        'resolved_target': prepared,
    }


def terminal_reply_scope(db, ticket):
    """Fingerprint the saved local source before provider address resolution."""
    if ticket is None:
        return None
    target = resolve_reply_target(ticket)
    envelope = _delivery_envelope(db, ticket, target, '')
    envelope.pop('review_context')
    source = {
        'envelope': envelope,
        'message_id': target.message_id or ticket.source_external_id or ticket.external_id,
        'url': target.url,
    }
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


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
    if 'terminal_reply_scope' in run_data:
        from distr.core.db.kanban import KanbanTicket

        expected = run_data['terminal_reply_scope']
        if not expected or expected != terminal_reply_scope(db, db.get(KanbanTicket, ticket_id)):
            raise ValueError('The ticket or reply source changed after workflow completion.')
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
    scope = workflow or {}
    evidence = _receipt_scope(row)
    if any(evidence.get(field) is not None and str(evidence[field]) != str(scope.get(field))
           for field in ('run_id', 'workflow_id')):
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


def _workflow_source_key(envelope):
    from distr.core.incoming.source_reply import _valid_source_key

    channel = envelope.get("channel")
    prepared = envelope.get("resolved_target")
    if isinstance(prepared, dict) and prepared.get("channel") in {"gmail", "mailshot"}:
        channel = prepared["channel"]
    elif channel == "email" and envelope.get("source_provider") == "gmail":
        channel = "gmail"
    thread = envelope.get("thread_id")
    return _valid_source_key(f"{channel}:{thread}") if isinstance(thread, str) else None


def _validate_delivery_scope(db, ticket_id, envelope, workflow):
    from distr.core.db.kanban import KanbanTicket

    if workflow is not None:
        _validate_workflow_context(db, workflow, envelope, ticket_id)
    ticket = db.get(KanbanTicket, ticket_id)
    target = resolve_reply_target(ticket) if ticket is not None else None
    if (not target or not target.can_send
            or _delivery_envelope(db, ticket, target, envelope['subject'], envelope.get('resolved_target')) != envelope):
        raise ValueError('The ticket or reply destination changed while waiting. Review a new reply before sending.')


def _recheck_delivery_scope(ticket_id, envelope, workflow):
    from distr.core.db import get_session
    from distr.core.incoming.source_reply import ReplyPreflightError

    try:
        with get_session() as db:
            if db.get_bind().dialect.name == 'sqlite':
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            _validate_delivery_scope(db, ticket_id, envelope, workflow)
    except (ValueError, TypeError, KeyError) as exc:
        raise ReplyPreflightError(f'Nothing was sent. {exc}') from None
    except Exception:
        logger.warning('Could not verify the reply scope before submission', exc_info=True)
        raise ReplyPreflightError('Nothing was sent. The current reply scope could not be verified.') from None


def _claim(key, *, ticket_id, envelope, body, supported, legacy_key=None, workflow=None, telegram_attempt=None):
    """Persist the unique claim and revalidate scope before any transport call."""
    from sqlalchemy.exc import IntegrityError
    from distr.core.db import get_session
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow.telegram_reply_receipts import damaged_workflow_outcome, workflow_outcome
    with get_session() as db:
        try:
            if db.get_bind().dialect.name == 'sqlite':
                db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            _validate_delivery_scope(db, ticket_id, envelope, workflow)
            existing = (db.query(OrchestratorEvent).filter(OrchestratorEvent.event_uid.in_({key, legacy_key or key}))
                        .order_by((OrchestratorEvent.event_uid == key).desc()).all())
            retry_unsent = None
            for receipt in existing:
                payload = (_matching_receipt(receipt, ticket_id=ticket_id, envelope=envelope, body=body, workflow=workflow)
                           if receipt.event_uid == key else None)
                managed = None
                if payload and envelope['channel'] == 'telegram':
                    managed = workflow_outcome(receipt, payload)
                if (receipt.event_uid == key and supported and _is_delivery_row(receipt, ticket_id)
                        and receipt.status in {'not_sent', 'unsupported'}
                        and (receipt.status == 'unsupported' or telegram_attempt is None or payload is not None)
                        and (managed is None or managed['delivery_status'] == receipt.status)):
                    retry_unsent = receipt
                    continue
                if payload is None:
                    if receipt.event_uid == key and envelope['channel'] == 'telegram' and workflow is not None:
                        if damaged := damaged_workflow_outcome(receipt, ticket_id=ticket_id, workflow=workflow):
                            return {**damaged, 'duplicate': True}
                    return _receipt_conflict(receipt.status)
                if managed is not None:
                    return {**managed, 'delivered': False, 'duplicate': True, 'receipt_matches': True,
                            'target': envelope['recipient'], 'body': body}
                outcome = {'delivered': False, 'duplicate': True,
                           'unconfirmed': receipt.status in {'sending', 'unconfirmed', 'failed'},
                           'channel': envelope['channel'], 'target': envelope['recipient']}
                outcome.update(delivery_status=receipt.status, receipt_matches=True, body=body)
                if receipt.status == 'sent':
                    outcome['result'] = payload.get('detail')
                return outcome
            if workflow is not None:
                # The content key changes with revised text or a changed destination.
                # Serialize the run-level check with the claim before calling a provider.
                for receipt, _, scoped in _protected_attempts(db, ticket_id, workflow):
                    if scoped:
                        return _receipt_conflict(receipt.status)
                from distr.core.incoming.source_reply import manual_reply_status_for_workflow

                manual_status = manual_reply_status_for_workflow(db, workflow, _workflow_source_key(envelope))
                if manual_status:
                    return {**_receipt_conflict(manual_status),
                            "reason": "Message Client already has a delivery record for this conversation and result. "
                                      "Check the original conversation before sending again."}
            state = 'sending' if supported else 'unsupported'
            payload = {'channel': envelope['channel'], 'target': envelope['recipient'], 'body': body, 'envelope': envelope}
            if workflow is not None:
                payload['workflow'] = workflow
            if telegram_attempt is not None:
                payload['telegram_attempt'] = telegram_attempt
            # Keep the identity independent of both the mutable payload and run-history cleanup.
            evidence = json.dumps({
                **{field: workflow[field] for field in ('run_id', 'workflow_id', 'chat_id', 'turn_id')
                   if workflow.get(field) is not None},
                "source_key": _workflow_source_key(envelope),
            }) if workflow else None
            if retry_unsent is not None:
                previous = json.loads(retry_unsent.payload or '{}')
                payload['previous_unsent'] = [*(previous.get('previous_unsent') or []),
                    {'status': retry_unsent.status, 'detail': previous.get('detail')}]
                retry_unsent.status, retry_unsent.payload = state, json.dumps(payload, default=str)
                retry_unsent.project_id, retry_unsent.board_id = envelope['project_id'], envelope['board_id']
                retry_unsent.evidence = evidence
                retry_unsent.summary = f"{envelope['channel']} reply {state}"
            else:
                db.add(OrchestratorEvent(event_uid=key, source='reply_delivery', event_type=REPLY_EVENT_TYPE,
                    status=state, ticket_id=int(ticket_id), project_id=envelope['project_id'], board_id=envelope['board_id'],
                    evidence=evidence,
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


ASK_FIRST_REASON = "you asked me to check with you before anything is sent to a client"


def _ask_first_reason(ticket_id: int, target: Any, envelope: dict) -> tuple[bool, str]:
    """(hold?, why). Holds when 'Ask before messaging clients' is on, or the client/project is set aside."""
    try:
        from distr.core.initiative.outcome_flow import _reply_attention, ask_before_client_messages

        ask = ask_before_client_messages()
        attention = _reply_attention(ticket_id, target, envelope)
    except Exception:
        logger.warning("Could not read the client-message approval setting; holding the reply", exc_info=True)
        return True, ASK_FIRST_REASON
    if attention.get("client_ignored"):
        label = attention["client_ignored"].get("label") or "this client"
        return True, f"you asked me not to worry about {label}, so I won't reply on my own"
    if attention.get("project_ignored"):
        return True, "this project is set aside, so I won't reply on my own"
    return (True, ASK_FIRST_REASON) if ask else (False, "")


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
        # Proposal-born email tickets reply to the proposal's original inbound message.
        source_message_id = target.message_id or ticket.source_external_id or ticket.external_id
        actual_subject = subject or ('Your request' if target.channel == 'email' else '')
        envelope = _delivery_envelope(db, ticket, target, actual_subject)
        if approval_context is not None:
            try:
                _validate_workflow_context(db, approval_context, envelope, int(ticket_id))
            except (ValueError, TypeError, KeyError) as exc:
                return {'delivered': False, 'scope_changed': True, 'reason': f'Nothing was sent. {exc}'}

    if not target.can_send:
        out = {
            "delivered": False,
            "reason": "incomplete provenance",
            "missing": list(target.missing),
        }
        if target.channel in {"email", "mailshot"} and "source_contact" in target.missing:
            out["detail"] = ("Nothing was sent. The original email has no reply address I can verify, "
                             "so I won't guess one.")
        return out

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
    # "Ask before messaging clients or others" (initiative_ask_external_comms). When it is on,
    # no client reply leaves without a person approving the exact text, in every posture.
    ask_first, ask_reason = (False, "") if force else _ask_first_reason(int(ticket_id), target, envelope)
    if ask_first and approval_id:
        # A retry carrying an approval for this exact reply: consume it, then send.
        gated = _gate_reply(int(ticket_id), target, shaped, approval_id, reason=ask_reason, envelope=envelope,
                            approval_context=approval_context)
        if gated is not None:
            return gated
        return _deliver_prepared(int(ticket_id), shaped, envelope, adapter or POSTURE_ADAPTERS.get(target.channel),
                                 workflow=approval_context)
    try:
        # Own outcomes: send now via the originating source (audited), or hold when asked to.
        # Standard: hold for Continue / Check it / Send message to client in Telegram.
        from distr.core.initiative.outcome_flow import before_client_reply

        routed = before_client_reply(ticket_id=int(ticket_id), shaped=shaped, envelope=envelope, target=target,
                                     adapter=adapter or POSTURE_ADAPTERS.get(target.channel), force=force,
                                     approval_context=approval_context, ask_first=ask_first)
    except Exception:
        logger.warning("Posture routing for the client reply failed; using the standard gate", exc_info=True)
        routed = None
    if routed is not None and routed.get("needs_approval_gate"):
        own_adapter = adapter or POSTURE_ADAPTERS.get(target.channel)
        gated = _gate_reply(int(ticket_id), target, shaped, approval_id,
                            reason=str(routed.get("reason") or ask_reason or ASK_FIRST_REASON), envelope=envelope,
                            approval_context=approval_context)
        if gated is not None:
            return {**gated, "held_for_approval": True}
        return _deliver_prepared(int(ticket_id), shaped, envelope, own_adapter, workflow=approval_context)
    if routed is not None:
        return routed
    promises = find_promise_claims(shaped)

    from distr.core.workflow.phases import classify

    class_key = classify(ticket_text, has_repo_target=has_repo)
    reason = _approval_reason(promises=promises, shaped=shaped, class_key=class_key)
    if ask_first and adapter is not None:
        # Quiet, Standard without a Telegram card, Standard with Telegram off: hold for approval.
        reason = " and ".join(bit for bit in (ask_reason, reason) if bit)

    if reason and not force:
        gated = _gate_reply(int(ticket_id), target, shaped, approval_id, reason=reason, envelope=envelope,
                            approval_context=approval_context)
        if gated is not None:
            return gated

    if adapter is None and force:
        adapter = POSTURE_ADAPTERS.get(target.channel)  # Paul already approved this exact text.
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
        if envelope['channel'] == 'telegram':
            from distr.core.workflow.telegram_reply_receipts import workflow_outcome

            managed = workflow_outcome(row, payload)
            if managed is not None and not managed['delivered']:
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
    telegram_attempt = None
    if adapter is _send_telegram and workflow is not None:
        from distr.core.workflow.telegram_reply_receipts import new_attempt

        telegram_attempt = new_attempt(key)
    claim = _claim(key, ticket_id=int(ticket_id), envelope=envelope, body=shaped,
                   supported=adapter is not None, legacy_key=legacy_key, workflow=workflow, telegram_attempt=telegram_attempt)
    if claim is not None:
        return claim
    if adapter is None:
        return {
            "delivered": False,
            "unsupported": True,
            "channel": channel,
            "body": shaped,
        }

    def before_send():
        _recheck_delivery_scope(int(ticket_id), envelope, workflow)

    try:
        if adapter is _send_email:
            result = adapter(target=envelope['resolved_target'], body=shaped, subject=envelope['subject'],
                             key=key, before_send=before_send)
        elif adapter is _send_whatsapp:
            result = adapter(target=send_to, body=shaped, subject=envelope['subject'], before_send=before_send)
        elif adapter is _send_telegram:
            result = adapter(target=send_to, body=shaped, project_id=envelope['project_id'], key=key, before_send=before_send,
                             attempt_token=telegram_attempt['token'] if telegram_attempt else None)
        elif adapter is _send_jira:
            result = adapter(target=send_to, body=shaped, source_url=envelope['source_url'], before_send=before_send)
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

    if telegram_attempt and result.get('awaiting_confirmation'):
        return {'delivered': False, 'delivery_status': 'sending', 'delivery_key': key, 'channel': channel,
                'reason': 'Waiting for Telegram to confirm the closing reply.'}
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
            adapter = ADAPTERS.get(target.channel) or POSTURE_ADAPTERS.get(target.channel)
            message_id = target.message_id or ticket.source_external_id or ticket.external_id
            current = _delivery_envelope(db, ticket, target, envelope['subject'], envelope.get('resolved_target'))
        if current != envelope:
            raise ValueError('The ticket or destination changed. Review a fresh reply before sending.')
        if adapter is _send_jira and not current['source_url']:
            raise ValueError('This ticket has no saved Jira issue URL. Verify the original issue before reviewing a new reply.')
        if adapter is _send_telegram and target.thread_id.startswith('-') and current['project_id'] is None:
            raise ValueError('This Telegram group reply has no linked workflow project to verify.')
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
