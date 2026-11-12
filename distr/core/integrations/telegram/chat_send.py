"""Post into a Telegram group that is explicitly linked to a project.

The desktop app reaches Telegram only through the Decisions relay WebSocket. The relay's
existing ``send_message`` frame already carries an explicit ``chat_id`` and posts to it
(decisions.tensology.com backend ``_handle_ws_message``: ``chat_id = body.get("chat_id") or ...``),
answering with ``message_sent`` or ``error`` for the same ``client_message_id``. No new relay
or Tensology API is used.

Paul's own conversation stays on the paired private chat through ``send_to_telegram``; this
path is only for replying in a group the work came from. It fails closed:

* the chat id comes from the ticket's provenance and must be a Telegram group id
  (negative integer); nothing is ever guessed or looked up by name;
* the group must carry an explicit project link at the moment of sending; the link is
  checked again immediately before the frame is written;
* the paired private chat is refused here (it has its own sender);
* nothing is queued for later: if the relay isn't connected, nothing is sent.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

GROUP_CHAT_RE = re.compile(r"^-\d{5,20}$")
CORRELATION_PREFIX = "group-reply:"
MAX_TEXT = 4000
MAX_RATE_WAIT_S = 2.0
_lock = threading.Lock()


class GroupSendRefused(Exception):
    """Nothing was sent; ``str(exc)`` says why in plain words."""


def is_group_chat_id(chat_id: Any) -> bool:
    return bool(GROUP_CHAT_RE.fullmatch(str(chat_id if chat_id is not None else "").strip()))


def linked_group_projects(chat_id: Any, index: Optional[dict] = None) -> set[int]:
    """Project ids explicitly linked to this Telegram group (empty when none)."""
    chat = str(chat_id if chat_id is not None else "").strip()
    if not is_group_chat_id(chat):
        return set()
    try:
        if index is None:
            from distr.core.incoming.project_links import source_project_index

            index = source_project_index()
        return {int(pid) for pid in (index.get(("telegram", chat)) or set())}
    except Exception:
        logger.debug("Telegram group link lookup failed", exc_info=True)
        return set()


def _paired_ids(manager: Any) -> set[str]:
    ids = set()
    for value in (getattr(manager, "telegram_user_id", None),):
        if value not in (None, ""):
            ids.add(str(value).strip())
    return ids


def send_to_chat(manager: Any, chat_id: Any, text: str, *,
                 link_check: Optional[Callable[[str], bool]] = None, key: str = "",
                 before_send: Optional[Callable[[], None]] = None) -> dict[str, Any]:
    """Write one ``send_message`` frame for a linked group to the relay.

    Returns ``{"success": True, "chat_id": ..., "client_message_id": ...}`` once the relay
    socket accepted the frame (the relay then confirms with ``message_sent``). Raises
    :class:`GroupSendRefused` when anything is missing; nothing is sent in that case.
    """
    chat = str(chat_id if chat_id is not None else "").strip()
    check = link_check or (lambda value: bool(linked_group_projects(value)))
    if not is_group_chat_id(chat):
        raise GroupSendRefused("I can only post into a Telegram group, and this isn't one I can verify.")
    if chat in _paired_ids(manager):
        raise GroupSendRefused("That's your own Telegram chat, not a group.")
    if not check(chat):
        raise GroupSendRefused("That Telegram group isn't linked to a project, so I won't post there.")
    body = str(text or "").strip()
    try:
        from distr.core.integrations.telegram.sender import _audit_outbound_telegram_text

        body = (_audit_outbound_telegram_text(body) or "").strip()
    except Exception:
        logger.debug("Telegram outbound audit unavailable", exc_info=True)
    if not body:
        raise GroupSendRefused("There was nothing to post.")
    if len(body) > MAX_TEXT:
        raise GroupSendRefused("The reply is too long for one Telegram message, so it's waiting for you.")
    write = getattr(manager, "_send_websocket_message", None)
    connected = getattr(manager, "is_connected", None)
    try:
        live = bool(callable(write) and callable(connected) and connected())
    except Exception:
        live = False
    if not live:
        raise GroupSendRefused("That Telegram group is linked to your project, but Telegram can't post into groups "
                               "right now because the relay isn't connected, so the reply is waiting for you to send it.")
    digest = hashlib.sha256(f"{chat}|{key}|{body}".encode("utf-8")).hexdigest()[:32]
    frame = {"type": "send_message", "chat_id": int(chat), "text": body,
             "client_message_id": f"{CORRELATION_PREFIX}{digest}"}
    with _lock:
        interval = float(getattr(manager, "_min_send_interval", 0) or 0)
        last = float(getattr(manager, "_last_send_time", 0) or 0)
        wait = last + interval - time.time()
        if wait > 0:
            time.sleep(min(wait, MAX_RATE_WAIT_S))
        # Link re-checked immediately before transport: an unlink in the meantime wins.
        if not check(chat):
            raise GroupSendRefused("That Telegram group was unlinked from the project just now, so nothing was posted.")
        if before_send is not None:
            before_send()
        try:
            accepted = write(frame)
        except Exception as exc:
            logger.warning("Telegram group post was not accepted by the relay socket", exc_info=True)
            return {"success": False, "error": f"Telegram did not accept the message ({type(exc).__name__})",
                    "chat_id": chat}
        if accepted:
            try:
                manager._last_send_time = time.time()
            except Exception:
                pass
    logger.info("[Telegram] Group reply handed to relay (chat_id=%s, receipt=%s)", chat, frame["client_message_id"])
    return {"success": bool(accepted), "error": None if accepted else "Telegram did not accept the message",
            "chat_id": chat, "client_message_id": frame["client_message_id"]}
