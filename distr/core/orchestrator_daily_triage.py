"""Orchestrator daily triage.

This module turns source scans into decision candidates. It is intentionally
deterministic: the orchestrator can use an LLM later to improve wording, but the product
must still produce useful triage when providers are down.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from distr.core.initiative.draft_queue import DraftEntry, DraftQueue
from distr.core.initiative.tiers import PermissionTier


EXPECTED_WORK_SOURCES = {
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "trello": "Trello",
    "jira": "Jira",
    "clickup": "ClickUp",
    "monday": "Monday",
    "slack_app": "Slack",
    "email": "Email",
}

TRIAGE_BUCKETS = {
    "needs_reply": "Needs Reply",
    "make_ticket": "Make Ticket",
    "waiting_on_me": "Waiting On Me",
    "risk_blocker": "Risk / Blocker",
    "fyi": "FYI",
}


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if int(count or 0) == 1 else (plural or f"{singular}s")


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    return f"{int(count or 0)} {_plural(int(count or 0), singular, plural)}"


def build_daily_triage(
    *,
    work_scan: dict[str, Any] | None,
    developer_context: dict[str, Any] | None = None,
    recent_events: list[dict[str, Any]] | None = None,
    max_candidates: int = 12,
) -> dict[str, Any]:
    """Build an orchestrator triage packet from available source scans."""
    scan = work_scan if isinstance(work_scan, dict) else {}
    source_health = _source_health(scan)
    candidates: list[dict[str, Any]] = []

    for proposal in _list(scan.get("proposals")):
        candidate = _candidate_from_proposal(proposal)
        if candidate:
            candidates.append(candidate)

    for source, messages in (scan.get("messages") or {}).items():
        if not isinstance(messages, list) or not messages:
            continue
        candidates.extend(_candidates_from_messages(source, messages))

    developer_candidates = _developer_context_candidates(developer_context or {})
    candidates.extend(developer_candidates)

    candidates = _dedupe_candidates(candidates)
    candidates.sort(key=lambda c: (float(c.get("confidence") or 0), _priority(c)), reverse=True)
    candidates = candidates[: max(1, int(max_candidates or 12))]
    buckets = _bucket_candidates(candidates)

    missing_required = [
        s for s in source_health
        if not s.get("connected") and s.get("provider") in {"clickup", "monday", "slack_app", "email"}
    ]

    return {
        "mode": "daily_standup_triage",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": _summary(candidates, source_health),
        "source_health": source_health,
        "buckets": buckets,
        "candidates": candidates,
        "recent_event_count": len(recent_events or []),
        "missing_required_sources": missing_required,
    }


def enqueue_triage_candidates(
    queue: DraftQueue,
    candidates: list[dict[str, Any]],
    *,
    limit: int = 6,
) -> int:
    """Add top orchestrator triage candidates to Initiative approvals without duplicates."""
    existing_ids = {
        (((entry.execute_payload or {}).get("candidate") or {}).get("id"))
        for entry in queue.get_all()
        if entry.action_type == "orchestrator_triage_candidate"
    }
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=18)
    added = 0
    for candidate in candidates[: max(0, int(limit or 0))]:
        cid = candidate.get("id")
        if not cid or cid in existing_ids:
            continue
        queue.add(
            DraftEntry(
                id=f"orchestrator-{cid[:18]}",
                action_type="orchestrator_triage_candidate",
                description=str(candidate.get("question") or candidate.get("title") or "Decision needs review"),
                draft=_candidate_draft(candidate),
                reason="A work scan found something that needs your review.",
                created_at=now.isoformat(),
                expires_at=expires.isoformat(),
                permission_tier=PermissionTier.APPROVE,
                execute_payload={"kind": "orchestrator_triage_ack", "candidate": candidate},
            )
        )
        existing_ids.add(cid)
        added += 1
    return added


def format_triage_markdown(triage: dict[str, Any], *, max_candidates: int = 8) -> str:
    """Render a concise user-facing summary for chat/Telegram."""
    candidates = _list(triage.get("candidates"))[:max_candidates]
    buckets = triage.get("buckets") if isinstance(triage.get("buckets"), dict) else {}
    source_health = _list(triage.get("source_health"))
    connected = [s.get("label") for s in source_health if s.get("connected")]
    missing = [s.get("label") for s in source_health if not s.get("connected")]
    lines = [
        "## Quick Check-in",
        f"- {triage.get('summary') or 'No decisions found yet.'}",
    ]
    if connected:
        lines.append(f"- Listening to: {', '.join([str(x) for x in connected if x])}.")
    if missing:
        lines.append(f"- Not wired yet: {', '.join([str(x) for x in missing if x])}.")

    if candidates:
        if buckets:
            lines.extend(["", "## Intake Buckets"])
            for key, label in TRIAGE_BUCKETS.items():
                items = _list(buckets.get(key))
                if items:
                    lines.append(f"- {label}: {len(items)}")
        lines.extend(["", "## Needs Your Call"])
        for idx, candidate in enumerate(candidates, start=1):
            lines.append(f"{idx}. {candidate.get('question') or candidate.get('title')}")
            evidence = candidate.get("evidence") or []
            if evidence:
                lines.append(f"   Evidence: {str(evidence[0])[:220]}")
    else:
        lines.extend(["", "## Needs Your Call", "- Nothing actionable found yet."])

    return "\n".join(lines)


def _candidate_from_proposal(proposal: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(proposal, dict):
        return None
    action_type = str(proposal.get("action_type") or "review").strip()
    payload = proposal.get("payload") if isinstance(proposal.get("payload"), dict) else {}
    description = _clip(proposal.get("description") or action_type, 260)
    source = str(payload.get("source") or proposal.get("source") or "work_scan").strip().lower()

    if action_type == "message_triage":
        if _looks_like_reply_candidate(description, payload):
            suggested = "draft_reply"
            question = f"{description} Should I draft a reply or turn it into a ticket?"
        else:
            suggested = "create_ticket"
            question = f"{description} Should I create or update a ticket from this?"
    elif action_type == "ticket_lane_move":
        suggested = "update_ticket"
        question = f"{description} Want me to move these forward?"
    elif action_type in {"workflow_start", "project_cli_task"}:
        suggested = "execute_work"
        question = f"{description} Should I run the linked workflow/agent?"
    elif action_type == "board_triage":
        suggested = "review_board"
        question = f"{description} Should I triage this board now?"
    else:
        suggested = "ask_paul"
        question = f"{description} What should I do with this?"

    return _candidate(
        source=source,
        action_type=suggested,
        title=description,
        question=question,
        evidence=[description],
        payload={"proposal": proposal},
        confidence=float(payload.get("confidence") or proposal.get("confidence") or 0.55),
        risk_level=str(payload.get("risk_level") or proposal.get("risk_level") or "medium"),
    )


def _candidates_from_messages(source: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    work_messages = [m for m in messages if isinstance(m, dict)]
    if not work_messages:
        return []
    sample = work_messages[0]
    sender = sample.get("sender") or sample.get("chat_title") or sample.get("jid_phone") or source.title()
    preview = sample.get("text_preview") or sample.get("latest_preview") or ""
    return [
        _candidate(
            source=source,
            action_type="create_ticket",
            title=f"{_count_phrase(len(work_messages), f'{source} message')} may need ticketing",
            question=f"{sender} has {_count_phrase(len(work_messages), f'work-looking {source} message')}. Should I create a ticket or attach them to an existing one?",
            evidence=[_clip(preview, 240)] if preview else [],
            payload={"message_ids": [m.get("id") for m in work_messages[:8]], "source": source},
            confidence=0.62,
            risk_level="medium",
        )
    ]


def _looks_like_reply_candidate(description: str, payload: dict[str, Any]) -> bool:
    text = " ".join(
        str(x or "")
        for x in (
            description,
            payload.get("latest_preview"),
            payload.get("latest_sender"),
        )
    ).lower()
    if payload.get("linked_board_id") or payload.get("message_ids"):
        return False
    reply_markers = ("messaged", "message from", "just got", "asked", "are you around", "can you", "could you")
    ticket_markers = ("ticket", "bug", "fix", "quote", "invoice", "deadline", "project", "urgent", "issue")
    return any(marker in text for marker in reply_markers) and not any(marker in text for marker in ticket_markers)


def _developer_context_candidates(context: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(context, dict) or not context:
        return []
    candidates = []
    active = context.get("active") or context.get("current") or context.get("recent")
    if active:
        candidates.append(
            _candidate(
                source="developer_context",
                action_type="attach_agent_work_to_ticket",
                title="Recent agent/IDE work may need to be linked back to a ticket",
                question="I can see recent development context. Should I attach the latest agent work back to the relevant ticket or board?",
                evidence=[_clip(json.dumps(active, ensure_ascii=False, default=str), 260)],
                payload={"developer_context": active},
                confidence=0.5,
                risk_level="low",
            )
        )
    return candidates


def _source_health(scan: dict[str, Any]) -> list[dict[str, Any]]:
    configured = {
        str(s.get("provider") or "").lower(): s
        for s in _list(scan.get("connected_sources"))
        if isinstance(s, dict)
    }
    unavailable = {
        str(s.get("source") or "").lower(): s
        for s in _list(scan.get("unavailable_sources"))
        if isinstance(s, dict)
    }
    rows = []
    for provider, label in EXPECTED_WORK_SOURCES.items():
        row = configured.get(provider, {})
        rows.append(
            {
                "provider": provider,
                "label": label,
                "connected": bool(row.get("connected")),
                "configured": provider in configured,
                "reason": (unavailable.get(provider) or {}).get("reason") or "",
            }
        )
    return rows


def _candidate(
    *,
    source: str,
    action_type: str,
    title: str,
    question: str,
    evidence: list[str],
    payload: dict[str, Any],
    confidence: float,
    risk_level: str,
) -> dict[str, Any]:
    base = {
        "source": source,
        "action_type": action_type,
        "bucket": _bucket_for_action(action_type, risk_level),
        "title": _clip(title, 240),
        "question": _clip(question, 320),
        "evidence": [_clip(e, 300) for e in evidence if e],
        "payload": payload,
        "confidence": round(max(0.0, min(float(confidence), 1.0)), 2),
        "risk_level": risk_level or "medium",
    }
    raw = json.dumps(
        {
            "source": base["source"],
            "action_type": base["action_type"],
            "title": base["title"],
            "payload": base["payload"],
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    base["id"] = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return base


def _bucket_candidates(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in TRIAGE_BUCKETS}
    for candidate in candidates:
        bucket = str(candidate.get("bucket") or _bucket_for_action(
            str(candidate.get("action_type") or ""),
            str(candidate.get("risk_level") or ""),
        ))
        if bucket not in buckets:
            bucket = "fyi"
        buckets[bucket].append(candidate)
    return buckets


def _bucket_for_action(action_type: str, risk_level: str = "") -> str:
    action = (action_type or "").strip().lower()
    risk = (risk_level or "").strip().lower()
    if risk in {"high", "critical", "blocker"}:
        return "risk_blocker"
    if action == "draft_reply":
        return "needs_reply"
    if action == "create_ticket":
        return "make_ticket"
    if action in {"update_ticket", "execute_work", "attach_agent_work_to_ticket", "review_board"}:
        return "waiting_on_me"
    return "fyi"


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for candidate in candidates:
        cid = candidate.get("id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append(candidate)
    return out


def _priority(candidate: dict[str, Any]) -> int:
    return {
        "create_ticket": 5,
        "update_ticket": 4,
        "execute_work": 3,
        "draft_reply": 3,
        "attach_agent_work_to_ticket": 2,
        "review_board": 1,
    }.get(str(candidate.get("action_type") or ""), 0)


def _summary(candidates: list[dict[str, Any]], source_health: list[dict[str, Any]]) -> str:
    if not candidates:
        return "I did not find anything that needs a decision yet."
    counts: dict[str, int] = {}
    for candidate in candidates:
        key = str(candidate.get("action_type") or "decision")
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{count} {key.replace('_', ' ')}" for key, count in sorted(counts.items())]
    connected = sum(1 for s in source_health if s.get("connected"))
    return f"I found {len(candidates)} thing(s) that may need your call across {connected} connected source(s): {', '.join(parts)}."


def _candidate_draft(candidate: dict[str, Any]) -> str:
    evidence = candidate.get("evidence") or []
    lines = [
        f"Decision: {candidate.get('question') or candidate.get('title')}",
        f"Suggested action: {candidate.get('action_type')}",
        f"Source: {candidate.get('source')}",
    ]
    if evidence:
        lines.append("Evidence:")
        lines.extend(f"- {item}" for item in evidence[:4])
    return "\n".join(lines)


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip() + "…"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
