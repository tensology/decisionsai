"""Track Telegram closing replies until the provider acknowledges the exact attempt."""

import json
import re
import time
from uuid import uuid4

PREFIX = 'telegram-closing:'
DEADLINE_SECONDS = 120
_TOKEN = re.compile(r'telegram-closing:([0-9a-f]{40}):[0-9a-f]{32}')


def new_attempt(key):
    return {'token': f'{PREFIX}{key.removeprefix("ticket-reply:")}:{uuid4().hex}',
            'deadline': time.time() + DEADLINE_SECONDS, 'attempted': False}


def _row(db, key):
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow.reply_delivery import REPLY_EVENT_TYPE

    return db.query(OrchestratorEvent).filter_by(
        event_uid=key, source='reply_delivery', event_type=REPLY_EVENT_TYPE,
    ).one_or_none()


def _payload(row):
    try:
        payload = json.loads(row.payload) if row is not None else None
        if isinstance(payload, dict) and payload.get('channel') == 'telegram' and isinstance(payload.get('telegram_attempt'), dict):
            return payload
    except (ValueError, TypeError):
        pass
    return None


def _match(db, token):
    match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
    row = _row(db, f'ticket-reply:{match[1]}') if match else None
    payload = _payload(row)
    return (row, payload) if payload and payload['telegram_attempt'].get('token') == token else (None, None)


def _save(db, row, payload, status):
    row.status = status
    row.summary = f'telegram reply {status}'
    row.payload = json.dumps(payload)
    db.commit()


def authorize(frame, *, owner, relay_verified=True):
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
        if row is None or row.status != 'sending' or payload['telegram_attempt'].get('attempted'):
            return False
        attempt = payload['telegram_attempt']
        envelope = payload.get('envelope')
        if str(chat) != payload.get('target') or frame.get('text') != payload.get('body'):
            return False
        try:
            _validate_delivery_scope(db, row.ticket_id, envelope, payload.get('workflow'))
            permitted = (envelope['project_id'] in projects if chat < 0 else owner == chat)
            if not relay_verified or not permitted or attempt['deadline'] <= time.time():
                raise ValueError('Recipient, project link or send deadline changed.')
        except (ValueError, TypeError, KeyError):
            payload['detail'] = ('Nothing was sent. The Telegram reply scope or send deadline changed.' if relay_verified else
                                 'Nothing was sent. The Telegram relay cannot verify closing reply delivery.')
            _save(db, row, payload, 'not_sent')
            return False
        attempt['attempted'] = True
        _save(db, row, payload, 'sending')
        return True


def confirm(token, *, telegram_chat_id=None, telegram_message_id=None, uncertain=False):
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
            payload['detail'] = 'Telegram delivery was not confirmed. Check the original conversation before sending again.'
            _save(db, row, payload, 'unconfirmed')
            return True
        if (type(telegram_chat_id) is not int or str(telegram_chat_id) != payload.get('target')
                or type(telegram_message_id) is not int or telegram_message_id <= 0):
            return False
        payload['detail'] = {'telegram_message_id': telegram_message_id, 'chat_id': telegram_chat_id}
        _save(db, row, payload, 'sent')
        return True


def accept_receipt(token, *, telegram_chat_id=None, telegram_message_id=None, uncertain=False):
    from functools import partial
    from distr.core.integrations.telegram.receipt_writes import persist_or_defer_receipt_write

    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        return False
    if not uncertain and (type(telegram_chat_id) is not int or type(telegram_message_id) is not int):
        return False
    entry = ('closing-uncertain', token) if uncertain else ('closing-sent', token, telegram_chat_id, telegram_message_id)
    return persist_or_defer_receipt_write(
        partial(confirm, token, telegram_chat_id=telegram_chat_id, telegram_message_id=telegram_message_id,
                uncertain=uncertain), journal_entry=entry,
    )


def _outcome(row, payload):
    attempt = payload['telegram_attempt']
    status = row.status
    deadline = attempt.get('deadline')
    if status == 'sending' and (type(deadline) not in (float, int) or deadline <= time.time()):
        status = 'unconfirmed' if attempt.get('attempted') else 'not_sent'
    reason = {
        'sending': 'Waiting for Telegram to confirm the closing reply.',
        'unconfirmed': 'Telegram delivery was not confirmed. Check the original conversation before sending again.',
        'not_sent': payload.get('detail') or 'Nothing was sent. Review the Telegram recipient and workflow before trying again.',
    }.get(status)
    return {'delivered': status == 'sent', 'delivery_status': status, 'unconfirmed': status == 'unconfirmed',
            'preflight_failed': status == 'not_sent', 'reason': reason, 'channel': 'telegram',
            'delivery_key': row.event_uid, 'result': payload.get('detail')}


def reviewed_outcome(db, details):
    from distr.core.workflow.reply_delivery import _delivery_key, _matching_receipt

    try:
        envelope, body, workflow = details['delivery'], details['body'], details['workflow']
        if envelope['channel'] != 'telegram':
            return None
        ticket_id = int(details['ticket_id'])
        row = _row(db, _delivery_key(ticket_id, body, envelope))
        payload = _payload(row)
        if payload and _matching_receipt(row, ticket_id=ticket_id, envelope=envelope, body=body, workflow=workflow):
            return _outcome(row, payload)
    except (ValueError, TypeError, KeyError):
        pass
    return None


def run_status(db, run, key):
    row = _row(db, key)
    payload = _payload(row)
    workflow = payload.get('workflow') if payload else None
    if (isinstance(workflow, dict) and row.ticket_id == run.ticket_id
            and workflow.get('run_id') == run.id and workflow.get('workflow_id') == run.workflow_id
            and workflow.get('chat_id') == run.chat_id):
        return _outcome(row, payload)['delivery_status']
    return None


def recovery_page(after_id, *, limit=5):
    """Read attempted, unresolved deliveries. The caller only requests receipts."""
    from distr.core.db import get_session
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow.reply_delivery import REPLY_EVENT_TYPE

    with get_session() as db:
        rows = (db.query(OrchestratorEvent)
                .filter(OrchestratorEvent.id > after_id, OrchestratorEvent.source == 'reply_delivery',
                        OrchestratorEvent.event_type == REPLY_EVENT_TYPE,
                        OrchestratorEvent.summary.in_(('telegram reply sending', 'telegram reply unconfirmed')))
                .order_by(OrchestratorEvent.id).limit(limit).all())
        tokens = []
        for row in rows:
            payload = _payload(row)
            attempt = payload['telegram_attempt'] if payload else {}
            token = attempt.get('token')
            if attempt.get('attempted') is True and isinstance(token, str) and _TOKEN.fullmatch(token):
                tokens.append(token)
        return (rows[-1].id if len(rows) == limit else 0), tokens
