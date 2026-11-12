"""Inbound Telegram messages from chats that are not Paul's paired private chat.

Paul's own channel is his paired private Telegram chat (and WhatsApp for himself). A group is
never his channel, even when he writes in it. This module decides what an inbound message from
any other chat may do, and it fails closed:

* ``linked_group``: a group or supergroup with an explicit project link and complete provenance
  (group chat id, message id, a human sender id). It is kept in Incoming as before (messages.py
  persists every group message) and, when "Be my assistant" is on, Paul hears about it once,
  in the app (same-channel rule: this heads-up answers no message of his, so it is never pushed
  to Telegram or spoken). Nothing is sent to the group; a reply to the group goes back on that
  same group through the normal reply flow (ask first, explicit link, ``chat_send``).
* ``unlinked_group``: kept in Incoming only. No notification, no action.
* ``refused``: provenance is incomplete (no chat id, no message id, no sender, a bot, a channel
  post), or it's a private chat that isn't the paired one. Nothing is acted on.

A group message is never treated as a command from Paul, and no chat id is ever guessed: the
only chat ids used are the ones the relay delivered with the message.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}
NOTIFY_GAP_S = 30 * 60  # at most one heads-up per linked group in this window
_lock = threading.Lock()
_last_notified: dict[str, float] = {}


@dataclass(frozen=True)
class InboundDecision:
    kind: str  # owner_private | linked_group | unlinked_group | refused
    reason: str
    chat_id: str = ""
    chat_title: str = ""
    message_id: str = ""
    sender_id: str = ""
    sender_label: str = ""
    from_owner: bool = False
    project_ids: frozenset = field(default_factory=frozenset)

    @property
    def actionable(self) -> bool:
        return self.kind == "linked_group"


def _int_id(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        text = str(value).strip()
        if not text.lstrip("-").isdigit():
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def _chat_type(inner: dict[str, Any]) -> str:
    chat = inner.get("chat") if isinstance(inner.get("chat"), dict) else {}
    return str(inner.get("chat_type") or chat.get("type") or "").strip().lower()


def _chat_title(inner: dict[str, Any]) -> str:
    chat = inner.get("chat") if isinstance(inner.get("chat"), dict) else {}
    return str(chat.get("title") or inner.get("chat_title") or chat.get("username") or "").strip()


def _sender_label(sender: dict[str, Any]) -> str:
    name = " ".join(p for p in (str(sender.get("first_name") or "").strip(),
                                str(sender.get("last_name") or "").strip()) if p).strip()
    return name or str(sender.get("username") or "").strip() or "Someone"


def classify_inbound(inner: dict[str, Any], *, paired_user_id: Any, index: Optional[dict] = None) -> InboundDecision:
    """Decide what an inbound relay message may do. Pure apart from the link lookup."""
    inner = inner if isinstance(inner, dict) else {}
    chat = _int_id(inner.get("chat_id"))
    paired = _int_id(paired_user_id)
    ctype = _chat_type(inner)
    if chat is None:
        return InboundDecision("refused", "no chat id")
    if chat > 0:
        if paired is not None and paired > 0 and chat == paired and ctype in ("", "private"):
            return InboundDecision("owner_private", "paired private chat", chat_id=str(chat))
        return InboundDecision("refused", "private chat that isn't the paired one", chat_id=str(chat))
    if ctype not in GROUP_TYPES:
        # Channel posts carry no sender; an unknown type can't be verified.
        return InboundDecision("refused", f"unsupported chat type '{ctype or 'unknown'}'", chat_id=str(chat))
    message_id = _int_id(inner.get("message_id"))
    sender = inner.get("from") if isinstance(inner.get("from"), dict) else {}
    sender_id = _int_id(sender.get("id"))
    if message_id is None or message_id <= 0:
        return InboundDecision("refused", "no message id", chat_id=str(chat))
    if sender_id is None or sender_id <= 0:
        return InboundDecision("refused", "no sender", chat_id=str(chat))
    if sender.get("is_bot") is True:
        return InboundDecision("refused", "sent by a bot", chat_id=str(chat))
    from distr.core.integrations.telegram.chat_send import linked_group_projects

    projects = frozenset(linked_group_projects(chat, index))
    base = dict(chat_id=str(chat), chat_title=_chat_title(inner), message_id=str(message_id),
                sender_id=str(sender_id), sender_label=_sender_label(sender),
                from_owner=bool(paired is not None and sender_id == paired))
    if not projects:
        return InboundDecision("unlinked_group", "group has no project link", **base)
    return InboundDecision("linked_group", "explicitly linked group", project_ids=projects, **base)


def _project_names(ids) -> list[str]:
    try:
        from distr.core.initiative.outcome_flow import _project_name_map

        names = _project_name_map()
        return sorted({names[i] for i in ids if i in names})
    except Exception:
        return []


def _assistant_on() -> tuple[bool, dict[str, Any]]:
    try:
        from distr.core.initiative import outcome_flow as of

        settings = of.load_settings()
        return (of.current_level(settings) in {"operate", "own"}
                and of._flag(settings, "initiative_recommendations", False)
                and of._flag(settings, "initiative_scan_telegram", True)), settings
    except Exception:
        return False, {}


def _heads_up(decision: InboundDecision, text: str, settings: dict[str, Any]) -> str:
    """Tell Paul once, in the app. Same-channel rule: there is no incoming message from him to answer."""
    from datetime import datetime

    from distr.core.initiative import channel_select
    from distr.core.initiative import outcome_flow as of

    now = datetime.now()
    names = _project_names(decision.project_ids)
    where = decision.chat_title or "a linked Telegram group"
    project = f" ({', '.join(names)})" if names else ""
    body = of.humanize(f"{decision.sender_label} wrote in {where}{project}: {of._preview(text, 160)}", max_len=300)
    nudge = {"id": f"tg-{decision.chat_id}-{decision.message_id}", "fp": f"telegram:{decision.chat_id}",
             "area": "inbound", "project": names[0] if names else "", "pinned": False, "text": body,
             "why": "New in a Telegram group linked to your project", "source": "telegram",
             "created": now.astimezone().isoformat(timespec="minutes"), "created_ts": now.timestamp()}
    choice = channel_select.decide(now=now, incoming=None)
    return channel_select.place([nudge], choice, settings, now=now, level=of.current_level(settings),
                                meta={"source": "telegram_group", "chat_id": decision.chat_id})


def handle_group_inbound(inner: dict[str, Any], *, paired_user_id: Any, index: Optional[dict] = None,
                         notify: Optional[Callable[[InboundDecision, str, dict], Any]] = None) -> InboundDecision:
    """Called by the Telegram message handler for every non-private message it stored."""
    decision = classify_inbound(inner, paired_user_id=paired_user_id, index=index)
    if decision.kind != "linked_group":
        logger.info("[Telegram] Non-paired inbound kept without action (%s, chat_id=%s)",
                    decision.reason, decision.chat_id or "none")
        return decision
    text = str((inner or {}).get("text") or (inner or {}).get("caption") or "").strip()
    if not text or decision.from_owner:
        return decision  # Paul's own words in a group need no heads-up, and are never commands
    on, settings = _assistant_on()
    if not on:
        return decision
    with _lock:
        now = time.time()
        if now - _last_notified.get(decision.chat_id, 0.0) < NOTIFY_GAP_S:
            return decision
        _last_notified[decision.chat_id] = now
    try:
        (notify or _heads_up)(decision, text, settings)
    except Exception:
        logger.warning("[Telegram] Linked group heads-up failed", exc_info=True)
    return decision


def reset_for_tests() -> None:
    with _lock:
        _last_notified.clear()
