"""Find an original conversation using the retained proposal's source identity."""
import hashlib
import json
from collections import Counter
from itertools import islice

from sqlalchemy import and_, func, or_

from distr.core.db import TelegramGroupMessage, WhatsAppMessage, get_session
from distr.core.incoming.service import (
    _email_created_at, _mail_plain_and_html, _whatsapp_incoming_item, _with_mail_preview_urls,
    telegram_incoming_item,
)
from distr.core.incoming.telegram_topics import split_telegram_source, telegram_message_source

BEFORE = 12
AFTER = 27
SOURCE_NAMES = {"gmail": "Gmail", "mailshot": "Mailshot", "whatsapp": "WhatsApp", "telegram": "Telegram"}


def submitted_proposal_source_key(chat):
    """Advertise a source action only for a root thread with a saved handoff."""
    from distr.core.chat import _chat_params
    from distr.core.incoming.proposals import _review_key_for_handoff

    receipt = _chat_params(chat.params).get("incoming_proposal_handoff")
    if chat.parent_id is not None or not chat.project_id or not isinstance(receipt, dict) or receipt.get("start_pending"):
        return None
    key, proposal = receipt.get("key"), receipt.get("proposal")
    if not isinstance(key, str) or not key or not isinstance(proposal, dict) or not _conversation_origins(proposal):
        return None
    return _review_key_for_handoff(key)


def _conversation_origins(proposal):
    origins = {}
    refs = proposal.get("source_scope")
    refs = refs if isinstance(refs, list) else []
    if not refs and proposal.get("merged_from"):
        # Older merges may only have per-message provenance. Never infer the
        # sources of a merge from its flattened top-level message IDs.
        refs = []
        for row in proposal.get("evidence") or []:
            if not isinstance(row, dict) or row.get("context_only") or row.get("direction") == "outbound":
                continue
            ref = row.get("original_source")
            if isinstance(ref, dict):
                refs.append({**ref, "message_ids": [ref.get("source_message_id")],
                             "participant": row.get("subject") or row.get("sender_name")})
    elif not refs:
        refs = [proposal]
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        conversation = ref.get("source_conversation") or {}
        if not isinstance(conversation, dict):
            conversation = {}
        source = str(ref.get("source") or ref.get("channel") or conversation.get("channel") or "").lower()
        thread = str(ref.get("source_thread_id") or conversation.get("thread_id") or "")
        raw_ids = ref.get("message_ids")
        ids = [str(value) for value in raw_ids if value] if isinstance(raw_ids, (list, tuple)) else []
        if source not in SOURCE_NAMES or not thread or thread.startswith("merged:") or not ids:
            continue
        label = ref.get("participant") or ref.get("source_label") or ""
        origin = origins.setdefault((source, thread), {"label": str(label), "ids": []})
        origin["ids"] = list(dict.fromkeys([*origin["ids"], *ids]))
    return origins


def _read_local_rows(query, timestamp, row_id, anchor, *, matches=None):
    sent_at, anchor_id = anchor
    before = query.filter(or_(timestamp < sent_at, and_(timestamp == sent_at, row_id < anchor_id)))
    after = query.filter(or_(timestamp > sent_at, and_(timestamp == sent_at, row_id >= anchor_id)))
    def page(rows, limit):
        if matches is None:
            return rows.limit(limit).all()
        return list(islice(filter(matches, rows.yield_per(128)), limit))

    return [*reversed(page(before.order_by(timestamp.desc(), row_id.desc()), BEFORE)),
            *page(after.order_by(timestamp, row_id), AFTER + 1)]


def _local_conversation(source, thread, ids):
    with get_session() as db:
        if source == "whatsapp":
            model = WhatsAppMessage
            timestamp = func.coalesce(model.whatsapp_timestamp, 0)
            query = db.query(model).filter(or_(
                model.jid == thread,
                and_(or_(model.jid.is_(None), model.jid == ""), model.jid_phone == thread),
            ))
            message_id = model.message_id
            serialize = lambda row: _whatsapp_incoming_item(row, [], {})
        else:
            model = TelegramGroupMessage
            timestamp = model.telegram_message_id
            group_id, topic = split_telegram_source(thread)
            query = db.query(model).filter(model.chat_id == group_id)
            message_id = model.telegram_message_id
            ids = [int(item) for item in ids if item.isascii() and item.isdecimal() and len(item) <= 18]
            anchors = query.filter(message_id.in_(ids)).order_by(timestamp, model.id).all()
            scopes = {telegram_message_source(row) for row in anchors}
            if not scopes or (topic is not None and scopes != {thread}):
                return [], ""
            if len(scopes) != 1:
                raise LookupError("The original messages span different Telegram topics. Open them in Telegram.")
            scope = scopes.pop()
            anchor = anchors[0]
            rows = _read_local_rows(query, timestamp, model.id, (anchor.telegram_message_id, anchor.id),
                                    matches=lambda row: telegram_message_source(row) == scope)
            # Older proposals still verify the saved group identity in the reader.
            return [{**telegram_incoming_item(row), "source_thread_id": thread} for row in rows], str(anchor.telegram_message_id)
        anchor = query.with_entities(timestamp, model.id, message_id).filter(
            message_id.in_(ids),
        ).order_by(timestamp, model.id).first()
        if anchor is None:
            return [], ""
        rows = _read_local_rows(query, timestamp, model.id, anchor[:2])
        return [serialize(row) for row in rows], str(anchor[2])


