"""Pinned owner identity for every chat persona.

On 13 Nov Paul opened a chat with the Hayley Williams agent and said "Hello,
my name is Paul." The agent replied "Hey Paul, good to meet you", treating the
owner of the install as a stranger. The owner block is placed right after the
persona (so agent personas and the condensed local-model prompt keep it) and
says who the owner is and that a self-introduction is not a first meeting.
"""

from __future__ import annotations

import os
import re

_GENERIC = {"", "user", "root", "admin", "owner"}


def owner_name(fallback: str = "") -> str:
    """Owner's first name: DECISIONS_OWNER_NAME, then the OS account's first name."""
    configured = str(os.environ.get("DECISIONS_OWNER_NAME") or "").strip()
    name = configured or str(fallback or "").strip()
    first = name.split()[0] if name.split() else ""
    return "" if first.lower() in _GENERIC else first


def owner_identity_block(name: str) -> str:
    if not name:
        return ""
    return (
        "## Owner\n"
        f"{name} owns this Decisions install and is the person you work for; every chat and agent here is "
        f"{name}'s. You already know {name}. If someone says \"my name is {name}\", \"it's {name}\" or "
        f"\"I'm {name}\", that is {name} re-introducing themselves: greet them as someone you know "
        "(e.g. \"Hey " + name + ", good to have you back\"), never as a first meeting, and never say \"nice to "
        "meet you\" or \"good to meet you\" to them. Only treat a speaker as someone else when they give a "
        f"different name, and then remember {name} is still the owner."
    )


def introduces_as_owner(text: str, name: str) -> bool:
    if not name:
        return False
    pattern = rf"\b(?:my\s+name\s+is|i'?m|i\s+am|it'?s|this\s+is)\s+{re.escape(name)}\b"
    return bool(re.search(pattern, str(text or ""), re.IGNORECASE))
