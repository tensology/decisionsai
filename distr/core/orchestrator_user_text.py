"""One humanizing layer for orchestrator-facing user updates.

Chat notices, approval lines, workflow status, routing reasons, and spoken
announcements used to leave the engine as raw templates: enum names, ticket
IDs, internal slug blurbs, and telegraphic plan outlines. Every path that
shows or speaks an orchestrator update should run through
``humanize_orchestrator_update`` (and ``prepare_spoken_user_text`` for voice).
The function is idempotent so it is safe at more than one chokepoint.
"""

from __future__ import annotations

import re
import time
from typing import Iterable

# Throwaway live-test projects must not interrupt the operator's main chat or
# be read aloud. The notice can still be stored in the test thread itself.
_THROWAWAY_RE = re.compile(
    r"(?i)\b(?:safe to delete|e2e ux test(?:\s+\d+)?|e2e board-tool|e2e move me|e2e merge [ab])\b"
)
_TIER_ENUM_RE = re.compile(
    r"\s*\((?:APPROVE|ESCALATE|SILENT|NOTIFY|ASK|PolicyDecision\.[A-Z_]+)\)"
)
_WORKFLOW_ID_RE = re.compile(r"(?i)\bworkflow\s*#\s*\d+\b")
_BOARD_ID_RE = re.compile(r"(?i)\(?\s*board\s*id\s*\d+\s*\)?")
_PROJECT_ID_RE = re.compile(r"(?i)\(?\s*project\s*id\s*\d+\s*\)?")
_TOOL_NAME_RE = re.compile(
    r"\b(?:create_ticket|merge_ticket|move_ticket|update_ticket|list_tickets|"
    r"delete_ticket|execute_code|run_workflow|ticket_lane_move)\b"
)
_INTERNAL_SLUG_REASON_RE = re.compile(
    r"(?i)^\s*(?:implementation work|design work|existing project|deploy to)\b"
    r"|implements and verifies, and never deploys"
)
_APPROVAL_RE = re.compile(
    r"(?i)^waiting on your approval(?:\s*\([^)]*\))?\s*:\s*"
    r"(?P<board>.+?)\s+has\s+(?P<count>\d+)\s+backlog items?\s+"
    r"that should move into\s+(?P<lane>.+?)\s*$"
)
_STARTED_WF_BECAUSE_RE = re.compile(
    r"(?i)^started workflow\s+(?P<name>.+?)\s+because\s+(?P<detail>.+?)\s*$"
)
_STARTED_WF_ID_RE = re.compile(
    r"(?i)^started workflow\s*#\s*\d+(?:\s*:\s*|\s+)(?P<detail>.+)?$"
)
_STARTED_WF_PLAIN_RE = re.compile(
    r"(?i)^started workflow(?:\s+(?:in\s+)?(?P<name>.+?))?(?:\s*:\s*(?P<detail>.+))?$"
)
_HANDLED_DIRECT_RE = re.compile(
    r"(?i)^handled directly because\s+(?P<detail>.+?)\s*$"
)
_TICKET_CREATED_RE = re.compile(
    r"(?i)^ticket created in\s+(?P<lane>.+?)\.?\s*$"
)
_TICKET_MOVED_RE = re.compile(
    r"(?i)^ticket moved(?:\s*:\s*(?P<src>.+?)\s+to\s+(?P<dst>.+?)|\s+to\s+(?P<only>.+?))?\.?\s*$"
)
_NO_STEPS_RE = re.compile(
    r"(?i)^(?P<head>I couldn't finish this one|I stopped that run)\s*\.?\s*"
    r"No steps were recorded\s*\.?\s*$"
)
_AUDIT_EVIDENCE_RE = re.compile(
    r"(?i)the ticket now contains the recorded result and audit evidence"
)

