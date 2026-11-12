"""Same-channel rule for "Be my assistant" suggestions and replies (12 Nov 2026).

Paul's rule (live call, 12 Nov 2026 15:16 and 15:19 SAST) replaces the earlier
"intelligent channel selection" design, which scored Telegram / app / spoken activity,
picked the "best" place and asked "Where do you want suggestions like this?" when unsure:

* A reply always goes back on the channel the incoming message came from. WhatsApp in,
  WhatsApp out; Gmail in, Gmail out; Telegram in, Telegram out; spoken (hotkey /
  push-to-talk) in, spoken out; in the app in, in the app out.
* Nothing here scores activity, learns a "preferred" channel or picks a "best" one.
* No incoming channel (a proactive suggestion nobody asked for): it stays in the app.
  It is never pushed to Telegram or spoken just because Paul looked active there.
* An unknown channel name is refused (kept in the app, marked not delivered). It is
  never mapped onto some other named channel.
* If the same channel can't take it, nothing is sent anywhere else. The suggestion is
  kept in the app marked "not delivered" with the reason, and the audit log says so.
* Exactly one channel per suggestion. WhatsApp is never used from this path.

Client replies (tickets, proposals, Message Client) follow the same rule in
``workflow.reply_channel`` / ``workflow.reply_delivery`` / ``incoming.source_reply``.
See ``docs/same-channel-replies.md``.

The old question flow is retired: nothing new is ever asked. ``answer`` / ``parse_answer``
stay only so a question that was already open before this change can be closed; any
suggestions it was holding are released into the app.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

logger = logging.getLogger("distr.core.initiative.channel_select")

CHANNELS = ("in_app", "telegram", "spoken")
CHANNEL_WORDS = {"in_app": "here", "telegram": "Telegram", "spoken": "spoken"}
# Kept for old callers/tests that import it; never sent any more.
ASK_TEXT = "Where do you want suggestions like this: here, Telegram or spoken?"
NO_INCOMING_CHANNEL = "in_app"
ASK_TTL_S = 12 * 3600
STATE_KEY = "assistant_channel"

# Incoming channel names seen on the inbound side, mapped onto the three places a
# suggestion can be answered. Anything else is "unknown" and is refused.
_INCOMING_ALIASES = {
    "in_app": "in_app", "app": "in_app", "web": "in_app", "chat": "in_app", "desktop": "in_app",
    "telegram": "telegram", "tg": "telegram",
    "spoken": "spoken", "voice": "spoken", "ptt": "spoken", "push_to_talk": "spoken", "hotkey": "spoken",
}


@dataclass
class Choice:
    channel: str | None
    confidence: float
    ask: bool
    ask_on: str | None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "confidence": round(self.confidence, 2), "ask": self.ask,
                "ask_on": self.ask_on, "reasons": list(self.reasons)}


# ---------------------------------------------------------------------------
# State (kept inside the outcome-flow state file, under its lock)
# ---------------------------------------------------------------------------

def _of():
    from distr.core.initiative import outcome_flow

    return outcome_flow


def _blank() -> dict[str, Any]:
    return {"telegram_last_user_ts": None, "pending_ask": None,
            "history": {ch: {"offered": 0, "acted": 0, "dismissed": 0} for ch in CHANNELS}}


def channel_state(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """The selector's state (a copy). Pass ``state`` when already holding the outcome-flow lock."""
    state = _of()._load_state() if state is None else state
    cur = _blank()
    saved = state.get(STATE_KEY)
    if isinstance(saved, dict):
        for key, value in saved.items():
            cur[key] = value
    hist = cur.get("history") if isinstance(cur.get("history"), dict) else {}
    cur["history"] = {ch: {k: int((hist.get(ch) or {}).get(k) or 0) for k in ("offered", "acted", "dismissed")}
                      for ch in CHANNELS}
    return cur


def _update(mutator) -> dict[str, Any]:
    of = _of()
    with of._LOCK:
        state = of._load_state()
        cur = channel_state(state)
        mutator(cur)
        state[STATE_KEY] = cur
        of._save_state(state)
        return cur


