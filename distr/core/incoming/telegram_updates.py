"""Owner-facing execution updates for proposals explicitly approved in Telegram."""
from __future__ import annotations

import json
import hashlib
import threading
import time
import logging
from sys import float_info
from uuid import uuid4

from sqlalchemy import case, func, update

from distr.core.chat import _chat_params
from distr.core.db import Chat, get_session
from distr.core.planning.ownership import current_owner, owner_is_gone

_lock = threading.RLock()
_KEY = "telegram_proposal_update"
_PROCESS = uuid4().hex
_ACK_TIMEOUT = 120
_RELAY_RATE_LIMIT_WAIT = 60
_DELIVERED_HISTORY_LIMIT = 64
_RECIPIENT_CHANGED = 'Telegram recipient changed. Review this retained delivery before sending to another owner.'
_LEGACY_PRETRANSPORT_RECIPIENT_ERROR = 'Telegram recipient changed before transport. Review this retained delivery before retrying.'
logger = logging.getLogger(__name__)
_STAGES = {
    "development": "Work started", "testing": "Running tests", "refinement": "Refining the change",
    "retesting": "Retesting", "verification": "Checking the result", "blocked": "Needs your attention",
    "done": "Completed", "failed": "Work could not be completed",
}


def receipt_has_valid_structure(receipt):
    if not isinstance(receipt, dict):
        return False
    pending = receipt.get('pending')
    backlog = receipt.get('backlog', [])
    return (pending is None or isinstance(pending, dict)) and isinstance(backlog, list) and all(
        isinstance(delivery, dict) for delivery in backlog
    )


def valid_delivery_receipt(receipt):
    if not receipt_has_valid_structure(receipt):
        return False
    if receipt.get('recipient_id') is not None and (type(receipt['recipient_id']) is not int or not 0 < receipt['recipient_id'] < 2**63):
        return False
    if receipt.get('sent') is not None and not isinstance(receipt['sent'], str):
        return False
    delivered = receipt.get('delivered_keys', [])
    if not isinstance(delivered, list) or any(not isinstance(key, str) for key in delivered):
        return False
    backlog = receipt.get('backlog', [])
    pending = receipt.get('pending')
    deliveries = backlog if pending is None else [pending, *backlog]
    for pending in deliveries:
        if not isinstance(pending.get('text'), str) or not isinstance(pending.get('key'), str):
            return False
        offset = pending.get('offset', 0)
        if type(offset) is not int or not 0 <= offset <= len(pending['text']):
            return False
        retry_at = pending.get('relay_retry_at', 0)
        if type(retry_at) not in (int, float) or not 0 <= retry_at <= float_info.max:
            return False
        awaiting = pending.get('awaiting')
        if awaiting is not None and (not isinstance(awaiting, dict) or not isinstance(awaiting.get('token'), str)
                or type(awaiting.get('end')) is not int or not offset < awaiting['end'] <= len(pending['text'])):
            return False
        if awaiting is not None:
            if awaiting.get('state') not in ('queued', 'sending', 'unconfirmed'):
                return False
            attempted_at = awaiting.get('at', 0)
            if type(attempted_at) not in (int, float) or not -float_info.max <= attempted_at <= float_info.max:
                return False
    return True


def _write_receipt(db, chat_id, receipt):
    # Execution can advance while Telegram enqueue is in progress. Patch only
    # our JSON member in the local SQLite store rather than replacing a stale
    # copy of all chat parameters (which also holds the execution state).
    params = case((func.json_valid(Chat.params) == 1, Chat.params), else_="{}")
    db.execute(
        update(Chat).where(Chat.id == int(chat_id)).values(
            params=func.json_set(params, f"$.{_KEY}", func.json(json.dumps(receipt)))
        ).execution_options(synchronize_session=False)
    )
    db.commit()


def _manager():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    return getattr(app, "telegram_manager", None) if app else None


def _recipient_id(value):
    if type(value) is int and 0 < value < 2**63:
        return value
    if isinstance(value, str) and 0 < len(value) <= 19 and value.isascii() and value.isdigit():
        return _recipient_id(int(value))
    return None