_OUTLINE_HEADING_RE = re.compile(
    r"(?i)^(plan(?:\s+and\s+outcome)?|plan status|list|outcome|steps?)\s*[.:]*$"
)
_BARE_NUMBER_RE = re.compile(r"^\d+\.+$")
_REPORT_IDS_RE = re.compile(r"(?i)^report ticket ids?\b")
_PLAN_STEP_RE = re.compile(
    r"(?i)^(?:list(?:\s+the\s+(?:board|tickets?))?|move ticket\b|merge ticket\b|"
    r"report\b|keep(?:ing)?\s+\d+)\b"
)
_BLOCKED_MOVE_RE = re.compile(
    r"(?i)^(?:move\s+)?#?(?P<id>\d+)\s+to\s+(?P<lane>[^,]+),\s*blocked\.?$"
)
_BLOCKED_MERGE_RE = re.compile(
    r"(?i)^merge\s+#?(?P<a>\d+)\s+into\s+#?(?P<b>\d+)\s*,?\s*blocked\.?$"
)
_OUTCOME_HINT_RE = re.compile(
    r"(?i)\b(?:blocked|done|finished|couldn't|could not|refused|failed|"
    r"nothing (?:on the board |has )?changed|here's where|the board tools|"
    r"want me to|tell me to|i'll|i have|i've)\b"
)

# In-process dedupe for repeated proactive chatter.
_RECENT_EMITS: dict[str, float] = {}
_DEFAULT_EMIT_INTERVAL_SEC = 90.0


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _sentences(text: str) -> list[str]:
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    for chunk in re.split(r"\n+", clean):
        chunk = chunk.strip()
        if not chunk:
            continue
        bits = re.split(r"(?<=[.!?])\s+", chunk)
        parts.extend(bit.strip() for bit in bits if bit.strip())
    return parts


def _is_throwaway(text: str) -> bool:
    return bool(_THROWAWAY_RE.search(text or ""))


def _strip_machinery(text: str) -> str:
    out = _TIER_ENUM_RE.sub("", text)
    out = _WORKFLOW_ID_RE.sub("a workflow", out)
    out = _BOARD_ID_RE.sub("", out)
    out = _PROJECT_ID_RE.sub("", out)
    out = _TOOL_NAME_RE.sub("the board tools", out)
    out = out.replace("(safe to delete)", "")
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"\.{2,}", ".", out)
    out = re.sub(r"\s+,", ",", out)
    return _collapse(out).strip(" ,;")


def _item_phrase(count: str) -> str:
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 0
    if n == 1:
        return "a backlog item"
    return f"{n} backlog items"


