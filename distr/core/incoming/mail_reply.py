"""Send Incoming Quick Reply mail through Gmail or Tensology Mailshot."""
from __future__ import annotations

from typing import Any
from uuid import uuid4
import hashlib
import json


class MailReplyUnconfirmed(RuntimeError):
    def __init__(self, status='unconfirmed'):
        self.receipt_status = status
        super().__init__('Send was not confirmed. Check the original conversation before sending again; your text is retained.')


class MailReplyPreflightError(ValueError):
    """Quick Reply stopped before claiming or attempting a provider send."""


def incoming_mail_reply_status(key: str) -> str:
    """Read the exact local receipt without contacting the provider."""
    from distr.core.db import IncomingMailReplyReceipt, get_session

    with get_session() as db:
        row = db.get(IncomingMailReplyReceipt, key)
        if row is None:
            return 'missing'
        return row.status if row.status in {'sent', 'sending', 'unconfirmed'} else 'unconfirmed'


def _receipt_result(row, fingerprint):
    if row.fingerprint != fingerprint:
        raise ValueError('This reply key belongs to different reviewed text. Check the original conversation before starting another reply.')
    if row.status == 'sent':
        return json.loads(row.payload)['result']
    raise MailReplyUnconfirmed(row.status)


def _existing_receipt(db, key, fingerprint):
    from distr.core.db import IncomingMailReplyReceipt
    return db.get(IncomingMailReplyReceipt, key) or db.query(IncomingMailReplyReceipt).filter(
        IncomingMailReplyReceipt.fingerprint == fingerprint,
        IncomingMailReplyReceipt.status.in_(['sending', 'unconfirmed']),
    ).first()


def _send_once(preview, key, deliver):
    from distr.core.db import IncomingMailReplyReceipt, get_session
    fingerprint = hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
    with get_session() as db:
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
        row = _existing_receipt(db, key, fingerprint)
        if row:
            return _receipt_result(row, fingerprint)
        db.add(IncomingMailReplyReceipt(id=key, fingerprint=fingerprint,
            payload=json.dumps({'preview': preview}), status='sending'))
        db.commit()
    try:
        result = deliver()
    except Exception as exc:
        with get_session() as db:
            db.get(IncomingMailReplyReceipt, key).status = 'unconfirmed'
            db.commit()
        raise MailReplyUnconfirmed() from exc
    with get_session() as db:
        row = db.get(IncomingMailReplyReceipt, key)
        row.payload = json.dumps({'preview': preview, 'result': result})
        row.status = 'sent'
        db.commit()
    return result


def mailshot_send_confirmed(result: Any) -> bool:
    return isinstance(result, dict) and not result.get('error') and result.get('success') is not False and bool(
        result.get('success') is True or result.get('id') or result.get('_id') or result.get('message_id') or result.get('messageId'))


def send_incoming_mail_reply(
    *,
    channel: str,
    to: str,
    subject: str,
    body: str,
    message_id: str = "",
    dry_run: bool = False,
    idempotency_key: str = "",
) -> dict[str, Any]:
    """Reply/send via the matching channel. dry_run never contacts providers."""
    channel_key = str(channel or "").strip().lower()
    if channel_key in {"mailshake", "mailshot", "tensology"}:
        channel_key = "mailshot"
    elif channel_key in {"gmail", "google", "email"}:
        channel_key = "gmail"
    else:
        raise ValueError("channel must be gmail or mailshot")

    recipient = str(to or "").strip()
    text = str(body or "")
    subject_text = str(subject or "").strip() or "Re:"
    mid = str(message_id or "").strip()
    if not recipient:
        raise ValueError("A recipient is required.")
    if not text.strip():
        raise ValueError("Reply text is required.")

    preview = {
        "channel": channel_key,
        "to": recipient,
        "subject": subject_text,
        "body": text,
        "message_id": mid,
    }
    if dry_run:
        return {"success": True, "dry_run": True, **preview}
    key = idempotency_key or uuid4().hex
    # Replays must not contact providers or depend on their current connectivity.
    from distr.core.db import get_session
    fingerprint = hashlib.sha256(json.dumps(preview, sort_keys=True).encode()).hexdigest()
    with get_session() as db:
        receipt = _existing_receipt(db, key, fingerprint)
        if receipt:
            return _receipt_result(receipt, fingerprint)

    if channel_key == "gmail":
        from distr.core.agent.services.integrations.google_workspace import (
            GMAIL_SEND_URL, GmailReplyPreparationError, GoogleWorkspaceConnector, gmail_send_confirmed,
        )

        if not mid:
            raise MailReplyPreflightError('Nothing was sent. The original Gmail message ID is missing. Refresh Incoming and try again.')
        try:
            connector = GoogleWorkspaceConnector()
            if not connector.is_connected():
                raise GmailReplyPreparationError('Gmail is not connected in Settings.')
            payload = connector.prepare_email_reply(mid, text, to=recipient, subject=subject_text, require_reference=True)
        except GmailReplyPreparationError as exc:
            raise MailReplyPreflightError(f'Nothing was sent. {exc}') from None
        except Exception:
            raise MailReplyPreflightError('Nothing was sent. Gmail preparation failed. Check the connection and retry.') from None

        def deliver_gmail():
            result = connector._make_request('POST', GMAIL_SEND_URL, json=payload, timeout=30)
            if not gmail_send_confirmed(result, thread_id=payload['threadId']):
                raise RuntimeError('Gmail did not confirm the reply.')
            return {"success": True, "dry_run": False, **preview}
        return _send_once(preview, key, deliver_gmail)

    # Mailshot / Tensology mail
    from distr.core.tensology_client import configured_tensology_client

    client = configured_tensology_client(source="decisionsai-incoming-quick-reply")
    payload = {
        "to": recipient,
        "subject": subject_text,
        "body": text,
        "body_type": "plain",
    }
    if mid:
        detail = client.get(f'mail/messages/{mid}') or {}
        original = detail.get('email') if isinstance(detail, dict) and isinstance(detail.get('email'), dict) else detail
        if not isinstance(original, dict):
            raise ValueError('The original email is unavailable. Refresh before replying.')
        rfc_id = str(original.get('messageId') or original.get('message_id_header') or '').strip()
        if not rfc_id:
            raise ValueError('The original email reply reference is unavailable. Refresh before replying.')
        references = original.get('references') or []
        if isinstance(references, str):
            references = references.replace(',', ' ').split()
        payload['inReplyTo'] = rfc_id
        payload['references'] = ','.join(dict.fromkeys([*references, rfc_id]))
    def deliver_mailshot():
        result = client.post("mail/send", payload, approved=True, idempotency_key=key)
        if not mailshot_send_confirmed(result):
            raise RuntimeError('Mailshot did not confirm the reply.')
        return {"success": True, "dry_run": False, "provider_result": result, **preview}
    return _send_once(preview, key, deliver_mailshot)