def _chunk_end(text, offset, limit=3500):
    end, units = offset, 0
    while end < len(text):
        size = 2 if ord(text[end]) > 0xFFFF else 1
        if units + size > limit:
            break
        units += size
        end += 1
    return end


def _configured_recipient():
    """Read a unique configured owner while the manager is unavailable."""
    try:
        from distr.core.settings import load_settings_from_db
        accounts = load_settings_from_db().get('connected_accounts') or []
        if isinstance(accounts, str):
            accounts = json.loads(accounts)
        if not isinstance(accounts, list):
            return None
        owners = {_recipient_id(account.get('user_id')) for account in accounts
                  if isinstance(account, dict) and account.get('provider') == 'telegram'}
        owners.discard(None)
        return next(iter(owners)) if len(owners) == 1 else None
    except Exception:
        return None


def _queue_identity(manager):
    identity = getattr(manager, '_proposal_delivery_queue_id', None)
    if not isinstance(identity, str) or not identity:
        identity = uuid4().hex
        manager._proposal_delivery_queue_id = identity
    return identity


def queue_execution_update(ticket, chat_id, execution, status):
    if (ticket.get("source_metadata") or {}).get("approval_source") != "telegram":
        return
    if status not in _STAGES:
        return
    job = str(execution.get("job_id") or execution.get("id") or "run")
    key = f"{job}:{status}"
    header = f"{ticket.get('project_name') or 'Project'}: {ticket.get('reference') or ticket.get('title') or 'Ticket'}"
    message = f"{header}\n{_STAGES[status]}."
    if status in {"done", "failed", "blocked"}:
        packet = execution.get("output_packet") or {}
        public_result = packet.get("human_summary") if status == "done" else ""
        response = str(public_result or packet.get("output") or packet.get("summary") or execution.get("worker_question") or execution.get("summary") or execution.get("error") or "").strip()
        if status == "failed" and str(execution.get("status") or "") in {"completed", "completed_noop"}:
            message += " Final verification evidence is missing. Review the result before retrying."
        if response:
            message += f"\n\n{response}"
    manager = _manager()
    owner = _recipient_id(getattr(manager, 'telegram_user_id', None)) or _configured_recipient()
    with _lock, get_session() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chat = db.get(Chat, int(chat_id))
        if chat is None:
            return
        params = _chat_params(chat.params)
        receipt = params.get(_KEY, {})
        if not valid_delivery_receipt(receipt):
            logger.warning('Thread %s has invalid Telegram delivery state; reconcile it before sending.', chat_id)
            return
        if owner and not receipt.get('recipient_id'):
            receipt['recipient_id'] = owner
        pending = receipt.get('pending') or {}
        backlog = receipt.get('backlog') or []
        known_keys = {receipt.get('sent'), pending.get('key'), *receipt.get('delivered_keys', []),
                      *(item.get('key') for item in backlog)}
        if key in known_keys:
            return
        terminal_keys = {job + ':done', job + ':failed'}
        if status not in {'done', 'failed'} and known_keys & terminal_keys:
            return  # A delayed intermediate stage cannot undo the final result.
        update = {"key": key, "text": message, "offset": 0}
        # Never erase a final result with an unresolved delivery, including
        # when a newer job starts. Intermediate statuses may still coalesce.
        if pending and (backlog or pending.get('awaiting') or pending.get('offset', 0) or pending.get('relay_retry_at')
                        or str(pending.get('key') or '').rsplit(':', 1)[-1] in {'done', 'failed'}):
            # Only untouched, unqueued backlog stages can coalesce. Retain any
            # transport receipt until its ACK can reconcile the exact chunk.
            backlog = [item for item in backlog if not (
                item['key'].rsplit(':', 1)[0] == job and item['key'].rsplit(':', 1)[-1] not in {'done', 'failed'}
                and not item.get('awaiting') and not item.get('offset', 0))]
            backlog.append(update)
            receipt['backlog'] = backlog
        else:
            receipt["pending"] = update
        _write_receipt(db, chat_id, receipt)
    flush_execution_updates()


