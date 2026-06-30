"""
R27 — Voice phrase detection for proactive / planner workflows.

Matched from ``TranscriptionFrame`` text (already lowercased by caller).
"""

from __future__ import annotations

import json
import logging
import re
import string
from typing import Literal

from distr.core.initiative.draft_queue import DraftEntry

logger = logging.getLogger(__name__)

# Standard draft queue ids are UUID strings (``service.py``).
_UUID_FULL = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)
_UUID_HEX32 = re.compile(r"\b([0-9a-f]{32})\b", re.IGNORECASE)

# Agenda / planner readouts (keep phrases short to reduce false positives)
_AGENDA_HINTS = (
    "what's on my agenda",
    "what is on my agenda",
    "whats on my agenda",
    "on my agenda",
    "read my planner",
    "read the planner",
    "read my day plan",
    "my day plan",
    "today's plan",
    "todays plan",
    "my schedule today",
    "weekly outlook",
    "read my week plan",
    "month plan summary",
)


def wants_agenda_readout(text_lower: str) -> bool:
    """True when the user is asking for planner / agenda content by voice."""
    words = text_lower.split()
    if len(words) > 16:
        return False
    return any(h in text_lower for h in _AGENDA_HINTS)


_PENDING_DRAFT_HINTS = (
    "pending approval",
    "pending approvals",
    "pending draft",
    "pending drafts",
    "read pending draft",
    "read my draft",
    "read the draft",
    "what needs approval",
    "what needs my approval",
    "approval queue",
    "any pending actions",
    "pending actions",
    "initiative draft",
)


def wants_pending_draft_readout(text_lower: str) -> bool:
    """True when the user wants the next initiative draft read aloud before deciding."""
    words = text_lower.split()
    if len(words) > 18:
        return False
    return any(h in text_lower for h in _PENDING_DRAFT_HINTS)


def match_voice_wait_cancel(text_lower: str) -> bool:
    """
    Short utterances that cancel a timed voice confirmation / readout wait
    without approving or rejecting a draft.
    """
    words = text_lower.split()
    if len(words) > 4:
        return False
    t = text_lower.strip()
    if t in ("never mind", "forget it", "not now", "skip"):
        return True
    if len(words) == 1 and t == "cancel":
        return True
    return False


def match_schedule_confirm(text_lower: str) -> bool:
    """User confirms saving a parsed voice reminder as a ``ProactiveTask`` row."""
    words = text_lower.split()
    if len(words) > 8:
        return False
    t = text_lower.strip()
    if t in (
        "confirm schedule",
        "confirm reminder",
        "yes add it",
        "yes schedule it",
        "schedule it",
        "add it now",
        "save it",
        "save reminder",
    ):
        return True
    if len(words) <= 3 and t.startswith("yes ") and ("save" in t or "add" in t):
        return True
    return False


def match_draft_decision(text_lower: str) -> Literal["approve", "reject"] | None:
    """
    Map short approve/reject utterances to a draft-queue decision.

    Uses conservative word-count guards so normal chat is not swallowed.
    """
    words = text_lower.split()
    n = len(words)

    if n <= 5:
        if any(
            p in text_lower
            for p in (
                "approve that",
                "approve it",
                "yes approve",
                "yes continue",
                "continue",
                "go ahead",
                "go ahead and approve",
                "go ahead and continue",
                "yes go ahead",
                "accept that",
                "accept it",
                "execute it",
                "execute this",
                "execute the work",
                "run the work",
                "run now",
                "run this",
                "run this now",
                "do the work",
                "execute now",
                "execute this now",
                "go and execute",
                "go ahead and execute",
            )
        ):
            return "approve"
        if any(
            p in text_lower
            for p in (
                "reject that",
                "reject it",
                "cancel that",
                "dismiss that",
                "no reject",
            )
        ):
            return "reject"

    if n == 1 and words[0] == "approve":
        return "approve"
    if n == 1 and words[0] == "reject":
        return "reject"
    if n == 2 and words[0] == "go" and words[1] == "ahead":
        return "approve"

    return None


def _norm_draft_id_token(raw: str) -> str:
    return raw.strip().lower().replace("-", "")


def extract_draft_id_token(text_lower: str) -> str | None:
    """
    Pull a draft id substring from free text: full UUID, 32-char hex, or 8–31 hex prefix.
    Prefer the longest / most structured match.
    """
    t = text_lower.strip()
    if len(t) > 220:
        return None
    m = _UUID_FULL.search(t)
    if m:
        return m.group(1).lower()
    m32 = _UUID_HEX32.search(t.replace(" ", ""))
    if m32:
        return m32.group(1).lower()
    # Last 8–31 hex token (voice often drops hyphens)
    for tok in reversed(t.split()):
        tok = tok.strip(string.punctuation)  # noqa: PLW2901
        if re.fullmatch(r"[0-9a-f]{8,31}", tok, re.IGNORECASE):
            return tok.lower()
    return None


