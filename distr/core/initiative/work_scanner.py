"""Read-only work source scanner for Initiative.

This module turns boards, tickets, and message surfaces into compact proposed
work items. It never mutates state; policy and action handlers decide whether a
proposal is surfaced, queued for approval, or executed.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Any


WORK_KEYWORDS = {
    "bug",
    "fix",
    "client",
    "customer",
    "invoice",
    "deadline",
    "deploy",
    "ticket",
    "project",
    "urgent",
    "issue",
    "quote",
    "contract",
    "website",
    "app",
    "workflow",
    "board",
    "task",
    "approval",
    "blocker",
    "blocked",
    "follow up",
    "follow-up",
    "meeting",
    "standup",
}


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if int(count or 0) == 1 else (plural or f"{singular}s")


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    return f"{int(count or 0)} {_plural(int(count or 0), singular, plural)}"


def _is_current_unhandled_whatsapp_message(
    row: Any,
    *,
    now: datetime,
    ticketed_message_ids: set[int],
    linked_board: bool,
) -> bool:
    if bool(getattr(row, "processed", False)):
        return False
    if str(getattr(row, "snapshot_group", "") or "").strip():
        return False
    try:
        if int(getattr(row, "id", 0) or 0) in ticketed_message_ids:
            return False
    except Exception:
        pass
    created = getattr(row, "created_date", None)
    if not created:
        return False
    freshness_window = timedelta(hours=12 if linked_board else 3)
    return created >= now - freshness_window


def build_work_scan(settings: dict[str, Any]) -> dict[str, Any]:
    scan = {
        "boards": [],
        "proposals": [],
        "messages": {"whatsapp": [], "telegram": [], "email": [], "slack": [], "discord": []},
        "tasks": {"clickup": [], "monday": []},
        "connected_sources": [],
        "unavailable_sources": [],
    }

    try:
        scan["connected_sources"] = _connected_work_sources(settings)
    except Exception as exc:
        scan["unavailable_sources"].append({"source": "connected_sources", "reason": str(exc)})

    try:
        _scan_local_boards(scan, settings)
    except Exception as exc:
        scan["unavailable_sources"].append({"source": "local_boards", "reason": str(exc)})

    if settings.get("initiative_scan_external_boards", False):
        try:
            _scan_external_boards(scan)
        except Exception:
            # External boards are opportunistic. A bad token, timeout, or missing
            # connector should not interrupt local Initiative cycles.
            pass

    if settings.get("initiative_scan_whatsapp", True):
        try:
            _scan_whatsapp(scan)
        except Exception as exc:
            scan["unavailable_sources"].append({"source": "whatsapp", "reason": str(exc)})

    if settings.get("initiative_scan_telegram", True):
        try:
            _scan_telegram(scan)
        except Exception as exc:
            scan["unavailable_sources"].append({"source": "telegram", "reason": str(exc)})

    if settings.get("initiative_scan_email", False):
        try:
            _scan_email(scan)
        except Exception as exc:
            scan["unavailable_sources"].append({"source": "email", "reason": str(exc)})

    try:
        _scan_advanced_work_connectors(scan, settings)
    except Exception as exc:
        scan["unavailable_sources"].append({"source": "advanced_connectors", "reason": str(exc)})

    return scan


def _connected_work_sources(settings: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    raw = settings.get("connected_accounts") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = []
    if not isinstance(raw, list):
        return []

    work_providers = {
        "jira": "Jira",
        "trello": "Trello",
        "slack_app": "Slack",
        "discord_bot": "Discord",
        "clickup": "ClickUp",
        "monday": "Monday",
        "whatsapp": "WhatsApp",
        "telegram": "Telegram",
        "google": "Gmail",
        "gmail": "Gmail",
    }
    sources = []
    for account in raw:
        if not isinstance(account, dict):
            continue
        provider = str(account.get("provider") or "").strip().lower()
        label = work_providers.get(provider)
        if not label:
            continue
        connected = bool(
            account.get("is_valid", True)
            and (
                account.get("status") == "connected"
                or account.get("api_token")
                or account.get("bot_token")
                or account.get("access_token")
                or account.get("user_id")
                or account.get("app_user_id")
                or (provider == "google" and account.get("access_token"))
                or provider in {"jira", "trello"}
            )
        )
        sources.append(
            {
                "provider": provider,
                "label": label,
                "name": account.get("name") or label,
                "connected": connected,
            }
        )
    return sources


def _scan_local_boards(scan: dict[str, Any], settings: dict[str, Any]) -> None:
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanTicket

    with get_session() as session:
        boards = (
            session.query(KanbanBoard)
            .filter(KanbanBoard.archived.is_(False))
            .order_by(KanbanBoard.position.asc(), KanbanBoard.id.asc())
            .all()
        )
        for board in boards:
            lanes = list(board.lanes or [])
            lane_rows = []
            for lane in lanes:
                tickets = (
                    session.query(KanbanTicket)
                    .filter(KanbanTicket.lane_id == lane.id)
                    .order_by(KanbanTicket.position.asc(), KanbanTicket.id.asc())
                    .limit(8)
                    .all()
                )
                lane_rows.append({
                    "id": lane.id,
                    "name": lane.name or "",
                    "position": lane.position or 0,
                    "ticket_count": len(lane.tickets or []),
                    "tickets": [_ticket_row(t, lane.name or "", board) for t in tickets],
                })

            board_row = {
                "id": board.id,
                "name": board.name or "",
                "source": board.source or "database",
                "source_lane": "Current",
                "done_lane": "QA / Assess",
                "default_workflow_id": board.default_workflow_id,
                "default_project_id": board.default_project_id,
                "send_to_cli": bool(board.send_to_cli),
                "lanes": lane_rows,
            }
            scan["boards"].append(board_row)
            _add_board_proposals(scan, board_row)


def _scan_external_boards(scan: dict[str, Any]) -> None:
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.gui.web.routes.kanban import _build_external_board_detail_payload

    with get_session() as session:
        boards = (
            session.query(KanbanBoard)
            .filter(KanbanBoard.source.in_(["trello", "jira"]))
            .filter(KanbanBoard.external_board_id.isnot(None))
            .order_by(KanbanBoard.position.asc(), KanbanBoard.id.asc())
            .limit(6)
            .all()
        )
        board_refs = [
            {
                "local_id": b.id,
                "source": b.source,
                "external_board_id": b.external_board_id,
                "name": b.name or "",
                "default_workflow_id": b.default_workflow_id,
                "default_project_id": b.default_project_id,
            }
            for b in boards
        ]

    for ref in board_refs:
        provider = (ref.get("source") or "").lower()
        ext_id = str(ref.get("external_board_id") or "").strip()
        if provider not in {"trello", "jira"} or not ext_id:
            continue
        detail = _build_external_board_detail_payload(provider, ext_id)
        lanes = detail.get("lanes") if isinstance(detail, dict) else []
        if not isinstance(lanes, list):
            continue
        board_row = {
            "id": ref["local_id"],
            "external_board_id": ext_id,
            "name": detail.get("name") or ref.get("name") or f"{provider} board {ext_id}",
            "source": provider,
            "url": detail.get("url") or "",
            "default_workflow_id": ref.get("default_workflow_id") or detail.get("default_workflow_id"),
            "default_project_id": ref.get("default_project_id") or detail.get("default_project_id"),
            "lanes": [
                {
                    "id": lane.get("id"),
                    "name": lane.get("name") or "",
                    "ticket_count": len(lane.get("tickets") or []),
                    "tickets": [
                        {
                            "id": card.get("id"),
                            "title": card.get("title") or "",
                            "description_preview": (card.get("description") or "")[:240],
                            "lane": lane.get("name") or "",
                            "url": card.get("url") or "",
                            "priority": card.get("priority") or "medium",
                        }
                        for card in (lane.get("tickets") or [])[:5]
                    ],
                }
                for lane in lanes
            ],
        }
        scan["boards"].append(board_row)
        total = sum(lane.get("ticket_count", 0) for lane in board_row["lanes"])
        if total:
            scan["proposals"].append({
                "action_type": "board_triage",
                "description": f"{board_row['name']} ({provider}) has {_count_phrase(total, 'fetched item')} available for review.",
                "payload": {
                    "source": provider,
                    "board_id": board_row["id"],
                    "external_board_id": ext_id,
                    "confidence": 0.64,
                    "risk_level": "low",
                },
            })


def _ticket_row(ticket: Any, lane_name: str, board: Any) -> dict[str, Any]:
    return {
        "id": ticket.id,
        "title": ticket.title or "",
        "description_preview": (ticket.description or "")[:240],
        "lane": lane_name,
        "priority": ticket.priority or "medium",
        "workflow_status": ticket.workflow_status or "",
        "linked_workflow_id": ticket.linked_workflow_id or board.default_workflow_id,
        "linked_project_id": ticket.linked_project_id or board.default_project_id,
        "send_to_cli": bool(ticket.send_to_cli or board.send_to_cli),
        "created_date": ticket.created_date.isoformat() if ticket.created_date else "",
    }


def _add_board_proposals(scan: dict[str, Any], board: dict[str, Any]) -> None:
    lanes = board.get("lanes") or []
    lane_by_name = {str(l.get("name") or "").strip().lower(): l for l in lanes}
    backlog = lane_by_name.get("backlog")
    current_name = (board.get("source_lane") or "Current").strip()
    current = lane_by_name.get(current_name.lower()) or lane_by_name.get("current")

    if backlog and backlog.get("tickets"):
        candidates = (backlog.get("tickets") or [])[:3]
        scan["proposals"].append({
            "action_type": "ticket_lane_move",
            "description": (
                f"{board['name']} has {_count_phrase(len(backlog.get('tickets') or []), 'backlog item')} "
                f"that should move into {current_name}."
            ),
            "payload": {
                "board_id": board["id"],
                "ticket_ids": [t["id"] for t in candidates],
                "target_lane": current_name,
                "source": "initiative_work_scan",
                "confidence": 0.72,
                "risk_level": "medium",
            },
        })

    runnable = []
    source_lane = current or backlog
    for ticket in (source_lane or {}).get("tickets", []):
        status = (ticket.get("workflow_status") or "").lower()
        if status in {"running", "waiting"}:
            continue
        if ticket.get("send_to_cli") and ticket.get("linked_project_id"):
            runnable.append(("project_cli_task", ticket))
        elif ticket.get("linked_workflow_id"):
            runnable.append(("workflow_start", ticket))

    if runnable:
        action_type, ticket = runnable[0]
        scan["proposals"].append({
            "action_type": action_type,
            "description": f"Ticket #{ticket['id']} on {board['name']} looks ready to execute: {ticket['title']}",
            "payload": {
                "board_id": board["id"],
                "ticket_ids": [ticket["id"]],
                "workflow_id": ticket.get("linked_workflow_id"),
                "project_id": ticket.get("linked_project_id"),
                "source": "initiative_work_scan",
                "confidence": 0.78,
                "risk_level": "medium",
            },
        })


def _scan_whatsapp(scan: dict[str, Any]) -> None:
    from distr.core.db import WhatsAppMessage, WhatsAppPhoneLink, get_session
    from distr.core.db.kanban import KanbanBoard, KanbanTicket

    now = datetime.utcnow()
    cutoff = now - timedelta(days=7)
    fresh_cutoff = now - timedelta(minutes=3)
    with get_session() as session:
        rows = (
            session.query(WhatsAppMessage)
            .filter(WhatsAppMessage.from_me.is_(False))
            .filter((WhatsAppMessage.processed.is_(False)) | (WhatsAppMessage.processed.is_(None)))
            .filter(WhatsAppMessage.created_date >= cutoff)
            .order_by(WhatsAppMessage.created_date.desc())
            .limit(20)
            .all()
        )

        links = session.query(WhatsAppPhoneLink).all()
        link_by_phone = {
            (link.phone_number or "").strip(): link
            for link in links
            if (link.phone_number or "").strip()
        }
        board_meta: dict[int, dict[str, Any]] = {}
        for link in links:
            if not link.board_id or link.board_id in board_meta:
                continue
            board = session.query(KanbanBoard).filter(KanbanBoard.id == link.board_id).first()
            if board:
                board_meta[link.board_id] = {
                    "name": board.name or f"Board {board.id}",
                }
        ticketed_message_ids = set()
        for row in session.query(KanbanTicket).filter(KanbanTicket.whatsapp_message_id.isnot(None)).all():
            value = getattr(row, "whatsapp_message_id", None)
            if value is None and isinstance(row, (tuple, list)) and row:
                value = row[0]
            if value is not None:
                ticketed_message_ids.add(int(value))

        work_like = []
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            text = (row.text or row.caption or "").strip()
            if not text and row.media_type:
                text = f"[{row.media_type} message]"
            phone = row.jid_phone or (row.jid or "").split("@")[0] or "unknown"
            link = link_by_phone.get(phone)
            linked_board = board_meta.get(link.board_id) if link else None
            linked_whatsapp_enabled = bool(linked_board)
            if not _is_current_unhandled_whatsapp_message(
                row,
                now=now,
                ticketed_message_ids=ticketed_message_ids,
                linked_board=linked_whatsapp_enabled,
            ):
                continue
            is_work_related = _looks_work_related(text)
            item = {
                "id": row.id,
                "jid": row.jid or "",
                "jid_phone": phone,
                "sender": row.sender_push_name or row.sender_phone or row.sender_jid or "",
                "media_type": row.media_type or "",
                "text_preview": text[:240],
                "created_date": row.created_date.isoformat() if row.created_date else "",
                "work_related": is_work_related,
                "linked_board_id": link.board_id if link else None,
                "linked_board_name": linked_board.get("name") if linked_board else "",
                "linked_board_whatsapp_linked": linked_whatsapp_enabled,
                "auto_snapshot": bool(link.auto_snapshot) if link else False,
            }
            if is_work_related or linked_whatsapp_enabled:
                work_like.append(item)

            group = grouped.setdefault(
                phone,
                {
                    "jid_phone": phone,
                    "jid": row.jid or "",
                    "message_ids": [],
                    "latest_sender": item["sender"],
                    "latest_preview": text[:240],
                    "latest_created_date": item["created_date"],
                    "work_related_count": 0,
                    "linked_board_id": item["linked_board_id"],
                    "linked_board_name": item["linked_board_name"],
                    "linked_board_whatsapp_linked": linked_whatsapp_enabled,
                    "auto_snapshot": item["auto_snapshot"],
                    "fresh": bool(row.created_date and row.created_date >= fresh_cutoff),
                },
            )
            group["message_ids"].append(row.id)
            group["work_related_count"] += 1 if is_work_related else 0
            group["fresh"] = bool(group["fresh"] or (row.created_date and row.created_date >= fresh_cutoff))
        scan["messages"]["whatsapp"] = work_like

        grouped_candidates = [
            g for g in grouped.values()
            if g["fresh"] or g["work_related_count"] > 0 or g.get("linked_board_whatsapp_linked")
        ]
        grouped_candidates.sort(
            key=lambda g: (
                bool(g.get("linked_board_id")),
                int(g.get("work_related_count") or 0),
                g.get("latest_created_date") or "",
            ),
            reverse=True,
        )
        if grouped_candidates:
            chosen = grouped_candidates[0]
            count = len(chosen["message_ids"])
            board_name = chosen.get("linked_board_name") or ""
            if board_name:
                description = (
                    f"{_count_phrase(count, 'WhatsApp message')} arrived from {chosen['latest_sender'] or chosen['jid_phone']} "
                    f"and the chat is linked to board '{board_name}'. Ask whether to snapshot them into tickets."
                )
            elif chosen["work_related_count"] > 0:
                description = (
                    f"{_count_phrase(count, 'recent WhatsApp message')} from {chosen['latest_sender'] or chosen['jid_phone']} "
                    "look work-related and may need ticketing or follow-up."
                )
            else:
                description = (
                    f"You just got a WhatsApp message from {chosen['latest_sender'] or chosen['jid_phone']}."
                )
            scan["proposals"].append({
                "action_type": "message_triage",
                "description": description,
                "payload": {
                    "source": "whatsapp",
                    "jid_phone": chosen["jid_phone"],
                    "message_ids": chosen["message_ids"][:8],
                    "message_count": count,
                    "latest_sender": chosen["latest_sender"],
                    "latest_preview": chosen["latest_preview"],
                    "linked_board_id": chosen.get("linked_board_id"),
                    "linked_board_name": board_name,
                    "linked_board_whatsapp_linked": bool(chosen.get("linked_board_whatsapp_linked")),
                    "auto_snapshot": bool(chosen.get("auto_snapshot")),
                    "confidence": 0.68,
                    "risk_level": "medium",
                },
                "draft": (
                    f"{description}\n\nLatest: {chosen['latest_preview']}\n\n"
                    "Suggested next step: ask whether to create a ticket snapshot, then use "
                    "create_ticket action='whatsapp_snapshot_to_ticket' with the message_ids."
                ),
                "telegram_message": description,
            })


def _scan_telegram(scan: dict[str, Any]) -> None:
    from distr.core.db import TelegramGroupMessage, get_session

    cutoff = datetime.utcnow() - timedelta(days=7)
    with get_session() as session:
        rows = (
            session.query(TelegramGroupMessage)
            .filter(TelegramGroupMessage.processed.is_(False))
            .filter(TelegramGroupMessage.created_date >= cutoff)
            .order_by(TelegramGroupMessage.created_date.desc())
            .limit(20)
            .all()
        )
        work_like = []
        for row in rows:
            text = (row.text or row.caption or "").strip()
            if not _looks_work_related(text):
                continue
            work_like.append({
                "id": row.id,
                "chat_title": row.chat_title or "",
                "media_type": row.media_type or "",
                "text_preview": text[:240],
                "created_date": row.created_date.isoformat() if row.created_date else "",
            })
        scan["messages"]["telegram"] = work_like
        if work_like:
            scan["proposals"].append({
                "action_type": "message_triage",
                "description": f"{_count_phrase(len(work_like), 'recent Telegram group message')} look work-related and may need review.",
                "payload": {
                    "source": "telegram",
                    "message_ids": [m["id"] for m in work_like[:5]],
                    "confidence": 0.62,
                    "risk_level": "medium",
                },
            })


def _scan_email(scan: dict[str, Any]) -> None:
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

    connector = GoogleWorkspaceConnector()
    if not connector.is_connected():
        scan["unavailable_sources"].append({"source": "email", "reason": "Google/Gmail is not connected"})
        return

    messages = connector.check_inbox(max_results=10, query="in:inbox newer_than:7d") or []
    work_like = []
    for msg in messages:
        subject = str(msg.get("subject") or "").strip()
        snippet = str(msg.get("snippet") or msg.get("body") or "").strip()
        combined = f"{subject}\n{snippet}".strip()
        item = {
            "id": msg.get("id") or "",
            "thread_id": msg.get("threadId") or "",
            "from": msg.get("from") or "",
            "subject": subject,
            "snippet": snippet[:240],
            "date": msg.get("date") or "",
            "labels": msg.get("labels") or [],
            "work_related": _looks_work_related(combined),
        }
        if item["work_related"]:
            work_like.append(item)

    scan["messages"]["email"] = work_like
    if work_like:
        chosen = work_like[0]
        scan["proposals"].append({
            "action_type": "message_triage",
            "description": (
                f"{len(work_like)} recent email(s) look work-related. "
                f"Top email: {chosen['subject'] or chosen['snippet']}"
            ),
            "payload": {
                "source": "email",
                "message_ids": [m["id"] for m in work_like[:5] if m.get("id")],
                "thread_ids": [m["thread_id"] for m in work_like[:5] if m.get("thread_id")],
                "confidence": 0.66,
                "risk_level": "medium",
            },
        })


def _scan_advanced_work_connectors(scan: dict[str, Any], settings: dict[str, Any]) -> None:
    accounts = _connected_work_accounts(settings)
    if _account_token(accounts, "slack_app", "bot_token") or os.environ.get("DECISIONSAI_SLACK_BOT_TOKEN"):
        try:
            _scan_slack(scan, accounts)
        except Exception as exc:
            scan["unavailable_sources"].append({"source": "slack", "reason": str(exc)})

    if _account_token(accounts, "discord_bot", "bot_token") or os.environ.get("DECISIONSAI_DISCORD_BOT_TOKEN"):
        try:
            _scan_discord(scan, accounts)
        except Exception as exc:
            scan["unavailable_sources"].append({"source": "discord", "reason": str(exc)})

    if _account_token(accounts, "clickup", "api_token"):
        try:
            _scan_clickup(scan, accounts)
        except Exception as exc:
            scan["unavailable_sources"].append({"source": "clickup", "reason": str(exc)})

    if _account_token(accounts, "monday", "api_token"):
        try:
            _scan_monday(scan, accounts)
        except Exception as exc:
            scan["unavailable_sources"].append({"source": "monday", "reason": str(exc)})


def _connected_work_accounts(settings: dict[str, Any]) -> list[dict[str, Any]]:
    import json

    raw = settings.get("connected_accounts") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = []
    if not isinstance(raw, list):
        return []
    return [account for account in raw if isinstance(account, dict)]


def _account_token(accounts: list[dict[str, Any]], provider: str, key: str) -> str:
    for account in accounts:
        if str(account.get("provider") or "").strip().lower() == provider:
            return str(account.get(key) or "").strip()
    return ""


def _http_get_json(url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None, timeout: float = 6.0) -> Any:
    import requests

    response = requests.get(url, headers=headers or {}, params=params or {}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, (dict, list)) else {}


def _http_post_json(url: str, *, headers: dict[str, str] | None = None, json_payload: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    import requests

    response = requests.post(url, headers=headers or {}, json=json_payload or {}, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _scan_slack(scan: dict[str, Any], accounts: list[dict[str, Any]]) -> None:
    token = os.environ.get("DECISIONSAI_SLACK_BOT_TOKEN") or _account_token(accounts, "slack_app", "bot_token")
    if not token:
        return
    headers = {"Authorization": f"Bearer {token}"}
    channel_payload = _http_get_json(
        "https://slack.com/api/conversations.list",
        headers=headers,
        params={"types": "public_channel,private_channel,mpim,im", "limit": 20, "exclude_archived": "true"},
    )
    if channel_payload.get("ok") is False:
        raise RuntimeError(str(channel_payload.get("error") or "slack_api_error"))
    oldest = str(int(time.time() - (7 * 24 * 60 * 60)))
    work_like = []
    for channel in (channel_payload.get("channels") or [])[:6]:
        channel_id = str(channel.get("id") or "")
        if not channel_id:
            continue
        history = _http_get_json(
            "https://slack.com/api/conversations.history",
            headers=headers,
            params={"channel": channel_id, "limit": 8, "oldest": oldest},
        )
        if history.get("ok") is False:
            continue
        for msg in history.get("messages") or []:
            text = str(msg.get("text") or "").strip()
            if not _looks_work_related(text):
                continue
            work_like.append({
                "channel_id": channel_id,
                "channel_name": channel.get("name") or channel_id,
                "user": msg.get("user") or "",
                "ts": msg.get("ts") or "",
                "text_preview": text[:240],
                "work_related": True,
            })
            if len(work_like) >= 12:
                break
        if len(work_like) >= 12:
            break
    scan["messages"]["slack"] = work_like
    if work_like:
        chosen = work_like[0]
        scan["proposals"].append({
            "action_type": "message_triage",
            "description": (
                f"{_count_phrase(len(work_like), 'recent Slack message')} look work-related. "
                f"Top channel: {chosen['channel_name']}."
            ),
            "payload": {
                "source": "slack",
                "channel_ids": sorted({m["channel_id"] for m in work_like if m.get("channel_id")})[:5],
                "confidence": 0.62,
                "risk_level": "medium",
            },
        })


def _scan_clickup(scan: dict[str, Any], accounts: list[dict[str, Any]]) -> None:
    token = _account_token(accounts, "clickup", "api_token")
    if not token:
        return
    headers = {"Authorization": token}
    teams_payload = _http_get_json("https://api.clickup.com/api/v2/team", headers=headers)
    teams = teams_payload.get("teams") or []
    tasks = []
    for team in teams[:3]:
        team_id = str(team.get("id") or "")
        if not team_id:
            continue
        payload = _http_get_json(
            f"https://api.clickup.com/api/v2/team/{team_id}/task",
            headers=headers,
            params={"include_closed": "false", "subtasks": "true", "page": 0, "order_by": "updated", "reverse": "true"},
        )
        for task in payload.get("tasks") or []:
            name = str(task.get("name") or "").strip()
            text = f"{name}\n{task.get('text_content') or task.get('description') or ''}"
            if not _looks_work_related(text) and str(task.get("priority") or "") not in {"1", "urgent", "high"}:
                continue
            status = task.get("status") or {}
            priority = task.get("priority") or {}
            tasks.append({
                "id": task.get("id") or "",
                "name": name,
                "status": status.get("status") if isinstance(status, dict) else str(status or ""),
                "priority": priority.get("priority") if isinstance(priority, dict) else str(priority or ""),
                "url": task.get("url") or "",
                "team": team.get("name") or team_id,
                "updated": task.get("date_updated") or "",
            })
            if len(tasks) >= 15:
                break
        if len(tasks) >= 15:
            break
    scan["tasks"]["clickup"] = tasks
    if tasks:
        scan["proposals"].append({
            "action_type": "task_triage",
            "description": f"{len(tasks)} ClickUp task(s) look active or important. Top task: {tasks[0]['name']}",
            "payload": {
                "source": "clickup",
                "task_ids": [task["id"] for task in tasks[:8] if task.get("id")],
                "confidence": 0.64,
                "risk_level": "medium",
            },
        })


def _scan_discord(scan: dict[str, Any], accounts: list[dict[str, Any]]) -> None:
    token = os.environ.get("DECISIONSAI_DISCORD_BOT_TOKEN") or _account_token(accounts, "discord_bot", "bot_token")
    if not token:
        return
    headers = {"Authorization": f"Bot {token}"}
    guilds_payload = _http_get_json("https://discord.com/api/v10/users/@me/guilds", headers=headers)
    guilds = guilds_payload if isinstance(guilds_payload, list) else guilds_payload.get("guilds") or []
    work_like = []
    for guild in guilds[:3]:
        guild_id = str(guild.get("id") or "")
        if not guild_id:
            continue
        channels_payload = _http_get_json(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers,
        )
        channels = channels_payload if isinstance(channels_payload, list) else channels_payload.get("channels") or []
        for channel in channels[:8]:
            channel_type = int(channel.get("type") or 0)
            if channel_type not in {0, 5, 10, 11, 12}:
                continue
            channel_id = str(channel.get("id") or "")
            if not channel_id:
                continue
            messages_payload = _http_get_json(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=headers,
                params={"limit": 8},
            )
            messages = messages_payload if isinstance(messages_payload, list) else messages_payload.get("messages") or []
            for msg in messages:
                text = str(msg.get("content") or "").strip()
                if not _looks_work_related(text):
                    continue
                author = msg.get("author") or {}
                work_like.append({
                    "guild_id": guild_id,
                    "guild_name": guild.get("name") or guild_id,
                    "channel_id": channel_id,
                    "channel_name": channel.get("name") or channel_id,
                    "author": author.get("username") if isinstance(author, dict) else "",
                    "id": msg.get("id") or "",
                    "text_preview": text[:240],
                    "created_date": msg.get("timestamp") or "",
                    "work_related": True,
                })
                if len(work_like) >= 12:
                    break
            if len(work_like) >= 12:
                break
        if len(work_like) >= 12:
            break
    scan["messages"]["discord"] = work_like
    if work_like:
        chosen = work_like[0]
        scan["proposals"].append({
            "action_type": "message_triage",
            "description": (
                f"{_count_phrase(len(work_like), 'recent Discord message')} look work-related. "
                f"Top channel: {chosen['channel_name']}."
            ),
            "payload": {
                "source": "discord",
                "channel_ids": sorted({m["channel_id"] for m in work_like if m.get("channel_id")})[:5],
                "confidence": 0.6,
                "risk_level": "medium",
            },
        })


def _scan_monday(scan: dict[str, Any], accounts: list[dict[str, Any]]) -> None:
    token = _account_token(accounts, "monday", "api_token")
    if not token:
        return
    headers = {"Authorization": token, "Content-Type": "application/json"}
    query = """
    query DecisionsDailyPlan {
      boards(limit: 10) {
        id
        name
        items_page(limit: 8) {
          items {
            id
            name
            updated_at
            group { title }
            column_values { text }
          }
        }
      }
    }
    """
    payload = _http_post_json(
        "https://api.monday.com/v2",
        headers=headers,
        json_payload={"query": query},
    )
    if payload.get("errors"):
        raise RuntimeError("monday_api_error")
    items = []
    for board in ((payload.get("data") or {}).get("boards") or []):
        for item in (((board.get("items_page") or {}).get("items")) or []):
            col_text = " ".join(
                str(col.get("text") or "")
                for col in (item.get("column_values") or [])
                if isinstance(col, dict)
            )
            text = f"{item.get('name') or ''}\n{col_text}"
            if not _looks_work_related(text):
                continue
            group = item.get("group") or {}
            items.append({
                "id": item.get("id") or "",
                "name": item.get("name") or "",
                "board_id": board.get("id") or "",
                "board_name": board.get("name") or "",
                "group": group.get("title") if isinstance(group, dict) else "",
                "updated_at": item.get("updated_at") or "",
            })
            if len(items) >= 15:
                break
        if len(items) >= 15:
            break
    scan["tasks"]["monday"] = items
    if items:
        scan["proposals"].append({
            "action_type": "task_triage",
            "description": f"{_count_phrase(len(items), 'Monday item')} look work-related. Top item: {items[0]['name']}",
            "payload": {
                "source": "monday",
                "item_ids": [item["id"] for item in items[:8] if item.get("id")],
                "board_ids": sorted({item["board_id"] for item in items if item.get("board_id")})[:5],
                "confidence": 0.62,
                "risk_level": "medium",
            },
        })


def _looks_work_related(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(word in lowered for word in WORK_KEYWORDS)
