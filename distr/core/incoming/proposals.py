"""Incoming proposals — the first thing in the Incoming workspace.

Modelled on Tensology's ``IncomingWorkProposal`` pipeline (see
``www.tensology.com/backend/webapp/apps/main/incoming_triage.py``):

    connected conversation  ->  triage  ->  proposal (title, tasks, project, estimate)

Two rules shape this module:

1. **Built from the connected channels, not the site.** Proposals are derived from
   the same WhatsApp / Telegram / Gmail / Mailshot messages the Incoming workspace
   already shows, so Decisions does not depend on Tensology to have proposals.
2. **Reconciled with Tensology, never duplicated.** Tensology is the canonical
   record while both run side by side. A proposal that already exists on Tensology
   (same channel + thread) is shown once, from Tensology, and the local candidate
   for that thread is suppressed.

Read-only: this module never writes channel stores or proposals.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# A local candidate must be *work*: an explicit request or a reported problem.
# Matches Tensology's "is_actionable" intent; the LLM triage phase makes it exact.
_REQUEST_RE = re.compile(
    r"\?|please|can you|could you|would you|need(?:s|ed)?\b|require|fix|broken|"
    r"\berror\b|\bbug\b|not working|doesn'?t work|investigate|quote|proposal|invoice|"
    r"payment|deadline|urgent|asap|wrong|incorrect|missing|reverify|expire|failed|"
    r"outage|\bdown\b|follow up|chase|refund|reconnect|push\b",
    re.I,
)
# Newsletters, bots and digests are never client work.
_NOISE_RE = re.compile(
    r"no-?reply|donotreply|newsletter|digest|daily\.dev|informer@|notification|"
    r"marketing|mailer|automated",
    re.I,
)
# Our own messages (Paul), and simulated test traffic.
_SELF_RE = re.compile(r"(^|\W)paul(\W|$)|paulvonhoesslin|paulhoft", re.I)
_TEST_RE = re.compile(r"\bE2E\b|simulated|fake jid|test e2e", re.I)
# A conversation that only ends in "thanks"/"ok" has no work left in it.
_ACK_RE = re.compile(
    r"^\W*(?:thanks?(?: you)?|thankyou|appreciate[ds]?|ok(?:ay)?|sure|great|perfect|"
    r"regards|cheers|noted|will do|see you|sounds good|as long as|take (?:all )?the time|"
    r"no worries|got it|understood|i understand|awesome|nice|cool|morning team)\b",
    re.I,
)
_CHANNELS = {"whatsapp", "gmail", "mailshot", "telegram"}


def _parse_iso(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _latest_activity(row: dict[str, Any]) -> datetime | None:
    for key in ("created_at", "updated_at", "last_activity"):
        at = _parse_iso(row.get(key))
        if at:
            return at
    times = [
        at
        for ev in (row.get("evidence") or [])
        if isinstance(ev, dict) and (at := _parse_iso(ev.get("sent_at")))
    ]
    return max(times) if times else None


def _from_tensology(row: dict[str, Any]) -> dict[str, Any] | None:
    """Map a Tensology work-request onto the Incoming proposal shape."""
    if not isinstance(row, dict) or row.get("id") in (None, ""):
        return None
    conversation = row.get("source_conversation") if isinstance(row.get("source_conversation"), dict) else {}
    project = row.get("project") if isinstance(row.get("project"), dict) else {}
    source = str(row.get("source") or "tensology").strip().lower() or "tensology"
    thread_id = str(conversation.get("thread_id") or "").strip()
    at = _latest_activity(row)
    return {
        "id": f"tensology:{row.get('id')}",
        "origin": "tensology",
        "dedupe_key": f"{source}:{thread_id}" if thread_id else f"tensology:{row.get('id')}",
        "title": str(row.get("title") or "").strip(),
        "description": str(row.get("description") or "").strip(),
        "tasks": [str(item).strip() for item in (row.get("tasks") or []) if str(item).strip()],
        "priority": str(row.get("priority") or "medium").lower(),
        "estimated_minutes": row.get("estimated_minutes"),
        "estimated_amount": row.get("estimated_amount"),
        "confidence": row.get("confidence"),
        "rationale": str(row.get("rationale") or "").strip(),
        "project_id": project.get("id"),
        "project_name": str(project.get("name") or ""),
        "source": source,
        "source_label": source.title(),
        "source_thread_id": thread_id,
        "participant": str(row.get("participant") or conversation.get("participant_name") or ""),
        "status": str(row.get("status") or "pending"),
        "created_at": at.isoformat() if at else "",
        "can_push_to_thread": True,
    }


def _local_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group connected channel messages into proposal-shaped candidates."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        source = str(item.get("source") or "").lower()
        if source not in _CHANNELS:
            continue
        thread_id = str(item.get("source_thread_id") or item.get("sender") or item.get("key") or "").strip()
        if not thread_id:
            continue
        bucket = grouped.setdefault(
            (source, thread_id),
            {
                "source": source,
                "thread_id": thread_id,
                "items": [],
                "label": item.get("conversation_label") or item.get("subject") or "",
                "project_id": item.get("project_id") or item.get("board_id"),
                "project_name": item.get("project_name") or item.get("board_name") or "",
            },
        )
        bucket["items"].append(item)

    out: list[dict[str, Any]] = []
    for (source, thread_id), bucket in grouped.items():
        rows = sorted(bucket["items"], key=lambda row: str(row.get("created_at") or ""))
        latest = rows[-1]
        sender = str(latest.get("sender") or "")
        label = str(bucket["label"] or latest.get("subject") or "").strip()
        text = " ".join(str(row.get("text") or "") for row in rows[-6:])
        latest_text = str(latest.get("text") or "").strip()
        # 1. never our own outbound messages
        if latest.get("from_me") or str(latest.get("direction") or "").lower() == "outbound":
            continue
        # 2. never newsletters/bots, our own identity, or simulated tests
        if _NOISE_RE.search(sender) or _NOISE_RE.search(str(latest.get("addr") or "")):
            continue
        if _SELF_RE.search(sender) or _TEST_RE.search(f"{sender} {label} {text}"):
            continue
        # 3. a conversation whose latest message is an acknowledgement is concluded, not work
        if _ACK_RE.match(latest_text):
            continue
        # 4. must contain a real request or problem
        if not _REQUEST_RE.search(f"{label} {text}"):
            continue
        # 5. Tensology ties every proposal to a project; so do we
        if not (bucket["project_id"] or bucket["project_name"]):
            continue
        title = " ".join((label or latest_text).split())[:120] or "Incoming request"
        out.append(
            {
                "id": f"local:{source}:{thread_id}",
                "origin": "local",
                "dedupe_key": f"{source}:{thread_id}",
                "title": title,
                "description": " ".join(str(latest.get("text") or "").split())[:800],
                "tasks": [],
                "priority": "medium",
                "estimated_minutes": None,
                "estimated_amount": None,
                "confidence": None,
                "rationale": f"Derived from {len(rows)} {source} message(s); awaiting triage.",
                "project_id": bucket["project_id"],
                "project_name": bucket["project_name"],
                "source": source,
                "source_label": source.title(),
                "source_thread_id": thread_id,
                "participant": sender,
                "status": "candidate",
                "created_at": str(latest.get("created_at") or ""),
                "can_push_to_thread": bool(bucket["project_id"]),
            }
        )
    return out


def _proposal_prompt(proposal: dict[str, Any]) -> str:
    lines = [str(proposal.get("title") or "Incoming request").strip()]
    description = str(proposal.get("description") or "").strip()
    if description:
        lines += ["", description]
    tasks = [str(task).strip() for task in (proposal.get("tasks") or []) if str(task).strip()]
    if tasks:
        lines += ["", "Tasks:"] + [f"- {task}" for task in tasks]
    source = str(proposal.get("source_label") or proposal.get("source") or "").strip()
    participant = str(proposal.get("participant") or "").strip()
    if source:
        lines += ["", f"Source: {source}" + (f" — {participant}" if participant else "")]
    return "\n".join(lines)


def _tensology_id(proposal_id: Any) -> int | None:
    """Extract the numeric Tensology proposal id from an ``id`` like ``tensology:45``."""
    raw = str(proposal_id or "")
    if not raw.startswith("tensology:"):
        return None
    tail = raw.split(":", 1)[1]
    return int(tail) if tail.isdigit() else None


def dismiss_proposal(proposal: dict[str, Any]) -> bool:
    """Dismiss a proposal. On Tensology this clears it so it stops being proposed."""
    proposal_id = _tensology_id(proposal.get("id"))
    if proposal_id is None:
        return False  # local proposals have no upstream record to dismiss
    from distr.core.tensology_client import decide_work_request

    decide_work_request(proposal_id, "dismiss")
    return True


def create_proposal_thread(proposal: dict[str, Any]) -> int:
    """Create a Development thread for an Incoming proposal.

    Uses the same path as the studio task route (``ChatService.create_new_chat``),
    so a pushed proposal lands in a normal thread tied to its project.

    When the proposal also lives on Tensology, it is actioned there
    (``send_to_decisions``) so it drops off the pending list and is never
    proposed twice.
    """
    from distr.core.chat import ChatService
    from distr.core.settings import load_settings_from_db

    settings = load_settings_from_db()
    provider = str(
        settings.get("conversational_llm_provider") or settings.get("agent_provider") or "ollama"
    ).strip()
    model_name = str(
        settings.get("conversational_llm_model") or settings.get("agent_model") or ""
    ).strip()
    project_id = proposal.get("project_id")
    chat_id, _ = ChatService.create_new_chat(
        llm_provider=provider,
        llm_model=model_name,
        title=str(proposal.get("title") or "Incoming proposal")[:120],
        starting_question=_proposal_prompt(proposal),
        project_id=int(project_id) if project_id else None,
        route_mode="auto",
        execution_profile="code",
        autonomy_level="goal",
        activate=False,
    )
    # Write back to Tensology so the same proposal is not shown twice: pushing a
    # thread here is the same decision as "Send to Decisions" on the site.
    proposal_id = _tensology_id(proposal.get("id"))
    if proposal_id is not None:
        try:
            from distr.core.tensology_client import decide_work_request

            decide_work_request(proposal_id, "send_to_decisions")
        except Exception:  # never fail the local thread because the site is down
            import logging

            logging.getLogger(__name__).warning(
                "Tensology write-back failed for proposal %s", proposal_id, exc_info=True
            )
    return int(chat_id)


def list_incoming_proposals(*, limit: int = 100) -> dict[str, Any]:
    """Return the Incoming proposal list: Tensology (canonical) first, then local.

    Deduped by ``channel:thread`` so a thread already proposed on Tensology is
    never shown twice.
    """
    from distr.core.incoming.service import list_development_incoming

    proposals: list[dict[str, Any]] = []
    seen: set[str] = set()

    tensology_error = ""
    try:
        from distr.core.tensology_client import fetch_pending_work_requests

        for row in fetch_pending_work_requests():
            proposal = _from_tensology(row)
            if proposal:
                # Tensology is canonical: keep every one (several proposals can
                # share a conversation). Only use its keys to suppress local dupes.
                seen.add(proposal["dedupe_key"])
                proposals.append(proposal)
    except Exception as exc:  # best-effort: never break Incoming if Tensology is down
        tensology_error = str(exc)[:200]

    try:
        incoming = list_development_incoming(limit=limit, network=False)  # local channels only: fast
        for proposal in _local_candidates(incoming.get("items") or []):
            if proposal["dedupe_key"] in seen:
                continue  # already on Tensology — no duplicate
            seen.add(proposal["dedupe_key"])
            proposals.append(proposal)
    except Exception as exc:
        tensology_error = tensology_error or str(exc)[:200]

    proposals.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    counts: dict[str, int] = {}
    for proposal in proposals:
        counts[proposal["source"]] = counts.get(proposal["source"], 0) + 1
    return {
        "proposals": proposals,
        "counts": counts,
        "tensology": {"error": tensology_error, "count": sum(1 for p in proposals if p["origin"] == "tensology")},
    }
