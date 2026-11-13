"""Track Telegram closing replies until the provider acknowledges the exact attempt."""

import json
import re
import time
from hashlib import sha256
from uuid import uuid4

PREFIX = 'telegram-closing:'
DEADLINE_SECONDS = 120
_TOKEN = re.compile(r'telegram-closing:([0-9a-f]{40}):[0-9a-f]{32}')
UNCONFIRMED = 'Telegram delivery was not confirmed. Check the original conversation before sending again.'


def new_attempt(key):
    return {'token': f'{PREFIX}{key.removeprefix("ticket-reply:")}:{uuid4().hex}',
            'deadline': time.time() + DEADLINE_SECONDS, 'attempted': False}


def _row(db, key):
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow.reply_delivery import REPLY_EVENT_TYPE

    return db.query(OrchestratorEvent).filter_by(
        event_uid=key, source='reply_delivery', event_type=REPLY_EVENT_TYPE,
    ).one_or_none()


def _telegram_payload(row):
    try:
        payload = json.loads(row.payload) if row is not None else None
        if isinstance(payload, dict) and payload.get('channel') == 'telegram':
            return payload
    except (ValueError, TypeError):
        pass
    return None


def _payload(row):
    payload = _telegram_payload(row)
    return payload if payload and isinstance(payload.get('telegram_attempt'), dict) else None


def _match(db, token):
    match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
    row = _row(db, f'ticket-reply:{match[1]}') if match else None
    payload = _payload(row)
    return (row, payload) if payload and payload['telegram_attempt'].get('token') == token else (None, None)


def _save(db, row, payload, status):
    if payload.get('manual_draft_id'):
        draft = _manual_draft(db, payload)
        if draft is None:
            return False
        draft.status = 'draft' if status == 'not_sent' else status
    row.status = status
    row.summary = f'telegram reply {status}'
    row.payload = json.dumps(payload)
    db.commit()
    return True


def _manual_key(draft_id):
    return 'ticket-reply:' + sha256(f'manual-telegram:{draft_id}'.encode()).hexdigest()[:40]


def reserve_manual_reply(db, draft, chat_id, scope):
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.incoming.source_reply import ReplyPreflightError
    from distr.core.workflow.reply_delivery import REPLY_EVENT_TYPE

    key = _manual_key(draft['id'])
    row = _row(db, key)
    if row is None:
        row = OrchestratorEvent(event_uid=key, source='reply_delivery', event_type=REPLY_EVENT_TYPE)
        db.add(row)
    elif row.status != 'not_sent':
        raise ReplyPreflightError('A Telegram delivery is already on record. Check the reply status before trying again.')
    attempt = new_attempt(key)
    row.status, row.summary = 'sending', 'telegram reply sending'
    row.payload = json.dumps({
        'channel': 'telegram', 'target': draft['target']['thread_id'], 'body': draft['text'],
        'telegram_attempt': attempt, 'manual_draft_id': draft['id'], 'chat_id': chat_id,
        'approved_scope': scope,
    })
    return attempt['token']


def _manual_draft(db, payload):
    from distr.core.db import SourceReplyDraft
    from distr.core.incoming.source_reply import _claim_scope, _decode_reply, _matches_claim, _readable_reply

    row = db.get(SourceReplyDraft, payload['manual_draft_id'])
    if row is None or row.chat_id != payload.get('chat_id') or row.status not in {'sending', 'unconfirmed'}:
        return None
    draft = _decode_reply(row)
    claim = _claim_scope(row)
    if (claim is None or not _readable_reply(draft) or not _matches_claim(draft, claim)
            or draft['source_key'] != f"telegram:{payload.get('target')}" or draft['text'] != payload.get('body')):
        return None
    return row


def finish_manual_preflight(token):
    from distr.core.db import get_session

    with get_session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row, payload = _match(db, token)
        if row and row.status == 'sending' and payload['telegram_attempt'].get('attempted') is False:
            _save(db, row, payload, 'not_sent')


def manual_display_status(db, draft, status):
    if status == 'sending' and draft['target']['channel'] == 'telegram':
        row = _row(db, _manual_key(draft['id']))
        payload = _payload(row)
        if payload and payload.get('manual_draft_id') == draft['id']:
            # Keep the claim even when the outcome is uncertain. Receipt lookup can still resolve it.
            if _outcome(row, payload)['delivery_status'] != 'sending':
                return 'unconfirmed'
    return status


def _matches_destination(target, chat, message_thread_id):
    from distr.core.incoming.telegram_topics import split_telegram_source, telegram_topic_id

    expected_chat, topic = split_telegram_source(target)
    if type(chat) is not int or str(chat) != expected_chat:
        return False
    if topic is None:
        return message_thread_id is None
    return type(message_thread_id) is int and message_thread_id == telegram_topic_id(topic)


def _matches_payload_destination(payload, chat, topic):
    target = payload.get('target')
    envelope = payload.get('envelope')
    if payload.get('workflow') is not None and (not isinstance(envelope, dict) or target != envelope.get('recipient')):
        return False
    return _matches_destination(target, chat, topic)