def _mail_item(source, thread, row):
    message_id = str(row["id"])
    text, _, snippet = _mail_plain_and_html(row)
    subject = row.get("subject") or "(No subject)"
    return {
        "key": f"{source}:{message_id}", "source": source,
        "source_message_id": message_id, "source_thread_id": thread,
        "conversation_label": subject, "sender": row.get("from") or "Unknown sender",
        "text": text, "snippet": snippet, "created_at": _email_created_at(row.get("date")),
        "from_me": "SENT" in (row.get("labels") or []),
        "attachments": _with_mail_preview_urls(row.get("attachments") or [], message_id=message_id, provider=source),
    }


def _mail_conversation(source, thread, ids):
    if source == "gmail":
        from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

        rows = GoogleWorkspaceConnector().get_email_thread(thread)
        if rows is None:
            raise RuntimeError("The Gmail conversation could not be checked.")
        rows = [row for row in rows if str(row.get("threadId") or "") == thread and row.get("id")]
        anchor = next((index for index, row in enumerate(rows) if str(row["id"]) in ids), None)
        if anchor is None:
            return [], ""
        window = rows[max(anchor - BEFORE, 0):anchor + AFTER + 1]
        return [_mail_item(source, thread, row) for row in window], str(rows[anchor]["id"])

    from urllib.parse import quote
    from distr.core.kanban.jira_intake import mailshot_message_to_intake_dict
    from distr.core.tensology_client import TensologyApiError, configured_tensology_client

    message_id = ids[0]
    client = configured_tensology_client(source="development-proposal-source")
    try:
        detail = client.get(f"mail/messages/{quote(message_id, safe='')}") or {}
    except TensologyApiError as error:
        if error.status != 404:
            raise
        return [], ""
    row = mailshot_message_to_intake_dict(detail.get("email") or detail)
    if row["id"] != message_id or row["thread_id"] != thread:
        return [], ""
    return [_mail_item(source, thread, row)], message_id


def read_proposal_conversation(proposal, source_key=None):
    origins = _conversation_origins(proposal)
    if not origins:
        raise LookupError("The proposal has no original conversation to open.")
    choices = []
    for (source, thread), origin in origins.items():
        key = hashlib.sha256(json.dumps([source, thread, sorted(origin["ids"])]).encode()).hexdigest()[:32]
        label = f"{SOURCE_NAMES[source]} · {origin['label']}" if origin["label"] else SOURCE_NAMES[source]
        choices.append({"key": key, "label": label, "source": source, "thread_id": thread})
    if source_key is None and (len(choices) > 1 or proposal.get("source_scope") or proposal.get("merged_from")):
        counts = Counter(choice["label"] for choice in choices)
        seen = Counter()
        for choice in choices:
            label = choice["label"]
            if counts[label] > 1:
                seen[label] += 1
                choice["label"] = f"{label} ({seen[label]} of {counts[label]})"
        return {"choices": choices}
    choice = next((choice for choice in choices if source_key is None or choice["key"] == source_key), None)
    if choice is None:
        raise LookupError("This conversation is no longer linked to the proposal.")
    source, thread = choice["source"], choice["thread_id"]
    ids = origins[(source, thread)]["ids"]
    if source in {"whatsapp", "telegram"}:
        items, anchor = _local_conversation(source, thread, ids)
    else:
        items, anchor = _mail_conversation(source, thread, ids)
    if not anchor:
        raise LookupError("The original message is no longer available in this conversation.")
    return {"source": source, "thread_id": thread, "anchor_id": anchor, "items": items, "source_key": choice["key"]}