def _ts(now: datetime | float | None) -> float:
    if now is None:
        return time.time()
    if isinstance(now, (int, float)):
        return float(now)
    return now.timestamp()


def record_telegram_user_activity(now: datetime | float | None = None) -> None:
    """Bookkeeping only: the user wrote in their paired private Telegram chat.

    This is no longer used to choose a channel.
    """
    ts = _ts(now)

    def mutate(cur):
        cur["telegram_last_user_ts"] = ts

    try:
        _update(mutate)
    except Exception:
        logger.debug("channel_select: telegram activity not recorded", exc_info=True)


def record_dismissed(channel: str) -> None:
    if channel not in CHANNELS:
        return

    def mutate(cur):
        cur["history"][channel]["dismissed"] += 1

    try:
        _update(mutate)
    except Exception:
        logger.debug("channel_select: dismissal not recorded", exc_info=True)


def pending_ask(cur: dict[str, Any], now_ts: float) -> dict[str, Any] | None:
    """A question left open by the retired design (before 12 Nov 15:16), if any."""
    ask = cur.get("pending_ask")
    if isinstance(ask, dict) and now_ts - float(ask.get("created_ts") or 0) < ASK_TTL_S:
        return ask
    return None


def waiting_for_answer(now: datetime | None = None) -> bool:
    """Always False: nothing waits on a channel question any more."""
    return False


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def incoming_channel(value: Any) -> str | None:
    """Normalise an incoming channel name. '' / None -> None; unknown -> 'unknown'."""
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return None
    return _INCOMING_ALIASES.get(raw, "unknown")


def same_channel(incoming: Any) -> Choice:
    """Reply on the channel the incoming message came from. Pure: no I/O, no scoring."""
    channel = incoming_channel(incoming)
    if channel is None:
        return Choice(NO_INCOMING_CHANNEL, 1.0, False, None,
                      ["no incoming message, so it stays in the app"])
    if channel == "unknown":
        return Choice(None, 0.0, False, None,
                      [f"incoming channel {str(incoming)!r} is unknown, so nothing is sent anywhere"])
    return Choice(channel, 1.0, False, None, [f"replying where the message came from ({CHANNEL_WORDS[channel]})"])


def decide(*, now: datetime | None = None, incoming: Any = None, board_observed: bool = False,
           board_recent: bool = False, in_meeting: bool | None = None) -> Choice:
    """Where this goes: the incoming message's channel, else the app.

    ``board_observed`` / ``board_recent`` / ``in_meeting`` are accepted for old callers and
    ignored: activity no longer picks a channel.
    """
    return same_channel(incoming)


# ---------------------------------------------------------------------------
# Deliver (exactly one channel, never a different one)
# ---------------------------------------------------------------------------

def _spoken_text(texts: Iterable[str]) -> str:
    body = " ".join(t.strip() for t in texts if t and t.strip())
    try:
        from distr.core.human_engagement import voice_friendly_text

        return voice_friendly_text(body) or body
    except Exception:
        return body


def _send(channel: str, texts: list[str], settings: dict[str, Any]) -> bool:
    of = _of()
    if channel == "telegram":
        # Only ever the paired private chat the message came from (send_telegram never targets a group).
        return bool(of.send_telegram("\n\n".join(texts), settings=settings, require_toggle=False))
    if channel == "spoken":
        try:
            return bool(of._speak(of._preview(_spoken_text(texts), 400)))
        except Exception:
            logger.warning("channel_select: spoken reply failed", exc_info=True)
            return False
    if channel == "in_app":
        return True  # the stored nudge is the delivery
    return False


