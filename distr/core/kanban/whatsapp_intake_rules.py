"""WhatsApp intake surface rules (board-linked announce + Telegram push).

Paul's rules for no-face-to-face company boards (AuctionNow, Tensology.com):

1. Only announce when the chat is linked to a board. Unlinked messages stay quiet
   (stored, but no voice/Oracle/chat "a message came in" nudge).
2. Board-linked messages also push a short notice to Telegram. Track whether that
   push actually happened so Jupiter can tell Paul when it fails.
3. Normal WhatsApp bodies stay quiet unless Paul explicitly asks to hear new
   messages (e.g. "any new messages come in, please read them to me").

Enable globally with::

    DECISIONSAI_WA_INTAKE_SURFACE=board_linked

Or per board via ``orchestrator_policy.whatsapp_intake.mode = "board_linked_quiet"``.

When disabled (``legacy`` / unset), inbound routing keeps the previous
WorkIntake + MessageBus behaviour.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import text

from distr.core.db import engine

logger = logging.getLogger(__name__)

SURFACE_LEGACY = "legacy"
SURFACE_BOARD_LINKED = "board_linked"

# Explicit readout asks (rule 3). Keep conservative — only clear "read my WA" intent.
_EXPLICIT_READ_RE = re.compile(
    r"(?is)\b("
    r"any\s+new\s+messages?\s*(come\s+in|came\s+in|arrived)?"
    r"|new\s+messages?\s*(come\s+in|came\s+in|arrived)"
    r"|read\s+(?:my\s+)?(?:whatsapp\s+|wa\s+)?messages?"
    r"|read\s+them\s+to\s+me"
    r"|what(?:'?s|\s+is)\s+(?:new\s+)?on\s+whatsapp"
    r"|whatsapp\s+updates?"
    r"|check\s+(?:my\s+)?whatsapp"
    r")\b"
)

# Optional board-name allowlist when surface mode is board_linked:
# DECISIONSAI_WA_INTAKE_SURFACE_BOARDS=AuctionNow,Tensology
# (unset = all linked boards; unlinked always silent when mode is on)


@dataclass
class WhatsAppLinkInfo:
    board_id: int | None = None
    board_name: str = ""
    contact_name: str = ""
    phone_jid: str = ""
    phone_number: str = ""
    auto_snapshot: bool = False
    link_id: int | None = None


@dataclass
class IntakeSurfaceDecision:
    """Routing decision for one inbound WhatsApp text."""

    surface_mode: str
    linked: bool
    board_id: int | None = None
    board_name: str = ""
    contact_name: str = ""
    should_announce: bool = False
    should_telegram_push: bool = False
    should_route_work_intake: bool = True
    should_inject_message_bus: bool = True
    reason: str = ""
    link: WhatsAppLinkInfo = field(default_factory=WhatsAppLinkInfo)


@dataclass
class TelegramPushResult:
    attempted: bool
    ok: bool
    error: str = ""
    push_id: int | None = None
    dry_run: bool = False


def wa_intake_surface_mode() -> str:
    """Return ``board_linked`` or ``legacy``.

    Env ``DECISIONSAI_WA_INTAKE_SURFACE``:
      - board_linked | 1 | true | on | yes → board_linked
      - legacy | 0 | false | off | no | unset → legacy
    """
    raw = os.environ.get("DECISIONSAI_WA_INTAKE_SURFACE", "").strip().lower()
    if raw in {"board_linked", "board-linked", "1", "true", "on", "yes"}:
        return SURFACE_BOARD_LINKED
    if raw in {"legacy", "0", "false", "off", "no"}:
        return SURFACE_LEGACY
    # Settings fallback (optional; never require Settings for unit tests).
    try:
        from distr.core.utils import load_settings_from_db

        settings = load_settings_from_db() or {}
        setting = str(settings.get("wa_intake_surface_mode") or "").strip().lower()
        if setting in {"board_linked", "board-linked", "1", "true", "on", "yes"}:
            return SURFACE_BOARD_LINKED
        if setting in {"legacy", "0", "false", "off", "no"}:
            return SURFACE_LEGACY
    except Exception:
        logger.debug("wa_intake_surface_mode settings lookup failed", exc_info=True)
    return SURFACE_LEGACY


def board_linked_surface_enabled() -> bool:
    return wa_intake_surface_mode() == SURFACE_BOARD_LINKED


def is_explicit_whatsapp_read_request(text: str) -> bool:
    """True when Paul is asking to hear/read new WhatsApp messages."""
    return bool(_EXPLICIT_READ_RE.search(str(text or "")))


def _board_name_allowlist() -> set[str] | None:
    """Optional name filter. None = no filter (all boards).

    ``DECISIONSAI_WA_INTAKE_SURFACE_BOARDS`` comma list. Special value ``*`` or
    empty when mode is on → all boards. When unset and mode is board_linked,
    default hints include AuctionNow + Tensology but do **not** exclude other
    linked boards — the allowlist only applies when the env var is explicitly set.
    """
    raw = os.environ.get("DECISIONSAI_WA_INTAKE_SURFACE_BOARDS")
    if raw is None:
        return None
    cleaned = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
    if not cleaned or cleaned == ["*"]:
        return None
    return set(cleaned)


def _policy_whatsapp_intake(board: Any) -> dict[str, Any]:
    raw = getattr(board, "orchestrator_policy", None) if board is not None else None
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            parsed = {}
    if not isinstance(parsed, dict):
        return {}
    section = parsed.get("whatsapp_intake") or {}
    return section if isinstance(section, dict) else {}


def board_requests_board_linked_quiet(board: Any) -> bool:
    """True when board orchestrator_policy opts into board_linked_quiet mode."""
    section = _policy_whatsapp_intake(board)
    mode = str(section.get("mode") or "").strip().lower()
    return mode in {"board_linked_quiet", "board_linked", "quiet_unless_asked"}


def resolve_whatsapp_link(jid: str, *, jid_phone: str = "") -> WhatsAppLinkInfo:
    """Look up WhatsAppPhoneLink (+ board name) for a chat JID/phone."""
    info = WhatsAppLinkInfo(phone_jid=str(jid or ""), phone_number=str(jid_phone or ""))
    tid = (jid or jid_phone or "").strip()
    if not tid:
        return info
    try:
        from distr.core.db import WhatsAppPhoneLink, get_session
        from distr.core.db.kanban import KanbanBoard

        with get_session() as session:
            link = (
                session.query(WhatsAppPhoneLink)
                .filter(
                    (WhatsAppPhoneLink.phone_jid == tid)
                    | (WhatsAppPhoneLink.phone_number == tid)
                    | (WhatsAppPhoneLink.phone_jid == jid)
                    | (WhatsAppPhoneLink.phone_number == jid_phone)
                )
                .order_by(WhatsAppPhoneLink.auto_snapshot.desc(), WhatsAppPhoneLink.id.asc())
                .first()
            )
            if link is None:
                return info
            board = session.query(KanbanBoard).filter(KanbanBoard.id == int(link.board_id)).first()
            info.board_id = int(link.board_id) if link.board_id else None
            info.board_name = str(getattr(board, "name", "") or "") if board else ""
            info.contact_name = str(link.contact_name or "")
            info.phone_jid = str(link.phone_jid or jid or "")
            info.phone_number = str(link.phone_number or jid_phone or "")
            info.auto_snapshot = bool(link.auto_snapshot)
            info.link_id = int(link.id)
            return info
    except Exception:
        logger.debug("resolve_whatsapp_link failed", exc_info=True)
        return info


def _board_in_scope(link: WhatsAppLinkInfo, board: Any = None) -> bool:
    allow = _board_name_allowlist()
    if allow is None:
        return True
    name = (link.board_name or getattr(board, "name", "") or "").strip().lower()
    if name and name in allow:
        return True
    # Also match substring so "AuctionNow Delivery" still counts.
    return any(hint in name for hint in allow if hint)


def decide_intake_surface(
    *,
    jid: str,
    jid_phone: str = "",
    from_me: bool = False,
    link: WhatsAppLinkInfo | None = None,
    board: Any = None,
) -> IntakeSurfaceDecision:
    """Decide announce / TG / WorkIntake / MessageBus for one inbound message."""
    link = link or resolve_whatsapp_link(jid, jid_phone=jid_phone)
    linked = bool(link.board_id)
    global_mode = wa_intake_surface_mode()
    board_mode = board_requests_board_linked_quiet(board) if board is not None else False
    # When we have a link, also check that board's policy from DB if board obj not passed.
    if linked and not board_mode and board is None and link.board_id:
        try:
            from distr.core.db import get_session
            from distr.core.db.kanban import KanbanBoard

            with get_session() as session:
                board_row = session.query(KanbanBoard).filter(KanbanBoard.id == int(link.board_id)).first()
                board_mode = board_requests_board_linked_quiet(board_row)
                if board_row is not None and not link.board_name:
                    link.board_name = str(board_row.name or "")
        except Exception:
            logger.debug("board policy lookup failed", exc_info=True)

    active = global_mode == SURFACE_BOARD_LINKED or board_mode
    # Outbound (from_me) never announce/push.
    if from_me:
        return IntakeSurfaceDecision(
            surface_mode=global_mode if active else SURFACE_LEGACY,
            linked=linked,
            board_id=link.board_id,
            board_name=link.board_name,
            contact_name=link.contact_name,
            should_announce=False,
            should_telegram_push=False,
            should_route_work_intake=False,
            should_inject_message_bus=False,
            reason="from_me",
            link=link,
        )

    if not active:
        return IntakeSurfaceDecision(
            surface_mode=SURFACE_LEGACY,
            linked=linked,
            board_id=link.board_id,
            board_name=link.board_name,
            contact_name=link.contact_name,
            should_announce=False,
            should_telegram_push=False,
            should_route_work_intake=True,
            should_inject_message_bus=True,
            reason="legacy_surface",
            link=link,
        )

    if not linked:
        return IntakeSurfaceDecision(
            surface_mode=SURFACE_BOARD_LINKED,
            linked=False,
            should_announce=False,
            should_telegram_push=False,
            should_route_work_intake=False,
            should_inject_message_bus=False,
            reason="unlinked_silent",
            link=link,
        )

    if not _board_in_scope(link, board):
        # Linked but outside allowlist → treat as silent store (still no surprise announce).
        return IntakeSurfaceDecision(
            surface_mode=SURFACE_BOARD_LINKED,
            linked=True,
            board_id=link.board_id,
            board_name=link.board_name,
            contact_name=link.contact_name,
            should_announce=False,
            should_telegram_push=False,
            should_route_work_intake=True,
            should_inject_message_bus=False,
            reason="linked_out_of_scope_quiet",
            link=link,
        )

    return IntakeSurfaceDecision(
        surface_mode=SURFACE_BOARD_LINKED,
        linked=True,
        board_id=link.board_id,
        board_name=link.board_name,
        contact_name=link.contact_name,
        should_announce=True,
        should_telegram_push=True,
        should_route_work_intake=True,
        should_inject_message_bus=False,  # quiet bodies; announce + TG only
        reason="board_linked_announce_and_telegram",
        link=link,
    )


def format_announce_speech(decision: IntakeSurfaceDecision, *, preview: str = "") -> str:
    who = (decision.contact_name or "a contact").strip() or "a contact"
    board = (decision.board_name or "a board").strip() or "a board"
    snippet = " ".join(str(preview or "").split())
    if snippet:
        snippet = snippet[:80] + ("…" if len(snippet) > 80 else "")
        return f"WhatsApp message from {who} on {board}: {snippet}"
    return f"A WhatsApp message came in from {who} on {board}."


def format_telegram_notice(
    decision: IntakeSurfaceDecision,
    *,
    preview: str = "",
    sender_phone: str = "",
) -> str:
    who = (decision.contact_name or sender_phone or "WhatsApp contact").strip()
    board = (decision.board_name or f"board #{decision.board_id}").strip()
    snippet = " ".join(str(preview or "").split())
    lines = [
        "**WhatsApp (board-linked)**",
        f"From: {who}",
        f"Board: {board}",
    ]
    if snippet:
        lines.append("")
        lines.append(snippet[:500] + ("…" if len(snippet) > 500 else ""))
    return "\n".join(lines)


def ensure_telegram_push_table() -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS whatsapp_intake_telegram_pushes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jid VARCHAR,
                board_id INTEGER,
                source_message_id VARCHAR,
                contact_name VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'pending',
                error TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                created_at FLOAT NOT NULL,
                updated_at FLOAT NOT NULL
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_wa_intake_tg_pushes_status "
            "ON whatsapp_intake_telegram_pushes(status, created_at)"
        ))


def record_telegram_push(
    *,
    jid: str,
    board_id: int | None,
    source_message_id: str,
    contact_name: str,
    status: str,
    error: str = "",
    dry_run: bool = False,
) -> int:
    """Persist TG push attempt so failures surface for Jupiter → Paul."""
    ensure_telegram_push_table()
    now = time.time()
    with engine.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO whatsapp_intake_telegram_pushes (
                jid, board_id, source_message_id, contact_name,
                status, error, dry_run, created_at, updated_at
            ) VALUES (
                :jid, :board_id, :mid, :contact,
                :status, :error, :dry_run, :now, :now
            )
        """), {
            "jid": jid or "",
            "board_id": int(board_id) if board_id is not None else None,
            "mid": source_message_id or "",
            "contact": contact_name or "",
            "status": status,
            "error": (error or "")[:1000],
            "dry_run": 1 if dry_run else 0,
            "now": now,
        })
        return int(result.lastrowid or 0)