def _rewrite_templates(text: str) -> str:
    raw = _collapse(text)
    if not raw:
        return ""

    already = raw[0].lower() + raw[1:] if raw else raw
    if already.startswith((
        "i need your go-ahead",
        "i'll handle this directly",
        "i started the ",
        "i started a workflow",
        "i've started the workflow",
        "i've added a ticket",
        "i moved the ticket",
        "the workflow finished",
        "the workflow is moving again",
        "the workflow is waiting",
        "the workflow was interrupted",
        "the workflow stopped before",
        "i'll hand this to development",
    )):
        return raw

    match = _APPROVAL_RE.match(raw)
    if match:
        board = _strip_machinery(match.group("board")).strip(" :,-")
        lane = match.group("lane").strip().rstrip(".")
        return (
            f"I need your go-ahead to move {_item_phrase(match.group('count'))} "
            f"on {board} into {lane}."
        )

    match = _NO_STEPS_RE.match(raw)
    if match:
        head = match.group("head")
        if "stopped" in head.lower():
            return "I stopped that run before any steps were logged."
        return "I couldn't finish this one, and no steps were logged."

    # Multi-line "I couldn't finish this one.\nNo steps were recorded."
    if re.search(r"(?i)no steps were recorded", raw) and re.search(
        r"(?i)i couldn't finish this one|i stopped that run", raw
    ):
        if re.search(r"(?i)i stopped that run", raw):
            return "I stopped that run before any steps were logged."
        return "I couldn't finish this one, and no steps were logged."

    lowered = raw.lower()
    if lowered == "the workflow started and is preparing the first phase." or lowered == (
        "the workflow started and is preparing the first phase"
    ):
        return "I've started the workflow and I'm getting the first step ready."
    if "the workflow resumed and dispatched the next phase" in lowered:
        return "The workflow is moving again, and I've sent the next step to the worker."
    if lowered.startswith("the workflow is waiting for a decision"):
        return "The workflow is waiting for a decision from you. I've left the details on the ticket."
    if "waiting to be resumed from its saved checkpoint" in lowered:
        return "The workflow was interrupted. I can pick it up from where it stopped when you say so."
    if lowered.startswith("the workflow completed") or _AUDIT_EVIDENCE_RE.search(raw):
        if "completed" in lowered or "finished" in lowered or _AUDIT_EVIDENCE_RE.search(raw):
            if "stopped before" in lowered or "failed" in lowered:
                return (
                    "The workflow stopped before it finished. "
                    "I've written the reason and the next step on the ticket."
                )
            return "The workflow finished, and I saved the result on the ticket."
    if lowered.startswith("the workflow stopped before completion"):
        return (
            "The workflow stopped before it finished. "
            "I've written the reason and the next step on the ticket."
        )

    match = _TICKET_CREATED_RE.match(raw)
    if match:
        return f"I've added a ticket in {match.group('lane').strip().rstrip('.')}."
    if re.match(r"(?i)^ticket created on the project board\.?$", raw):
        return "I've added a ticket on the project board."

    match = _TICKET_MOVED_RE.match(raw)
    if match:
        def _lane(value: str | None) -> str:
            return (value or "").strip().rstrip(".")
        src, dst, only = _lane(match.group("src")), _lane(match.group("dst")), _lane(match.group("only"))
        if src and dst:
            return f"I moved the ticket from {src} to {dst}."
        if only:
            return f"I moved the ticket to {only}."

    match = _STARTED_WF_BECAUSE_RE.match(raw)
    if match:
        name = match.group("name").strip()
        detail = match.group("detail").strip().rstrip(".")
        if _INTERNAL_SLUG_REASON_RE.search(detail) or not detail:
            return (
                f"I started the {name} workflow because this is a larger piece of work "
                "that should be planned, built, reviewed and checked."
            )
        detail = detail[0].lower() + detail[1:] if detail else detail
        return f"I started the {name} workflow because {detail}."

    match = _STARTED_WF_ID_RE.match(raw)
    if match:
        detail = _strip_machinery(match.group("detail") or "")
        detail = re.sub(r"^(?:on|with|for)\s+", "", detail, flags=re.I)
        detail = re.sub(r"\s{2,}", " ", detail).strip(" .")
        return f"I started a workflow: {detail}." if detail else "I started a workflow."

    match = _STARTED_WF_PLAIN_RE.match(raw)
    if match and raw.lower().startswith("started workflow"):
        name = (match.group("name") or "").strip()
        detail = (match.group("detail") or "").strip().rstrip(".")
        if name and detail:
            return f"I started a workflow in {name}: {detail}."
        if name:
            return f"I started a workflow in {name}."
        if detail:
            return f"I started a workflow: {detail}."
        return "I started a workflow."

    match = _HANDLED_DIRECT_RE.match(raw)
    if match:
        detail = match.group("detail").strip().rstrip(".")
        low = detail.lower()
        if "short, one-step" in low or "one-step change" in low:
            return "I'll handle this directly. It's a short, one-step change."
        if "question or a small request" in low:
            return (
                "I'll handle this directly. It reads as a question or a small request, "
                "not a multi-step piece of work."
            )
        if "you chose to send this as one agent turn" in low:
            return "I'll handle this directly, because you asked me to do it in one go."
        if "none of the built-in workflows" in low:
            return (
                "I'll handle this directly. None of the usual workflows is a fit, "
                "so I'll do it in one go."
            )
        if "could not be started" in low or "could not be started" in low:
            return (
                "I'll handle this directly. The workflow I wanted to start couldn't be "
                "opened, so I'll do it in one go."
            )
        if "one development-agent turn" in low:
            return "I'll handle this directly. I can do this in one go."
        detail = detail[0].lower() + detail[1:] if detail else detail
        return f"I'll handle this directly, because {detail}."

    if raw.lower().startswith("handled directly as a development hand-off"):
        return (
            "I'll hand this to Development as one turn. If the work is large, "
            "Development will start a workflow when the thread opens."
        )

    return _strip_machinery(raw)


