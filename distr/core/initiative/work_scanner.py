"""Read-only work source scanner for Initiative.

This module turns boards, tickets, and message surfaces into compact proposed
work items. It never mutates state; policy and action handlers decide whether a
proposal is surfaced, queued for approval, or executed.
"""

from __future__ import annotations

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
}


def build_work_scan(settings: dict[str, Any]) -> dict[str, Any]:
    scan = {
        "boards": [],
        "proposals": [],
        "messages": {"whatsapp": [], "telegram": [], "email": []},
        "unavailable_sources": [],
    }

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
        # Email scanning is connector/sub-agent territory. Until a mailbox source
        # is wired into the local runtime, Initiative should stay quiet here
        # instead of surfacing a false "broken" source on every cycle.
        pass

    return scan


def _scan_local_boards(scan: dict[str, Any], settings: dict[str, Any]) -> None:
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket

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
                "agent_enabled": bool(board.agent_enabled),
                "whatsapp_checkin_enabled": bool(getattr(board, "whatsapp_checkin_enabled", False)),
                "source_lane": board.agent_source_lane or settings.get("kanban_agent_source_lane", "") or "Current",
                "done_lane": board.agent_done_lane or settings.get("kanban_agent_done_lane", "") or "QA / Assess",
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
                "description": f"{board_row['name']} ({provider}) has {total} fetched item(s) available for review.",
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
                f"{board['name']} has {len(backlog.get('tickets') or [])} backlog item(s) "
                f"that could be promoted into {current_name}."
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
    from distr.core.db.kanban import KanbanBoard

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
                    "whatsapp_checkin_enabled": bool(getattr(board, "whatsapp_checkin_enabled", False)),
                }

        work_like = []
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            text = (row.text or row.caption or "").strip()
            if not text and row.media_type:
                text = f"[{row.media_type} message]"
            phone = row.jid_phone or (row.jid or "").split("@")[0] or "unknown"
            link = link_by_phone.get(phone)
            linked_board = board_meta.get(link.board_id) if link else None
            linked_whatsapp_enabled = bool(linked_board and linked_board.get("whatsapp_checkin_enabled"))
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
                "linked_board_whatsapp_checkin_enabled": linked_whatsapp_enabled,
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
                    "linked_board_whatsapp_checkin_enabled": linked_whatsapp_enabled,
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
            if g["fresh"] or g["work_related_count"] > 0 or g.get("linked_board_whatsapp_checkin_enabled")
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
                    f"{count} WhatsApp message(s) arrived from {chosen['latest_sender'] or chosen['jid_phone']} "
                    f"and the chat is linked to board '{board_name}'. Ask whether to snapshot them into tickets."
                )
            elif chosen["work_related_count"] > 0:
                description = (
                    f"{count} recent WhatsApp message(s) from {chosen['latest_sender'] or chosen['jid_phone']} "
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
                    "linked_board_whatsapp_checkin_enabled": bool(chosen.get("linked_board_whatsapp_checkin_enabled")),
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
                "description": f"{len(work_like)} recent Telegram group message(s) look work-related and may need review.",
                "payload": {
                    "source": "telegram",
                    "message_ids": [m["id"] for m in work_like[:5]],
                    "confidence": 0.62,
                    "risk_level": "medium",
                },
            })


def _looks_work_related(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(word in lowered for word in WORK_KEYWORDS)
