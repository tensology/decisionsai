"""Saved chat appearance and read state, shared by the API and desktop loader."""
import json
import logging

from distr.core.chat_title_auto import _chat_additional_context, _thread_rows
from distr.core.skin_migration import migrate_selected_oracle


def chat_skin(chat, default="oracle"):
    return migrate_selected_oracle(_chat_additional_context(chat.additional_context).get("skin_name") or default)


def set_chat_skin(chat, skin):
    context = _chat_additional_context(chat.additional_context)
    context["skin_name"] = migrate_selected_oracle(skin)
    chat.additional_context = json.dumps(context, ensure_ascii=False)


def validate_chat_skin(skin):
    from distr.core.paths import AVATARS_DIR
    from distr.core.skin_discovery import get_skin_by_name

    skin = migrate_selected_oracle(skin)
    if skin in {".", ".."} or "/" in skin or "\\" in skin or not get_skin_by_name(AVATARS_DIR, skin):
        raise ValueError("Choose an installed skin")
    return skin


def sidebar_state(session, chat, settings, *, mark_read=None):
    rows = sorted((r for r in _thread_rows(session, chat.id) if not r.is_hidden), key=lambda r: r.id)
    replies = [r for r in rows if (r.response or "").strip()]
    latest_reply_id = replies[-1].id if replies else 0
    latest_message = next(((r.response or r.input or "").strip() for r in reversed(rows) if (r.response or r.input or "").strip()), "")
    context = _chat_additional_context(chat.additional_context)
    seen = int(context.get("last_read_reply_id", latest_reply_id))
    # Seed existing threads without turning historical replies into new unread work.
    if mark_read is not None or "last_read_reply_id" not in context:
        context["last_read_reply_id"] = max(seen, min(mark_read, latest_reply_id)) if mark_read is not None else latest_reply_id
    if "skin_name" not in context:
        context["skin_name"] = migrate_selected_oracle(settings.get("selected_oracle"))
    chat.additional_context = json.dumps(context, ensure_ascii=False)
    from distr.core.chat_agents import agent_identity

    return {**agent_identity(session, chat.id), "skin_name": context["skin_name"], "latest_message": latest_message,
            "latest_reply_id": latest_reply_id,
            "unread": latest_reply_id > context["last_read_reply_id"]}


def restore_chat_skin(chat_id):
    from distr.core.db import Chat, get_session
    from distr.core.services.settings_service import update_oracle_skin
    from distr.core.settings import load_settings_from_db

    with get_session() as session:
        from distr.core.chat_agents import primary_agent

        chat = session.get(Chat, chat_id)
        if not chat:
            return
        primary = primary_agent(session)
        skin = primary.skin_name if primary else chat_skin(chat, load_settings_from_db().get("selected_oracle"))
        try:
            skin = validate_chat_skin(skin)
        except ValueError:
            logging.getLogger(__name__).warning("Saved skin %s unavailable; using Oracle for chat %s", skin, chat_id)
            skin = validate_chat_skin("oracle")
        else:
            if not primary:
                set_chat_skin(chat, skin)
                session.commit()
    update_oracle_skin(skin, persist_chat=False)