def humanize_orchestrator_update(
    text: str,
    *,
    spoken: bool = False,
    drop_throwaway: bool = False,
) -> str:
    """Return a conversational update, or '' if the update should be dropped.

    ``drop_throwaway`` (also implied by ``spoken``) suppresses notices about
    live-test / "safe to delete" projects so they are not read into the room
    or posted into the operator's main chat.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    if (spoken or drop_throwaway) and _is_throwaway(raw):
        return ""
    human = _rewrite_templates(raw)
    human = _strip_machinery(human)
    if (spoken or drop_throwaway) and _is_throwaway(human):
        return ""
    if spoken:
        human = _humanize_spoken_agent_text(human or raw)
    human = re.sub(r"\.{2,}", ".", human.strip())
    return human


def _rewrite_spoken_sentence(sentence: str) -> str:
    text = _collapse(sentence)
    if not text:
        return ""
    if _OUTLINE_HEADING_RE.match(text) or _BARE_NUMBER_RE.match(text) or _REPORT_IDS_RE.match(text):
        return ""
    match = _BLOCKED_MOVE_RE.match(text)
    if match:
        lane = match.group("lane").strip()
        return f"I couldn't move that ticket to {lane}."
    match = _BLOCKED_MERGE_RE.match(text)
    if match:
        return "I couldn't merge those tickets."
    # Soften leftover "#345" markers for speech.
    text = re.sub(r"#(\d+)\b", r"ticket \1", text)
    text = re.sub(r"(?i)\bboard\s+(\d+)\b", "the board", text)
    return text


def _looks_like_plan_outline(sentences: Iterable[str]) -> bool:
    items = list(sentences)
    if not items:
        return False
    if any(_OUTLINE_HEADING_RE.match(s) or _BARE_NUMBER_RE.match(s) for s in items[:4]):
        return True
    return False


def _humanize_spoken_agent_text(text: str) -> str:
    """Drop telegraphic plan outlines and rewrite status fragments for speech."""
    sentences = _sentences(text)
    if not sentences:
        return _collapse(text)
    if _looks_like_plan_outline(sentences):
        kept: list[str] = []
        in_outline = True
        for sentence in sentences:
            if in_outline and (
                _OUTLINE_HEADING_RE.match(sentence)
                or _BARE_NUMBER_RE.match(sentence)
                or _REPORT_IDS_RE.match(sentence)
                or _PLAN_STEP_RE.match(sentence)
            ):
                continue
            if in_outline and not _OUTCOME_HINT_RE.search(sentence):
                # Still in the outline (imperative plan lines that didn't match).
                if len(sentence.split()) <= 12 and not sentence.lower().startswith(
                    ("i ", "i'", "the ", "here's", "here is")
                ):
                    continue
            in_outline = False
            rewritten = _rewrite_spoken_sentence(sentence)
            if rewritten:
                kept.append(rewritten)
        sentences = kept or [
            _rewrite_spoken_sentence(s)
            for s in sentences
            if _OUTCOME_HINT_RE.search(s) and _rewrite_spoken_sentence(s)
        ]
    else:
        sentences = [_rewrite_spoken_sentence(s) for s in sentences]
    sentences = [s for s in sentences if s]
    return " ".join(sentences).strip()


def prepare_spoken_user_text(text: str) -> str:
    """Short, natural speech for orchestrator updates and agent replies."""
    from distr.core.agent.services.llm.text_utils import clean_text_for_tts

    human = humanize_orchestrator_update(text, spoken=True)
    if not human:
        # Conversational agent text that wasn't a droppable notice.
        if _is_throwaway(text) and re.match(
            r"(?i)^\s*(waiting on your approval|started workflow|the workflow |ticket )",
            str(text or ""),
        ):
            return ""
        human = _humanize_spoken_agent_text(str(text or ""))
    return clean_text_for_tts(human, spoken_prose=True)


def should_speak_sentence(sentence: str) -> str:
    """Filter one already-split TTS sentence. Empty means skip speaking it."""
    text = _collapse(sentence)
    if not text:
        return ""
    if _is_throwaway(text) and re.match(
        r"(?i)^\s*(waiting on your approval|started workflow|the workflow |ticket |i need your go-ahead)",
        text,
    ):
        return ""
    return _rewrite_spoken_sentence(text)


def should_emit_orchestrator_update(
    text: str,
    *,
    now: float | None = None,
    min_interval_sec: float = _DEFAULT_EMIT_INTERVAL_SEC,
) -> bool:
    """True if this update is not a recent duplicate of something already sent."""
    key = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    if not key:
        return False
    ts = time.time() if now is None else now
    last = _RECENT_EMITS.get(key)
    if last is not None and (ts - last) < min_interval_sec:
        return False
    _RECENT_EMITS[key] = ts
    if len(_RECENT_EMITS) > 200:
        cutoff = ts - min_interval_sec
        stale = [k for k, v in _RECENT_EMITS.items() if v < cutoff]
        for k in stale:
            _RECENT_EMITS.pop(k, None)
    return True


def reset_orchestrator_update_dedupe() -> None:
    _RECENT_EMITS.clear()
