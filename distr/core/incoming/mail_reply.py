"""Send Incoming Quick Reply mail through Gmail or Tensology Mailshot."""
from __future__ import annotations

from typing import Any


def send_incoming_mail_reply(
    *,
    channel: str,
    to: str,
    subject: str,
    body: str,
    message_id: str = "",
    dry_run: bool = False,
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

    if channel_key == "gmail":
        from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

        connector = GoogleWorkspaceConnector()
        if hasattr(connector, "is_connected") and not connector.is_connected():
            raise RuntimeError("Gmail is not connected in Settings.")
        if mid and hasattr(connector, "reply_to_email"):
            ok = bool(connector.reply_to_email(mid, text, "plain"))
            if not ok:
                # Fall back to a plain send when the provider cannot thread the reply.
                ok = bool(connector.send_email(recipient, subject_text, text, "plain"))
        else:
            ok = bool(connector.send_email(recipient, subject_text, text, "plain"))
        if not ok:
            raise RuntimeError("Gmail did not accept the reply.")
        return {"success": True, "dry_run": False, **preview}

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
        payload["in_reply_to"] = mid
        payload["thread_id"] = mid
    result = client.post("mail/send", payload, approved=True)
    return {"success": True, "dry_run": False, "provider_result": result, **preview}
