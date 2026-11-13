"""Close-the-loop replies, shaped for the channel the ticket came from.

A ticket's job is not done when the code lands. It is done when the person who
asked gets a decent answer. This module resolves a ticket's provenance to a
channel, shapes the reply for that channel, and refuses to send anything that
looks like it is promising work that did not happen.

Nothing here sends by itself: it produces the text and the target, so the send
stays an explicit, auditable action.
"""

from __future__ import annotations

import re
from email.errors import HeaderParseError
from email.headerregistry import Address
from email.utils import getaddresses
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ChannelProfile:
    """How a reply should read on one channel."""

    key: str
    label: str
    max_chars: int
    greeting: bool
    signoff: bool
    # One line of tone guidance handed to the model writing the reply.
    tone: str
    supports_thread_reply: bool = True


CHANNEL_PROFILES: dict[str, ChannelProfile] = {
    "whatsapp": ChannelProfile(
        key="whatsapp",
        label="WhatsApp",
        max_chars=700,
        greeting=False,
        signoff=False,
        tone="Short and human, like texting a colleague. No greeting, no sign-off, no bullet lists.",
    ),
    "telegram": ChannelProfile(
        key="telegram",
        label="Telegram",
        max_chars=700,
        greeting=False,
        signoff=False,
        tone="Short and direct. No greeting, no sign-off, no bullet lists.",
    ),
    "email": ChannelProfile(
        key="email",
        label="Email",
        max_chars=2200,
        greeting=True,
        signoff=True,
        tone="Plain professional prose. One short greeting, at most three short paragraphs, a simple sign-off.",
    ),
    "mailshot": ChannelProfile(
        key="mailshot",
        label="Mailshot",
        max_chars=2200,
        greeting=False,
        signoff=True,
        tone="Broadcast email. No personal greeting, no 'I hope this finds you well', get to the point.",
    ),
    "jira": ChannelProfile(
        key="jira",
        label="Jira",
        max_chars=1400,
        greeting=False,
        signoff=False,
        tone="Concise tracker comment: what changed, how it was verified, what is outstanding.",
    ),
    "trello": ChannelProfile(
        key="trello",
        label="Trello",
        max_chars=1400,
        greeting=False,
        signoff=False,
        tone="Concise card comment: what changed, how it was verified, what is outstanding.",
    ),
    "web": ChannelProfile(
        key="web",
        label="App",
        max_chars=1400,
        greeting=False,
        signoff=False,
        tone="Brief in-app update.",
        supports_thread_reply=False,
    ),
    "tensology": ChannelProfile(
        key="tensology",
        label="Tensology",
        max_chars=1400,
        greeting=False,
        signoff=False,
        tone="Concise tracker update: what changed, how it was verified, what is outstanding.",
        # The Tensology integration posts the execution report itself, so this
        # module must not try to email anyone on its behalf.
        supports_thread_reply=False,
    ),
}

# Shaping fallback only (tone/length for an unrecognised name). It is NEVER a delivery
# channel: resolve_reply_target() reports an unknown or missing provider as channel
# "unknown" with can_send=False, so nothing is sent on a channel the message didn't come from.
DEFAULT_CHANNEL = "email"

UNKNOWN_PROFILE = ChannelProfile(
    key="unknown",
    label="Unknown channel",
    max_chars=700,
    greeting=False,
    signoff=False,
    tone="Draft only. The original channel is unknown, so this reply cannot be sent.",
    supports_thread_reply=False,
)

# Aliases seen in ticket provenance that map onto a profile.
_CHANNEL_ALIASES = {
    "gmail": "email",
    "google_mail": "email",
    "mail": "email",
    "outlook": "email",
    "whats_app": "whatsapp",
    "wa": "whatsapp",
    "tg": "telegram",
    "jira_cloud": "jira",
    "trello_card": "trello",
    "campaign": "mailshot",
    "bulk_email": "mailshot",
    "tensology": "tensology",
    "tensology_board": "tensology",
    "website": "web",
    "app": "web",
}

# Phrases that promise more than a workflow can honestly claim.
_PROMISE_PATTERNS = (
    r"\bi(?:'ve| have)? (?:just )?(?:deployed|shipped|released)\b",
    r"\bit(?:'s| is) (?:now )?live in production\b",
    r"\bwe(?:'ve| have) (?:deployed|shipped|released)\b",
    r"\bguaranteed?\b",
)

_EM_DASH_RE = re.compile(r"\s*[\u2014\u2013]\s*")
_FILLER_RE = re.compile(
    r"(?i)\b(?:i hope this (?:email|message) finds you well|"
    r"hope you(?:'re| are) (?:doing )?well|"
    r"just wanted to (?:reach out|follow up)|"
    r"please do not hesitate to reach out)\b[,.]?\s*"
)