def list_failed_telegram_pushes(*, limit: int = 20) -> list[dict[str, Any]]:
    ensure_telegram_push_table()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT * FROM whatsapp_intake_telegram_pushes
            WHERE status='failed'
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
        """), {"limit": max(1, min(int(limit), 100))}).mappings().all()
    return [dict(r) for r in rows]


def announce_board_linked_message(decision: IntakeSurfaceDecision, *, preview: str = "") -> bool:
    """Voice/Oracle nudge for a board-linked inbound message."""
    if not decision.should_announce:
        return False
    speech = format_announce_speech(decision, preview=preview)
    try:
        from distr.core.signals import speak_text_directly_event_queue

        speak_text_directly_event_queue(speech)
        return True
    except Exception:
        logger.exception("WhatsApp board-linked announce failed")
        return False


def push_board_linked_to_telegram(
    decision: IntakeSurfaceDecision,
    *,
    preview: str = "",
    sender_phone: str = "",
    source_message_id: str = "",
    jid: str = "",
) -> TelegramPushResult:
    """Push a short board-linked notice to Telegram and record success/failure."""
    if not decision.should_telegram_push:
        return TelegramPushResult(attempted=False, ok=False, error="not_requested")

    body = format_telegram_notice(
        decision, preview=preview, sender_phone=sender_phone
    )
    dry = os.environ.get("DECISIONSAI_WHATSAPP_DRY_RUN", "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    # Dry-run still "attempts" the path for tracking, but does not send.
    if dry:
        push_id = record_telegram_push(
            jid=jid or decision.link.phone_jid,
            board_id=decision.board_id,
            source_message_id=source_message_id,
            contact_name=decision.contact_name or sender_phone,
            status="dry_run",
            error="",
            dry_run=True,
        )
        logger.info(
            "WhatsApp intake TG push dry-run board=%s contact=%s mid=%s push_id=%s",
            decision.board_id,
            decision.contact_name or sender_phone,
            source_message_id,
            push_id,
        )
        return TelegramPushResult(attempted=True, ok=True, push_id=push_id, dry_run=True)

    try:
        from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

        manager = _telegram_manager_from_app()
        if not manager:
            push_id = record_telegram_push(
                jid=jid or decision.link.phone_jid,
                board_id=decision.board_id,
                source_message_id=source_message_id,
                contact_name=decision.contact_name or sender_phone,
                status="failed",
                error="telegram_manager_unavailable",
            )
            logger.warning(
                "WhatsApp board-linked TG push FAILED (no telegram manager) "
                "board=%s contact=%s mid=%s push_id=%s — tell Paul",
                decision.board_id,
                decision.contact_name or sender_phone,
                source_message_id,
                push_id,
            )
            return TelegramPushResult(
                attempted=True, ok=False, error="telegram_manager_unavailable", push_id=push_id
            )
        ok = bool(manager.send_to_telegram(body))
        status = "sent" if ok else "failed"
        error = "" if ok else "send_to_telegram_returned_false"
        push_id = record_telegram_push(
            jid=jid or decision.link.phone_jid,
            board_id=decision.board_id,
            source_message_id=source_message_id,
            contact_name=decision.contact_name or sender_phone,
            status=status,
            error=error,
        )
        if not ok:
            logger.warning(
                "WhatsApp board-linked TG push FAILED board=%s contact=%s mid=%s "
                "push_id=%s — tell Paul",
                decision.board_id,
                decision.contact_name or sender_phone,
                source_message_id,
                push_id,
            )
        else:
            logger.info(
                "WhatsApp board-linked TG push ok board=%s mid=%s push_id=%s",
                decision.board_id,
                source_message_id,
                push_id,
            )
        return TelegramPushResult(attempted=True, ok=ok, error=error, push_id=push_id)
    except Exception as exc:
        push_id = record_telegram_push(
            jid=jid or decision.link.phone_jid,
            board_id=decision.board_id,
            source_message_id=source_message_id,
            contact_name=decision.contact_name or sender_phone,
            status="failed",
            error=str(exc)[:1000],
        )
        logger.exception(
            "WhatsApp board-linked TG push EXCEPTION board=%s mid=%s push_id=%s — tell Paul",
            decision.board_id,
            source_message_id,
            push_id,
        )
        return TelegramPushResult(
            attempted=True, ok=False, error=str(exc)[:1000], push_id=push_id
        )


def apply_board_linked_surface_actions(
    decision: IntakeSurfaceDecision,
    *,
    preview: str = "",
    sender_phone: str = "",
    source_message_id: str = "",
    jid: str = "",
) -> dict[str, Any]:
    """Announce + TG push for a board-linked decision; return tracking dict."""
    announced = announce_board_linked_message(decision, preview=preview)
    tg = push_board_linked_to_telegram(
        decision,
        preview=preview,
        sender_phone=sender_phone,
        source_message_id=source_message_id,
        jid=jid,
    )
    return {
        "announced": announced,
        "telegram_attempted": tg.attempted,
        "telegram_ok": tg.ok,
        "telegram_error": tg.error,
        "telegram_push_id": tg.push_id,
        "telegram_dry_run": tg.dry_run,
        "reason": decision.reason,
        "board_id": decision.board_id,
    }