def flush_execution_updates():
    manager = _manager()
    owner = _recipient_id(getattr(manager, 'telegram_user_id', None))
    if manager is None or not owner:
        return
    with _lock:
        queue_identity = _queue_identity(manager)
    process_owner = current_owner()
    params = case((func.json_valid(Chat.params) == 1, Chat.params), else_="{}")
    with get_session() as db:
        chat_ids = [chat_id for (chat_id,) in db.query(Chat.id).filter(
            Chat.parent_id.is_(None), func.json_type(params, f"$.{_KEY}.pending") != "null").all()]
    for chat_id in chat_ids:
        chunk = _prepare_chunk(chat_id, owner, queue_identity, process_owner)
        if chunk is None:
            continue
        text, token = chunk
        try:
            accepted = manager.send_to_telegram(text, interaction_token=token) is True
        except Exception:
            accepted = False
        if not accepted:
            # Another writer may have appended a stage or resolved the claim
            # during enqueue. Release only this still-queued attempt.
            _change_queued_attempt(token, lambda pending, _awaiting: pending.pop('awaiting'),
                                   queue_id=queue_identity, expected_state='queued')


def _prepare_chunk(chat_id, owner, queue_identity, process_owner):
    with _lock, get_session() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chat = db.get(Chat, chat_id)
        if chat is None or chat.parent_id is not None:
            return None
        receipt = _chat_params(chat.params).get(_KEY, {})
        if not valid_delivery_receipt(receipt):
            logger.warning('Thread %s has invalid Telegram delivery state; other threads remain deliverable.', chat.id)
            return None
        pending = receipt.get('pending')
        if not pending:
            return None
        # Bind a legacy/offline receipt once. A reconnect cannot redirect it.
        changed = not receipt.get('recipient_id')
        if changed:
            receipt['recipient_id'] = owner
        if receipt['recipient_id'] != owner:
            if pending.get('delivery_error') != _RECIPIENT_CHANGED:
                pending['delivery_error'] = _RECIPIENT_CHANGED
                _write_receipt(db, chat_id, receipt)
            return None
        if pending.get('delivery_error') == _RECIPIENT_CHANGED:
            pending.pop('delivery_error')
            changed = True
        now = time.time()
        if pending.get('relay_retry_at', 0) > now:
            if changed:
                _write_receipt(db, chat_id, receipt)
            return None
        awaiting = pending.get('awaiting')
        if awaiting and awaiting.get('state') == 'unconfirmed' and awaiting.get('error') == _LEGACY_PRETRANSPORT_RECIPIENT_ERROR:
            pending.pop('awaiting')
            awaiting = None
            changed = True
        if awaiting:
            state = awaiting['state']
            recorded_owner = awaiting.get('owner')
            same_process = awaiting.get('process') == _PROCESS and recorded_owner in (None, process_owner)
            owner_gone = not same_process and (recorded_owner is None or owner_is_gone(recorded_owner))
            if state == 'sending' and (owner_gone or now - awaiting.get('at', 0) > _ACK_TIMEOUT):
                awaiting.update(state='unconfirmed', error='Telegram delivery was not confirmed. Check the conversation before retrying.')
                changed = True
            still_queued = (same_process and awaiting.get('queue_id') == queue_identity) or (not same_process and not owner_gone)
            if state != 'queued' or still_queued:
                if changed:
                    _write_receipt(db, chat_id, receipt)
                return None
        text = pending['text']
        offset = pending.get('offset', 0)
        if offset >= len(text):
            if changed:
                _write_receipt(db, chat_id, receipt)
            return None
        # A fresh attempt ID keeps callbacks from retired queues from touching
        # a new claim at the same offset. Only a provider ACK advances it.
        key_digest = hashlib.sha256(pending['key'].encode()).hexdigest()[:12]
        token = f"proposal-update:{chat_id}:{key_digest}:{offset}:{uuid4().hex[:12]}"
        end = _chunk_end(text, offset)
        pending.pop('relay_retry_at', None)
        pending['awaiting'] = {'token': token, 'end': end, 'state': 'queued', 'process': _PROCESS,
                               'owner': process_owner, 'queue_id': queue_identity, 'at': now}
        _write_receipt(db, chat_id, receipt)
        return text[offset:end], token