def normalize_channel(value: Any) -> str:
    """Map a provenance value onto a known channel profile key."""
    raw = str(value or "").strip().lower().replace("-", "_")
    if not raw:
        return DEFAULT_CHANNEL
    if raw in CHANNEL_PROFILES:
        return raw
    return _CHANNEL_ALIASES.get(raw, DEFAULT_CHANNEL)


def channel_profile(value: Any) -> ChannelProfile:
    return CHANNEL_PROFILES.get(normalize_channel(value)) or CHANNEL_PROFILES[DEFAULT_CHANNEL]


def find_promise_claims(text: str) -> list[str]:
    """Return any phrases that read as promises about work that may not be live."""
    found: list[str] = []
    for pattern in _PROMISE_PATTERNS:
        for match in re.finditer(pattern, str(text or ""), flags=re.IGNORECASE):
            found.append(match.group(0).strip())
    return found


def humanize_reply(text: str) -> str:
    """Strip the tell-tale AI register without changing the facts.

    Applies the rules we already hold elsewhere: no em dashes, no filler
    openers, no double spaces. It never invents or removes information.
    """
    out = str(text or "")
    out = _EM_DASH_RE.sub(", ", out)
    out = _FILLER_RE.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


@dataclass
class ReplyTarget:
    """Where a reply should go and what shape it should take."""

    channel: str
    label: str
    profile: ChannelProfile
    thread_id: str = ""
    contact: str = ""
    url: str = ""
    can_send: bool = False
    missing: list[str] = field(default_factory=list)
    # Provider message id to reply to, when it isn't the ticket's own source_external_id
    # (proposal-born email tickets). Not part of to_dict(); delivery-only.
    message_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "label": self.label,
            "thread_id": self.thread_id,
            "contact": self.contact,
            "url": self.url,
            "can_send": self.can_send,
            "missing": list(self.missing),
            "max_chars": self.profile.max_chars,
        }


def _get(source: Any, name: str, default: Any = "") -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _reply_mailbox(value: str) -> str:
    """Accept one mailbox, without guessing an address from a display label."""
    if not value or any(char in value for char in "\r\n\x00"):
        return ""
    # Provider contacts may contain a display name. Only strip an explicit
    # angle-address wrapper; permissive address-list parsers can silently repair
    # malformed inputs or accept a recipient list where one origin is expected.
    wrapped = re.fullmatch(r'[^<>]+<([^<>]+)>', value)
    if wrapped and len(getaddresses([value])) != 1:
        return ""
    candidate = wrapped.group(1).strip() if wrapped else value
    try:
        address = Address(addr_spec=candidate)
    except (ValueError, HeaderParseError):
        return ""
    return address.addr_spec if address.username and address.domain else ""


_PROPOSAL_REF_RE = re.compile(r"^local:(whatsapp|telegram|gmail|mailshot):(.+):[0-9a-f]{12,64}$")


def proposal_origin(ticket: Any) -> dict[str, Any] | None:
    """The original source conversation behind an Incoming-proposal ticket, or None.

    Only the explicit handoff reference is used; nothing is guessed. For a proposal that came
    from email, the reply address comes from the original inbound message the proposal kept
    (see ``_proposal_mail_contact``). If there is none, source_contact stays empty and the
    ticket keeps reporting what's missing.
    """
    ref = str(_get(ticket, "source_external_id", "") or "").strip()
    match = _PROPOSAL_REF_RE.match(ref)
    if not match:
        return None
    source, thread = match.group(1), match.group(2).strip()
    if not thread:
        return None
    contact = _get(ticket, "source_contact", "") or ""
    message_id = ""
    if source in {"gmail", "mailshot"}:
        found_contact, message_id = _proposal_mail_contact(ticket, source, thread)
        # An explicit, valid contact already on the ticket wins; otherwise use the original sender.
        if not _reply_mailbox(str(contact).strip()):
            contact = found_contact
    topic_unavailable = source == "telegram" and _proposal_telegram_topic_unavailable(ticket, thread)
    return {
        "source_provider": source,
        "source_thread_id": thread,
        "source_contact": contact,
        "source_url": _get(ticket, "source_url", "") or "",
        "source_external_id": "",
        "external_id": "",
        "source_message_id": message_id,
        **({"telegram_topic_unavailable": True} if topic_unavailable else {}),
    }


