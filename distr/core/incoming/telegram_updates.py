"""Owner-facing execution updates for proposals explicitly approved in Telegram."""
from __future__ import annotations

import json
import hashlib
import threading
import time
import logging
from functools import partial
from sys import float_info
from uuid import uuid4

from sqlalchemy import case, func, update

from distr.core.chat import _chat_params
from distr.core.db import Chat, get_session
from distr.core.integrations.telegram.receipt_writes import flush_pending_receipt_writes, persist_or_defer_receipt_write
from distr.core.planning.ownership import current_owner, owner_is_gone

_lock = threading.RLock()
_KEY = "telegram_proposal_update"
_PROCESS = uuid4().hex
ACK_TIMEOUT_SECONDS = 120
_RELAY_RATE_LIMIT_WAIT = 60
_RECEIPT_LOOKUP_INTERVAL = 60
_DELIVERED_HISTORY_LIMIT = 64
_RECIPIENT_CHANGED = 'Telegram recipient changed. Review this retained delivery before sending to another owner.'
_RECIPIENT_UNVERIFIED = 'The original Telegram approval chat could not be verified. Review the proposal before sending this result.'
_APPROVAL_RECIPIENT_CONFLICT = 'The saved Telegram delivery and proposal approval name different recipients. Review both records before sending.'
_LEGACY_PRETRANSPORT_RECIPIENT_ERROR = 'Telegram recipient changed before transport. Review this retained delivery before retrying.'
logger = logging.getLogger(__name__)
_STAGES = {
    "development": "Work started", "testing": "Running tests", "refinement": "Refining the change",
    "retesting": "Retesting", "verification": "Checking the result", "blocked": "Needs your attention",
    "done": "Completed", "failed": "Work could not be completed",
}
_LOCAL_OUTCOMES = {"completed", "completed_noop", "failed", "blocked", "cancelled"}
_PUBLIC_STATUS = {
    'queued': 'Telegram update queued.',
    'sending': 'Telegram update sending.',
    'retrying': 'Telegram update waiting to retry.',
    'unconfirmed': 'Telegram update unconfirmed. Check the original Telegram conversation before sending again.',
    'held': 'Telegram update on hold. Verify the original Telegram approval chat.',
    'sent': 'Last Telegram update sent.',
    'unavailable': 'Telegram update status unavailable.',
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
            lookup_at = awaiting.get('lookup_at', 0)
            if type(lookup_at) not in (int, float) or not 0 <= lookup_at <= float_info.max:
                return False
    return True


def _write_receipt(db, chat_id, receipt, *, handoff=None):
    # Execution can advance while Telegram enqueue is in progress. Patch only
    # our JSON member in the local SQLite store rather than replacing a stale
    # copy of all chat parameters (which also holds the execution state).
    params = case((func.json_valid(Chat.params) == 1, Chat.params), else_="{}")
    params = func.json_set(params, f"$.{_KEY}", func.json(json.dumps(receipt)))
    if handoff is not None:
        params = func.json_set(params, "$.incoming_proposal_handoff", func.json(json.dumps(handoff)))
    db.execute(
        update(Chat).where(Chat.id == int(chat_id)).values(params=params).execution_options(synchronize_session=False)
    )
    db.commit()


def _confirmed_delivery_keys(receipt):
    return {receipt.get('sent'), *receipt.get('delivered_keys', [])}


def _promote_pending(receipt):
    """Restore the oldest unconfirmed delivery without altering its attempt."""
    pending = receipt.get('pending')
    backlog = receipt.get('backlog')
    delivered = _confirmed_delivery_keys(receipt)
    if pending is not None:
        if pending.get('key') not in delivered:
            return False
        receipt['pending'] = None
    elif not backlog:
        return False
    while backlog and backlog[0]['key'] in delivered:
        backlog.pop(0)
    if backlog:
        receipt['pending'] = backlog.pop(0)
    if not backlog:
        receipt.pop('backlog', None)
    return True


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


def retain_local_proposal_outcome(params, execution):
    """Save the exact approved result alongside the terminal execution."""
    status = execution.get("status")
    if not isinstance(status, str) or status not in _LOCAL_OUTCOMES:
        return
    handoff = params.get("incoming_proposal_handoff")
    approval = handoff.get("telegram_approval") if isinstance(handoff, dict) else None
    job_id = execution.get("job_id")
    if not isinstance(approval, dict) or not job_id or approval.get("job_id") != job_id:
        return
    recipient = _recipient_id(approval.get("chat_id"))
    proposal = handoff.get("proposal")
    if recipient is None or not isinstance(proposal, dict):
        return
    from distr.core.tensology_client import _remote_execution_status

    review = execution.get("outcome")
    if isinstance(review, dict) and not execution.get("verification"):
        execution = {**execution, "verification": review.get("verification")}
    stage = "blocked" if status == "blocked" else _remote_execution_status(status, execution)
    approval["outcome"] = {
        "stage": stage,
        "text": _execution_update_text(proposal, execution, stage),
    }


def _local_outcome(approval):
    outcome = approval.get("outcome") if isinstance(approval, dict) else None
    if not isinstance(outcome, dict):
        return
    recipient = _recipient_id(approval.get("chat_id"))
    job_id = approval.get("job_id")
    stage = outcome.get("stage")
    message = outcome.get("text")
    if (recipient is None or not isinstance(job_id, str) or not job_id
            or not isinstance(stage, str) or stage not in _STAGES or not isinstance(message, str) or not message):
        return
    return job_id, stage, message, recipient


def _previous_approvals(handoff):
    previous = handoff.get("telegram_previous_approvals")
    return previous if isinstance(previous, list) else []


def retain_previous_proposal_approval(handoff, approval, new_job_id):
    if _local_outcome(approval) and approval["job_id"] != new_job_id:
        handoff["telegram_previous_approvals"] = [*_previous_approvals(handoff), approval]


def queue_local_proposal_outcome(chat_id, params):
    handoff = params.get("incoming_proposal_handoff")
    if not isinstance(handoff, dict):
        return
    previous = _previous_approvals(handoff)
    for index, approval in enumerate([*previous, handoff.get("telegram_approval")]):
        outcome = _local_outcome(approval)
        if not outcome:
            continue
        job_id, stage, message, recipient = outcome
        receipt = params.get(_KEY, {})
        if (index == len(previous) and valid_delivery_receipt(receipt)
                and receipt.get("recipient_id") == recipient and f"{job_id}:{stage}" in _delivery_keys(receipt)):
            continue
        _queue_execution_update(chat_id, job_id, stage, message, recipient, local_approval=approval)


def sync_local_proposal_outcomes():
    """Recover a terminal result if the desktop stopped before queuing it."""
    params = case((func.json_valid(Chat.params) == 1, Chat.params), else_="{}")
    with get_session() as db:
        rows = db.query(Chat.id, Chat.params).filter(
            Chat.parent_id.is_(None),
            (func.json_type(params, "$.incoming_proposal_handoff.telegram_approval.outcome") == "object")
            | (func.json_array_length(params, "$.incoming_proposal_handoff.telegram_previous_approvals") > 0),
        ).all()
    for chat_id, raw in rows:
        queue_local_proposal_outcome(chat_id, _chat_params(raw))


def queue_execution_update(ticket, chat_id, execution, status):
    metadata = ticket.get("source_metadata") or {}
    if not isinstance(metadata, dict) or metadata.get("approval_source") != "telegram":
        return
    if status not in _STAGES:
        return
    job = str(execution.get("job_id") or execution.get("id") or "run")
    _queue_execution_update(chat_id, job, status, _execution_update_text(ticket, execution, status),
                            _recipient_id(metadata.get("approval_telegram_chat_id")))


def _execution_update_text(ticket, execution, status):
    header = f"{ticket.get('project_name') or 'Project'}: {ticket.get('reference') or ticket.get('title') or 'Ticket'}"
    message = f"{header}\n{_STAGES[status]}."
    if status in {"done", "failed", "blocked"}:
        packet = execution.get("output_packet")
        packet = packet if isinstance(packet, dict) else {}
        public_result = packet.get("human_summary") if status == "done" else ""
        response = str(public_result or packet.get("output") or packet.get("summary") or execution.get("worker_question") or execution.get("summary") or execution.get("error") or "").strip()
        if status == "failed" and str(execution.get("status") or "") in {"completed", "completed_noop"}:
            message += " Final verification evidence is missing. Review the result before retrying."
        if response:
            message += f"\n\n{response}"
    return message


def _delivery_keys(receipt):
    return {*_confirmed_delivery_keys(receipt), (receipt.get("pending") or {}).get("key"),
            *(item["key"] for item in receipt.get("backlog", []))}


def _sending_expired(awaiting, now):
    return awaiting.get('state') == 'sending' and awaiting.get('at', now) + ACK_TIMEOUT_SECONDS <= now


def _mark_ack_timeout(awaiting):
    awaiting.update(state='unconfirmed', local_timeout=True,
                    error='Telegram delivery was not confirmed. Check the conversation before retrying.')


def public_proposal_update_status(params):
    """Describe thread delivery without exposing private receipt data."""
    receipt = params.get(_KEY, {})
    if not valid_delivery_receipt(receipt):
        return {'state': 'unavailable', 'message': _PUBLIC_STATUS['unavailable']}
    delivered = _confirmed_delivery_keys(receipt)
    head = receipt.get('pending')
    if head is None or head.get('key') in delivered:
        head = next((item for item in receipt.get('backlog', []) if item.get('key') not in delivered), None)
    if head:
        attempt = head.get('awaiting') or {}
        awaiting = attempt.get('state')
        held = not receipt.get('recipient_id') or bool(head.get('delivery_error'))
        if awaiting == 'unconfirmed' or (awaiting == 'sending' and (held or _sending_expired(attempt, time.time()))):
            state = 'unconfirmed'
        elif held:
            state = 'held'
        elif awaiting == 'sending':
            state = 'sending'
        else:
            state = 'retrying' if head.get('relay_retry_at') else 'queued'
    else:
        handoff = params.get('incoming_proposal_handoff')
        approvals = [*_previous_approvals(handoff), handoff.get('telegram_approval')] if isinstance(handoff, dict) else []
        if any(f'{outcome[0]}:{outcome[1]}' not in delivered
               for approval in approvals if (outcome := _local_outcome(approval))):
            state = 'queued'
        elif receipt.get('sent') or receipt.get('delivered_keys'):
            state = 'sent'
        else:
            return None
    return {'state': state, 'message': _PUBLIC_STATUS[state]}


def _queue_execution_update(chat_id, job, status, message, approved_owner, *, local_approval=None):
    manager = _manager()
    configured_owner = None if approved_owner else (
        _recipient_id(getattr(manager, 'telegram_user_id', None)) or _configured_recipient())
    with _lock, get_session() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chat = db.get(Chat, int(chat_id))
        if chat is None:
            return
        params = _chat_params(chat.params)
        handoff = params.get("incoming_proposal_handoff")
        saved_approval = handoff.get("telegram_approval") if isinstance(handoff, dict) else None
        if local_approval is None and saved_approval is not None:
            return
        previous_index = None
        if local_approval is not None:
            if (not isinstance(saved_approval, dict) or chat.project_id != local_approval.get("project_id")
                    or _recipient_id(saved_approval.get("chat_id")) != approved_owner):
                return
            previous = _previous_approvals(handoff)
            # Recheck the head under the write lock. A stale poll cannot skip an
            # older approved result or send it again after it enters the queue.
            previous_index = next((index for index, approval in enumerate(previous)
                                   if _local_outcome(approval) and approval.get("project_id") == chat.project_id
                                   and _recipient_id(approval.get("chat_id")) == approved_owner), None)
            expected = saved_approval if previous_index is None else previous[previous_index]
            if local_approval != expected:
                return
        key = f"{job}:{status}"
        receipt = params.get(_KEY, {})
        if not valid_delivery_receipt(receipt):
            logger.warning('Thread %s has invalid Telegram delivery state; reconcile it before sending.', chat_id)
            return
        changed = _promote_pending(receipt)
        pending = receipt.get('pending') or {}
        backlog = receipt.get('backlog') or []
        retained = ([pending] if pending else []) + backlog
        owner = approved_owner or (configured_owner if not retained else None)
        if owner and not receipt.get('recipient_id'):
            receipt['recipient_id'] = owner
            changed = True
        conflict = approved_owner is not None and receipt.get('recipient_id') != approved_owner
        if conflict:
            for delivery in retained:
                if delivery.get('delivery_error') != _APPROVAL_RECIPIENT_CONFLICT:
                    delivery['delivery_error'] = _APPROVAL_RECIPIENT_CONFLICT
                    changed = True
        known_keys = _delivery_keys(receipt)
        saved_handoff = None
        if previous_index is not None and not conflict:
            remaining = previous[:previous_index] + previous[previous_index + 1:]
            if remaining:
                handoff["telegram_previous_approvals"] = remaining
            else:
                handoff.pop("telegram_previous_approvals")
            saved_handoff = handoff
        terminal_keys = {job + ':done', job + ':failed'}
        if key in known_keys or (status not in {'done', 'failed'} and known_keys & terminal_keys):
            if changed or saved_handoff is not None:
                _write_receipt(db, chat_id, receipt, handoff=saved_handoff)
            return  # A delayed intermediate stage cannot undo the final result.
        update = {"key": key, "text": message, "offset": 0}
        if conflict:
            update['delivery_error'] = _APPROVAL_RECIPIENT_CONFLICT
        elif not receipt.get('recipient_id'):
            update['delivery_error'] = _RECIPIENT_UNVERIFIED
        # Never erase a final result with an unresolved delivery, including
        # when a newer job starts. Intermediate statuses may still coalesce.
        if pending and (backlog or pending.get('awaiting') or pending.get('offset', 0) or pending.get('relay_retry_at')
                        or str(pending.get('key') or '').rsplit(':', 1)[-1] in {'done', 'failed'}
                        or (local_approval is not None and pending['key'].endswith(':blocked'))):
            # Only untouched, unqueued backlog stages can coalesce. Retain any
            # transport receipt until its ACK can reconcile the exact chunk.
            backlog = [item for item in backlog if not (
                item['key'].rsplit(':', 1)[0] == job and item['key'].rsplit(':', 1)[-1] not in {'done', 'failed'}
                and not item.get('awaiting') and not item.get('offset', 0))]
            backlog.append(update)
            receipt['backlog'] = backlog
        else:
            receipt["pending"] = update
        _write_receipt(db, chat_id, receipt, handoff=saved_handoff)
    flush_execution_updates()


def flush_execution_updates():
    if not flush_pending_receipt_writes():
        return

    manager = _manager()
    owner = _recipient_id(getattr(manager, 'telegram_user_id', None))
    with _lock:
        queue_identity = _queue_identity(manager) if owner else None
    process_owner = current_owner() if owner else None
    params = case((func.json_valid(Chat.params) == 1, Chat.params), else_="{}")
    pending = f"$.{_KEY}.pending"
    candidates = (func.json_type(params, pending) == "object") | (func.json_array_length(params, f"$.{_KEY}.backlog") > 0)
    if not owner:
        candidates = (func.json_extract(params, f"{pending}.awaiting.state") == "sending") & (
            func.json_extract(params, f"{pending}.awaiting.at") <= time.time() - ACK_TIMEOUT_SECONDS)
    with get_session() as db:
        chat_ids = [chat_id for (chat_id,) in db.query(Chat.id).filter(
            Chat.parent_id.is_(None), candidates,
        ).all()]
    for chat_id in chat_ids:
        chunk = _prepare_chunk(chat_id, owner, queue_identity, process_owner,
                               lookup_supported=getattr(manager, '_proposal_receipt_recovery', False) is True)
        if chunk is None:
            continue
        text, token = chunk
        if text is None:
            try:
                manager.request_proposal_receipt(token)
            except Exception:
                logger.warning('Telegram proposal receipt lookup could not be requested.', exc_info=True)
            continue
        try:
            accepted = manager.send_to_telegram(text, interaction_token=token) is True
        except Exception:
            accepted = False
        if not accepted:
            # Another writer may have appended a stage or resolved the claim
            # during enqueue. Release only this still-queued attempt.
            release = partial(_change_queued_attempt, token, lambda pending, _awaiting: pending.pop('awaiting'),
                              queue_id=queue_identity, expected_state='queued')
            if persist_or_defer_receipt_write(release) is None:
                logger.warning('Telegram proposal queue claim will be released after the database recovers.')


def _prepare_chunk(chat_id, owner, queue_identity, process_owner, *, lookup_supported=False):
    with _lock, get_session() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        chat = db.get(Chat, chat_id)
        if chat is None or chat.parent_id is not None:
            return None
        receipt = _chat_params(chat.params).get(_KEY, {})
        if not valid_delivery_receipt(receipt):
            logger.warning('Thread %s has invalid Telegram delivery state; other threads remain deliverable.', chat.id)
            return None
        changed = _promote_pending(receipt)
        pending = receipt.get('pending')
        if not pending:
            if changed:
                _write_receipt(db, chat_id, receipt)
            return None
        now = time.time()
        awaiting = pending.get('awaiting') or {}
        if _sending_expired(awaiting, now):
            _mark_ack_timeout(awaiting)
            changed = True
        if not owner:
            if changed:
                _write_receipt(db, chat_id, receipt)
            return None
        if not receipt.get('recipient_id'):
            awaiting = pending.get('awaiting') or {}
            if awaiting.get('state') not in ('sending', 'unconfirmed') and pending.get('delivery_error') != _RECIPIENT_UNVERIFIED:
                pending['delivery_error'] = _RECIPIENT_UNVERIFIED
                changed = True
            if changed:
                _write_receipt(db, chat_id, receipt)
            return None
        if pending.get('delivery_error') == _APPROVAL_RECIPIENT_CONFLICT:
            if changed:
                _write_receipt(db, chat_id, receipt)
            return None
        if receipt['recipient_id'] != owner:
            if changed or pending.get('delivery_error') != _RECIPIENT_CHANGED:
                pending['delivery_error'] = _RECIPIENT_CHANGED
                _write_receipt(db, chat_id, receipt)
            return None
        if pending.get('delivery_error') in (_RECIPIENT_CHANGED, _RECIPIENT_UNVERIFIED):
            pending.pop('delivery_error')
            changed = True
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
            if state == 'sending' and (owner_gone or 'at' not in awaiting):
                _mark_ack_timeout(awaiting)
                changed = True
            if (lookup_supported and awaiting['state'] == 'unconfirmed'
                    and awaiting.get('lookup_at', 0) <= now - _RECEIPT_LOOKUP_INTERVAL):
                awaiting['lookup_at'] = now
                _write_receipt(db, chat_id, receipt)
                return None, awaiting['token']
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
        _promote_pending(receipt)
        pending = receipt.get('pending') or {}
        awaiting = pending.get('awaiting') or {}
        if awaiting.get('token') != token:
            return False
        if change(receipt, pending, awaiting) is False:
            return False
        _write_receipt(db, chat_id, receipt)
    return True


def mark_delivery_attempt(token, *, queue_id=None, recipient_id=None, attempt_id=None):
    def claim(_receipt, pending, awaiting):
        if not _receipt.get('recipient_id') or pending.get('delivery_error') == _APPROVAL_RECIPIENT_CONFLICT:
            return False
        if awaiting.get('state') != 'queued' or awaiting.get('process') != _PROCESS:
            return False
        if queue_id is not None and awaiting.get('queue_id') != queue_id:
            return False
        if recipient_id is not None and _receipt.get('recipient_id') != recipient_id:
            return False
        awaiting.update(state='sending', at=time.time())
        if attempt_id is not None:
            awaiting['attempt_id'] = attempt_id
    return _change_delivery(token, claim)


def _change_queued_attempt(token, change, *, queue_id, expected_state):
    def apply(_receipt, pending, awaiting):
        if not queue_id or awaiting.get('queue_id') != queue_id or awaiting.get('process') != _PROCESS:
            return False
        if awaiting.get('state') != expected_state:
            return False
        change(pending, awaiting)
    return _change_delivery(token, apply)


def mark_delivery_not_sent(token, *, queue_id, attempt_id):
    """Release the exact socket write that was refused before transport."""
    def release(_receipt, _pending, awaiting):
        state = awaiting.get('state')
        recoverable = state == 'sending' or (state == 'unconfirmed' and awaiting.get('local_timeout') is True)
        if (not queue_id or not attempt_id or awaiting.get('queue_id') != queue_id
                or awaiting.get('attempt_id') != attempt_id or not recoverable):
            return False
        awaiting.pop('attempt_id')
        awaiting.pop('local_timeout', None)
        awaiting.pop('error', None)
        awaiting.update(state='queued', at=time.time())

    return _change_delivery(token, release)


def accept_socket_refusal(token, *, queue_id, attempt_id):
    return persist_or_defer_receipt_write(
        partial(mark_delivery_not_sent, token, queue_id=queue_id, attempt_id=attempt_id),
        journal_entry=("proposal-socket-refused", token, queue_id, attempt_id),
    )


def mark_delivery_recipient_changed(token, *, queue_id):
    def discard(pending, _awaiting):
        pending.pop('awaiting')
        pending['delivery_error'] = _RECIPIENT_CHANGED

    return _change_queued_attempt(token, discard, queue_id=queue_id, expected_state='queued')


def defer_rate_limited_delivery(token, *, received_at=None):
    """Release a matching attempt the relay rejected before provider dispatch."""
    retry_at = (time.time() if received_at is None else received_at) + _RELAY_RATE_LIMIT_WAIT

    def defer(_receipt, pending, awaiting):
        if awaiting.get('state') == 'queued' or awaiting.get('error') == _LEGACY_PRETRANSPORT_RECIPIENT_ERROR:
            return False
        pending.pop('awaiting')
        pending['relay_retry_at'] = retry_at

    return _change_delivery(token, defer)


def accept_relay_receipt(token, *, telegram_message_id=None, telegram_chat_id=None, error='', rate_limited=False):
    """Keep an exact relay callback until the local receipt can be written."""
    if rate_limited:
        received_at = time.time()
        persist = partial(defer_rate_limited_delivery, token, received_at=received_at)
        journal_entry = ("proposal-rate-limit", token, received_at)
    else:
        persist = partial(record_delivery, token, telegram_message_id=telegram_message_id,
                          telegram_chat_id=telegram_chat_id, error=error, flush=False)
        message_id = _recipient_id(telegram_message_id)
        chat_id = _recipient_id(telegram_chat_id)
        if not error and message_id is not None and chat_id is not None:
            journal_entry = (token, message_id, chat_id)
        else:
            journal_entry = ("proposal-unconfirmed", token)
    changed = persist_or_defer_receipt_write(persist, journal_entry=journal_entry)
    if changed is None:
        logger.warning('Telegram proposal receipt will be saved again after the database recovers.')
    elif changed and not (rate_limited or error):
        flush_execution_updates()


def record_delivery(token, *, telegram_message_id=None, telegram_chat_id=None, error='', flush=True):
    acknowledged_id = _recipient_id(telegram_message_id)
    acknowledged_recipient = _recipient_id(telegram_chat_id)
    confirmed = False

    def finish(receipt, pending, awaiting):
        nonlocal confirmed
        if awaiting.get('state') == 'queued' or awaiting.get('error') == _LEGACY_PRETRANSPORT_RECIPIENT_ERROR:
            return False
        awaiting.pop('local_timeout', None)
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
            # Preserve the legacy last-sent key when first extending its receipt.
            delivered = dict.fromkeys([*receipt.get('delivered_keys', []), receipt.get('sent'), pending['key']])
            receipt['delivered_keys'] = [key for key in delivered if isinstance(key, str)][-_DELIVERED_HISTORY_LIMIT:]
            receipt.update(sent=pending['key'], pending=None)
            _promote_pending(receipt)
    changed = _change_delivery(token, finish)
    if changed and confirmed and flush:
        flush_execution_updates()
    return changed
