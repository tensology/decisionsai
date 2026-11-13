"""Topic-aware source identities for Telegram group messages."""

import json
import re

_TOPIC_SOURCE = re.compile(r"(-[0-9]+)~topic~([1-9][0-9]*|general|unknown-[0-9]+)")


def split_telegram_source(source):
    match = _TOPIC_SOURCE.fullmatch(str(source or ""))
    return match.groups() if match else (source, None)


def telegram_topic_id(topic):
    if isinstance(topic, str) and topic.isascii() and topic.isdecimal() and len(topic) <= 19:
        value = int(topic)
        if 0 < value < 2**63:
            return value
    return None


def telegram_message_source(row):
    chat_id = str(row.chat_id or "")
    if not re.fullmatch(r"-[0-9]+", chat_id):
        return chat_id
    try:
        raw = json.loads(row.raw_data or "{}")
    except (ValueError, TypeError):
        return chat_id
    if not isinstance(raw, dict):
        return chat_id
    chat = raw.get("chat")
    topic_flag = raw.get("is_topic_message")
    if topic_flag is True:
        topic_id = raw.get("message_thread_id")
        topic = str(topic_id) if type(topic_id) is int and 0 < topic_id < 2**63 else None
    elif isinstance(chat, dict) and chat.get("is_forum") is True:
        topic = "general" if topic_flag is False else None
    else:
        return chat_id
    # Older relays omitted the topic marker. Keep each such forum message isolated.
    return f"{chat_id}~topic~{topic or f'unknown-{row.telegram_message_id}'}"


def telegram_topic_label(group_label, source):
    _, topic = split_telegram_source(source)
    if topic is None:
        return group_label
    if topic == "general":
        return f"{group_label} · General"
    if topic.startswith("unknown-"):
        return f"{group_label} · Topic unavailable for message {topic.removeprefix('unknown-')}"
    return f"{group_label} · Topic {topic}"


def telegram_source_has_topic(db, thread, message_ids):
    """Check a topic identity, including older proposals saved with only the group."""
    from distr.core.db import TelegramGroupMessage

    if split_telegram_source(thread)[1] is not None:
        return True
    ids = [int(value) for item in message_ids if (value := str(item)).isascii() and value.isdecimal() and len(value) <= 18]
    if not ids:
        return False
    rows = db.query(TelegramGroupMessage).filter(
        TelegramGroupMessage.chat_id == thread, TelegramGroupMessage.telegram_message_id.in_(ids),
    )
    return any(telegram_message_source(row) != thread for row in rows)


def telegram_source_matches_topic(db, thread, message_ids):
    from distr.core.db import TelegramGroupMessage

    chat, topic = split_telegram_source(thread)
    if telegram_topic_id(topic) is None:
        return False
    ids = {int(value) for item in message_ids
           if (value := str(item)).isascii() and value.isdecimal() and len(value) <= 18}
    if not ids or len(ids) != len(set(map(str, message_ids))):
        return False
    rows = db.query(TelegramGroupMessage).filter(
        TelegramGroupMessage.chat_id == chat, TelegramGroupMessage.telegram_message_id.in_(ids),
    ).all()
    return {row.telegram_message_id for row in rows} == ids and all(telegram_message_source(row) == thread for row in rows)