def _change_delivery(token, change):
    try:
        chat_id = int(str(token).split(':')[1]) if str(token).startswith('proposal-update:') else None
    except (ValueError, IndexError):
        return False
    if not chat_id:
        return False
    with _lock, get_session() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chat = db.get(Chat, chat_id)
        if chat is None:
            return False
        receipt = _chat_params(chat.params).get(_KEY, {})
        if not valid_delivery_receipt(receipt):
            return False
        pending = receipt.get('pending') or {}
        awaiting = pending.get('awaiting') or {}
        if awaiting.get('token') != token:
            return False
        if change(receipt, pending, awaiting) is False:
            return False
        _write_receipt(db, chat_id, receipt)
    return True


def mark_delivery_attempt(token, *, queue_id=None, recipient_id=None):
    def claim(_receipt, _pending, awaiting):
        if awaiting.get('state') != 'queued' or awaiting.get('process') != _PROCESS:
            return False
        if queue_id is not None and awaiting.get('queue_id') != queue_id:
            return False
        if recipient_id is not None and _receipt.get('recipient_id') != recipient_id:
            return False
        awaiting.update(state='sending', at=time.time())
    return _change_delivery(token, claim)


def _change_queued_attempt(token, change, *, queue_id, expected_state):
    def apply(_receipt, pending, awaiting):
        if not queue_id or awaiting.get('queue_id') != queue_id or awaiting.get('process') != _PROCESS:
            return False
        if awaiting.get('state') != expected_state:
            return False
        change(pending, awaiting)
    return _change_delivery(token, apply)


def mark_delivery_not_sent(token, *, queue_id):
    return _change_queued_attempt(token, lambda _pending, awaiting: awaiting.update(state='queued', at=time.time()),
                                  queue_id=queue_id, expected_state='sending')


def mark_delivery_recipient_changed(token, *, queue_id):
    def discard(pending, _awaiting):
        pending.pop('awaiting')
        pending['delivery_error'] = _RECIPIENT_CHANGED

    return _change_queued_attempt(token, discard, queue_id=queue_id, expected_state='queued')


def defer_rate_limited_delivery(token):
    """Release a matching attempt the relay rejected before provider dispatch."""
    def defer(_receipt, pending, awaiting):
        if awaiting.get('state') == 'queued' or awaiting.get('error') == _LEGACY_PRETRANSPORT_RECIPIENT_ERROR:
            return False
        pending.pop('awaiting')
        pending['relay_retry_at'] = time.time() + _RELAY_RATE_LIMIT_WAIT

    return _change_delivery(token, defer)


def record_delivery(token, *, telegram_message_id=None, telegram_chat_id=None, error=''):
    acknowledged_id = _recipient_id(telegram_message_id)
    acknowledged_recipient = _recipient_id(telegram_chat_id)
    confirmed = False

    def finish(receipt, pending, awaiting):
        nonlocal confirmed
        if awaiting.get('state') == 'queued' or awaiting.get('error') == _LEGACY_PRETRANSPORT_RECIPIENT_ERROR:
            return False
        if error or acknowledged_id is None:
            awaiting.update(state='unconfirmed', error=error or 'Telegram did not return a delivery identity.')
            return
        if acknowledged_recipient is None or receipt.get('recipient_id') not in (None, acknowledged_recipient):
            awaiting.update(state='unconfirmed', error='Telegram acknowledgment is missing a matching recipient. Reconcile the original conversation before retrying.')
            return
        # The provider's acknowledged chat also binds legacy in-flight receipts.
        receipt['recipient_id'] = acknowledged_recipient
        confirmed = True
        pending['offset'] = awaiting['end']
        pending.pop('awaiting', None)
        if pending['offset'] >= len(pending['text']):
            backlog = receipt.get('backlog') or []
            # Preserve the legacy last-sent key when first extending its receipt.
            delivered = dict.fromkeys([*receipt.get('delivered_keys', []), receipt.get('sent'), pending['key']])
            receipt['delivered_keys'] = [key for key in delivered if isinstance(key, str)][-_DELIVERED_HISTORY_LIMIT:]
            receipt.update(sent=pending['key'], pending=backlog.pop(0) if backlog else None)
            if backlog:
                receipt['backlog'] = backlog
            else:
                receipt.pop('backlog', None)
    changed = _change_delivery(token, finish)
    if changed and confirmed:
        flush_execution_updates()
    return changed
