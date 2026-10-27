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
}

DEFAULT_CHANNEL = "email"

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


def resolve_reply_target(ticket: Any) -> ReplyTarget:
    """Resolve a ticket's provenance into a sendable reply target.

    Reports what is missing instead of guessing a channel, so a ticket with no
    provenance cannot be auto-replied to by accident.
    """
    provider = _get(ticket, "source_provider", "") or _get(ticket, "external_source", "")
    channel = normalize_channel(provider)
    profile = channel_profile(channel)

    thread_id = str(
        _get(ticket, "source_thread_id", "")
        or _get(ticket, "whatsapp_message_wa_id", "")
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
    if not thread_id and not url:
        missing.append("source_thread_id")
    if profile.key in {"email", "mailshot"} and not contact:
        missing.append("source_contact")

    target = ReplyTarget(
        channel=profile.key,
        label=profile.label,
        profile=profile,
        thread_id=thread_id,
        contact=contact,
        url=url,
        can_send=not missing,
        missing=missing,
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
        if not body.lower().startswith(("hi ", "hello ", "hey ")):
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