def resolve_draft_entry_by_voice_id(
    token: str | None, entries: list[DraftEntry],
) -> tuple[DraftEntry | None, Literal["none", "one", "ambiguous"]]:
    """
    Map a voice id (full UUID, 32-char hex, or prefix) to at most one ``DraftEntry``.
    """
    if not token or not entries:
        return None, "none"

    tnorm = _norm_draft_id_token(token)
    if not tnorm:
        return None, "none"

    def entry_norm(eid: str) -> str:
        return eid.lower().replace("-", "")

    exact = [e for e in entries if entry_norm(e.id) == tnorm]
    if len(exact) == 1:
        return exact[0], "one"
    if len(exact) > 1:
        return None, "ambiguous"

    prefixed = [e for e in entries if entry_norm(e.id).startswith(tnorm)]
    if len(prefixed) == 1:
        return prefixed[0], "one"
    if len(prefixed) > 1:
        return None, "ambiguous"
    return None, "none"


def match_draft_decision_for_id(text_lower: str) -> tuple[Literal["approve", "reject"], str] | None:
    """
    ``approve draft <uuid>`` / ``reject id <prefix>`` style commands.

    Returns ``(decision, raw_id_token)``; caller resolves the token against the queue.
    """
    words = text_lower.split()
    if len(words) > 14 or len(text_lower) > 220:
        return None
    t = text_lower.strip()
    for lead, decision in (
        ("approve draft ", "approve"),
        ("approve the draft ", "approve"),
        ("approve id ", "approve"),
        ("approve that draft ", "approve"),
        ("reject draft ", "reject"),
        ("reject the draft ", "reject"),
        ("reject id ", "reject"),
        ("reject that draft ", "reject"),
    ):
        if t.startswith(lead):
            tok = extract_draft_id_token(t[len(lead) :])
            if tok:
                return (decision, tok)  # type: ignore[arg-type]
            return None
    # ``approve <uuid>`` / ``reject <uuid>`` when last token looks like an id
    for verb, decision in (("approve ", "approve"), ("reject ", "reject")):
        if t.startswith(verb):
            rest = t[len(verb) :].strip()
            if not rest or " " in rest:
                continue
            tok = extract_draft_id_token(rest)
            if tok and rest == rest.split()[0]:
                return (decision, tok)  # type: ignore[arg-type]
    return None


_READ_DRAFT_ID_LEADS = (
    "read draft ",
    "read the draft ",
    "speak draft ",
    "read pending draft ",
    "pending draft ",
)


def match_read_draft_by_id_request(text_lower: str) -> str | None:
    """
    ``read draft <uuid>`` — returns raw id token, or None if this is a generic readout phrase.
    """
    t = text_lower.strip()
    if len(t) > 220:
        return None
    for lead in _READ_DRAFT_ID_LEADS:
        if t.startswith(lead):
            tail = t[len(lead) :].strip()
            if not tail:
                return None
            return extract_draft_id_token(tail)
    return None


_REMIND_RE = re.compile(
    r"^remind me to\s+(.+?)\s+(daily|weekly|every day|each day|every week)\s*\.?$",
    re.IGNORECASE | re.DOTALL,
)


def match_reminder_request(text_lower: str) -> dict | None:
    """
    Parse ``remind me to … daily`` style requests.

    Returns ``{"instruction": str, "frequency": str}`` or None.
    """
    m = _REMIND_RE.match(text_lower.strip())
    if not m:
        return None
    instruction = (m.group(1) or "").strip()
    freq_raw = (m.group(2) or "").strip().lower()
    if not instruction or len(instruction) > 500:
        return None
    if "week" in freq_raw:
        frequency = "weekly"
    else:
        frequency = "daily"
    return {"instruction": instruction, "frequency": frequency}


def load_latest_planner_markdown() -> str | None:
    """Load the most recent saved planner markdown from SQLite."""
    try:
        from distr.core.db import get_session
        from distr.core.db.planner_output import fetch_latest_planner

        with get_session() as session:
            row = fetch_latest_planner(session, scope=None)
            if not row or not (row.content or "").strip():
                return None
            return (row.content or "").strip()
    except Exception:
        logger.warning("voice_commands: failed to load latest planner", exc_info=True)
        return None


def save_voice_reminder_proactive_task(*, instruction: str, frequency: str) -> tuple[bool, str]:
    """
    Insert a user ``ProactiveTask`` from a confirmed voice reminder phrase.

    ``frequency`` must be ``daily`` or ``weekly`` (normalized by ``match_reminder_request``).
    Returns ``(ok, message)`` where ``message`` is a human-readable summary or error detail.
    """
    inst = (instruction or "").strip()
    if not inst:
        return False, "empty instruction"
    if len(inst) > 4000:
        inst = inst[:4000]
    freq = (frequency or "daily").lower()
    if freq not in ("daily", "weekly"):
        freq = "daily"
    name = f"Voice reminder: {inst[:52]}"
    if len(name) > 120:
        name = name[:117] + "..."
    day_val = "monday" if freq == "weekly" else None
    time_val = "09:00"
    try:
        from distr.core.db import get_session
        from distr.core.db.proactive import ProactiveTask

        row = ProactiveTask(
            name=name,
            frequency=freq,
            time=time_val,
            day=day_val,
            instruction=inst,
            enabled=True,
            priority=55,
            tier=1,
            conditions=json.dumps({"source": "voice_reminder"}),
            outcome_history=json.dumps([]),
        )
        with get_session() as session:
            session.add(row)
        return True, name
    except Exception as e:
        logger.warning("save_voice_reminder_proactive_task failed: %s", e, exc_info=True)
        return False, str(e)