def _matches_provider_message(payload, chat, message_id, topic):
    return type(message_id) is int and message_id > 0 and _matches_payload_destination(payload, chat, topic)


def authorize(frame, *, owner, relay_verified=True, relay_topics=False):
    """Claim the one socket attempt after verifying live recipient and workflow scope."""
    from distr.core.db import get_session
    from distr.core.integrations.telegram.chat_send import linked_group_projects
    from distr.core.workflow.reply_delivery import _validate_delivery_scope

    chat = frame.get('chat_id')
    projects = linked_group_projects(chat) if type(chat) is int and chat < 0 else set()
    with get_session() as db:
        if db.get_bind().dialect.name == 'sqlite':
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row, payload = _match(db, frame.get('client_message_id'))
        if row is None or row.status != 'sending' or payload['telegram_attempt'].get('attempted') is not False:
            return False
        attempt = payload['telegram_attempt']
        envelope = payload.get('envelope')
        topic_id = frame.get('message_thread_id')
        if not _matches_payload_destination(payload, chat, topic_id) or frame.get('text') != payload.get('body'):
            return False
        try:
            if payload.get('manual_draft_id'):
                from distr.core.db import Chat
                from distr.core.incoming.source_reply import _approval_scope_error, _decode_reply, _reply_scope_revision

                saved = _manual_draft(db, payload)
                if saved is None:
                    return False
                draft = _decode_reply(saved)
                root = db.get(Chat, saved.chat_id)
                if _approval_scope_error(db, root, draft, _reply_scope_revision(db, root),
                                         approved_scope=payload['approved_scope']):
                    raise ValueError('The approved Development reply changed.')
                permitted = chat < 0 and draft['project_id'] in projects and owner is not None
            else:
                _validate_delivery_scope(db, row.ticket_id, envelope, payload.get('workflow'))
                permitted = (envelope['project_id'] in projects if chat < 0 else owner == chat)
            if not relay_verified or (topic_id is not None and not relay_topics) or not permitted or attempt['deadline'] <= time.time():
                raise ValueError('Recipient, project link or send deadline changed.')
        except (ValueError, TypeError, KeyError):
            payload['detail'] = ('Nothing was sent. The Telegram reply scope or send deadline changed.' if relay_verified else
                                 'Nothing was sent. The Telegram relay cannot verify closing reply delivery.')
            _save(db, row, payload, 'not_sent')
            return False
        attempt['attempted'] = True
        return _save(db, row, payload, 'sending')


def confirm(token, *, telegram_chat_id=None, telegram_message_id=None, message_thread_id=None, uncertain=False):
    """Apply an exact acknowledgment once. Late confirmation may resolve uncertainty."""
    from distr.core.db import get_session

    with get_session() as db:
        if db.get_bind().dialect.name == 'sqlite':
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row, payload = _match(db, token)
        if (row is None or row.status not in {'sending', 'unconfirmed'}
                or payload['telegram_attempt'].get('attempted') is not True):
            return False
        if uncertain:
            payload['detail'] = UNCONFIRMED
            return _save(db, row, payload, 'unconfirmed')
        if not _matches_provider_message(payload, telegram_chat_id, telegram_message_id, message_thread_id):
            return False
        payload['detail'] = {'telegram_message_id': telegram_message_id, 'chat_id': telegram_chat_id}
        if message_thread_id is not None:
            payload['detail']['message_thread_id'] = message_thread_id
        return _save(db, row, payload, 'sent')


def accept_receipt(token, *, telegram_chat_id=None, telegram_message_id=None, message_thread_id=None, uncertain=False):
    from functools import partial
    from distr.core.integrations.telegram.receipt_writes import persist_or_defer_receipt_write

    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return False
    if not uncertain and (type(telegram_chat_id) is not int or type(telegram_message_id) is not int):
        return False
    if message_thread_id is not None and (type(message_thread_id) is not int or message_thread_id <= 0):
        return False
    entry = ('closing-uncertain', token) if uncertain else ('closing-sent', token, telegram_chat_id, telegram_message_id)
    if not uncertain and message_thread_id is not None:
        entry = (*entry, message_thread_id)
    return persist_or_defer_receipt_write(
        partial(confirm, token, telegram_chat_id=telegram_chat_id, telegram_message_id=telegram_message_id,
                message_thread_id=message_thread_id, uncertain=uncertain), journal_entry=entry,
    )


