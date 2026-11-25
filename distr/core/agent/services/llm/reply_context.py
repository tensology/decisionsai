"""Keep conversational replies on the channel and destination the user established."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from email.utils import getaddresses


_CHANNELS = {
    "mailshot": r"mail\s*shot|tensology (?:mail|inbox)", "gmail": r"gmail|google mail",
    "whatsapp": r"whats\s*app", "telegram": r"telegram",
}
_TOOLS = {"mailshot": "tensology_workspace", "gmail": "google_workspace", "whatsapp": "create_ticket"}
_DRAFT = re.compile(r"\b(?:draft|compose|rewrite)\b|\b(?:write|prepare)\b[^.!?]{0,35}\b(?:reply|response|email|message)\b", re.I)
_STATUS = re.compile(
    r"\b(?:did|have|had) (?:you|we)\b[^.!?]{0,45}\b(?:draft|write|save|send|sent|reply)\b"
    r"|\b(?:show|list|read|check|where (?:is|are))\b[^.!?]{0,30}\bdrafts?\b"
    r"|\b(?:did|was|is) (?:the|my|that|this)\b[^.!?]{0,50}\b(?:draft|message|email)\b[^.!?]{0,30}\b(?:sent|saved|go out)\b",
    re.I,
)
_CONTINUATION = re.compile(r"^(?:and |okay |ok |yeah |yes )*(?:make it|shorter\b|more polite\b|that one\b|go ahead\b|send it\b|reply\b|respond\b|what (?:does|did|is) (?:it|that|he|she|they)\b)", re.I)
_EDIT_DRAFT = re.compile(r"^(?:(?:please|yes|yeah|okay)\b[,\s]+)*(?:make (?:it|the reply|the draft) (?:shorter|longer|clearer|more\b|less\b)|shorter\b|more polite\b)", re.I)
_DRAFT_OFFER = re.compile(r"\b(?:shall I|should I|want me to|would you like me to)\s+(?:draft|compose|write a reply)\b[^?\n]*\?\s*$", re.I)
_YES = re.compile(r"^(?:yes|yeah|yep|please do|go ahead|yes,? please)[.!\s]*$", re.I)
_EMAIL = re.compile(r"\b(?:email|e-mail|mailbox|inbox)\b", re.I)
_DONT_SEND = re.compile(r"\b(?:do not|don['’]t|dont|never)\s+(?:actually\s+)?send\b", re.I)
_DONT_DRAFT = re.compile(r"\b(?:do not|don['’]t|dont|never)\s+(?:save\s+a\s+)?(?:draft|compose|rewrite)\b", re.I)
_SEND_THE_DRAFT = re.compile(r"\bsend\s+(?:the|that|this|my|your)\s+(?:email\s+|whats\s*app\s+)?draft\b", re.I)
_READ_REQUEST = re.compile(r"^(?:(?:can|could|would) you |please )*(?:read|show|list|find|check|look up|fetch|see)\b", re.I)
_SEND_REQUEST = re.compile(r"(?:^|\bthen |\band )\s*(?:(?:can|could|would) you |please )*(?:send|forward)\b", re.I)
_EMAIL_ADDRESS = re.compile(r"[\w.!#$%&'*+/=?^\x60{|}~-]+@[\w.-]+\.[A-Za-z]+")

_MAIL_ACTIONS = {
    "tensology_workspace": (
        "mailshot", {"read_mail", "read_email", "get_email", "read_latest_mail"},
        {"list_mail", "list_inbox", "check_inbox", "list_drafts"},
        {"save_draft", "draft_email", "create_draft"}, {"send_mail", "send_email"},
    ),
    "google_workspace": (
        "gmail", {"read_email", "get_email", "find_and_read_email"},
        {"check_inbox", "list_emails_by_type", "list_drafts", "get_draft"},
        {"draft_email"}, {"send_email", "reply_email"},
    ),
}
_WA_READ = {"list_messages", "read", "conversation", "search", "ingest"}
_WA_DRAFT = {"set_draft", "set_whatsapp_draft", "draft", "prepare_draft", "write_reply"}
_WA_SEND = {"send", "send_message", "send_reply", "send_approved"}


@dataclass(frozen=True)
class Destination:
    channel: str
    recipient: str = ""
    message_ids: frozenset[str] = frozenset()


def _tool_route(tool: str, action: str) -> tuple[str, str]:
    if tool in _MAIL_ACTIONS:
        channel, exact_reads, discovery, drafts, sends = _MAIL_ACTIONS[tool]
    elif tool == "whatsapp_toolkit" or (
        tool == "create_ticket" and (action.startswith(("whatsapp_", "wa_")) or action in {"set_whatsapp_draft", "send_whatsapp"})
    ):
        channel = "whatsapp"
        action = "send" if action == "send_whatsapp" else action.removeprefix("whatsapp_").removeprefix("wa_")
        exact_reads, discovery, drafts, sends = _WA_READ, {
            "latest_activity", "list_drafts", "get_draft", "list_chats", "list_contacts",
        }, _WA_DRAFT, _WA_SEND
    else:
        return "", ""
    for mode, actions in (("read", exact_reads), ("discover", discovery), ("draft", drafts), ("send", sends)):
        if action in actions:
            return channel, mode
    return "", ""


def is_reply_read(tool: str, action: str) -> bool:
    return _tool_route(tool, action)[1] in {"read", "discover"}


def _user_channels(text: str) -> set[str]:
    channels = set()
    for channel, pattern in _CHANNELS.items():
        mentions = list(re.finditer(
            rf"\b(?P<excluded>(?:not|instead of)\s+(?:(?:in|on|via|through)\s+)?)?(?:{pattern})\b",
            text, re.I,
        ))
        if mentions and not mentions[-1]["excluded"]:
            channels.add(channel)
    return channels


def _is_follow_up(text: str) -> bool:
    return bool(_DRAFT.search(text) or _STATUS.search(text) or _CONTINUATION.match(text) or _YES.fullmatch(text))


def _user_history(owner, current: str) -> list[tuple[int, str]]:
    from .tool_execution_policy import _latest_user_instruction

    texts = [
        message["content"] for message in getattr(owner, "_messages", None) or []
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
    if not texts or _latest_user_instruction(texts[-1]) != current:
        texts.append(current)
    start = max(0, len(texts) - 5)
    return [(index, _latest_user_instruction(texts[index])) for index in range(start, len(texts))]


def _chat_id(owner):
    manager = getattr(owner, "chat_manager", None)
    try:
        return manager.get_current_chat() if manager else None
    except Exception:
        return None


def _focus(owner, current: str) -> tuple[set[str], set[Destination]]:
    explicit = _user_channels(current)
    saved_chat, saved_turn, saved = getattr(owner, "_reply_read_focus", (None, None, set()))
    if saved_chat != _chat_id(owner):
        saved = set()
    history = _user_history(owner, current)
    destinations = saved if saved_turn == history[-1] else set()
    channels = set()
    for prior_index, prior in reversed(history[:-1]):
        if destinations:
            break
        if saved and saved_turn == (prior_index, prior):
            destinations = saved
            break
        channels = _user_channels(prior)
        if channels or not _is_follow_up(prior):
            break

    return explicit or {item.channel for item in destinations} or channels, {
        item for item in destinations if not explicit or item.channel in explicit
    }


def reply_connector_names(owner, user_text: str) -> tuple[str, ...]:
    from .tool_execution_policy import _latest_user_instruction

    text = _latest_user_instruction(user_text)
    if not _is_follow_up(text):
        return ()
    channels, _ = _focus(owner, text)
    return tuple(_TOOLS[channel] for channel in sorted(channels) if channel in _TOOLS)


def _email_addresses(value) -> set[str]:
    if isinstance(value, dict):
        return _email_addresses(value.get("email") or value.get("address"))
    if isinstance(value, (tuple, list)):
        return set().union(*(_email_addresses(item) for item in value))
    return {address.casefold() for _, address in getaddresses([str(value or "")]) if "@" in address}


def _mail_params(args: dict) -> dict:
    nested = args.get("params")
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except ValueError:
            nested = None
    return {**args, **nested} if isinstance(nested, dict) else args


def _whatsapp_target(args: dict) -> str:
    return str(args.get("jid_phone") or args.get("jid") or "").strip().split("@", 1)[0].lstrip("+")


def _accepted_draft_offer(owner, text: str) -> bool:
    if not _YES.fullmatch(text):
        return False
    for message in reversed(getattr(owner, "_messages", None) or []):
        if message.get("role") == "assistant" and message.get("content"):
            return bool(_DRAFT_OFFER.search(str(message["content"])))
        if message.get("role") == "user" and str(message.get("content") or "").strip() != text:
            break
    return False


def reply_user_instruction(tool: str, action: str, text: str) -> str:
    """An explicit no-send request still allows a requested unsent draft."""
    if _tool_route(tool, action)[1] == "draft" and _DRAFT.search(text):
        return _DONT_SEND.sub("keep unsent", text)
    return text


def remember_reply_read(owner, tool: str, action: str, args: dict, text: str, result) -> None:
    channel, mode = _tool_route(tool, action)
    output = str(result or "").strip()
    if mode != "read" or not text or not output:
        return
    if channel == "mailshot":
        try:
            message = json.loads(output)
        except ValueError:
            return
        if not isinstance(message, dict) or message.get("error") or message.get("type") in {"sent", "drafts"}:
            return
        ids = frozenset(str(message[key]) for key in ("_id", "id", "messageId", "message_id") if message.get(key))
        recipients = _email_addresses(message.get("reply_to") or message.get("replyTo") or message.get("from"))
    elif channel == "gmail":
        header = re.match(r"(?:Matched [^\n]*\n)?Message ID: ([^\n]+)\nFrom: ([^\n]+)(?:\nReply-To: ([^\n]+))?", output)
        if not header:
            return
        ids = frozenset({header[1].strip()})
        recipients = _email_addresses(header[3] or header[2])
    else:
        target = _whatsapp_target(args)
        if tool == "whatsapp_toolkit":
            try:
                rows = json.loads(output).get("messages") or []
            except (ValueError, AttributeError):
                return
            if not rows:
                return
        elif not re.match(r"I pulled [1-9]\d* messages?\b", output):
            return
        ids = frozenset({target}) if target else frozenset()
        recipients = {target} if target else set()
    if not ids:
        return
    destination = Destination(channel, next(iter(recipients)) if len(recipients) == 1 else "", ids)
    chat = _chat_id(owner)
    turn = _user_history(owner, text)[-1]
    saved_chat, saved_turn, saved = getattr(owner, "_reply_read_focus", (None, None, set()))
    if saved_chat != chat or saved_turn != turn:
        saved = set()
    # Re-reading the same message is one destination, even if an alias was returned.
    saved = {item for item in saved if item.channel != channel or not item.message_ids & ids}
    owner._reply_read_focus = (chat, turn, saved | {destination})


def reply_tool_block_reason(owner, tool: str, action: str, args: dict, text: str) -> str:
    channel, mode = _tool_route(tool, action)
    if not channel or not text or getattr(owner, "_is_automation_turn", False) is True:
        return ""
    status = bool(_STATUS.search(text))
    draft = bool(_DRAFT.search(_SEND_THE_DRAFT.sub("", text)) or _EDIT_DRAFT.match(text) or _accepted_draft_offer(owner, text))
    if mode in {"draft", "send"} and status:
        return "Blocked reply change: the user asked for draft or send status. Check existing records only."
    if mode == "draft" and _DONT_DRAFT.search(text):
        return "Blocked draft: the user asked to keep drafts unchanged."
    if mode == "send" and (draft or _DONT_SEND.search(text)):
        return "Blocked send: the user asked for a draft. Keep the response unsent."
    if mode == "send" and _READ_REQUEST.match(text) and not _SEND_REQUEST.search(text):
        return "Blocked send: the user asked to read or check a message."
    if mode == "send" and tool == "create_ticket" and not _SEND_REQUEST.search(text):
        return "Blocked WhatsApp send: the user has not requested a send in this turn."
    if not (_is_follow_up(text) or mode in {"draft", "send"}):
        return ""
    channels, destinations = _focus(owner, text)
    explicit = _user_channels(text)
    switching = bool(explicit and not destinations)
    if len(channels) > 1 and mode in {"draft", "send"}:
        return "Blocked reply: several channels are in scope. Ask which channel and thread to use."
    if len(channels) == 1 and channel not in channels:
        expected = next(iter(channels))
        return f"Blocked wrong reply channel. Continue with {_TOOLS.get(expected, expected)} using the message already discussed."
    if channel == "whatsapp" and _EMAIL.search(text) and channel not in explicit:
        return "Blocked WhatsApp for an email request. Use the mailbox already discussed or ask which mailbox."
    params = _mail_params(args)
    if mode in {"read", "discover"}:
        if draft and not channels:
            return "Blocked lookup for an unscoped draft. Ask which channel and thread to use."
        if draft:
            target = _whatsapp_target(params) if channel == "whatsapp" else str(
                params.get("message_id") or params.get("id") or params.get("email_id") or ""
            ).strip()
            is_latest = action.removeprefix("whatsapp_").removeprefix("wa_") in {
                "read_latest_mail", "find_and_read_email", "latest_activity",
            }
            fresh_read = bool(_READ_REQUEST.match(text) and (explicit or target))
            if not fresh_read and (is_latest or (
                mode == "read" and target and len(destinations) == 1
                and target not in next(iter(destinations)).message_ids
            )):
                return "Blocked lookup of a different message for this reply. Reuse the message already read."
        return ""
    if mode == "draft" and not draft:
        return "Blocked draft: the user has not asked to write or change a draft in this turn."
    if not channels and (mode == "draft" or _is_follow_up(text)):
        return "Blocked reply: no channel is established. Ask which channel and thread to use."
    whatsapp_target = _whatsapp_target(params) if channel == "whatsapp" else ""
    if channel == "whatsapp" and params.get("jid"):
        if _whatsapp_target({"jid": params["jid"]}) != whatsapp_target:
            return "Blocked conflicting WhatsApp targets. Use only the verified chat address."
    if not destinations:
        if mode == "draft" and not switching:
            return "Blocked reply: the channel is known but the destination is unverified. Read the exact message or ask which thread to use."
        return ""

    if channel == "whatsapp":
        mentioned = whatsapp_target in re.findall(r"\d+", text)
        if mentioned or (len(destinations) == 1 and whatsapp_target and whatsapp_target == next(iter(destinations)).recipient):
            return ""
    else:
        requested = _email_addresses(_EMAIL_ADDRESS.findall(text))
        to = _email_addresses(params.get("to"))
        copied = _email_addresses(params.get("cc")) | _email_addresses(params.get("bcc"))
        source_ref = params.get("inReplyTo") if channel == "mailshot" else params.get("message_id") or params.get("id")
        reference = str(source_ref or "").strip()
        if copied - requested:
            return "Blocked new copy recipients. Verify who should be copied before saving or sending the reply."
        verified_recipients = {item.recipient for item in destinations}
        if requested and to | copied == requested and not reference and not (to & verified_recipients):
            return ""
        if len(destinations) == 1:
            selected = next(iter(destinations))
            if (not reference or reference in selected.message_ids) and (
                (to and to == {selected.recipient}) or (not to and action == "reply_email" and reference)
            ):
                if mode == "draft" and not reference:
                    field = "inReplyTo" if channel == "mailshot" else "message_id"
                    return f"Blocked detached reply draft. Pass the verified original message as {field} so the draft stays in the same thread."
                return ""
    return "Blocked reply destination change. Use the verified recipient and message or ask which one the user means."