def _retained_proposal_for_ticket(ticket: Any) -> dict[str, Any] | None:
    """The proposal brief kept on the ticket's Development thread (no network)."""
    chat_id = _get(ticket, "source_chat_id", None)
    if not chat_id:
        return None
    try:
        from sqlalchemy.orm import object_session
        from sqlalchemy.orm.exc import UnmappedInstanceError
        from distr.core.db import Chat, get_session
        from distr.core.incoming.source_reply import _retained_proposal

        try:
            owner = object_session(ticket)
        except UnmappedInstanceError:
            owner = None
        if owner is not None:
            chat = owner.get(Chat, int(chat_id))
            return _retained_proposal(chat) if chat is not None else None
        with get_session() as db:
            chat = db.get(Chat, int(chat_id))
            return _retained_proposal(chat) if chat is not None else None
    except Exception:
        return None


def _proposal_mail_contact(ticket: Any, source: str, thread: str) -> tuple[str, str]:
    """(reply address, provider message id) of the latest inbound email the proposal came from.

    Uses only the proposal's own record of that conversation: its primary (inbound) message
    ids for this exact thread, and the sender address stored with that exact message. The
    address must parse as exactly one mailbox. No display-name or domain guessing, no other
    thread, no address book. Delivery then re-reads that message from Gmail/Mailshot and
    replies to its Reply-To/From, so the provider stays the authority. ('', '') when absent.
    """
    proposal = _retained_proposal_for_ticket(ticket)
    if not isinstance(proposal, dict):
        return "", ""
    try:
        from distr.core.incoming.source_reply import source_choices

        choices = {c["key"]: c for c in source_choices(proposal)}
    except Exception:
        return "", ""
    choice = choices.get(f"{source}:{thread}")
    if not choice or not choice.get("primary_message_ids"):
        return "", ""
    message_id = str(choice["primary_message_ids"][-1])
    evidence = [row for field in ("evidence", "context_evidence") for row in proposal.get(field) or []
                if isinstance(row, dict)]
    contact = ""
    for row in evidence:
        if str(row.get("id") or row.get("source_message_id") or "") != message_id:
            continue
        if str(row.get("direction") or "").lower() == "outbound" or row.get("context_only"):
            continue
        for field in ("reply_to", "sender_email", "sender", "sender_name", "from"):
            candidate = _reply_mailbox(str(row.get(field) or "").strip())
            if candidate:
                contact = candidate
                break
        if contact:
            break
    return contact, message_id


def _proposal_telegram_topic_unavailable(ticket: Any, thread: str) -> bool:
    from distr.core.db import get_session
    from distr.core.incoming.source_reply import source_choices
    from distr.core.incoming.telegram_topics import split_telegram_source, telegram_source_has_topic, telegram_source_matches_topic

    _, topic = split_telegram_source(thread)
    proposal = _retained_proposal_for_ticket(ticket)
    if proposal is None:
        return topic is not None
    source = next((choice for choice in source_choices(proposal) if choice["key"] == f"telegram:{thread}"), None)
    if source is None:
        return topic is not None
    with get_session() as db:
        if topic is not None:
            return not telegram_source_matches_topic(db, thread, source["primary_message_ids"])
        return telegram_source_has_topic(db, thread, source["primary_message_ids"])