def _outcome(row, payload):
    attempt = payload.get('telegram_attempt')
    attempted = attempt.get('attempted') if isinstance(attempt, dict) else None
    deadline = attempt.get('deadline') if isinstance(attempt, dict) else None
    status = row.status
    detail = payload.get('detail')
    if status == 'sent' and (attempted is not True or not isinstance(detail, dict) or not _matches_provider_message(
        payload, detail.get('chat_id'), detail.get('telegram_message_id'), detail.get('message_thread_id'),
    )):
        status, detail = 'unconfirmed', None
    if status == 'sending' and (type(deadline) not in (float, int) or deadline <= time.time()):
        status = 'not_sent' if attempted is False else 'unconfirmed'
    if status == 'not_sent' and attempted is not False:
        status, detail = 'unconfirmed', None
    reason = {
        'sending': 'Waiting for Telegram to confirm the closing reply.',
        'unconfirmed': UNCONFIRMED,
        'not_sent': payload.get('detail') or 'Nothing was sent. Review the Telegram recipient and workflow before trying again.',
    }.get(status)
    return {'delivered': status == 'sent', 'delivery_status': status, 'unconfirmed': status == 'unconfirmed',
            'preflight_failed': status == 'not_sent', 'reason': reason, 'channel': 'telegram',
            'delivery_key': row.event_uid, 'result': detail}


def workflow_outcome(row, payload):
    """Interpret a managed Telegram receipt, while leaving older untracked sends readable."""
    from distr.core.incoming.telegram_topics import split_telegram_source

    envelope = payload.get('envelope')
    if not isinstance(envelope, dict) or envelope.get('channel') != 'telegram':
        return None
    detail = payload.get('detail')
    if ('telegram_attempt' in payload or split_telegram_source(envelope.get('recipient'))[1] is not None
            or (isinstance(detail, dict) and 'telegram_message_id' in detail)):
        return _outcome(row, payload)
    return None


def damaged_workflow_outcome(row, *, ticket_id, workflow):
    """Hold an unreadable Telegram claim only if durable evidence identifies its workflow."""
    from distr.core.workflow.reply_delivery import _receipt_scope

    if row is None or row.ticket_id != ticket_id or row.status not in {'sending', 'unconfirmed', 'sent', 'failed'}:
        return None
    evidence = _receipt_scope(row)
    source = evidence.get('source_key')
    if (not isinstance(source, str) or not source.startswith('telegram:')
            or any(evidence.get(field) != workflow.get(field) for field in ('run_id', 'workflow_id', 'chat_id'))):
        return None
    return {'delivered': False, 'delivery_status': 'unconfirmed', 'unconfirmed': True,
            'reason': UNCONFIRMED, 'channel': 'telegram', 'delivery_key': row.event_uid}


def reviewed_outcome(db, details):
    from distr.core.workflow.reply_delivery import _delivery_key, _matching_receipt

    try:
        envelope, body, workflow = details['delivery'], details['body'], details['workflow']
        if envelope['channel'] != 'telegram':
            return None
        ticket_id = int(details['ticket_id'])
        row = _row(db, _delivery_key(ticket_id, body, envelope))
        payload = _telegram_payload(row)
        if payload and _matching_receipt(row, ticket_id=ticket_id, envelope=envelope, body=body, workflow=workflow):
            return workflow_outcome(row, payload)
        if row is not None and row.project_id == envelope['project_id'] and row.board_id == envelope['board_id']:
            return damaged_workflow_outcome(row, ticket_id=ticket_id, workflow=workflow)
    except (ValueError, TypeError, KeyError):
        pass
    return None


def run_status(db, run, key):
    row = _row(db, key)
    payload = _telegram_payload(row)
    workflow = payload.get('workflow') if payload else None
    if (isinstance(workflow, dict) and row.ticket_id == run.ticket_id
            and workflow.get('run_id') == run.id and workflow.get('workflow_id') == run.workflow_id
            and workflow.get('chat_id') == run.chat_id):
        outcome = workflow_outcome(row, payload)
        return outcome['delivery_status'] if outcome else None
    outcome = damaged_workflow_outcome(row, ticket_id=run.ticket_id,
                                       workflow={'run_id': run.id, 'workflow_id': run.workflow_id, 'chat_id': run.chat_id})
    return outcome['delivery_status'] if outcome else None


def recovery_page(after_id, *, limit=5):
    """Find unresolved attempts and release manual claims that expired before transport."""
    from distr.core.db import get_session
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow.reply_delivery import REPLY_EVENT_TYPE

    with get_session() as db:
        rows = (db.query(OrchestratorEvent)
                .filter(OrchestratorEvent.id > after_id, OrchestratorEvent.source == 'reply_delivery',
                        OrchestratorEvent.event_type == REPLY_EVENT_TYPE,
                        OrchestratorEvent.summary.in_(('telegram reply sending', 'telegram reply unconfirmed')))
                .order_by(OrchestratorEvent.id).limit(limit).all())
        tokens, expired = [], []
        for row in rows:
            payload = _payload(row)
            attempt = payload['telegram_attempt'] if payload else {}
            token = attempt.get('token')
            if not isinstance(token, str) or not _TOKEN.fullmatch(token):
                continue
            if attempt.get('attempted') is True:
                tokens.append(token)
            elif (payload.get('manual_draft_id') and attempt.get('attempted') is False
                  and _outcome(row, payload)['delivery_status'] == 'not_sent'):
                expired.append(token)
        cursor = rows[-1].id if len(rows) == limit else 0
    for token in expired:
        finish_manual_preflight(token)
    return cursor, tokens