def place(nudges: list[dict[str, Any]], choice: Choice, settings: dict[str, Any], *,
          now: datetime | None = None, level: str = "", meta: dict[str, Any] | None = None) -> str:
    """Store ``nudges`` and deliver them on exactly ``choice.channel``. Returns the channel tag.

    If that channel can't take them (or the channel is unknown), they are NOT sent anywhere
    else: they are kept in the app marked ``undelivered`` with the reason, and audited.
    """
    of = _of()
    now_ts = _ts(now)
    texts = [str(n.get("text") or "") for n in nudges]
    wanted = choice.channel if choice.channel in CHANNELS else None
    if wanted is None:
        delivered, reason = False, (choice.reasons[-1] if choice.reasons else "the incoming channel is unknown")
        tag = "unknown"
    elif wanted == "in_app":
        delivered, reason, tag = True, None, "in_app"
    else:
        tag = wanted
        try:
            delivered = bool(_send(wanted, texts, settings))
        except Exception:
            logger.warning("channel_select: %s delivery failed", wanted, exc_info=True)
            delivered = False
        reason = None if delivered else f"{CHANNEL_WORDS[wanted]} didn't take it, so nothing was sent anywhere else"

    with of._LOCK:
        state = of._load_state()
        a = dict(state.get("assistant") or {})
        stored = list(a.get("nudges") or [])
        for n in nudges:
            n["channel"] = tag
            if not delivered:
                n["undelivered"] = True
                n["delivery_note"] = reason
            stored.append(n)
        a["nudges"] = stored[-of.ASSISTANT_KEEP:]
        state["assistant"] = a
        cur = channel_state(state)
        if delivered and tag in CHANNELS:
            cur["history"][tag]["offered"] += len(nudges)
        state[STATE_KEY] = cur
        of._save_state(state)
    if not delivered:
        of.audit("assistant_channel_not_delivered", channel=tag, status="not_delivered", reason=reason, items=texts)
    of.audit("assistant_nudge_offered", channel=tag, level=level, status="offered" if delivered else "not_delivered",
             items=texts, choice=choice.to_dict(), **(meta or {}))
    return tag


# ---------------------------------------------------------------------------
# Closing a question left open by the retired design
# ---------------------------------------------------------------------------

_ANSWER_WORDS = {
    "here": None, "this": None,
    "telegram": "telegram", "in telegram": "telegram", "on telegram": "telegram",
    "app": "in_app", "the app": "in_app", "in the app": "in_app", "in app": "in_app", "decisions": "in_app",
    "spoken": "spoken", "voice": "spoken", "out loud": "spoken", "say it": "spoken", "speak": "spoken",
}


def parse_answer(text: str, *, asked_on: str) -> str | None:
    lowered = str(text or "").strip().lower()
    if lowered in CHANNELS:
        return lowered
    raw = re.sub(r"[^a-z ]", "", lowered).strip()
    if raw not in _ANSWER_WORDS:
        return None
    return _ANSWER_WORDS[raw] or asked_on


def answer(ask_id: str | None, channel: str, settings: dict[str, Any] | None = None, *,
           now: datetime | None = None) -> dict[str, Any]:
    """Close an old open question. Nothing is learned; held suggestions go to the app."""
    of = _of()
    if channel not in CHANNELS:
        return {"ok": False, "text": "I didn't catch that."}
    now_ts = _ts(now)
    released = 0
    with of._LOCK:
        state = of._load_state()
        cur = channel_state(state)
        ask = pending_ask(cur, now_ts)
        if not ask or (ask_id and ask.get("id") != ask_id):
            return {"ok": False, "text": "That question has already been answered, so there's nothing to change."}
        a = dict(state.get("assistant") or {})
        keep = []
        for n in a.get("nudges") or []:
            if n.get("held_for") == ask["id"]:
                n = {k: v for k, v in n.items() if k != "held_for"}
                n["channel"] = "in_app"
                released += 1
            if (n.get("channel_question") or {}).get("id") == ask["id"]:
                n = {k: v for k, v in n.items() if k != "channel_question"}
            keep.append(n)
        a["nudges"] = keep
        state["assistant"] = a
        cur["pending_ask"] = None
        state[STATE_KEY] = cur
        of._save_state(state)
    of.audit("assistant_channel_question_closed", channel="in_app", status="closed", released=released)
    return {"ok": True, "channel": "in_app", "released": released,
            "text": "Thanks. I don't pick a channel any more: I answer where you wrote to me, "
                    "and anything else stays in the app."}