def resolve_reply_target(ticket: Any) -> ReplyTarget:
    """Resolve a ticket's provenance into a sendable reply target.

    Reports what is missing instead of guessing a channel, so a ticket with no
    provenance cannot be auto-replied to by accident.

    Same-channel rule (Paul, 12 Nov 2026): the reply target is always the channel the
    request came in on (a proposal-born ticket uses the proposal's original source).
    There is no "preferred" or "best" channel, and an unknown or missing provider is
    reported as channel "unknown" (can_send=False) rather than defaulting to email.
    """
    provider = _get(ticket, "source_provider", "") or _get(ticket, "external_source", "")
    origin = proposal_origin(ticket) if str(provider or "").strip().lower() == "incoming_proposal" else None
    if origin is not None:
        # Proposal-born tickets keep the original conversation in source_external_id
        # ("local:<source>:<thread>:<digest>"). Reply where the request came from.
        return resolve_reply_target(origin)
    channel = normalize_channel(provider)
    profile = channel_profile(channel)

    thread_id = str(
        _get(ticket, "source_thread_id", "")
        or _get(ticket, "whatsapp_message_wa_id", "")
        or _get(ticket, "source_external_id", "")
        or _get(ticket, "external_id", "")
        or ""
    ).strip()
    contact = str(_get(ticket, "source_contact", "") or "").strip()
    url = str(
        _get(ticket, "source_url", "") or _get(ticket, "external_url", "") or ""
    ).strip()

    missing: list[str] = []
    if not provider:
        missing.append("source_provider")
    else:
        raw_provider = str(provider).strip().lower().replace("-", "_")
        if raw_provider not in CHANNEL_PROFILES and raw_provider not in _CHANNEL_ALIASES:
            missing.append("unsupported_source_provider")
    if missing:
        # Never relabel an unknown origin as email (or anything else).
        profile = UNKNOWN_PROFILE
    elif str(provider).strip().lower() == "outlook":
        # Outlook in must be Outlook out. There is no Outlook sender here, so never reply via Gmail.
        missing.append("outlook_reply_not_supported")
    if profile.key == "whatsapp":
        # The other identifiers above are provider message IDs, not chat JIDs.
        # Canonical WhatsApp intake stores msg.jid / msg.jid_phone here.
        thread_id = str(_get(ticket, "source_thread_id", "") or "").strip()
        if not thread_id:
            missing.append("source_thread_id")
    elif not thread_id and not url:
        missing.append("source_thread_id")
    if profile.key == "telegram":
        from distr.core.incoming.telegram_topics import split_telegram_source, telegram_topic_id

        _, topic = split_telegram_source(thread_id)
        if _get(ticket, "telegram_topic_unavailable", False) or (topic is not None and telegram_topic_id(topic) is None):
            missing.append("telegram_topic_delivery_unavailable")
    if profile.key in {"email", "mailshot"}:
        contact = _reply_mailbox(contact)
        if not contact:
            missing.append("source_contact")
    if profile.key == "tensology":
        # Delivered by the Tensology integration, not by an outbound adapter here.
        missing.append("handled_by_tensology_integration")

    target = ReplyTarget(
        channel=profile.key,
        label=profile.label,
        profile=profile,
        thread_id=thread_id,
        contact=contact,
        url=url,
        can_send=not missing,
        missing=missing,
        message_id=str(_get(ticket, "source_message_id", "") or "").strip() if isinstance(ticket, dict) else "",
    )
    return target


def shape_reply(text: str, channel: Any, *, contact: str = "") -> str:
    """Shape a reply body for a channel: tone, greeting, sign-off and length."""
    profile = channel_profile(channel)
    body = humanize_reply(text)
    if not body:
        return ""

    if profile.greeting:
        name = str(contact or "").strip()
        # Only greet by first name when it looks like a person, not an address.
        first = name.split("@")[0].split()[0] if name and "@" not in name else ""
        greeting = f"Hi {first}," if first else "Hi,"
        if not re.match(r'^(?:hi|hello|hey)(?:[\s,!:.]|$)', body, re.IGNORECASE):
            body = f"{greeting}\n\n{body}"

    if profile.signoff and not re.search(r"(?i)\b(regards|thanks|cheers)\b\s*,?\s*$", body):
        body = f"{body}\n\nThanks"

    if len(body) > profile.max_chars:
        cut = body[: profile.max_chars].rsplit(" ", 1)[0].rstrip()
        body = cut + "..."

    return body.strip()


def build_reply_instruction(ticket: Any, *, result_summary: str = "") -> str:
    """Return the phase instruction a workflow uses to close the loop.

    Includes the channel rules and provenance so the writing step has what it
    needs, and states plainly that it must not claim work is live unless it is.
    """
    target = resolve_reply_target(ticket)
    profile = target.profile

    lines = [
        f"[REPLY - {profile.label.upper()}]",
        f"Tone: {profile.tone}",
        f"Keep it under about {profile.max_chars} characters.",
    ]
    if target.contact:
        lines.append(f"Recipient: {target.contact}")
    if target.thread_id:
        lines.append(f"Reply on the existing thread ({target.thread_id}). Do not start a new one.")
    if target.url:
        lines.append(f"Source: {target.url}")
    if not target.can_send:
        lines.append(
            "Provenance is incomplete (" + ", ".join(target.missing) + "). Draft the reply "
            "and report needs_input instead of sending."
        )
    if result_summary.strip():
        lines.append(f"What happened: {result_summary.strip()}")
    lines.append(
        "Only say something shipped or is live if the result packet proves it. "
        "Otherwise say what is done and what is still pending."
    )
    return "\n".join(lines)


def append_reply_phase(instruction: str, ticket: Any, *, result_summary: str = "") -> str:
    """Append the reply phase to a workflow instruction when provenance exists."""
    target = resolve_reply_target(ticket)
    if not target.can_send and not target.missing:
        return instruction
    block = build_reply_instruction(ticket, result_summary=result_summary)
    base = str(instruction or "").rstrip()
    return f"{base}\n\n{block}" if base else block
