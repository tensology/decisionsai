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

# A conversation looks like work when it asks for something or reports a problem.
# Kept intentionally close to Tensology's "is_actionable" intent; the LLM triage
# phase (matching Tensology field-for-field) refines this later.
_ACTIONABLE_RE = re.compile(
    r"\?|please|can you|could you|would you|need(?:s|ed)?\b|require|fix|broken|error|bug|"
    r"invoice|payment|paid|quote|proposal|deadline|urgent|asap|not working|doesn'?t work|"
    r"\bdown\b|outage|issue|investigate|check|update|change|wrong|incorrect|missing|"
    r"add\b|remove\b|refund|verify|reverify|expire|failed|chase|follow up",
    re.I,
)
_NOISE_RE = re.compile(r"no-?reply|newsletter|notification|digest|marketing|mailer", re.I)
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
        if _NOISE_RE.search(sender):
            continue
        text = " ".join(str(row.get("text") or "") for row in rows[-6:])
        label = str(bucket["label"] or latest.get("subject") or "").strip()
        if not _ACTIONABLE_RE.search(f"{label} {text}"):
            continue
        title = " ".join((label or str(latest.get("text") or "")).split())[:120] or "Incoming request"
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
        incoming = list_development_incoming(limit=limit)
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
