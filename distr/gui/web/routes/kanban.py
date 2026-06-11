"""
API routes for Ticket Board management.
"""
from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from typing import Optional, List, Tuple
from urllib.parse import quote, urlparse, unquote
from datetime import datetime
import html
import json
import logging
import mimetypes
import re
import os
import shutil
import subprocess
import tempfile
import threading
import asyncio
import secrets
import time
import base64
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from distr.core.paths import DB_DIR
from distr.core.integrations.telegram.utils import relay_internal_token
from distr.core.integrations.whatsapp.paths import resolve_whatsapp_media_disk_path
from distr.core.kanban.ticket_policy import (
    infer_ticket_complexity,
    normalize_source_provider,
    normalize_ticket_complexity,
    resolve_ticket_cli_route,
)
from distr.core.db import get_session
from distr.core.db.orm_compat import orm_get_by_id
from distr.core.db import WhatsAppMessage
from distr.core.db.kanban import (
    KanbanBoard, KanbanLane, KanbanTicket,
    KanbanTicketFile, KanbanTicketLink, KanbanTicketTodo, KanbanTicketAuditEntry,
)
from distr.core.db.projects import Project
from distr.gui.web.security import is_allowed_local_origin
from distr.gui.web.routes.kanban_whatsapp import register_whatsapp_routes

logger = logging.getLogger(__name__)

KANBAN_UPLOADS_DIR = os.path.join(DB_DIR, "kanban_uploads")
DEFAULT_LANES = ["Backlog", "Current", "QA / Assess", "Done"]


def _whatsapp_message_sender(message) -> str:
    if getattr(message, "from_me", False):
        return "Me"
    return (
        getattr(message, "sender_push_name", None)
        or getattr(message, "sender_phone", None)
        or getattr(message, "jid_phone", None)
        or "Unknown"
    )


def _whatsapp_message_timestamp(message) -> str:
    value = getattr(message, "whatsapp_timestamp", None)
    if value:
        try:
            return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    created = getattr(message, "created_date", None)
    if created:
        try:
            return created.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return ""


_WHATSAPP_INTERNAL_MEDIA_BLOCK_RE = re.compile(
    r"\[(?:OCR|Image analysis|Image Analysis|Visual analysis|Visual Analysis)\][\s\S]*?(?:\n\s*\n|$)",
    re.S,
)

def _ticket_file_payload(ticket_id: int, file_record: KanbanTicketFile) -> dict:
    """Browser-safe ticket attachment payload with a stable view URL."""
    return {
        "id": file_record.id,
        "filename": file_record.filename,
        "description": file_record.description or "",
        "url": f"/api/tickets/tickets/{int(ticket_id)}/files/{int(file_record.id)}/content",
    }


def _whatsapp_snapshot_group_for_ticket(board_id: int, ticket_id: int) -> str:
    """Durable WhatsApp message batch marker for a created ticket."""
    return f"board_{int(board_id)}_ticket_{int(ticket_id)}"


def _whatsapp_snapshot_group_filter(ticket_id: int):
    """Match current and older WhatsApp ticket batch markers for cleanup."""
    ticket_token = f"ticket_{int(ticket_id)}"
    legacy_prefix = f"{int(ticket_id)}_%"
    return or_(
        WhatsAppMessage.snapshot_group == ticket_token,
        WhatsAppMessage.snapshot_group.like(f"%_{ticket_token}"),
        WhatsAppMessage.snapshot_group.like(legacy_prefix),
    )


def _clean_whatsapp_caption_for_ticket(caption: str, *, keep_transcription: bool = True) -> str:
    """Return client-visible caption/transcript text, without internal extraction notes."""
    cleaned = (caption or "").strip()
    if not cleaned:
        return ""
    cleaned = _WHATSAPP_INTERNAL_MEDIA_BLOCK_RE.sub("", cleaned).strip()
    if not keep_transcription:
        cleaned = re.sub(r"\[Transcription\][\s\S]*?(?:\n\s*\n|$)", "", cleaned, flags=re.S).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _whatsapp_message_body(message) -> str:
    parts = []
    text = (getattr(message, "text", None) or "").strip()
    media_type = (getattr(message, "media_type", None) or "").strip()
    media_mime_type = (getattr(message, "media_mime_type", None) or "").strip()
    is_audio_or_video = _is_whatsapp_voice_type(media_type, media_mime_type) or _is_whatsapp_video_type(media_type, media_mime_type)
    caption = _clean_whatsapp_caption_for_ticket(
        getattr(message, "caption", None) or "",
        keep_transcription=is_audio_or_video,
    )
    if text:
        parts.append(text)
    if caption and caption != text:
        parts.append(caption)
    if media_type:
        filename = (getattr(message, "media_filename", None) or "").strip()
        label = f"[{media_type}"
        if filename:
            label += f": {filename}"
        label += "]"
        parts.append(label)
    return "\n".join(parts).strip() or "[message]"


def _is_whatsapp_voice_type(media_type: str, media_mime_type: str) -> bool:
    t = str(media_type or "").lower()
    m = str(media_mime_type or "").lower()
    return t in ("voice", "audio", "ptt") or m.startswith("audio/")


def _is_whatsapp_video_type(media_type: str, media_mime_type: str) -> bool:
    t = str(media_type or "").lower()
    m = str(media_mime_type or "").lower()
    return t == "video" or m.startswith("video/")


def _is_whatsapp_image_type(media_type: str, media_mime_type: str) -> bool:
    t = str(media_type or "").lower()
    m = str(media_mime_type or "").lower()
    return t in ("photo", "image") or m.startswith("image/")


def _upsert_whatsapp_extracted_block(existing_caption: str, label: str, extracted_text: str) -> str:
    text = (extracted_text or "").strip()
    if not text:
        return existing_caption or ""
    block = f"[{label}] {text}"
    existing = (existing_caption or "").strip()
    if not existing:
        return block
    pattern = re.compile(rf"\[{re.escape(label)}\]\s.*?(?=\n\n\[[A-Za-z ]+\]\s|\Z)", re.S)
    if pattern.search(existing):
        return pattern.sub(block, existing).strip()
    return f"{block}\n\n{existing}".strip()


def _ensure_whatsapp_media_text(message) -> dict:
    """Ensure cached WhatsApp media has text extraction available for ticket drafting."""
    media_type = (getattr(message, "media_type", None) or "").strip()
    media_mime_type = (getattr(message, "media_mime_type", None) or "").strip()
    if not media_type:
        return {"status": "not_media", "analysis_type": "", "text": "", "error": ""}

    caption = getattr(message, "caption", None) or ""
    if "[Transcription]" in caption:
        return {"status": "ready", "analysis_type": "transcription", "text": caption, "error": ""}

    stored_path = (getattr(message, "media_local_path", None) or "").strip()
    local_path = resolve_whatsapp_media_disk_path(stored_path)
    if not local_path or not os.path.exists(local_path):
        return {"status": "missing_media", "analysis_type": "", "text": "", "error": "Media file not cached"}

    extracted = ""
    label = ""
    try:
        if _is_whatsapp_voice_type(media_type, media_mime_type):
            from distr.core.audio.voice_cloning import transcribe_audio_file

            extracted = (transcribe_audio_file(local_path) or "").strip()
            label = "Transcription"
        elif _is_whatsapp_video_type(media_type, media_mime_type):
            from distr.core.audio.voice_cloning import transcribe_audio_file

            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                    wav_path = tmp_wav.name
                try:
                    subprocess.run(
                        [ffmpeg_path, "-y", "-i", local_path, "-ar", "16000", "-ac", "1", wav_path],
                        capture_output=True,
                        timeout=180,
                        check=True,
                    )
                    extracted = (transcribe_audio_file(wav_path) or "").strip()
                finally:
                    try:
                        os.unlink(wav_path)
                    except Exception:
                        pass
            else:
                extracted = (transcribe_audio_file(local_path) or "").strip()
            label = "Transcription"
        elif _is_whatsapp_image_type(media_type, media_mime_type):
            return {"status": "attached", "analysis_type": "attachment", "text": "", "error": ""}
        else:
            return {"status": "unsupported", "analysis_type": "", "text": "", "error": "Unsupported media type for text extraction"}
    except Exception as exc:
        logger.warning("WhatsApp media text extraction failed for message %s: %s", getattr(message, "id", ""), exc)
        return {"status": "failed", "analysis_type": label.lower() if label else "", "text": "", "error": str(exc)}

    if not extracted:
        return {"status": "empty", "analysis_type": label.lower() if label else "", "text": "", "error": "No text extracted from media"}

    message.caption = _upsert_whatsapp_extracted_block(caption, label, extracted)
    return {"status": "ready", "analysis_type": label.lower(), "text": extracted, "error": ""}


def _ensure_whatsapp_messages_enriched(messages) -> dict:
    media = []
    counts = {"media": 0, "analyzed": 0, "missing": 0, "failed": 0, "unsupported": 0}
    for msg in messages:
        if not getattr(msg, "media_type", None):
            continue
        counts["media"] += 1
        result = _ensure_whatsapp_media_text(msg)
        status = result.get("status") or ""
        if status == "ready":
            counts["analyzed"] += 1
        elif status == "missing_media":
            counts["missing"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status in ("unsupported", "attached"):
            counts["unsupported"] += 1
        media.append({
            "message_id": msg.id,
            "media_type": msg.media_type or "",
            "filename": msg.media_filename or "",
            "analysis_status": status,
            "analysis_type": result.get("analysis_type") or "",
            "error": result.get("error") or "",
        })
    return {"counts": counts, "media": media}


def _infer_whatsapp_ticket_priority(title: str, description: str) -> str:
    text = f"{title}\n{description}".lower()
    if re.search(r"\b(critical|blocker|blocked|production down|urgent|asap|immediately|emergency|cannot work)\b", text):
        return "critical"
    if re.search(r"\b(high priority|important|today|tomorrow|deadline|broken|failing|can't|cannot)\b", text):
        return "high"
    if re.search(r"\b(low priority|when you can|no rush|nice to have|minor)\b", text):
        return "low"
    return "medium"


def _validate_whatsapp_ticket_quality(title: str, description: str, messages, enrichment: dict | None = None) -> dict:
    title = (title or "").strip()
    description = (description or "").strip()
    enrichment = enrichment or {}
    media_counts = (enrichment.get("counts") or {})
    issues = []
    warnings = []
    score = 100

    if len(title) < 12:
        issues.append("Title is too vague.")
        score -= 20
    if len(title) > 90:
        warnings.append("Title is long; keep it scannable.")
        score -= 5
    if len(description) < 180:
        issues.append("Description is too thin for a client-ready ticket.")
        score -= 25
    if "Source chat:" not in description:
        issues.append("Missing source chat context.")
        score -= 10
    if "Transcript:" not in description and "Client request:" not in description:
        issues.append("Missing transcript or client request section.")
        score -= 15
    if "Acceptance criteria" not in description:
        issues.append("Missing acceptance criteria.")
        score -= 15
    if "Questions / ambiguities" not in description:
        warnings.append("Questions / ambiguities section is missing.")
        score -= 5
    if messages and f"Messages included: {len(messages)}" not in description:
        warnings.append("Message count is not explicitly recorded.")
        score -= 5
    if int(media_counts.get("media") or 0) and "Attachments" not in description and "Media evidence" not in description:
        issues.append("Media is attached but not described in the ticket.")
        score -= 15
    if int(media_counts.get("missing") or 0):
        warnings.append(f"{media_counts.get('missing')} media file(s) were not cached locally.")
        score -= 5
    if int(media_counts.get("failed") or 0):
        warnings.append(f"{media_counts.get('failed')} media analysis attempt(s) failed.")
        score -= 5
    passed = score >= 75 and not issues
    return {
        "passed": passed,
        "score": max(0, min(100, score)),
        "issues": issues,
        "warnings": warnings,
        "metrics": {
            "title_chars": len(title),
            "description_chars": len(description),
            "message_count": len(messages or []),
            "media_count": int(media_counts.get("media") or 0),
            "media_analyzed_count": int(media_counts.get("analyzed") or 0),
            "media_missing_count": int(media_counts.get("missing") or 0),
            "media_failed_count": int(media_counts.get("failed") or 0),
        },
    }


def _whatsapp_ticket_quality_instructions(message_count: int, media_count: int) -> str:
    return f"""Quality bar before you answer:
- The ticket must be written for someone who has not seen the WhatsApp thread.
- Preserve the client's concrete request, names, dates, numbers, constraints, and expected outcome.
- Include a short "Client request" section.
- Include "Source chat:" and "Messages included: {message_count}" exactly once.
- Include "Acceptance criteria" with measurable bullets.
- Include "Questions / ambiguities" if anything is unclear; write "None identified" only if truly clear.
- If media is present ({media_count} item(s)), include "Media evidence" as a clean attachment list. Use human captions and voice/video transcriptions when available; do not mention OCR, bounding boxes, extraction status, or internal processing.
- Do not invent facts that are not in the messages."""


def _build_whatsapp_ticket_draft(messages) -> dict:
    count = len(messages)
    first = messages[0] if messages else None
    phone = (getattr(first, "jid_phone", None) or getattr(first, "sender_phone", None) or "WhatsApp").strip() if first else "WhatsApp"
    contact = next(
        (
            _whatsapp_message_sender(m)
            for m in messages
            if not getattr(m, "from_me", False) and _whatsapp_message_sender(m) != "Unknown"
        ),
        phone,
    )
    first_body = next(
        (
            re.sub(r"\s+", " ", _whatsapp_message_body(m)).strip()
            for m in messages
            if _whatsapp_message_body(m) and _whatsapp_message_body(m) != "[message]"
        ),
        "",
    )
    if first_body:
        title = f"WhatsApp: {first_body}"
    else:
        title = f"WhatsApp request from {contact}"
    if len(title) > 80:
        title = title[:77].rstrip() + "..."

    raw_lines = []
    transcript_lines = []
    attachment_lines = []
    media_count = 0
    for m in messages:
        sender = _whatsapp_message_sender(m)
        ts = _whatsapp_message_timestamp(m)
        body = _whatsapp_message_body(m)
        prefix = f"[{ts}] {sender}" if ts else sender
        raw_lines.append(f"{prefix}: {body}")
        transcript_lines.append(f"- {prefix}: {body}")
        if getattr(m, "media_type", None):
            media_count += 1
            filename = getattr(m, "media_filename", None) or getattr(m, "media_type", None) or "media"
            caption = _clean_whatsapp_caption_for_ticket(
                getattr(m, "caption", None) or "",
                keep_transcription=_is_whatsapp_voice_type(getattr(m, "media_type", "") or "", getattr(m, "media_mime_type", "") or "")
                or _is_whatsapp_video_type(getattr(m, "media_type", "") or "", getattr(m, "media_mime_type", "") or ""),
            )
            detail = f"- Message #{getattr(m, 'id', '')}: {filename}"
            if caption:
                detail += f" — {caption}"
            attachment_lines.append(detail)

    description_parts = [
        "WhatsApp conversation snapshot",
        "",
        f"Source chat: {contact} ({phone})",
        f"Messages included: {count}",
        "",
        "Client request:",
        first_body or "Review the WhatsApp transcript and attached media, then complete the requested work.",
        "",
        "Transcript:",
        *(transcript_lines or ["- No message text was available."]),
    ]
    if attachment_lines:
        description_parts.extend(["", "Media evidence:", *attachment_lines])
    description_parts.extend([
        "",
        "Acceptance criteria:",
        "- Confirm the request has been understood from the full WhatsApp context.",
        "- Use the transcript and media evidence when completing the work.",
        "- Flag missing information before marking the ticket complete.",
        "",
        "Questions / ambiguities:",
        "- Review required; no additional questions identified automatically.",
    ])

    description = "\n".join(description_parts).strip()
    priority = _infer_whatsapp_ticket_priority(title, description)
    complexity = infer_ticket_complexity(title, description, file_count=media_count)
    return {
        "title": title,
        "description": description,
        "priority": priority,
        "complexity": complexity,
        "raw_text": "\n".join(raw_lines).strip(),
    }


def _whatsapp_link_message_filter(identifiers: List[str]):
    """SQLAlchemy filter matching WhatsApp messages for a linked chat."""
    return or_(
        WhatsAppMessage.jid.in_(identifiers),
        WhatsAppMessage.jid_phone.in_(identifiers),
        WhatsAppMessage.sender_jid.in_(identifiers),
        WhatsAppMessage.sender_phone.in_(identifiers),
    )


def _whatsapp_unticketed_query(s, identifiers: List[str]):
    """Base query for messages not yet assigned to a ticket batch."""
    existing_ticket_message_ids = {
        row[0]
        for row in s.query(KanbanTicket.whatsapp_message_id)
        .filter(KanbanTicket.whatsapp_message_id.isnot(None))
        .all()
        if row[0]
    }
    query = s.query(WhatsAppMessage).filter(
        _whatsapp_link_message_filter(identifiers),
        WhatsAppMessage.snapshot_group.is_(None),
    )
    if existing_ticket_message_ids:
        query = query.filter(~WhatsAppMessage.id.in_(existing_ticket_message_ids))
    return query


def _whatsapp_last_ticketed_timestamp(s, identifiers: List[str]) -> Optional[int]:
    """Unix timestamp of the newest WhatsApp message already consumed into a ticket."""
    return s.query(func.max(WhatsAppMessage.whatsapp_timestamp)).filter(
        _whatsapp_link_message_filter(identifiers),
        WhatsAppMessage.snapshot_group.isnot(None),
    ).scalar()


def _whatsapp_snapshot_intake_stats(s, identifiers: List[str], scope: str, since_hours: int) -> dict:
    """Counts and timestamps to explain WhatsApp intake selection."""
    unticketed_query = _whatsapp_unticketed_query(s, identifiers)
    total_unticketed = unticketed_query.count()
    total_ticketed = s.query(WhatsAppMessage).filter(
        _whatsapp_link_message_filter(identifiers),
        WhatsAppMessage.snapshot_group.isnot(None),
    ).count()
    last_ticketed_at = _whatsapp_last_ticketed_timestamp(s, identifiers)
    stats = {
        "scope": scope,
        "since_hours": since_hours,
        "total_unticketed": total_unticketed,
        "total_ticketed": total_ticketed,
        "last_ticketed_at": last_ticketed_at,
        "older_unticketed_available": False,
    }
    if scope == "new_since_last_ticket" and last_ticketed_at:
        newer_count = unticketed_query.filter(
            WhatsAppMessage.whatsapp_timestamp > last_ticketed_at,
        ).count()
        stats["new_since_last_ticket_count"] = newer_count
        stats["older_unticketed_available"] = total_unticketed > newer_count
    elif scope == "new_since_last_ticket" and since_hours > 0:
        cutoff = int(datetime.utcnow().timestamp()) - (since_hours * 3600)
        recent_count = unticketed_query.filter(
            WhatsAppMessage.whatsapp_timestamp >= cutoff,
        ).count()
        stats["recent_window_count"] = recent_count
        stats["older_unticketed_available"] = total_unticketed > recent_count
    return stats


def _whatsapp_message_day(message) -> Optional[str]:
    ts = getattr(message, "whatsapp_timestamp", None)
    if ts:
        try:
            return datetime.utcfromtimestamp(int(ts)).date().isoformat()
        except Exception:
            pass
    created = getattr(message, "created_date", None)
    if created:
        try:
            return created.date().isoformat()
        except Exception:
            pass
    return None


def _latest_visible_whatsapp_message_days(messages, *, day_count: int = 2) -> list[str]:
    days: list[str] = []
    for message in messages:
        day = _whatsapp_message_day(message)
        if day and day not in days:
            days.append(day)
        if len(days) >= day_count:
            break
    return days


def _whatsapp_message_identifier_values(link) -> List[str]:
    raw_identifiers = [
        (getattr(link, "phone_jid", "") or "").strip(),
        (getattr(link, "phone_number", "") or "").strip(),
    ]
    identifiers = []
    for value in raw_identifiers:
        if not value:
            continue
        identifiers.append(value)
        bare_phone = value.split("@")[0].split(":")[0].strip()
        if bare_phone:
            identifiers.append(bare_phone)
            identifiers.append(f"{bare_phone}@s.whatsapp.net")
    return sorted({v for v in identifiers if v})


def _whatsapp_media_items(messages, enrichment: dict | None = None) -> List[dict]:
    enrichment_by_id = {
        int(row.get("message_id")): row
        for row in ((enrichment or {}).get("media") or [])
        if row.get("message_id") is not None
    }
    media_items = []
    for m in messages:
        if getattr(m, "media_type", None):
            enrich = enrichment_by_id.get(int(m.id)) or {}
            wa_key = (getattr(m, "message_id", None) or "").strip()
            preview_url = f"/api/tickets/whatsapp/relay-media/{int(m.id)}"
            if wa_key:
                preview_url += f"?wa_key={quote(wa_key, safe='')}"
            local_url = ""
            if getattr(m, "media_local_path", None):
                local_url = f"/api/tickets/whatsapp/media?path={quote(os.path.basename(m.media_local_path or ''), safe='')}"
            media_items.append({
                "message_id": m.id,
                "whatsapp_message_id": wa_key,
                "media_type": m.media_type,
                "media_mime_type": getattr(m, "media_mime_type", None) or "",
                "media_filename": m.media_filename or f"{m.media_type}",
                "media_path": preview_url,
                "download_url": preview_url,
                "local_preview_url": local_url,
                "analysis_status": enrich.get("analysis_status") or "",
                "analysis_type": enrich.get("analysis_type") or "",
                "analysis_error": enrich.get("error") or "",
                "preview_url": preview_url,
                "caption": _clean_whatsapp_caption_for_ticket(
                    getattr(m, "caption", None) or "",
                    keep_transcription=_is_whatsapp_voice_type(m.media_type or "", getattr(m, "media_mime_type", "") or "")
                    or _is_whatsapp_video_type(m.media_type or "", getattr(m, "media_mime_type", "") or ""),
                ),
                "sender": _whatsapp_message_sender(m),
                "timestamp": _whatsapp_message_timestamp(m),
            })
    return media_items


def _resolve_board_whatsapp_snapshot(
    s,
    board_id: int,
    link_id=None,
    limit: int = 500,
    message_ids: Optional[List[int]] = None,
    scope: str = "new_since_last_ticket",
    since_hours: int = 48,
    allow_empty: bool = False,
) -> dict:
    """Resolve a board's linked WhatsApp chat, unticketed messages, and target lane."""
    from distr.core.db import WhatsAppPhoneLink

    board = orm_get_by_id(s, KanbanBoard, board_id)
    if not board:
        raise HTTPException(404, "Board not found")

    link_query = s.query(WhatsAppPhoneLink).filter_by(board_id=board_id)
    if link_id:
        link_query = link_query.filter_by(id=link_id)
    link = link_query.order_by(WhatsAppPhoneLink.auto_snapshot.desc(), WhatsAppPhoneLink.id.asc()).first()
    if not link:
        raise HTTPException(404, "No WhatsApp link is configured for this board")

    identifiers = _whatsapp_message_identifier_values(link)
    if not identifiers:
        raise HTTPException(400, "The linked WhatsApp chat has no stored phone or JID")

    scope = (scope or "new_since_last_ticket").strip().lower()
    if scope not in ("new_since_last_ticket", "all_unticketed", "latest_two_visible_days"):
        scope = "new_since_last_ticket"
    try:
        since_hours = max(0, min(int(since_hours or 48), 24 * 30))
    except Exception:
        since_hours = 48

    message_query = _whatsapp_unticketed_query(s, identifiers)
    intake_stats = _whatsapp_snapshot_intake_stats(s, identifiers, scope, since_hours)
    clean_message_ids = []
    for raw_id in message_ids or []:
        try:
            clean_message_ids.append(int(raw_id))
        except Exception:
            pass

    if clean_message_ids:
        messages = message_query.filter(WhatsAppMessage.id.in_(clean_message_ids)).order_by(
            WhatsAppMessage.whatsapp_timestamp.asc(),
            WhatsAppMessage.id.asc(),
        ).all()
        found_ids = {int(m.id) for m in messages}
        missing_ids = [mid for mid in clean_message_ids if mid not in found_ids]
        if missing_ids:
            raise HTTPException(409, f"Some reviewed WhatsApp messages are no longer available for ticketing: {missing_ids}")
    else:
        if scope == "new_since_last_ticket":
            last_ticketed_at = intake_stats.get("last_ticketed_at")
            if last_ticketed_at:
                message_query = message_query.filter(WhatsAppMessage.whatsapp_timestamp > last_ticketed_at)
            else:
                scope = "latest_two_visible_days"
        messages_desc = message_query.order_by(
            WhatsAppMessage.whatsapp_timestamp.desc(),
            WhatsAppMessage.id.desc(),
        ).limit(limit).all()
        if scope == "latest_two_visible_days":
            latest_days = set(_latest_visible_whatsapp_message_days(messages_desc, day_count=2))
            messages_desc = [
                message for message in messages_desc
                if _whatsapp_message_day(message) in latest_days
            ]
            intake_stats["scope"] = "latest_two_visible_days"
            intake_stats["visible_days"] = sorted(latest_days, reverse=True)
        messages = list(reversed(messages_desc))

    if not messages:
        if allow_empty:
            source_lane_name = (getattr(board, "agent_source_lane", None) or "").strip()
            if not source_lane_name:
                try:
                    from distr.core.utils import load_settings_from_db

                    source_lane_name = (load_settings_from_db().get("kanban_agent_source_lane") or "").strip()
                except Exception:
                    source_lane_name = ""
            lane = None
            if source_lane_name:
                lane = s.query(KanbanLane).filter_by(board_id=board_id, name=source_lane_name).first()
            if not lane:
                lane = s.query(KanbanLane).filter(
                    KanbanLane.board_id == board_id,
                    KanbanLane.name.ilike("%backlog%"),
                ).first()
            if not lane:
                lane = s.query(KanbanLane).filter_by(board_id=board_id).order_by(KanbanLane.position.asc()).first()
            empty_reason = "no_unticketed_messages"
            if intake_stats.get("total_unticketed", 0) > 0 and scope == "new_since_last_ticket":
                if intake_stats.get("last_ticketed_at"):
                    empty_reason = "no_new_messages_since_last_ticket"
                else:
                    empty_reason = "no_unticketed_in_recent_window"
            return {
                "board": board,
                "link": link,
                "messages": [],
                "lane": lane,
                "empty": True,
                "empty_reason": empty_reason,
                "intake_stats": intake_stats,
            }
        raise HTTPException(404, "No unticketed WhatsApp messages found for this board link")

    source_lane_name = (getattr(board, "agent_source_lane", None) or "").strip()
    if not source_lane_name:
        try:
            from distr.core.utils import load_settings_from_db

            source_lane_name = (load_settings_from_db().get("kanban_agent_source_lane") or "").strip()
        except Exception:
            source_lane_name = ""
    lane = None
    if source_lane_name:
        lane = s.query(KanbanLane).filter_by(board_id=board_id, name=source_lane_name).first()
    if not lane:
        lane = s.query(KanbanLane).filter(
            KanbanLane.board_id == board_id,
            KanbanLane.name.ilike("%backlog%"),
        ).first()
    if not lane:
        lane = s.query(KanbanLane).filter_by(board_id=board_id).order_by(KanbanLane.position.asc()).first()
    if not lane:
        raise HTTPException(400, "Board has no columns")

    return {
        "board": board,
        "link": link,
        "messages": messages,
        "lane": lane,
        "empty": False,
        "intake_stats": intake_stats,
    }


def _yaml_scalar(s: str) -> str:
    """YAML front-matter safe string (JSON double-quoted string is valid YAML 1.2)."""
    return json.dumps(s if s is not None else "", ensure_ascii=False)


_JIRA_WIKI_IMG = re.compile(r"!([^|\n\r!]+)(?:\|[^\n\r!]*)?!")


def _find_jira_attachment(
    attachments: Optional[List], filename: Optional[str] = None, att_id: Optional[str] = None
):
    """Match a Jira REST attachment dict by Cloud media id or filename."""
    if not attachments:
        return None
    if att_id is not None and str(att_id).strip():
        sid = str(att_id).strip()
        for a in attachments:
            if str(a.get("id", "")) == sid:
                return a
    if filename and str(filename).strip():
        fn = str(filename).strip().lower()
        for a in attachments:
            if (a.get("filename") or "").strip().lower() == fn:
                return a
    return None


def _jira_proxy_src_attr(full_content_url: str) -> str:
    """Frontend (or markdown note) uses this path; proxy adds auth when loading."""
    return "/api/tickets/external-boards/jira/proxy-image?url=" + quote(full_content_url, safe="")


def _jira_url_should_proxy(url: str) -> bool:
    u = (url or "").lower()
    return bool(
        url
        and (
            "/secure/attachment/" in u
            or "/rest/api/" in u
            or "api.atlassian.com/ex/jira/" in u
            or "/download/resources/" in u
        )
    )


def _jira_wiki_attachments_to_html(text: str, attachments: List) -> str:
    """Replace Jira wiki image syntax !file.png|opts! with <img> or links using the attachment list."""

    def repl(m):
        fn = m.group(1).strip()
        att = _find_jira_attachment(attachments, filename=fn)
        if att:
            content_url = att.get("content") or ""
            mime = (att.get("mimeType") or "").lower()
            if content_url and mime.startswith("image/"):
                src = _jira_proxy_src_attr(content_url)
                return (
                    f'<img src="{html.escape(src, quote=True)}" alt="{esc_html(fn)}" '
                    'style="max-width:100%;border-radius:4px;margin:4px 0" loading="lazy">'
                )
            if content_url:
                href = _jira_proxy_src_attr(content_url)
                disp = att.get("filename") or fn
                return (
                    f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer" '
                    f'style="color:#5b9bd5">📎 {esc_html(disp)}</a>'
                )
        return f'<span style="color:#888;font-size:12px">📎 {esc_html(fn)} (not found on issue)</span>'

    out: List[str] = []
    pos = 0
    for m in _JIRA_WIKI_IMG.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        out.append(repl(m))
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def _html_to_plain_ticket_description(html: str) -> str:
    """Strip tags for .tickets markdown body; preserve rough line breaks."""
    if not html:
        return ""
    t = re.sub(r"(?i)<br\s*/?>", "\n", html)
    t = re.sub(r"</p\s*>", "\n\n", t)
    t = re.sub(r"</(h[1-6]|div|li|tr)\s*>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def _safe_attachment_basename(name: str, used: set) -> str:
    base = re.sub(r"[^\w\.\-]+", "_", (name or "file").strip()) or "file"
    base = base.replace("..", "_").strip("._") or "file"
    cand = base
    n = 2
    while cand.lower() in used:
        stem, dot, ext = base.rpartition(".")
        if dot:
            cand = f"{stem}_{n}.{ext}"
        else:
            cand = f"{base}_{n}"
        n += 1
    used.add(cand.lower())
    return cand


def _load_json_connected_accounts() -> List:
    try:
        settings = load_settings_from_db()
        raw = settings.get("connected_accounts") or "[]"
        return json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    except Exception:
        return []


def _download_jira_issue_attachments_for_project(
    project_folder: str, ticket_stamp: str, issue_key: str
) -> Tuple[str, Optional[str]]:
    """Save Jira attachments under .tickets/_imported/{ticket_stamp}/. Returns (markdown_block, error)."""
    import requests as req_lib
    from requests.auth import HTTPBasicAuth

    accounts = _load_json_connected_accounts()
    issue_key = (issue_key or "").strip()
    if not issue_key:
        return "", "missing issue key"

    for acct in accounts:
        if acct.get("provider", "").lower() != "jira" or not acct.get("email") or not acct.get("api_token"):
            continue
        domain = acct.get("domain") or ""
        if not domain:
            server_url = (acct.get("server_url") or "").strip().rstrip("/")
            if server_url:
                domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
        if not domain:
            continue
        auth = HTTPBasicAuth(acct["email"], acct["api_token"])
        base_url = f"https://{domain}" if not domain.startswith("http") else domain
        ir = req_lib.get(
            f"{base_url}/rest/api/2/issue/{issue_key}",
            params={"fields": "attachment"},
            auth=auth,
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if ir.status_code != 200:
            continue
        try:
            ir_body = ir.json()
        except Exception:
            continue
        if not isinstance(ir_body, dict):
            continue
        fields = ir_body.get("fields")
        if not isinstance(fields, dict):
            fields = {}
        atts = fields.get("attachment") or []
        if not isinstance(atts, list) or not atts:
            return "\n## Attachments (imported)\n_No files on this issue._\n", None
        imp_dir = os.path.join(project_folder, ".tickets", "_imported", ticket_stamp)
        os.makedirs(imp_dir, exist_ok=True)
        lines = ["\n## Attachments (imported from Jira)\n"]
        used_names: set = set()
        for a in atts:
            if not isinstance(a, dict):
                continue
            url = a.get("content") or ""
            if not url:
                continue
            raw_name = a.get("filename") or "attachment"
            fname = _safe_attachment_basename(raw_name, used_names)
            dest = os.path.join(imp_dir, fname)
            try:
                gr = req_lib.get(url, auth=auth, timeout=120, stream=True)
                if gr.status_code != 200:
                    lines.append(f"- (download failed: {raw_name!r} — HTTP {gr.status_code})\n")
                    continue
                with open(dest, "wb") as wf:
                    for chunk in gr.iter_content(chunk_size=65536):
                        if chunk:
                            wf.write(chunk)
            except OSError as e:
                lines.append(f"- (write failed: {raw_name!r} — {e})\n")
                continue
            rel = f".tickets/_imported/{ticket_stamp}/{fname}"
            mime = (a.get("mimeType") or "").lower()
            if mime.startswith("image/"):
                lines.append(f"![{fname}]({rel})\n")
            else:
                lines.append(f"- [{raw_name}]({rel})\n")
        return "".join(lines), None

    return "", "could not download Jira attachments (check credentials or issue key)"


def _download_trello_card_attachments_for_project(
    project_folder: str, ticket_stamp: str, card_id: str
) -> Tuple[str, Optional[str]]:
    """Save Trello card attachments under .tickets/_imported/{ticket_stamp}/."""
    import requests as req_lib

    accounts = _load_json_connected_accounts()
    card_id = (card_id or "").strip()
    if not card_id:
        return "", "missing card id"

    for acct in accounts:
        if acct.get("provider", "").lower() != "trello" or not acct.get("api_key") or not acct.get("api_token"):
            continue
        key, token = acct["api_key"], acct["api_token"]
        lr = req_lib.get(
            f"https://api.trello.com/1/cards/{card_id}/attachments",
            params={"key": key, "token": token, "fields": "name,url,mimeType,isUpload"},
            timeout=20,
        )
        if lr.status_code != 200:
            continue
        try:
            rows = lr.json()
        except Exception:
            continue
        if not isinstance(rows, list) or not rows:
            return "\n## Attachments (imported)\n_No files on this card._\n", None
        imp_dir = os.path.join(project_folder, ".tickets", "_imported", ticket_stamp)
        os.makedirs(imp_dir, exist_ok=True)
        lines = ["\n## Attachments (imported from Trello)\n"]
        used_names: set = set()
        for a in rows:
            if not isinstance(a, dict):
                continue
            url = a.get("url") or ""
            if not url:
                continue
            raw_name = a.get("name") or "attachment"
            fname = _safe_attachment_basename(raw_name, used_names)
            dest = os.path.join(imp_dir, fname)
            try:
                sep = "&" if "?" in url else "?"
                dl_url = url if "key=" in url else f"{url}{sep}key={key}&token={token}"
                gr = req_lib.get(dl_url, timeout=120, stream=True)
                if gr.status_code != 200:
                    lines.append(f"- (download failed: {raw_name!r} — HTTP {gr.status_code})\n")
                    continue
                with open(dest, "wb") as wf:
                    for chunk in gr.iter_content(chunk_size=65536):
                        if chunk:
                            wf.write(chunk)
            except OSError as e:
                lines.append(f"- (write failed: {raw_name!r} — {e})\n")
                continue
            rel = f".tickets/_imported/{ticket_stamp}/{fname}"
            mime = (a.get("mimeType") or "").lower()
            if mime.startswith("image/"):
                lines.append(f"![{fname}]({rel})\n")
            else:
                lines.append(f"- [{raw_name}]({rel})\n")
        return "".join(lines), None

    return "", "could not download Trello attachments (check credentials or card id)"


def _parse_jira_description(desc, attachments: Optional[List] = None):
    """Convert a Jira description field to simple HTML (wiki images, ADF, attachment-backed media).

    Jira returns descriptions as either:
    - A plain string (wiki markup, HTML, or plain text)
    - An Atlassian Document Format (ADF) dict/list (Jira Cloud)

    ``attachments`` should be the issue's ``fields.attachment`` list when available.
    """
    atts = attachments if isinstance(attachments, list) else []
    if not desc:
        return ""
    # Plain string
    if isinstance(desc, str):
        if atts and _JIRA_WIKI_IMG.search(desc):
            return _jira_wiki_attachments_to_html(desc, atts)
        return desc
    # ADF format
    if isinstance(desc, (dict, list)):
        parts: List[str] = []

        def _walk(node, in_list=False):
            if isinstance(node, list):
                for item in node:
                    _walk(item, in_list)
                return
            if not isinstance(node, dict):
                return
            ntype = node.get("type", "")
            attrs = node.get("attrs", {})
            content = node.get("content", [])
            marks = node.get("marks", [])

            if ntype == "text":
                text = node.get("text", "")
                # Apply marks (bold, italic, code, links)
                is_bold = any(m.get("type") == "strong" for m in marks)
                is_italic = any(m.get("type") == "em" for m in marks)
                is_code = any(m.get("type") == "code" for m in marks)
                is_link = any(m.get("type") == "link" for m in marks)
                link_url = ""
                for m in marks:
                    if m.get("type") == "link":
                        link_url = m.get("attrs", {}).get("href", "")
                if is_link and link_url:
                    inner = esc_html(text)
                    if is_bold:
                        inner = "<b>" + inner + "</b>"
                    if is_italic:
                        inner = "<i>" + inner + "</i>"
                    parts.append(f'<a href="{esc_html(link_url)}" target="_blank" style="color:#5b9bd5">{inner}</a>')
                else:
                    inner = esc_html(text)
                    if is_bold:
                        inner = "<b>" + inner + "</b>"
                    if is_italic:
                        inner = "<i>" + inner + "</i>"
                    if is_code:
                        inner = f'<code style="background:#1a1f3a;padding:1px 4px;border-radius:3px">{inner}</code>'
                    parts.append(inner)

            elif ntype == "paragraph":
                parts.append("<p>")
                for child in content:
                    _walk(child, False)
                parts.append("</p>")

            elif ntype == "heading":
                level = attrs.get("level", 2)
                parts.append(f"<h{level}>")
                for child in content:
                    _walk(child, False)
                parts.append(f"</h{level}>")

            elif ntype == "bulletList":
                parts.append("<ul>")
                for child in content:
                    _walk(child, True)
                parts.append("</ul>")

            elif ntype == "orderedList":
                parts.append("<ol>")
                for child in content:
                    _walk(child, True)
                parts.append("</ol>")

            elif ntype == "listItem":
                parts.append("<li>")
                for child in content:
                    _walk(child, False)
                parts.append("</li>")

            elif ntype == "codeBlock":
                parts.append('<pre style="background:#1a1f3a;padding:8px;border-radius:4px;overflow-x:auto;font-size:12px">')
                for child in content:
                    _walk(child, False)
                parts.append("</pre>")

            elif ntype == "hardBreak":
                parts.append("<br>")

            elif ntype == "inlineCard":
                url = attrs.get("url", "")
                if url:
                    parts.append(f'<a href="{esc_html(url)}" target="_blank" style="color:#5b9bd5">{esc_html(url)}</a>')

            elif ntype == "image":
                img_url = attrs.get("url", "") or attrs.get("src", "")
                alt = attrs.get("alt", "")
                if img_url:
                    src = _jira_proxy_src_attr(img_url) if _jira_url_should_proxy(img_url) else img_url
                    parts.append(
                        f'<img src="{html.escape(src, quote=True)}" alt="{esc_html(alt)}" '
                        'style="max-width:100%;border-radius:4px;margin:4px 0" loading="lazy">'
                    )

            elif ntype == "media":
                mid = attrs.get("id")
                att = _find_jira_attachment(atts, att_id=str(mid) if mid is not None else None)
                alt = attrs.get("alt") or (att.get("filename") if att else None) or f"media-{mid}"
                if att:
                    content_url = att.get("content") or ""
                    mime = (att.get("mimeType") or "").lower()
                    if content_url and mime.startswith("image/"):
                        src = _jira_proxy_src_attr(content_url)
                        parts.append(
                            f'<img src="{html.escape(src, quote=True)}" alt="{esc_html(str(alt))}" '
                            'style="max-width:100%;border-radius:4px;margin:4px 0" loading="lazy">'
                        )
                    elif content_url:
                        href = _jira_proxy_src_attr(content_url)
                        disp = att.get("filename") or str(alt)
                        parts.append(
                            f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer" '
                            f'style="color:#5b9bd5">📎 {esc_html(disp)}</a>'
                        )
                    else:
                        parts.append(f'<span style="color:#888;font-size:12px">📎 {esc_html(str(alt))}</span>')
                else:
                    parts.append(f'<span style="color:#888;font-size:12px">📎 {esc_html(str(alt))}</span>')

            else:
                # Unknown node type — recurse into content
                for child in content:
                    _walk(child, in_list)

        _walk(desc)
        result = "".join(parts).strip()
        result = re.sub(r"<p>\s*</p>", "", result)
        return result

    return str(desc)


def esc_html(s):
    """Escape HTML special characters."""
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _jira_board_column_config_columns(cfg) -> list:
    """Columns from agile board configuration. ``columnConfig`` is often present but may be ``null``."""
    if not isinstance(cfg, dict):
        return []
    cc = cfg.get("columnConfig")
    if not isinstance(cc, dict):
        return []
    cols = cc.get("columns")
    return cols if isinstance(cols, list) else []


def _trello_with_time_block(description: str, time_estimate: Optional[str], time_spent: Optional[str]) -> str:
    """Attach/update a structured time block in Trello description text."""
    import re as _re

    base = (description or "").strip()
    base = _re.sub(r"\n?---\nEstimate:.*?\nDuration:.*?$", "", base, flags=_re.S).rstrip()
    est = (time_estimate or "").strip() or "-"
    spent = (time_spent or "").strip() or "-"
    block = f"---\nEstimate: {est}\nDuration: {spent}"
    return f"{base}\n\n{block}" if base else block


def _sync_local_ticket_to_external(source: Optional[str], external_id: Optional[str], title: str, description: str, time_estimate: Optional[str], time_spent: Optional[str]) -> None:
    """Push local ticket updates to external providers when the ticket is linked."""
    src = (source or "").lower().strip()
    ext_id = (external_id or "").strip()
    if src not in ("trello", "jira") or not ext_id:
        return

    try:
        from distr.core.settings import load_settings_from_db
        settings = load_settings_from_db()
        raw = settings.get("connected_accounts") or "[]"
        accounts = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    except Exception:
        accounts = []

    if src == "trello":
        for acct in accounts:
            if acct.get("provider", "").lower() == "trello" and acct.get("api_key") and acct.get("api_token"):
                import requests as req_lib
                card_desc = _trello_with_time_block(description or "", time_estimate, time_spent)
                r = req_lib.put(
                    f"https://api.trello.com/1/cards/{ext_id}",
                    params={
                        "key": acct["api_key"],
                        "token": acct["api_token"],
                        "name": title or "",
                        "desc": card_desc,
                    },
                    timeout=15,
                )
                if r.status_code >= 300:
                    raise HTTPException(502, f"Trello update failed: {r.text[:300]}")
                return
        raise HTTPException(400, "No valid Trello account connected for ticket sync")

    # Jira
    for acct in accounts:
        if acct.get("provider", "").lower() == "jira" and acct.get("email") and acct.get("api_token"):
            import requests as req_lib
            from requests.auth import HTTPBasicAuth
            domain = acct.get("domain") or ""
            if not domain:
                server_url = (acct.get("server_url") or "").strip().rstrip("/")
                if server_url:
                    domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
            if not domain:
                continue
            auth = HTTPBasicAuth(acct["email"], acct["api_token"])
            base_url = f"https://{domain}" if not domain.startswith("http") else domain
            fields = {
                "summary": title or "",
                "description": description or "",
            }
            timetracking = {}
            if (time_estimate or "").strip():
                timetracking["originalEstimate"] = (time_estimate or "").strip()
            if (time_spent or "").strip():
                timetracking["timeSpent"] = (time_spent or "").strip()
            if timetracking:
                fields["timetracking"] = timetracking
            r = req_lib.put(
                f"{base_url}/rest/api/2/issue/{ext_id}",
                auth=auth,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"fields": fields},
                timeout=15,
            )
            if r.status_code >= 300:
                raise HTTPException(502, f"Jira update failed: {r.text[:300]}")
            return
    raise HTTPException(400, "No valid Jira account connected for ticket sync")


def _is_valid_time_tracking_value(value: Optional[str]) -> bool:
    """Validate Jira-style duration values: 30m, 2h, 1d 3h, 1w 2d 4h 30m."""
    import re as _re

    if value is None:
        return True
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v:
        return True
    return bool(_re.match(r"^\d+\s*[wdhm](\s+\d+\s*[wdhm])*$", v, _re.I))


def _ticket_source_payload(t: KanbanTicket) -> dict:
    return {
        "source_provider": t.source_provider or "",
        "source_external_id": t.source_external_id or "",
        "source_thread_id": t.source_thread_id or "",
        "source_contact": t.source_contact or "",
        "source_url": t.source_url or "",
        "source_label": t.source_label or "",
    }


def _project_context_payload(project: Optional[Project], prefix: str) -> dict:
    return {
        f"{prefix}_project_name": project.name if project else None,
        f"{prefix}_project_folder": project.folder_location if project else None,
    }


def _apply_ticket_source_fields(t: KanbanTicket, payload: BaseModel) -> None:
    fields_set = getattr(payload, "model_fields_set", set())
    if "source_provider" in fields_set:
        t.source_provider = normalize_source_provider(getattr(payload, "source_provider", None)) or None
    for attr in ("source_external_id", "source_thread_id", "source_contact", "source_url", "source_label"):
        if attr in fields_set:
            value = getattr(payload, attr, None)
            t.__setattr__(attr, (value or "").strip() or None)


def _emit_ticket_channel_intake(
    ticket: KanbanTicket,
    *,
    board: KanbanBoard | None = None,
    channel: str = "",
    extra_payload: dict | None = None,
) -> None:
    """Emit a Hermes channel intake event when a ticket enters from a channel."""
    provider = normalize_source_provider(channel or ticket.source_provider or "")
    if not provider:
        return
    if provider not in {"whatsapp", "telegram", "gmail"}:
        return
    try:
        from distr.core.hermes import emit_channel_intake_event

        board_id = board.id if board else None
        if board_id is None and ticket.lane_id:
            with get_session() as session:
                lane = session.query(KanbanLane).filter(KanbanLane.id == ticket.lane_id).first()
                board_id = lane.board_id if lane else None
        emit_channel_intake_event(
            channel=provider,
            ticket_id=int(ticket.id),
            board_id=board_id,
            workflow_id=ticket.linked_workflow_id,
            project_id=ticket.linked_project_id,
            summary=f"Ticket '{ticket.title}' created from {provider} intake.",
            payload={
                "title": ticket.title or "",
                "priority": ticket.priority or "",
                "complexity": ticket.complexity or "",
                "source_contact": ticket.source_contact or "",
                "source_external_id": ticket.source_external_id or "",
                **(extra_payload or {}),
            },
        )
    except Exception:
        logger.debug("Could not emit channel intake Hermes event", exc_info=True)


class BoardCreate(BaseModel):
    name: str
    description: Optional[str] = ""

class BoardUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    default_workflow_id: Optional[int] = None
    default_project_id: Optional[int] = None
    default_snippet_id: Optional[int] = None
    default_action_id: Optional[int] = None
    color: Optional[str] = None
    position: Optional[int] = None
    hermes_policy: Optional[dict] = None


class TicketCreate(BaseModel):
    lane_id: int
    title: str
    description: Optional[str] = ""
    priority: Optional[str] = "medium"
    complexity: Optional[str] = None
    source_chat_id: Optional[int] = None
    source_provider: Optional[str] = None
    source_external_id: Optional[str] = None
    source_thread_id: Optional[str] = None
    source_contact: Optional[str] = None
    source_url: Optional[str] = None
    source_label: Optional[str] = None

class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    complexity: Optional[str] = None
    lane_id: Optional[int] = None
    position: Optional[int] = None
    linked_workflow_id: Optional[int] = None
    workflow_queue_position: Optional[int] = None
    linked_project_id: Optional[int] = None
    linked_snippet_id: Optional[int] = None
    linked_action_id: Optional[int] = None
    time_estimate: Optional[str] = None
    time_spent: Optional[str] = None
    source_chat_id: Optional[int] = None
    source_provider: Optional[str] = None
    source_external_id: Optional[str] = None
    source_thread_id: Optional[str] = None
    source_contact: Optional[str] = None
    source_url: Optional[str] = None
    source_label: Optional[str] = None

class TicketMove(BaseModel):
    lane_id: int
    position: int


class WorkflowTicketReorder(BaseModel):
    ticket_ids: List[int]

class LinkCreate(BaseModel):
    title: str
    url: str

class TodoCreate(BaseModel):
    text: str

class TodoUpdate(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None

class CopyToBoard(BaseModel):
    board_id: int
    lane_id: Optional[int] = None
    title: str
    description: Optional[str] = ""
    priority: Optional[str] = "medium"
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    time_estimate: Optional[str] = None
    time_spent: Optional[str] = None
    complexity: Optional[str] = None


class LaneTicketCopyItem(BaseModel):
    title: str
    description: Optional[str] = ""
    priority: Optional[str] = "medium"
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    time_estimate: Optional[str] = None
    time_spent: Optional[str] = None
    complexity: Optional[str] = None


class BulkCopyLaneToBoard(BaseModel):
    board_id: int
    lane_id: int
    linked_project_id: Optional[int] = None
    linked_workflow_id: Optional[int] = None
    tickets: List[LaneTicketCopyItem] = Field(default_factory=list)


class ExternalBoardRegister(BaseModel):
    name: Optional[str] = None
    default_project_id: Optional[int] = None
    default_workflow_id: Optional[int] = None
    color: Optional[str] = None


class CopyExternalTicket(BaseModel):
    board_id: int
    lane_id: Optional[int] = None
    title: str
    description: Optional[str] = ""
    priority: Optional[str] = "medium"
    external_source: Optional[str] = None
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    time_estimate: Optional[str] = None
    time_spent: Optional[str] = None
    complexity: Optional[str] = None
    auto_send_to_project: Optional[bool] = False
    auto_send_to_cli: Optional[bool] = False
    auto_send_to_workflow: Optional[bool] = False
    source_chat_id: Optional[int] = None
    # When copying from Trello/Jira, the destination board may be a plain local board while
    # the project link lives on the external board config — set this to attach + .tickets export.
    linked_project_id: Optional[int] = None
    linked_workflow_id: Optional[int] = None


class SendToWorkflowRequest(BaseModel):
    workflow_id: Optional[int] = None


class SendToCliRequest(BaseModel):
    workflow_id: Optional[int] = None
    backend_id: Optional[str] = None
    model: Optional[str] = None
    instruction: Optional[str] = None
    codex_reasoning_effort: Optional[str] = None
    codex_service_tier: Optional[str] = None


class ExternalTicketCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    lane_id: Optional[str] = None
    priority: Optional[str] = "medium"


class ExternalBoardMoveTicket(BaseModel):
    """Move a Trello card or Jira issue to another list/column on the same board."""

    ticket_id: str = Field(..., description="Trello card id or Jira issue key")
    target_lane_id: str = Field(..., description="Trello list id or Jira board column name")
    position: int = Field(0, ge=0, description="0-based index within the target lane")


# ── External board detail cache (stale-while-revalidate; refresh in daemon threads) ──
_BOARD_DETAIL_LOCK = threading.Lock()
_BOARD_DETAIL_CACHE: dict = {}
_BOARD_DETAIL_REFRESHING: set = set()
_BOARD_DETAIL_STALE_AFTER_SEC = 45.0

_PROXY_IMAGE_LOCK = threading.Lock()
_PROXY_IMAGE_CACHE: dict = {}
_PROXY_IMAGE_TTL_SEC = 300.0
_PROXY_IMAGE_MAX_BYTES = 12 * 1024 * 1024
_PROXY_IMAGE_MAX_ENTRIES = 128


def _external_board_detail_cache_key(provider: str, board_id: str) -> str:
    return f"{(provider or '').lower()}:{board_id}"


def _invalidate_external_board_detail_cache(provider: str, board_id: str) -> None:
    key = _external_board_detail_cache_key(provider, board_id)
    with _BOARD_DETAIL_LOCK:
        _BOARD_DETAIL_CACHE.pop(key, None)
        _BOARD_DETAIL_REFRESHING.discard(key)


def _resolve_local_destination_lane(session, board_id: int, lane_id: Optional[int] = None):
    """Return (board, lane) for a local database board and optional destination lane."""
    board = session.query(KanbanBoard).filter_by(id=board_id, source="database").first()
    if not board:
        return None, None
    if lane_id is not None:
        lane = (
            session.query(KanbanLane)
            .filter(KanbanLane.id == lane_id, KanbanLane.board_id == board.id)
            .first()
        )
        if not lane:
            return board, None
        return board, lane
    lane = (
        session.query(KanbanLane)
        .filter_by(board_id=board.id)
        .order_by(KanbanLane.position)
        .first()
    )
    return board, lane


def _find_existing_external_copy(session, external_source: Optional[str], external_id: Optional[str]):
    if not external_source or not external_id:
        return None
    existing = (
        session.query(KanbanTicket)
        .filter(
            KanbanTicket.external_source == external_source,
            KanbanTicket.external_id == external_id,
        )
        .order_by(KanbanTicket.id.desc())
        .first()
    )
    if existing:
        return existing
    return (
        session.query(KanbanTicket)
        .filter(
            KanbanTicket.source_provider == normalize_source_provider(external_source),
            KanbanTicket.source_external_id == external_id,
        )
        .order_by(KanbanTicket.id.desc())
        .first()
    )


def _copy_external_ticket_into_lane(
    session,
    board: KanbanBoard,
    dest_lane: KanbanLane,
    *,
    title: str,
    description: str = "",
    priority: str = "medium",
    complexity: Optional[str] = None,
    time_estimate: str = "",
    time_spent: str = "",
    external_source: Optional[str] = None,
    external_id: Optional[str] = None,
    external_url: Optional[str] = None,
    linked_project_id: Optional[int] = None,
    linked_workflow_id: Optional[int] = None,
    source_chat_id: Optional[int] = None,
    position: Optional[int] = None,
    skip_workflow_linked: bool = False,
) -> dict:
    """Insert or reuse a copied external ticket on a local lane."""
    effective_project_id = linked_project_id if linked_project_id is not None else board.default_project_id
    effective_workflow_id = linked_workflow_id if linked_workflow_id is not None else board.default_workflow_id
    existing_external_ticket = _find_existing_external_copy(session, external_source, external_id)
    if existing_external_ticket:
        if existing_external_ticket.linked_workflow_id:
            if skip_workflow_linked:
                return {
                    "id": existing_external_ticket.id,
                    "reused": True,
                    "skipped": True,
                    "skip_reason": "already_linked_to_workflow",
                }
            raise HTTPException(409, "Ticket is already linked to a workflow")
        existing_external_ticket.linked_workflow_id = effective_workflow_id
        existing_external_ticket.linked_project_id = (
            effective_project_id or existing_external_ticket.linked_project_id
        )
        if not existing_external_ticket.workflow_queue_position and effective_workflow_id:
            max_queue_pos = (
                session.query(KanbanTicket.workflow_queue_position)
                .filter(KanbanTicket.linked_workflow_id == effective_workflow_id)
                .order_by(KanbanTicket.workflow_queue_position.desc())
                .first()
            )
            existing_external_ticket.workflow_queue_position = (
                (max_queue_pos[0] if max_queue_pos and max_queue_pos[0] is not None else -1) + 1
            )
        return {"id": existing_external_ticket.id, "reused": True, "skipped": False}
    if position is None:
        max_pos = max([t.position for t in dest_lane.tickets], default=-1)
        position = max_pos + 1
    ticket = KanbanTicket(
        lane_id=dest_lane.id,
        title=title,
        description=description or "",
        priority=priority or "medium",
        complexity=normalize_ticket_complexity(complexity) if complexity else infer_ticket_complexity(title, description or ""),
        time_estimate=time_estimate or "",
        time_spent=time_spent or "",
        position=position,
        external_source=external_source,
        external_id=external_id,
        external_url=external_url,
        source_provider=normalize_source_provider(external_source) or None,
        source_external_id=external_id,
        source_url=external_url,
        source_label=external_source,
        linked_workflow_id=effective_workflow_id,
        linked_project_id=effective_project_id,
        source_chat_id=source_chat_id,
    )
    session.add(ticket)
    session.flush()
    return {"id": ticket.id, "reused": False, "skipped": False}


def _external_board_detail_fetch_worker(provider: str, board_id: str, key: str) -> None:
    try:
        body = _build_external_board_detail_payload(provider, board_id)
    except Exception as e:
        logger.exception("Background external board fetch failed (%s)", key)
        body = {
            "name": "",
            "url": "",
            "lanes": [],
            "can_create_ticket": True,
            "cache_error": str(e) or type(e).__name__,
        }
    with _BOARD_DETAIL_LOCK:
        _BOARD_DETAIL_CACHE[key] = {"ready": True, "t": time.time(), "body": body}
        _BOARD_DETAIL_REFRESHING.discard(key)


def _schedule_external_board_detail_refresh(provider: str, board_id: str, key: str) -> None:
    with _BOARD_DETAIL_LOCK:
        if key in _BOARD_DETAIL_REFRESHING:
            return
        _BOARD_DETAIL_REFRESHING.add(key)
    threading.Thread(
        target=_external_board_detail_fetch_worker,
        args=(provider, board_id, key),
        name=f"kanban-board-{key}",
        daemon=True,
    ).start()


def _jira_proxy_url_allowed(url: str, accounts: list) -> bool:
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
    except Exception:
        return False
    if not host or p.scheme not in ("https", "http"):
        return False
    if host.endswith(".atlassian.net") or host.endswith(".atlassian.com"):
        return True
    for acct in accounts:
        if (acct.get("provider") or "").lower() != "jira":
            continue
        d = (acct.get("domain") or "").strip().lower()
        if not d:
            su = (acct.get("server_url") or "").strip().rstrip("/")
            if su:
                d = su.replace("https://", "").replace("http://", "").split("/")[0].lower()
        if not d:
            continue
        if host == d or host.endswith("." + d):
            return True
    return False


def _proxy_external_image_sync(provider: str, url: str) -> Tuple[bytes, str]:
    """Fetch binary image for Jira/Trello; returns (body, content_type). Raises HTTPException."""
    if not url:
        raise HTTPException(400, "Missing url parameter")
    if provider not in ("trello", "jira"):
        raise HTTPException(400, "Provider must be 'trello' or 'jira'")
    try:
        raw_url = unquote(url)
    except Exception:
        raw_url = url
    try:
        settings = load_settings_from_db()
        raw = settings.get("connected_accounts") or "[]"
        accounts = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
    except Exception:
        accounts = []

    import hashlib
    ckey = hashlib.sha256(f"{provider}:{raw_url}".encode("utf-8", errors="replace")).hexdigest()
    now = time.time()
    with _PROXY_IMAGE_LOCK:
        hit = _PROXY_IMAGE_CACHE.get(ckey)
        if hit and (now - hit[0] < _PROXY_IMAGE_TTL_SEC):
            return hit[1], hit[2]

    import requests as req_lib

    if provider == "trello":
        for acct in accounts:
            if acct.get("provider", "").lower() == "trello" and acct.get("api_key") and acct.get("api_token"):
                sep = "&" if "?" in raw_url else "?"
                authed_url = (
                    f"{raw_url}{sep}key={acct['api_key']}&token={acct['api_token']}"
                    if "key=" not in raw_url
                    else raw_url
                )
                try:
                    r = req_lib.get(authed_url, timeout=15, stream=True)
                except req_lib.exceptions.RequestException as e:
                    logger.warning("Trello proxy-image request failed: %s", e)
                    continue
                if r.status_code == 200:
                    data = r.content
                    if len(data) > _PROXY_IMAGE_MAX_BYTES:
                        raise HTTPException(413, "Image too large")
                    content_type = r.headers.get("content-type", "image/png")
                    with _PROXY_IMAGE_LOCK:
                        if len(_PROXY_IMAGE_CACHE) >= _PROXY_IMAGE_MAX_ENTRIES:
                            oldest = min(_PROXY_IMAGE_CACHE, key=lambda k: _PROXY_IMAGE_CACHE[k][0])
                            _PROXY_IMAGE_CACHE.pop(oldest, None)
                        _PROXY_IMAGE_CACHE[ckey] = (now, data, content_type)
                    return data, content_type
                logger.warning("Trello proxy-image HTTP %s", r.status_code)
                continue
        raise HTTPException(404, "No valid Trello account or image not found")

    if not _jira_proxy_url_allowed(raw_url, accounts):
        raise HTTPException(400, "URL not allowed for Jira image proxy")

    from requests.auth import HTTPBasicAuth

    last_status: Optional[int] = None
    last_detail = ""
    for acct in accounts:
        if acct.get("provider", "").lower() != "jira" or not acct.get("email") or not acct.get("api_token"):
            continue
        auth = HTTPBasicAuth(acct["email"], acct["api_token"])
        try:
            r = req_lib.get(raw_url, auth=auth, timeout=15, stream=True)
        except req_lib.exceptions.RequestException as e:
            logger.warning("Jira proxy-image request failed: %s", e)
            continue
        last_status = r.status_code
        last_detail = (r.text or "")[:200]
        if r.status_code == 200:
            try:
                data = r.content
            except Exception as e:
                logger.warning("Jira proxy-image read body failed: %s", e)
                continue
            if len(data) > _PROXY_IMAGE_MAX_BYTES:
                raise HTTPException(413, "Image too large")
            content_type = r.headers.get("content-type", "image/png")
            if "image/" not in content_type and "octet-stream" not in content_type:
                content_type = "image/png"
            with _PROXY_IMAGE_LOCK:
                if len(_PROXY_IMAGE_CACHE) >= _PROXY_IMAGE_MAX_ENTRIES:
                    oldest = min(_PROXY_IMAGE_CACHE, key=lambda k: _PROXY_IMAGE_CACHE[k][0])
                    _PROXY_IMAGE_CACHE.pop(oldest, None)
                _PROXY_IMAGE_CACHE[ckey] = (now, data, content_type)
            return data, content_type
        continue

    if last_status and 400 <= last_status < 600:
        raise HTTPException(last_status, f"Jira image fetch error: {last_detail}")
    raise HTTPException(404, "No valid Jira account for image proxy")


def _build_external_board_detail_payload(provider: str, board_id: str) -> dict:
    """Build lanes/tickets dict for an external Trello or Jira board (blocking HTTP; used by cache refresh)."""
    lanes = []
    board_name = ""
    board_url = ""
    active_sprint = None
    local_config = {}
    with get_session() as s:
        local_board = s.query(KanbanBoard).filter(
            KanbanBoard.source == provider,
            KanbanBoard.external_board_id == board_id
        ).first()
        if local_board:
            local_config = {
                "local_id": local_board.id,
                "default_project_id": local_board.default_project_id,
                "default_workflow_id": local_board.default_workflow_id,
                "color": local_board.color,
                "can_create_ticket": True,
            }
    try:
        from distr.core.settings import load_settings_from_db
        import json as _json
        settings = load_settings_from_db()
        raw = settings.get("connected_accounts") or "[]"
        if isinstance(raw, str):
            try:
                accounts = _json.loads(raw)
            except Exception:
                accounts = []
        else:
            accounts = raw if isinstance(raw, list) else []
        if provider == "trello":
            for acct in accounts:
                if acct.get("provider", "").lower() == "trello" and acct.get("api_key") and acct.get("api_token"):
                    import requests
                    br = requests.get(f"https://api.trello.com/1/boards/{board_id}",
                                      params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,url"}, timeout=10)
                    if br.status_code == 200:
                        try:
                            bd = br.json()
                        except Exception:
                            bd = {}
                        if isinstance(bd, dict):
                            board_name = bd.get("name", "")
                            board_url = bd.get("url", "")
                    lr = requests.get(f"https://api.trello.com/1/boards/{board_id}/lists",
                                      params={"key": acct["api_key"], "token": acct["api_token"], "cards": "open", "card_fields": "name,desc,url,labels,checklists,due"}, timeout=10)
                    if lr.status_code == 200:
                        try:
                            lists_raw = lr.json()
                        except Exception:
                            lists_raw = []
                        if not isinstance(lists_raw, list):
                            lists_raw = []
                        for lst in lists_raw:
                            if not isinstance(lst, dict):
                                continue
                            cards = []
                            for c in lst.get("cards", []):
                                if not isinstance(c, dict):
                                    continue
                                desc_text = c.get("desc", "") or ""
                                est_match = None
                                spent_match = None
                                try:
                                    import re as _re
                                    est_match = _re.search(r"(?:^|\n)Estimate:\s*(.+)", desc_text)
                                    spent_match = _re.search(r"(?:^|\n)Duration:\s*(.+)", desc_text)
                                except Exception:
                                    est_match = None
                                    spent_match = None
                                card_data = {
                                    "id": c["id"], "title": c["name"],
                                    "description": desc_text,
                                    "url": c.get("url", ""),
                                }
                                if est_match:
                                    card_data["time_estimate"] = (est_match.group(1) or "").strip()
                                if spent_match:
                                    card_data["time_spent"] = (spent_match.group(1) or "").strip()
                                # Fetch card details (labels, checklists, members)
                                try:
                                    cd = requests.get(f"https://api.trello.com/1/cards/{c['id']}",
                                                      params={"key": acct["api_key"], "token": acct["api_token"],
                                                               "fields": "name,desc,url,labels,checklists,due,members,shortUrl"},
                                                      timeout=5)
                                    if cd.status_code == 200:
                                        try:
                                            cd_data = cd.json()
                                        except Exception:
                                            cd_data = {}
                                        if not isinstance(cd_data, dict):
                                            cd_data = {}
                                        card_data["labels"] = [lb.get("name", lb.get("color", "")) for lb in cd_data.get("labels", [])]
                                        card_data["todos"] = [{"text": cl_item.get("name", ""), "done": cl_item.get("state", "") == "complete"}
                                                                 for cl in cd_data.get("checklists", [])
                                                                 for cl_item in cl.get("checkItems", [])]
                                        card_data["due"] = cd_data.get("due")
                                        card_data["members"] = [m.get("fullName", m.get("username", "")) for m in cd_data.get("members", [])]
                                        # Fetch Trello card attachments (images)
                                        try:
                                            att = requests.get(f"https://api.trello.com/1/cards/{c['id']}/attachments",
                                                               params={"key": acct["api_key"], "token": acct["api_token"],
                                                                        "fields": "name,url,previews,mimeType,isUpload"},
                                                               timeout=5)
                                            if att.status_code == 200:
                                                try:
                                                    att_list = att.json()
                                                except Exception:
                                                    att_list = []
                                                if not isinstance(att_list, list):
                                                    att_list = []
                                                media = []
                                                for a in att_list:
                                                    if not isinstance(a, dict):
                                                        continue
                                                    if a.get("mimeType", "").startswith("image/") or a.get("isUpload", False):
                                                        # Use preview if available (smaller), otherwise full URL
                                                        previews = a.get("previews", [])
                                                        img_url = None
                                                        if previews and len(previews) > 0:
                                                            # Find the largest preview
                                                            for prev in reversed(previews):
                                                                if prev.get("width", 0) <= 1200:
                                                                    img_url = prev.get("url")
                                                                    break
                                                            if not img_url:
                                                                img_url = previews[-1].get("url")
                                                        if not img_url:
                                                            img_url = a.get("url", "")
                                                        media.append({"url": img_url, "name": a.get("name", ""), "type": a.get("mimeType", "")})
                                                if media:
                                                    card_data["media"] = media
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                cards.append(card_data)
                            lanes.append({"id": lst["id"], "name": lst["name"], "tickets": cards})
                    break
        elif provider == "jira":
            for acct in accounts:
                if acct.get("provider", "").lower() == "jira" and acct.get("email") and acct.get("api_token"):
                    import requests
                    from requests.auth import HTTPBasicAuth
                    domain = acct.get("domain") or ""
                    if not domain:
                        server_url = (acct.get("server_url") or "").strip().rstrip("/")
                        if server_url:
                            domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                    if not domain:
                        continue
                    auth = HTTPBasicAuth(acct["email"], acct["api_token"])
                    base_url = f"https://{domain}" if not domain.startswith("http") else domain
                    issue_url = f"{base_url}/rest/agile/1.0/board/{board_id}/issue"
                    active_sprint = None
                    cr = requests.get(f"{base_url}/rest/agile/1.0/board/{board_id}/configuration",
                                      auth=auth, headers={"Accept": "application/json"}, timeout=10)
                    if cr.status_code == 200:
                        try:
                            cfg = cr.json()
                        except Exception:
                            cfg = {}
                        if not isinstance(cfg, dict):
                            cfg = {}
                        board_name = cfg.get("name", "")
                        loc = cfg.get("location")
                        project_key = (loc.get("projectKey") or loc.get("key") or "") if isinstance(loc, dict) else ""
                        board_url = f"https://{domain}/jira/software/projects/{project_key}/boards/{board_id}"
                        for col in _jira_board_column_config_columns(cfg):
                            if not isinstance(col, dict):
                                continue
                            cn = (col.get("name") or "").strip()
                            if not cn:
                                continue
                            lanes.append({"id": cn, "name": cn, "tickets": []})
                        # Determine per-board create permission for Jira (CREATE_ISSUES on board project).
                        can_create = True
                        if project_key:
                            perm_params = {"projectKey": project_key, "permissions": "CREATE_ISSUES"}
                            pr = requests.get(
                                f"{base_url}/rest/api/3/mypermissions",
                                auth=auth,
                                headers={"Accept": "application/json"},
                                params=perm_params,
                                timeout=10,
                            )
                            if pr.status_code != 200:
                                pr = requests.get(
                                    f"{base_url}/rest/api/2/mypermissions",
                                    auth=auth,
                                    headers={"Accept": "application/json"},
                                    params=perm_params,
                                    timeout=10,
                                )
                            if pr.status_code == 200:
                                try:
                                    prj = pr.json()
                                except Exception:
                                    prj = {}
                                perms = prj.get("permissions", {}) if isinstance(prj, dict) else {}
                                create_issue = perms.get("CREATE_ISSUES", {}) if isinstance(perms, dict) else {}
                                can_create = bool(create_issue.get("havePermission", True))
                        local_config["can_create_ticket"] = can_create
                        if (cfg.get("type") or "").lower() == "scrum":
                            sr = requests.get(
                                f"{base_url}/rest/agile/1.0/board/{board_id}/sprint",
                                auth=auth,
                                headers={"Accept": "application/json"},
                                params={"state": "active", "maxResults": 1},
                                timeout=10,
                            )
                            if sr.status_code == 200:
                                try:
                                    sprint_body = sr.json()
                                except Exception:
                                    sprint_body = {}
                                sprints = sprint_body.get("values", []) if isinstance(sprint_body, dict) else []
                                if sprints and isinstance(sprints[0], dict) and sprints[0].get("id") is not None:
                                    active_sprint = sprints[0]
                                    issue_url = f"{base_url}/rest/agile/1.0/sprint/{active_sprint['id']}/issue"
                    ir = requests.get(
                        issue_url,
                        auth=auth,
                        headers={"Accept": "application/json"},
                        params={
                            "maxResults": 100,
                            "fields": "summary,status,description,assignee,reporter,timetracking,labels,subtasks,priority,attachment",
                        },
                        timeout=10,
                    )
                    if ir.status_code == 200:
                        try:
                            ir_body = ir.json()
                        except Exception:
                            ir_body = {}
                        issues = ir_body.get("issues", []) if isinstance(ir_body, dict) else []
                        if not isinstance(issues, list):
                            issues = []
                        for issue in issues:
                            if not isinstance(issue, dict):
                                continue
                            fields = issue.get("fields", {})
                            if not isinstance(fields, dict):
                                fields = {}
                            status_name = fields.get("status", {}).get("name", "")
                            raw_desc = fields.get("description", "") or ""
                            attachments = fields.get("attachment") or []
                            if not isinstance(attachments, list):
                                attachments = []
                            description_text = _parse_jira_description(raw_desc, attachments)
                            card = {
                                "id": issue.get("key", ""), "title": fields.get("summary", ""),
                                "description": description_text,
                                "url": f"https://{domain}/browse/{issue.get('key', '')}",
                            }
                            # Enrich with Jira-specific fields
                            assignee = fields.get("assignee")
                            if assignee:
                                card["members"] = [assignee.get("displayName", assignee.get("name", ""))]
                            reporter = fields.get("reporter")
                            if reporter:
                                card["reporter"] = reporter.get("displayName", reporter.get("name", ""))
                            # Time tracking
                            timetracking = fields.get("timetracking")
                            if timetracking:
                                card["time_estimate"] = timetracking.get("originalEstimate", "")
                                card["time_spent"] = timetracking.get("timeSpent", "")
                            # Priority
                            priority = fields.get("priority")
                            if priority:
                                card["priority"] = priority.get("name", "medium").lower()
                            # Labels
                            labels = fields.get("labels", [])
                            if labels:
                                card["labels"] = labels
                            # Subtasks
                            subtasks = fields.get("subtasks", [])
                            if subtasks:
                                card["todos"] = [{"text": st.get("fields", {}).get("summary", ""), "done": st.get("fields", {}).get("status", {}).get("name", "").lower() in ("done", "closed")} for st in subtasks]
                            # Images for card strip / modal gallery (same as issue attachment list)
                            media = []
                            for a in attachments:
                                ct = a.get("mimeType", "") or ""
                                if ct.startswith("image/") and a.get("content"):
                                    media.append({
                                        "url": a.get("content", ""),
                                        "name": a.get("filename", ""),
                                        "type": ct,
                                        "thumbnail": a.get("thumbnail", "") or "",
                                    })
                            if media:
                                card["media"] = media
                            for lane in lanes:
                                if lane["name"].lower() == status_name.lower():
                                    lane["tickets"].append(card)
                                    break
                    break
    except Exception as e:
        logger.warning("External board detail fetch error: %s", e)
    response_data = {"name": board_name, "url": board_url, "lanes": lanes, "can_create_ticket": True}
    if active_sprint:
        response_data["active_sprint_id"] = active_sprint.get("id")
        response_data["active_sprint_name"] = active_sprint.get("name") or ""
    response_data.update(local_config)
    return response_data


def _external_board_activity_iso(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat() + "Z"


def _sort_external_board_list(boards: list[dict]) -> list[dict]:
    """Configured external boards first (most recently touched), then untouched boards by name."""
    configured = [b for b in boards if b.get("local_id")]
    unconfigured = [b for b in boards if not b.get("local_id")]
    configured.sort(
        key=lambda b: (
            b.get("modified_date") or "",
            (b.get("name") or "").lower(),
        ),
        reverse=True,
    )
    unconfigured.sort(key=lambda b: (b.get("name") or "").lower())
    return configured + unconfigured


def create_routes():
    router = APIRouter()

    def _relay_auth_headers(payload: str = ""):
        token = relay_internal_token()
        if token:
            return {"X-Relay-Internal-Token": token}
        return {}

    def _device_identity_path() -> Path:
        return Path(DB_DIR) / "device_identity.json"

    def _load_or_create_device_identity():
        p = _device_identity_path()
        if p.exists():
            try:
                obj = json.loads(p.read_text())
                if obj.get("device_id") and obj.get("private_key"):
                    return obj
            except Exception:
                pass
        priv = Ed25519PrivateKey.generate()
        priv_raw = priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        device = {
            "device_id": f"dev-{int(time.time())}-{secrets.token_hex(8)}",
            "private_key": base64.b64encode(priv_raw).decode(),
        }
        p.write_text(json.dumps(device))
        return device

    @router.post("/tickets/boards/{board_id}/use")
    async def set_board_in_use(board_id: int):
        """Set this board as the active/in-use board. Only one board can be in_use at a time.
        If the board has a linked project, returns a prompt to activate it."""
        with get_session() as s:
            # Deactivate all boards
            s.query(KanbanBoard).filter(KanbanBoard.in_use == True).update({"in_use": False})
            board = orm_get_by_id(s, KanbanBoard,board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            board.in_use = True
            s.flush()

            # Check if board has a linked project
            linked_project = None
            if board.default_project_id:
                from distr.core.db.projects import Project
                proj = orm_get_by_id(s, Project,board.default_project_id)
                if proj and not proj.in_use:
                    linked_project = {"id": proj.id, "name": proj.name}

            return JSONResponse({
                "success": True,
                "linked_project": linked_project,
            })

    # ── Boards ──

    @router.get("/tickets/boards")
    async def list_boards(include_archived: bool = False):
        with get_session() as s:
            query = s.query(KanbanBoard)
            if not include_archived:
                query = query.filter((KanbanBoard.archived == False) | (KanbanBoard.archived == None))
            boards = query.order_by(KanbanBoard.position, KanbanBoard.name).all()
            project_ids = {int(b.default_project_id) for b in boards if b.default_project_id}
            projects = {
                p.id: p
                for p in s.query(Project).filter(Project.id.in_(project_ids)).all()
            } if project_ids else {}
            result = []
            for b in boards:
                default_project = projects.get(b.default_project_id)
                result.append({
                    "id": b.id, "name": b.name, "description": b.description or "",
                    "source": b.source, "external_board_id": b.external_board_id,
                    "external_url": b.external_url, "color": b.color or "",
                    "position": b.position or 0,
                    "archived": getattr(b, 'archived', False) or False,
                    "in_use": getattr(b, 'in_use', False) or False,
                    "default_project_id": b.default_project_id,
                    **_project_context_payload(default_project, "default"),
                    "default_workflow_id": b.default_workflow_id,
                })
            return JSONResponse(result)

    @router.post("/tickets/boards")
    async def create_board(payload: BoardCreate):
        with get_session() as s:
            board = KanbanBoard(name=payload.name, description=payload.description or "", source="database")
            s.add(board)
            s.flush()
            for i, lane_name in enumerate(DEFAULT_LANES):
                s.add(KanbanLane(board_id=board.id, name=lane_name, position=i))
            s.flush()
            return JSONResponse({"success": True, "id": board.id})

    @router.put("/tickets/boards/{board_id}")
    async def update_board(board_id: int, payload: BoardUpdate):
        with get_session() as s:
            board = orm_get_by_id(s, KanbanBoard,board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            if payload.name is not None:
                board.name = payload.name
            if payload.description is not None:
                board.description = payload.description
            if payload.default_workflow_id is not None:
                board.default_workflow_id = payload.default_workflow_id if payload.default_workflow_id else None
            if payload.default_project_id is not None:
                board.default_project_id = payload.default_project_id if payload.default_project_id else None
            if payload.default_snippet_id is not None:
                board.default_snippet_id = payload.default_snippet_id if payload.default_snippet_id else None
            if payload.default_action_id is not None:
                board.default_action_id = payload.default_action_id if payload.default_action_id else None
            if payload.color is not None:
                board.color = payload.color if payload.color else None
            if payload.position is not None:
                board.position = payload.position
            if payload.hermes_policy is not None:
                import json as _json
                board.hermes_policy = _json.dumps(payload.hermes_policy or {})
            s.commit()
            
            # Sync Project's kanban_board_id reference if default_project_id changed
            if payload.default_project_id is not None:
                from distr.core.db.projects import Project
                if board.default_project_id:
                    proj = s.query(Project).filter(Project.id == board.default_project_id).first()
                    if proj and proj.kanban_board_id != board.id:
                        proj.kanban_board_id = board.id
                        s.commit()
            return JSONResponse({"success": True})

    @router.delete("/tickets/boards/{board_id}")
    async def delete_board(board_id: int):
        with get_session() as s:
            board = orm_get_by_id(s, KanbanBoard,board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            s.delete(board)
            return JSONResponse({"success": True})

    @router.post("/tickets/boards/{board_id}/archive")
    async def archive_board(board_id: int):
        with get_session() as s:
            board = orm_get_by_id(s, KanbanBoard,board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            board.archived = True
            return JSONResponse({"success": True})

    @router.post("/tickets/boards/{board_id}/unarchive")
    async def unarchive_board(board_id: int):
        with get_session() as s:
            board = orm_get_by_id(s, KanbanBoard,board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            board.archived = False
            return JSONResponse({"success": True})

    @router.post("/tickets/boards/reorder")
    async def reorder_boards(payload: dict):
        """Reorder boards. Expects {"order": [id1, id2, ...]}"""
        order = payload.get("order", [])
        if not order:
            return JSONResponse({"success": True})
        with get_session() as s:
            for pos, board_id in enumerate(order):
                board = orm_get_by_id(s, KanbanBoard,board_id)
                if board:
                    board.position = pos
            return JSONResponse({"success": True})

    @router.get("/tickets/boards/{board_id}")
    async def get_board(board_id: int):
        with get_session() as s:
            board = orm_get_by_id(s, KanbanBoard,board_id)
            if not board:
                raise HTTPException(404, "Board not found")
            # Get WhatsApp links for this board
            from distr.core.db import WhatsAppPhoneLink
            from distr.core.hermes import parse_board_hermes_policy
            whatsapp_links = s.query(WhatsAppPhoneLink).filter_by(board_id=board_id).all()
            project_ids = {int(board.default_project_id)} if board.default_project_id else set()
            for lane in board.lanes:
                for t in lane.tickets:
                    if t.linked_project_id:
                        project_ids.add(int(t.linked_project_id))
            projects = {
                p.id: p
                for p in s.query(Project).filter(Project.id.in_(project_ids)).all()
            } if project_ids else {}
            default_project = projects.get(board.default_project_id)
            lanes = []
            for lane in board.lanes:
                tickets = []
                for t in lane.tickets:
                    linked_project = projects.get(t.linked_project_id)
                    tickets.append({
                        "id": t.id, "title": t.title, "description": t.description or "",
                        "priority": t.priority or "medium", "position": t.position,
                        "complexity": normalize_ticket_complexity(t.complexity),
                        "time_estimate": t.time_estimate or "",
                        "time_spent": t.time_spent or "",
                        "external_source": t.external_source, "external_id": t.external_id,
                        "external_url": t.external_url,
                        "linked_workflow_id": t.linked_workflow_id,
                        "workflow_queue_position": t.workflow_queue_position or 0,
                        "linked_project_id": t.linked_project_id,
                        **_project_context_payload(linked_project, "linked"),
                        "workflow_status": t.workflow_status,
                        "linked_snippet_id": t.linked_snippet_id,
                        "linked_action_id": t.linked_action_id,
                        "files": [_ticket_file_payload(t.id, f) for f in t.files],
                        "links": [{"id": l.id, "title": l.title, "url": l.url} for l in t.links],
                        "todos": [{"id": td.id, "text": td.text, "done": td.done, "position": td.position} for td in t.todos],
                        "whatsapp_message_id": t.whatsapp_message_id,
                        "whatsapp_message_wa_id": t.whatsapp_message_wa_id,
                        "source_chat_id": t.source_chat_id,
                        **_ticket_source_payload(t),
                    })
                lanes.append({"id": lane.id, "name": lane.name, "position": lane.position, "tickets": tickets})
            return JSONResponse({
                "id": board.id, "name": board.name, "description": board.description or "",
                "source": board.source, "external_board_id": board.external_board_id,
                "external_url": board.external_url, "lanes": lanes,
                "default_workflow_id": board.default_workflow_id,
                "default_project_id": board.default_project_id,
                **_project_context_payload(default_project, "default"),
                "default_snippet_id": board.default_snippet_id,
                "default_action_id": board.default_action_id,
                "color": board.color or "",
                "in_use": getattr(board, 'in_use', False) or False,
                "hermes_policy": parse_board_hermes_policy(getattr(board, "hermes_policy", None)),
                "whatsapp_links": [{"id": l.id, "phone_number": l.phone_number, "contact_name": l.contact_name, "auto_snapshot": l.auto_snapshot or False} for l in whatsapp_links],
            })

    @router.get("/tickets/boards/{board_id}/activity")
    async def board_activity(board_id: int, event_limit: int = 50, rule_limit: int = 20):
        with get_session() as s:
            board = orm_get_by_id(s, KanbanBoard, board_id)
            if not board:
                raise HTTPException(404, "Board not found")
        try:
            from distr.core.hermes import list_board_activity

            return JSONResponse(list_board_activity(board_id, event_limit=event_limit, rule_limit=rule_limit))
        except Exception as e:
            logger.error("Board activity failed: %s", e, exc_info=True)
            raise HTTPException(500, str(e))

    @router.patch("/tickets/boards/{board_id}/learned-rules/{rule_id}")
    async def update_board_learned_rule(board_id: int, rule_id: int, payload: dict):
        with get_session() as s:
            board = orm_get_by_id(s, KanbanBoard, board_id)
            if not board:
                raise HTTPException(404, "Board not found")
        try:
            from distr.core.hermes import list_learned_rules, set_learned_rule_enabled

            rules = list_learned_rules(board_id=int(board_id), enabled_only=False, limit=200)
            match = next((row for row in rules if int(row.get("id") or 0) == int(rule_id)), None)
            if not match or int(match.get("scope_id") or 0) != int(board_id):
                raise HTTPException(404, "Learned rule not found for this board")
            enabled = bool(payload.get("enabled", True))
            if not set_learned_rule_enabled(int(rule_id), enabled):
                raise HTTPException(404, "Learned rule not found")
            return JSONResponse({"success": True, "id": int(rule_id), "enabled": enabled})
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Update learned rule failed: %s", e, exc_info=True)
            raise HTTPException(500, str(e))

    @router.post("/tickets/boards/{board_id}/learned-rules/{rule_id}/promote")
    async def promote_board_learned_rule(board_id: int, rule_id: int, payload: dict | None = None):
        payload = payload or {}
        category = str(payload.get("category") or "general").strip().lower() or "general"
        try:
            from distr.core.hermes import promote_learned_rule_to_board_policy

            policy = promote_learned_rule_to_board_policy(
                board_id=int(board_id),
                rule_id=int(rule_id),
                category=category,
            )
            return JSONResponse({"success": True, "hermes_policy": policy})
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            logger.error("Promote learned rule failed: %s", exc, exc_info=True)
            raise HTTPException(500, str(exc)) from exc

    # ── Tickets ──

    @router.post("/tickets/tickets")
    async def create_ticket(payload: TicketCreate):
        with get_session() as s:
            lane = orm_get_by_id(s, KanbanLane,payload.lane_id)
            if not lane:
                raise HTTPException(404, "Lane not found")
            # Get board defaults for new tickets
            board = orm_get_by_id(s, KanbanBoard,lane.board_id)
            max_pos = max([t.position for t in lane.tickets], default=-1)
            complexity = normalize_ticket_complexity(payload.complexity) if payload.complexity else infer_ticket_complexity(
                payload.title,
                payload.description or "",
            )
            ticket = KanbanTicket(
                lane_id=payload.lane_id, title=payload.title,
                description=payload.description or "", priority=payload.priority or "medium",
                complexity=complexity,
                position=max_pos + 1,
                linked_workflow_id=board.default_workflow_id if board else None,
                linked_project_id=board.default_project_id if board else None,
                linked_snippet_id=board.default_snippet_id if board else None,
                linked_action_id=board.default_action_id if board else None,
                source_chat_id=payload.source_chat_id,
            )
            _apply_ticket_source_fields(ticket, payload)
            s.add(ticket)
            s.flush()
            _emit_ticket_channel_intake(ticket, board=board)
            return JSONResponse({"success": True, "id": ticket.id, "lane_id": ticket.lane_id})

    @router.get("/tickets/tickets/{ticket_id}")
    async def get_ticket(ticket_id: int):
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            lane = orm_get_by_id(s, KanbanLane, t.lane_id) if t.lane_id else None
            board = orm_get_by_id(s, KanbanBoard, lane.board_id) if lane else None
            linked_project = orm_get_by_id(s, Project, t.linked_project_id) if t.linked_project_id else None
            board_project = orm_get_by_id(s, Project, board.default_project_id) if board and board.default_project_id else None
            audit_entries = (
                s.query(KanbanTicketAuditEntry)
                .filter(KanbanTicketAuditEntry.ticket_id == t.id)
                .order_by(KanbanTicketAuditEntry.created_date.desc())
                .limit(50)
                .all()
            )
            return JSONResponse({
                "id": t.id, "lane_id": t.lane_id, "title": t.title,
                "description": t.description or "", "priority": t.priority or "medium",
                "complexity": normalize_ticket_complexity(t.complexity),
                "position": t.position,
                "time_estimate": t.time_estimate or "",
                "time_spent": t.time_spent or "",
                "external_source": t.external_source, "external_id": t.external_id,
                "external_url": t.external_url,
                "linked_workflow_id": t.linked_workflow_id,
                "workflow_queue_position": t.workflow_queue_position or 0,
                "linked_project_id": t.linked_project_id,
                **_project_context_payload(linked_project, "linked"),
                "board_id": board.id if board else None,
                "board_name": board.name if board else None,
                "board_default_project_id": board.default_project_id if board else None,
                **_project_context_payload(board_project, "board_default"),
                "workflow_status": t.workflow_status,
                "linked_snippet_id": t.linked_snippet_id,
                "linked_action_id": t.linked_action_id,
                "whatsapp_message_id": t.whatsapp_message_id,
                "whatsapp_message_wa_id": t.whatsapp_message_wa_id,
                **_ticket_source_payload(t),
                "files": [_ticket_file_payload(t.id, f) for f in t.files],
                "links": [{"id": l.id, "title": l.title, "url": l.url} for l in t.links],
                "todos": [{"id": td.id, "text": td.text, "done": td.done, "position": td.position} for td in t.todos],
                "audit_entries": [
                    {
                        "id": entry.id,
                        "run_id": entry.run_id,
                        "step_id": entry.step_id,
                        "step_result_id": entry.step_result_id,
                        "execution_lane": entry.execution_lane,
                        "status": entry.status,
                        "final_verdict": entry.final_verdict,
                        "summary": entry.summary or "",
                        "details": entry.details or "",
                        "created_date": (
                            entry.created_date.isoformat() if entry.created_date else None
                        ),
                    }
                    for entry in audit_entries
                ],
                "source_chat_id": t.source_chat_id,
            })

    @router.get("/tickets/workflows/{workflow_id}/tickets")
    async def get_workflow_tickets(workflow_id: int):
        """Return local tickets allocated to a workflow without starting it."""
        from distr.core.db.projects import Project

        with get_session() as s:
            rows = (
                s.query(KanbanTicket, KanbanLane, KanbanBoard)
                .join(KanbanLane, KanbanTicket.lane_id == KanbanLane.id)
                .join(KanbanBoard, KanbanLane.board_id == KanbanBoard.id)
                .filter(KanbanTicket.linked_workflow_id == workflow_id)
                .order_by(KanbanTicket.workflow_queue_position.asc(), KanbanTicket.created_date.asc())
                .all()
            )
            project_ids = {
                int(pid)
                for t, _lane, board in rows
                for pid in [t.linked_project_id or board.default_project_id]
                if pid
            }
            projects = {
                p.id: p
                for p in s.query(Project).filter(Project.id.in_(project_ids)).all()
            } if project_ids else {}
            return JSONResponse([
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description or "",
                    "priority": t.priority or "medium",
                    "complexity": normalize_ticket_complexity(t.complexity),
                    "position": t.position,
                    "workflow_queue_position": t.workflow_queue_position or 0,
                    "workflow_status": t.workflow_status,
                    "linked_workflow_id": t.linked_workflow_id,
                    "linked_project_id": t.linked_project_id or board.default_project_id,
                    "linked_project_name": (
                        projects.get(t.linked_project_id or board.default_project_id).name
                        if projects.get(t.linked_project_id or board.default_project_id)
                        else None
                    ),
                    "cli_route": (
                        _resolve_ticket_execution_route(
                            s,
                            {
                                "project": projects.get(t.linked_project_id or board.default_project_id),
                                "ticket": t,
                                "board": board,
                                "complexity": normalize_ticket_complexity(t.complexity),
                            },
                        )
                        if projects.get(t.linked_project_id or board.default_project_id)
                        else {}
                    ),
                    "lane_id": lane.id,
                    "lane_name": lane.name,
                    "board_id": board.id,
                    "board_name": board.name,
                    "source_provider": t.source_provider,
                    "source_external_id": t.source_external_id,
                    "external_source": t.external_source,
                    "external_id": t.external_id,
                    "source_label": t.source_label,
                    "source_url": t.source_url,
                }
                for t, lane, board in rows
            ])

    @router.put("/tickets/workflows/{workflow_id}/tickets/reorder")
    async def reorder_workflow_tickets(workflow_id: int, payload: WorkflowTicketReorder):
        """Persist queue order for tickets already allocated to this workflow."""
        with get_session() as s:
            for pos, ticket_id in enumerate(payload.ticket_ids or []):
                t = orm_get_by_id(s, KanbanTicket, ticket_id)
                if t and t.linked_workflow_id == workflow_id:
                    t.workflow_queue_position = pos
            return JSONResponse({"success": True})

    @router.delete("/tickets/workflows/{workflow_id}/tickets/{ticket_id}")
    async def remove_workflow_ticket(workflow_id: int, ticket_id: int):
        """Remove a queued ticket from a workflow when it has no active run."""
        from distr.core.db.workflow import AutoWorkflowRun

        with get_session() as s:
            active = (
                s.query(AutoWorkflowRun)
                .filter(
                    AutoWorkflowRun.workflow_id == workflow_id,
                    AutoWorkflowRun.ticket_id == ticket_id,
                    AutoWorkflowRun.status.in_(["running", "waiting"]),
                )
                .first()
            )
            if active:
                raise HTTPException(409, "Ticket has an active workflow run")
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t or t.linked_workflow_id != workflow_id:
                raise HTTPException(404, "Workflow ticket not found")
            t.linked_workflow_id = None
            t.workflow_queue_position = 0
            return JSONResponse({"success": True})

    @router.get("/tickets/tickets/{ticket_id}/audit-entries")
    async def get_ticket_audit_entries(ticket_id: int):
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            rows = (
                s.query(KanbanTicketAuditEntry)
                .filter(KanbanTicketAuditEntry.ticket_id == t.id)
                .order_by(KanbanTicketAuditEntry.created_date.desc())
                .all()
            )
            return JSONResponse(
                {
                    "ticket_id": t.id,
                    "entries": [
                        {
                            "id": row.id,
                            "run_id": row.run_id,
                            "step_id": row.step_id,
                            "step_result_id": row.step_result_id,
                            "execution_lane": row.execution_lane,
                            "status": row.status,
                            "final_verdict": row.final_verdict,
                            "summary": row.summary or "",
                            "details": row.details or "",
                            "created_date": row.created_date.isoformat() if row.created_date else None,
                        }
                        for row in rows
                    ],
                }
            )

    @router.get("/tickets/tickets/{ticket_id}/execution-sessions")
    async def get_ticket_execution_sessions(ticket_id: int):
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
        from distr.core.kanban.project_execution import list_execution_sessions_for_ticket

        return JSONResponse({
            "ticket_id": ticket_id,
            "sessions": list_execution_sessions_for_ticket(ticket_id),
        })

    @router.get("/tickets/workflows/{workflow_id}/execution-sessions")
    async def get_workflow_execution_sessions(workflow_id: int, limit: int = 50, active_only: bool = False):
        from distr.core.db.workflow import AutoWorkflow
        with get_session() as s:
            wf = orm_get_by_id(s, AutoWorkflow, workflow_id)
            if not wf:
                raise HTTPException(404, "Workflow not found")
        from distr.core.kanban.project_execution import list_execution_sessions_for_workflow

        return JSONResponse({
            "workflow_id": workflow_id,
            "sessions": list_execution_sessions_for_workflow(
                workflow_id,
                limit=limit,
                active_only=active_only,
            ),
        })


    @router.get("/tickets/tickets/{ticket_id}/audit-report")
    async def get_ticket_audit_report(ticket_id: int):
        def _parse_iso(iso_value: Optional[str]):
            if not iso_value:
                return None
            try:
                return datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
            except Exception:
                return None

        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")

            rows = (
                s.query(KanbanTicketAuditEntry)
                .filter(KanbanTicketAuditEntry.ticket_id == t.id)
                .order_by(KanbanTicketAuditEntry.created_date.asc())
                .all()
            )
            serialized = [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "step_id": row.step_id,
                    "step_result_id": row.step_result_id,
                    "execution_lane": row.execution_lane,
                    "status": row.status,
                    "final_verdict": row.final_verdict,
                    "summary": row.summary or "",
                    "details": row.details or "",
                    "created_date": row.created_date.isoformat() if row.created_date else None,
                }
                for row in rows
            ]

            runs_map = {}
            for entry in serialized:
                run_key = str(entry.get("run_id") if entry.get("run_id") is not None else "no_run")
                bucket = runs_map.setdefault(
                    run_key,
                    {
                        "run_id": entry.get("run_id"),
                        "entries": [],
                        "started_at": None,
                        "finished_at": None,
                        "total_duration_seconds": 0,
                        "step_breakdown": [],
                        "status_counts": {},
                    },
                )
                bucket["entries"].append(entry)

            report_runs = []
            for bucket in runs_map.values():
                entries = bucket["entries"]
                if not entries:
                    continue
                run_start = _parse_iso(entries[0].get("created_date"))
                run_end = _parse_iso(entries[-1].get("created_date"))
                bucket["started_at"] = entries[0].get("created_date")
                bucket["finished_at"] = entries[-1].get("created_date")
                if run_start and run_end:
                    bucket["total_duration_seconds"] = max(
                        0,
                        int((run_end - run_start).total_seconds()),
                    )

                for e in entries:
                    st = (e.get("status") or "unknown").strip().lower()
                    bucket["status_counts"][st] = int(bucket["status_counts"].get(st, 0)) + 1

                step_starts = {}
                step_rows = {}
                for e in entries:
                    step_id = e.get("step_id")
                    if step_id is None:
                        continue
                    stamp = _parse_iso(e.get("created_date"))
                    status = (e.get("status") or "").strip().lower()
                    key = str(step_id)
                    row = step_rows.setdefault(
                        key,
                        {
                            "step_id": step_id,
                            "attempts": 0,
                            "total_seconds": 0,
                            "wait_seconds": 0,
                            "statuses": [],
                        },
                    )
                    row["statuses"].append(status)
                    if status == "running":
                        step_starts[key] = stamp
                        row["attempts"] += 1
                        continue
                    start_dt = step_starts.get(key)
                    if start_dt and stamp:
                        elapsed = max(0, int((stamp - start_dt).total_seconds()))
                        row["total_seconds"] += elapsed
                        if status == "waiting":
                            row["wait_seconds"] += elapsed
                        step_starts.pop(key, None)
                bucket["step_breakdown"] = sorted(
                    step_rows.values(),
                    key=lambda x: int(x.get("step_id") or 0),
                )
                report_runs.append(bucket)

            report_runs.sort(
                key=lambda r: (r.get("started_at") or ""),
                reverse=True,
            )

            return JSONResponse(
                {
                    "ticket_id": t.id,
                    "ticket_title": t.title,
                    "total_entries": len(serialized),
                    "runs": report_runs,
                    "entries": list(reversed(serialized)),
                }
            )

    @router.delete("/tickets/tickets/{ticket_id}/audit-report")
    async def clear_ticket_audit_report(ticket_id: int):
        """Clear report-tab data by deleting audit history entries for a ticket."""
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            deleted = (
                s.query(KanbanTicketAuditEntry)
                .filter(KanbanTicketAuditEntry.ticket_id == t.id)
                .delete(synchronize_session=False)
            )
            s.commit()
            return JSONResponse(
                {
                    "ok": True,
                    "ticket_id": t.id,
                    "deleted_entries": int(deleted or 0),
                }
            )

    @router.put("/tickets/tickets/{ticket_id}")
    async def update_ticket(ticket_id: int, payload: TicketUpdate):
        if not _is_valid_time_tracking_value(payload.time_estimate):
            raise HTTPException(422, "Invalid time_estimate format. Use values like '30m', '2h', or '1d 3h'.")
        if not _is_valid_time_tracking_value(payload.time_spent):
            raise HTTPException(422, "Invalid time_spent format. Use values like '30m', '2h', or '1d 3h'.")
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            fields_set = getattr(payload, "model_fields_set", getattr(payload, "__fields_set__", set()))
            if payload.title is not None:
                t.title = payload.title
            if payload.description is not None:
                t.description = payload.description
            if payload.priority is not None:
                t.priority = payload.priority
            if payload.complexity is not None:
                t.complexity = normalize_ticket_complexity(payload.complexity)
            if "linked_workflow_id" in fields_set or "linked_project_id" in fields_set:
                lane = orm_get_by_id(s, KanbanLane,t.lane_id) if t.lane_id else None
                board = orm_get_by_id(s, KanbanBoard,lane.board_id) if lane else None
                if "linked_workflow_id" in fields_set:
                    if payload.linked_workflow_id and t.linked_workflow_id and t.linked_workflow_id != payload.linked_workflow_id:
                        raise HTTPException(409, "Ticket is already linked to a workflow")
                    # Empty selection in UI means "inherit from board default".
                    t.linked_workflow_id = (
                        payload.linked_workflow_id
                        if payload.linked_workflow_id is not None
                        else (board.default_workflow_id if board else None)
                    )
                    if t.linked_workflow_id and not t.workflow_queue_position:
                        max_pos = (
                            s.query(KanbanTicket.workflow_queue_position)
                            .filter(KanbanTicket.linked_workflow_id == t.linked_workflow_id)
                            .order_by(KanbanTicket.workflow_queue_position.desc())
                            .first()
                        )
                        t.workflow_queue_position = ((max_pos[0] if max_pos and max_pos[0] is not None else -1) + 1)
                if "linked_project_id" in fields_set:
                    # Empty selection in UI means "inherit from board default".
                    t.linked_project_id = (
                        payload.linked_project_id
                        if payload.linked_project_id is not None
                        else (board.default_project_id if board else None)
                    )
            if payload.linked_snippet_id is not None:
                t.linked_snippet_id = payload.linked_snippet_id
            if payload.linked_action_id is not None:
                t.linked_action_id = payload.linked_action_id
            if payload.time_estimate is not None:
                t.time_estimate = payload.time_estimate.strip() if isinstance(payload.time_estimate, str) else payload.time_estimate
            if payload.time_spent is not None:
                t.time_spent = payload.time_spent.strip() if isinstance(payload.time_spent, str) else payload.time_spent
            if payload.lane_id is not None:
                t.lane_id = payload.lane_id
            if payload.position is not None:
                t.position = payload.position
            if payload.workflow_queue_position is not None:
                t.workflow_queue_position = payload.workflow_queue_position
            if "source_chat_id" in fields_set:
                t.source_chat_id = payload.source_chat_id
            _apply_ticket_source_fields(t, payload)
            # For local tickets linked to external providers, keep external card/issue in sync immediately on save.
            _sync_local_ticket_to_external(
                source=t.external_source,
                external_id=t.external_id,
                title=t.title or "",
                description=t.description or "",
                time_estimate=t.time_estimate,
                time_spent=t.time_spent,
            )
            return JSONResponse({"success": True})

    @router.put("/tickets/tickets/{ticket_id}/move")
    async def move_ticket(ticket_id: int, payload: TicketMove):
        notify_ctx = None
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            lane = orm_get_by_id(s, KanbanLane,payload.lane_id)
            if not lane:
                raise HTTPException(404, "Lane not found")
            old_lane_id = t.lane_id
            old_lane = orm_get_by_id(s, KanbanLane,old_lane_id)
            old_lane_name = old_lane.name if old_lane else ""
            board_name = ""
            if old_lane:
                bd = orm_get_by_id(s, KanbanBoard,old_lane.board_id)
                board_name = bd.name if bd else ""
            new_lane_name = lane.name
            if old_lane_id != payload.lane_id:
                notify_ctx = {
                    "board_name": board_name,
                    "from_lane_name": old_lane_name or None,
                    "to_lane_name": new_lane_name,
                }
            t.lane_id = payload.lane_id
            t.position = payload.position
            # Reorder siblings
            siblings = s.query(KanbanTicket).filter(
                KanbanTicket.lane_id == payload.lane_id,
                KanbanTicket.id != ticket_id
            ).order_by(KanbanTicket.position).all()
            for i, sib in enumerate(siblings):
                new_pos = i if i < payload.position else i + 1
                sib.position = new_pos
        if notify_ctx:
            try:
                from distr.core.kanban.ticket_chat_notify import notify_source_chat_ticket_moved

                notify_source_chat_ticket_moved(
                    ticket_id,
                    board_name=notify_ctx["board_name"],
                    to_lane_name=notify_ctx["to_lane_name"],
                    from_lane_name=notify_ctx["from_lane_name"],
                    reason="manual",
                )
            except Exception:
                logger.debug("move_ticket: chat notify failed", exc_info=True)
        return JSONResponse({"success": True})

    @router.delete("/tickets/tickets/{ticket_id}")
    async def delete_ticket(ticket_id: int):
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")

            # Clear snapshot_group for ALL messages linked to this ticket
            grouped = s.query(WhatsAppMessage).filter(_whatsapp_snapshot_group_filter(ticket_id)).all()
            for msg in grouped:
                msg.snapshot_group = None
                msg.processed = False
                msg.processed_date = None

            # Also clear the direct whatsapp_message_id link
            if t.whatsapp_message_id:
                wa_msg = orm_get_by_id(s, WhatsAppMessage,t.whatsapp_message_id)
                if wa_msg:
                    wa_msg.snapshot_group = None
                    wa_msg.processed = False
                    wa_msg.processed_date = None

            s.delete(t)
            return JSONResponse({"success": True})

    # ── Ticket Files ──

    @router.post("/tickets/tickets/{ticket_id}/files")
    async def upload_ticket_file(ticket_id: int, file: UploadFile = File(...)):
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            upload_dir = os.path.join(KANBAN_UPLOADS_DIR, str(ticket_id))
            os.makedirs(upload_dir, exist_ok=True)
            safe_name = os.path.basename(file.filename or "file")
            dest = os.path.join(upload_dir, safe_name)
            content = await file.read()
            with open(dest, "wb") as f:
                f.write(content)
            rec = KanbanTicketFile(ticket_id=ticket_id, filename=safe_name, file_path=dest)
            s.add(rec)
            s.flush()
            return JSONResponse({"success": True, **_ticket_file_payload(ticket_id, rec)})

    @router.post("/tickets/tickets/{ticket_id}/attach-file")
    async def attach_existing_file(ticket_id: int, payload: dict):
        """Attach an existing file (e.g. WhatsApp media) to a ticket by path."""
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            filename = payload.get("filename", "attachment")
            file_path = payload.get("file_path", "")
            description = payload.get("description", "")
            if not file_path or not os.path.exists(file_path):
                raise HTTPException(400, "File not found")
            rec = KanbanTicketFile(
                ticket_id=ticket_id,
                filename=filename,
                file_path=file_path,
                description=description,
            )
            s.add(rec)
            s.flush()
            return JSONResponse({"success": True, **_ticket_file_payload(ticket_id, rec)})

    @router.post("/tickets/tickets/{ticket_id}/attach-whatsapp-media")
    async def attach_whatsapp_media(ticket_id: int, request: Request):
        """Attach WhatsApp media from a message to a ticket."""
        body = await request.json()
        message_id = body.get("message_id")
        if not message_id:
            return JSONResponse({"error": "message_id required"}, status_code=400)

        with get_session() as s:
            ticket = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not ticket:
                raise HTTPException(404, "Ticket not found")

            msg = orm_get_by_id(s, WhatsAppMessage,message_id)
            if not msg or not msg.media_local_path:
                return JSONResponse({"success": True, "attached": False, "reason": "No media"})

            wa_src = resolve_whatsapp_media_disk_path(msg.media_local_path)
            # Check if file exists
            if not wa_src or not os.path.exists(wa_src):
                return JSONResponse({"success": True, "attached": False, "reason": "File not found"})

            # Add attachment to ticket
            from shutil import copy2
            import uuid
            ext = os.path.splitext(wa_src)[1] or ""
            dest_name = f"wa_{msg.id}_{uuid.uuid4().hex[:8]}{ext}"
            dest_dir = os.path.join(DB_DIR, "ticket_files", str(ticket_id))
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, dest_name)
            copy2(wa_src, dest_path)

            # Add to ticket files
            tf = KanbanTicketFile(
                ticket_id=ticket_id,
                filename=msg.media_filename or dest_name,
                file_path=dest_path,
                description=f"WhatsApp {msg.media_type}" if msg.media_type else "WhatsApp media"
            )
            s.add(tf)
            s.commit()

            return JSONResponse({
                "success": True,
                "attached": True,
                **_ticket_file_payload(ticket_id, tf),
            })


    @router.get("/tickets/tickets/{ticket_id}/files/{file_id}/content")
    async def view_ticket_file(ticket_id: int, file_id: int):
        """Serve a ticket attachment for inline preview or browser download."""
        with get_session() as s:
            rec = s.query(KanbanTicketFile).filter_by(id=file_id, ticket_id=ticket_id).first()
            if not rec:
                raise HTTPException(404, "File not found")
            file_path = os.path.realpath(rec.file_path or "")
            if not file_path or not os.path.exists(file_path):
                raise HTTPException(404, "File missing on disk")
            filename = os.path.basename(rec.filename or file_path) or "attachment"
            media_type = mimetypes.guess_type(filename)[0] or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
            return FileResponse(
                file_path,
                media_type=media_type,
                filename=filename,
                content_disposition_type="inline",
            )


    @router.delete("/tickets/tickets/{ticket_id}/files/{file_id}")
    async def delete_ticket_file(ticket_id: int, file_id: int):
        with get_session() as s:
            f = s.query(KanbanTicketFile).filter_by(id=file_id, ticket_id=ticket_id).first()
            if not f:
                raise HTTPException(404, "File not found")
            try:
                if os.path.exists(f.file_path):
                    os.remove(f.file_path)
            except Exception:
                pass
            s.delete(f)
            return JSONResponse({"success": True})

    # ── Ticket Links ──

    @router.post("/tickets/tickets/{ticket_id}/links")
    async def add_ticket_link(ticket_id: int, payload: LinkCreate):
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            link = KanbanTicketLink(ticket_id=ticket_id, title=payload.title, url=payload.url)
            s.add(link)
            s.flush()
            return JSONResponse({"success": True, "id": link.id})

    @router.delete("/tickets/tickets/{ticket_id}/links/{link_id}")
    async def delete_ticket_link(ticket_id: int, link_id: int):
        with get_session() as s:
            link = s.query(KanbanTicketLink).filter_by(id=link_id, ticket_id=ticket_id).first()
            if not link:
                raise HTTPException(404, "Link not found")
            s.delete(link)
            return JSONResponse({"success": True})

    # ── Ticket Todos ──

    @router.post("/tickets/tickets/{ticket_id}/todos")
    async def add_ticket_todo(ticket_id: int, payload: TodoCreate):
        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket,ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")
            max_pos = max([td.position for td in t.todos], default=-1)
            todo = KanbanTicketTodo(ticket_id=ticket_id, text=payload.text, position=max_pos + 1)
            s.add(todo)
            s.flush()
            return JSONResponse({"success": True, "id": todo.id})

    @router.put("/tickets/tickets/{ticket_id}/todos/{todo_id}")
    async def update_ticket_todo(ticket_id: int, todo_id: int, payload: TodoUpdate):
        with get_session() as s:
            todo = s.query(KanbanTicketTodo).filter_by(id=todo_id, ticket_id=ticket_id).first()
            if not todo:
                raise HTTPException(404, "Todo not found")
            if payload.text is not None:
                todo.text = payload.text
            if payload.done is not None:
                todo.done = payload.done
            return JSONResponse({"success": True})

    @router.delete("/tickets/tickets/{ticket_id}/todos/{todo_id}")
    async def delete_ticket_todo(ticket_id: int, todo_id: int):
        with get_session() as s:
            todo = s.query(KanbanTicketTodo).filter_by(id=todo_id, ticket_id=ticket_id).first()
            if not todo:
                raise HTTPException(404, "Todo not found")
            s.delete(todo)
            return JSONResponse({"success": True})

    # ── Copy external ticket to local board ──

    @router.post("/tickets/tickets/copy-to-board")
    async def copy_ticket_to_board(payload: CopyToBoard):
        if not _is_valid_time_tracking_value(payload.time_estimate):
            raise HTTPException(422, "Invalid time_estimate format. Use values like '30m', '2h', or '1d 3h'.")
        if not _is_valid_time_tracking_value(payload.time_spent):
            raise HTTPException(422, "Invalid time_spent format. Use values like '30m', '2h', or '1d 3h'.")
        with get_session() as s:
            board, dest_lane = _resolve_local_destination_lane(s, payload.board_id, payload.lane_id)
            if not board:
                raise HTTPException(404, "Database board not found")
            if not dest_lane:
                raise HTTPException(404, "Lane not found on board")
            max_pos = max([t.position for t in dest_lane.tickets], default=-1)
            ticket = KanbanTicket(
                lane_id=dest_lane.id, title=payload.title,
                description=payload.description or "", priority=payload.priority or "medium",
                complexity=normalize_ticket_complexity(payload.complexity) if payload.complexity else infer_ticket_complexity(payload.title, payload.description or ""),
                time_estimate=(payload.time_estimate or ""),
                time_spent=(payload.time_spent or ""),
                position=max_pos + 1,
                external_source=payload.external_source,
                external_id=payload.external_id,
                external_url=payload.external_url,
                source_provider=normalize_source_provider(payload.external_source) or None,
                source_external_id=payload.external_id,
                source_url=payload.external_url,
                source_label=payload.external_source,
                linked_workflow_id=board.default_workflow_id,
                linked_project_id=board.default_project_id,
            )
            s.add(ticket)
            s.flush()
            # Inherit board defaults
            if board.default_workflow_id:
                ticket.linked_workflow_id = board.default_workflow_id
            if board.default_project_id:
                ticket.linked_project_id = board.default_project_id
            s.flush()
            return JSONResponse({"success": True, "id": ticket.id})

    @router.post("/tickets/tickets/copy-external-to-board")
    async def copy_external_ticket_to_board(payload: CopyExternalTicket):
        """Copy an external (Trello/Jira) ticket to a local board and optionally send to project/CLI."""
        if not _is_valid_time_tracking_value(payload.time_estimate):
            raise HTTPException(422, "Invalid time_estimate format. Use values like '30m', '2h', or '1d 3h'.")
        if not _is_valid_time_tracking_value(payload.time_spent):
            raise HTTPException(422, "Invalid time_spent format. Use values like '30m', '2h', or '1d 3h'.")
        from distr.core.db.projects import Project

        with get_session() as s:
            board = s.query(KanbanBoard).filter_by(id=payload.board_id, source="database").first()
            if not board:
                project_id = payload.linked_project_id
                if project_id:
                    board = (
                        s.query(KanbanBoard)
                        .filter(
                            KanbanBoard.source == "database",
                            KanbanBoard.default_project_id == project_id,
                        )
                        .order_by(KanbanBoard.position.asc(), KanbanBoard.id.asc())
                        .first()
                    )
                    if not board:
                        project = orm_get_by_id(s, Project, project_id)
                        if not project:
                            raise HTTPException(404, "Linked project not found")
                        board = KanbanBoard(
                            name=project.name or f"Project {project_id}",
                            description=f"Workflow intake board for project: {project.name or project_id}",
                            source="database",
                            default_project_id=project_id,
                        )
                        s.add(board)
                        s.flush()
                        project.kanban_board_id = board.id
                        for i, lane_name in enumerate(DEFAULT_LANES):
                            s.add(KanbanLane(board_id=board.id, name=lane_name, position=i))
                        s.flush()
                if not board:
                    raise HTTPException(404, "Database board not found")
            board, dest_lane = _resolve_local_destination_lane(s, board.id, payload.lane_id)
            if not dest_lane:
                raise HTTPException(404, "Lane not found on board")
            copy_result = _copy_external_ticket_into_lane(
                s,
                board,
                dest_lane,
                title=payload.title,
                description=payload.description or "",
                priority=payload.priority or "medium",
                complexity=payload.complexity,
                time_estimate=payload.time_estimate or "",
                time_spent=payload.time_spent or "",
                external_source=payload.external_source,
                external_id=payload.external_id,
                external_url=payload.external_url,
                linked_project_id=payload.linked_project_id,
                linked_workflow_id=payload.linked_workflow_id,
                source_chat_id=payload.source_chat_id,
            )
            ticket = s.query(KanbanTicket).filter_by(id=copy_result["id"]).first()
            result = {
                "success": True,
                "id": copy_result["id"],
                "reused": copy_result.get("reused", False),
            }

            # Auto-send to project if requested
            if payload.auto_send_to_project:
                project_id = ticket.linked_project_id or board.default_project_id
                if project_id:
                    from distr.core.db.projects import Project
                    project = orm_get_by_id(s, Project, project_id)
                    if project and project.folder_location:
                        proj_root = os.path.abspath(os.path.expanduser(project.folder_location.strip()))
                        tickets_folder = None
                        if not os.path.isdir(proj_root):
                            result["sent_to_project"] = False
                            result["project_error"] = (
                                f"Project folder is missing or not a directory: {proj_root!r}"
                            )
                            logger.warning(
                                "copy-external: auto send skipped — bad project folder kanban_ticket_id=%s path=%r",
                                ticket.id,
                                proj_root,
                            )
                        else:
                            tickets_folder = os.path.join(proj_root, ".tickets")
                            try:
                                os.makedirs(tickets_folder, exist_ok=True)
                            except OSError as e:
                                result["sent_to_project"] = False
                                result["project_error"] = f"Cannot create .tickets folder: {e}"
                                tickets_folder = None
                                logger.warning(
                                    "copy-external: cannot mkdir .tickets kanban_ticket_id=%s under %r: %s",
                                    ticket.id,
                                    proj_root,
                                    e,
                                )
                        if tickets_folder:
                            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                            ticket_path = os.path.join(tickets_folder, f"ticket_{timestamp}.md")
                            desc_raw = ticket.description or ""
                            try:
                                desc_plain = (
                                    _html_to_plain_ticket_description(desc_raw)
                                    if "<" in desc_raw
                                    else (desc_raw.strip() or "(no description)")
                                )
                            except Exception as e:
                                logger.warning("copy-external: plain description failed: %s", e)
                                desc_plain = desc_raw.strip() or "(no description)"
                            import_md = ""
                            ext_src = (payload.external_source or "").lower().strip()
                            ext_id = (payload.external_id or "").strip()
                            if ext_src in ("jira", "trello") and ext_id:
                                try:
                                    if ext_src == "jira":
                                        import_md, iw = _download_jira_issue_attachments_for_project(
                                            proj_root, timestamp, ext_id
                                        )
                                    else:
                                        import_md, iw = _download_trello_card_attachments_for_project(
                                            proj_root, timestamp, ext_id
                                        )
                                    if iw:
                                        result["attachment_import_warning"] = iw
                                except Exception as e:
                                    logger.exception(
                                        "copy-external: attachment import failed (continuing without files)"
                                    )
                                    result["attachment_import_warning"] = str(e) or type(e).__name__
                                    import_md = "\n## Attachments\n_(import failed before ticket write)_\n"
                            content = (
                                f"---\nid: ticket_{timestamp}\ntitle: {_yaml_scalar(ticket.title or '')}\n"
                                f"project: {_yaml_scalar(project.name or '')}\n"
                                f"created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"priority: {_yaml_scalar(ticket.priority or 'medium')}\n"
                                f"status: open\nsource: {payload.external_source or 'external'}_{payload.external_id or ''}\n"
                                f"---\n\n## Description\n{desc_plain}\n{import_md}\n---\n*Sent from Ticket Board via DecisionsAI*\n"
                            )
                            try:
                                with open(ticket_path, "w", encoding="utf-8") as f:
                                    f.write(content)
                                result["sent_to_project"] = True
                                result["project_name"] = project.name
                                logger.info(
                                    "copy-external: auto send ok kanban_ticket_id=%s project_id=%s path=%s bytes=%s",
                                    ticket.id,
                                    project.id,
                                    ticket_path,
                                    len(content.encode("utf-8")),
                                )
                            except Exception as e:
                                logger.exception("copy-external: failed to write %s", ticket_path)
                                result["sent_to_project"] = False
                                result["project_error"] = str(e)
            if payload.auto_send_to_workflow:
                workflow_id = ticket.linked_workflow_id or board.default_workflow_id
                if workflow_id:
                    try:
                        from distr.core.workflow.service import start_workflow_run

                        context = f"Ticket: {ticket.title}"
                        if ticket.description:
                            context += f"\n\nDescription: {ticket.description}"
                        run_metadata = {
                            "source_type": "ticket_copy_send_to_workflow",
                            "board_id": board.id,
                            "board_name": board.name,
                            "ticket_id": ticket.id,
                            "ticket_title": ticket.title or "",
                            "project_id": str(ticket.linked_project_id) if ticket.linked_project_id else None,
                            "project_name": None,
                            "phase": "planning",
                        }
                        run_result = start_workflow_run(
                            workflow_id,
                            context=context,
                            board_id=board.id,
                            ticket_id=ticket.id,
                            run_metadata=run_metadata,
                        )
                        if "error" in run_result:
                            result["workflow_started"] = False
                            result["workflow_error"] = run_result["error"]
                        else:
                            result["workflow_started"] = True
                            result["workflow_id"] = workflow_id
                            result["workflow_run_id"] = run_result.get("run_id")
                    except Exception as e:
                        logger.exception("copy-external: auto send to workflow failed")
                        result["workflow_started"] = False
                        result["workflow_error"] = str(e)
                else:
                    result["workflow_started"] = False
                    result["workflow_error"] = "No workflow linked to ticket or board"

            return JSONResponse(result)

    @router.post("/tickets/tickets/bulk-copy-to-board")
    async def bulk_copy_lane_to_board(payload: BulkCopyLaneToBoard):
        """Copy multiple external tickets into one lane on a local board."""
        if not payload.tickets:
            return JSONResponse({"copied": 0, "reused": 0, "skipped": 0, "errors": []})
        with get_session() as s:
            board, dest_lane = _resolve_local_destination_lane(s, payload.board_id, payload.lane_id)
            if not board:
                raise HTTPException(404, "Database board not found")
            if not dest_lane:
                raise HTTPException(404, "Lane not found on board")
            max_pos = max([t.position for t in dest_lane.tickets], default=-1)
            copied = 0
            reused = 0
            skipped = 0
            errors: list[str] = []
            for idx, item in enumerate(payload.tickets):
                if not _is_valid_time_tracking_value(item.time_estimate):
                    errors.append(f"Ticket {idx + 1}: invalid time_estimate")
                    continue
                if not _is_valid_time_tracking_value(item.time_spent):
                    errors.append(f"Ticket {idx + 1}: invalid time_spent")
                    continue
                try:
                    copy_result = _copy_external_ticket_into_lane(
                        s,
                        board,
                        dest_lane,
                        title=item.title,
                        description=item.description or "",
                        priority=item.priority or "medium",
                        complexity=item.complexity,
                        time_estimate=item.time_estimate or "",
                        time_spent=item.time_spent or "",
                        external_source=item.external_source,
                        external_id=item.external_id,
                        external_url=item.external_url,
                        linked_project_id=payload.linked_project_id,
                        linked_workflow_id=payload.linked_workflow_id,
                        position=max_pos + 1 + idx,
                        skip_workflow_linked=True,
                    )
                except HTTPException as exc:
                    errors.append(f"Ticket {idx + 1}: {exc.detail}")
                    continue
                except Exception as exc:
                    errors.append(f"Ticket {idx + 1}: {exc}")
                    continue
                if copy_result.get("skipped"):
                    skipped += 1
                elif copy_result.get("reused"):
                    reused += 1
                else:
                    copied += 1
            return JSONResponse(
                {
                    "copied": copied,
                    "reused": reused,
                    "skipped": skipped,
                    "errors": errors,
                    "board_id": board.id,
                    "lane_id": dest_lane.id,
                }
            )

    @router.post("/tickets/external-boards/{provider}/{ext_board_id}/register")
    async def register_external_board(provider: str, ext_board_id: str, payload: ExternalBoardRegister):
        """Create or update a local KanbanBoard record for an external (Trello/Jira) board configuration."""
        if provider not in ("trello", "jira"):
            raise HTTPException(400, "Provider must be 'trello' or 'jira'")
        with get_session() as s:
            board = s.query(KanbanBoard).filter_by(source=provider, external_board_id=ext_board_id).first()
            if not board:
                # Get name from payload or use a default
                name = payload.name or f"{provider.title()} Board"
                board = KanbanBoard(
                    name=name,
                    source=provider,
                    external_board_id=ext_board_id,
                )
                s.add(board)
                s.flush()
                # Create default lanes
                for i, lane_name in enumerate(DEFAULT_LANES):
                    s.add(KanbanLane(board_id=board.id, name=lane_name, position=i))
            else:
                if payload.name is not None:
                    board.name = payload.name
            if payload.default_project_id is not None:
                board.default_project_id = payload.default_project_id if payload.default_project_id else None
            if payload.default_workflow_id is not None:
                board.default_workflow_id = payload.default_workflow_id if payload.default_workflow_id else None
            if payload.color is not None:
                board.color = payload.color if payload.color else None
            s.flush()
            return JSONResponse({
                "success": True,
                "id": board.id,
                "name": board.name,
                "source": board.source,
                "external_board_id": board.external_board_id,
                "default_project_id": board.default_project_id,
                "default_workflow_id": board.default_workflow_id,
                "color": board.color,
            })

    # ── Create tickets on external boards (Trello / Jira) ──

    @router.post("/tickets/external-boards/{provider}/{ext_board_id}/create-ticket")
    async def create_external_ticket(provider: str, ext_board_id: str, payload: ExternalTicketCreate):
        """Create a ticket (card/issue) on an external Trello or Jira board."""
        if provider not in ("trello", "jira"):
            raise HTTPException(400, "Provider must be 'trello' or 'jira'")
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            import json
            accounts = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
        except Exception:
            accounts = []

        if provider == "trello":
            for acct in accounts:
                if acct.get("provider", "").lower() == "trello" and acct.get("api_key") and acct.get("api_token"):
                    import requests as req_lib
                    if not payload.lane_id:
                        raise HTTPException(400, "Please select a list/column for the Trello card")
                    card_data = {
                        "key": acct["api_key"],
                        "token": acct["api_token"],
                        "idList": payload.lane_id,
                        "name": payload.title,
                        "desc": payload.description or "",
                    }
                    r = req_lib.post("https://api.trello.com/1/cards", params=card_data, timeout=15)
                    if r.status_code not in (200, 201):
                        raise HTTPException(r.status_code, f"Trello API error: {r.text}")
                    card = r.json()
                    _invalidate_external_board_detail_cache(provider, ext_board_id)
                    return JSONResponse({
                        "success": True,
                        "ticket": {
                            "id": card.get("id", ""),
                            "title": card.get("name", payload.title),
                            "url": card.get("url", ""),
                        }
                    })
            raise HTTPException(404, "No valid Trello account found")

        elif provider == "jira":
            for acct in accounts:
                if acct.get("provider", "").lower() == "jira" and acct.get("email") and acct.get("api_token"):
                    import requests as req_lib
                    from requests.auth import HTTPBasicAuth
                    domain = acct.get("domain") or ""
                    if not domain:
                        server_url = (acct.get("server_url") or "").strip().rstrip("/")
                        if server_url:
                            domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                    if not domain:
                        continue
                    auth = HTTPBasicAuth(acct["email"], acct["api_token"])
                    base_url = f"https://{domain}" if not domain.startswith("http") else domain
                    project_key = ""
                    try:
                        cr = req_lib.get(f"{base_url}/rest/agile/1.0/board/{ext_board_id}/configuration",
                                         auth=auth, headers={"Accept": "application/json"}, timeout=10)
                        if cr.status_code == 200:
                            project_key = cr.json().get("location", {}).get("projectKey", "") or cr.json().get("name", "")
                    except Exception:
                        pass
                    if not project_key:
                        raise HTTPException(400, "Could not determine Jira project key for this board")
                    issue_data = {
                        "fields": {
                            "project": {"key": project_key},
                            "summary": payload.title,
                            "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": payload.description or ""}]}]},
                            "issuetype": {"name": "Task"},
                        }
                    }
                    pri_map = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Highest"}
                    jira_pri = pri_map.get(payload.priority, "Medium")
                    issue_data["fields"]["priority"] = {"name": jira_pri}
                    r = req_lib.post(f"{base_url}/rest/api/2/issue",
                                     json=issue_data, auth=auth,
                                     headers={"Accept": "application/json", "Content-Type": "application/json"},
                                     timeout=15)
                    if r.status_code not in (200, 201):
                        raise HTTPException(r.status_code, f"Jira API error: {r.text}")
                    issue = r.json()
                    issue_key = issue.get("key", "")
                    if payload.lane_id:
                        try:
                            transitions = req_lib.get(f"{base_url}/rest/api/2/issue/{issue_key}/transitions",
                                                         auth=auth, headers={"Accept": "application/json"}, timeout=10)
                            if transitions.status_code == 200:
                                for t in transitions.json().get("transitions", []):
                                    if t.get("to", {}).get("name", "").lower() == payload.lane_id.lower():
                                        req_lib.post(f"{base_url}/rest/api/2/issue/{issue_key}/transitions",
                                                     json={"transition": {"id": t["id"]}}, auth=auth,
                                                     headers={"Accept": "application/json", "Content-Type": "application/json"},
                                                     timeout=10)
                                        break
                        except Exception:
                            pass
                    _invalidate_external_board_detail_cache(provider, ext_board_id)
                    return JSONResponse({
                        "success": True,
                        "ticket": {
                            "id": issue_key,
                            "title": payload.title,
                            "url": f"{base_url}/browse/{issue_key}",
                        }
                    })
            raise HTTPException(404, "No valid Jira account found")

        raise HTTPException(400, "Unsupported provider")

    @router.put("/tickets/external-boards/{provider}/{board_id}/move-ticket")
    async def move_external_board_ticket(provider: str, board_id: str, payload: ExternalBoardMoveTicket):
        """Move/reorder a ticket on Trello (list + pos) or Jira (workflow transition into target column)."""
        if provider not in ("trello", "jira"):
            raise HTTPException(400, "Provider must be 'trello' or 'jira'")
        ticket_id = (payload.ticket_id or "").strip()
        target_lane = (payload.target_lane_id or "").strip()
        if not ticket_id or not target_lane:
            raise HTTPException(400, "ticket_id and target_lane_id are required")
        try:
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            accounts = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
        except Exception:
            accounts = []

        if provider == "trello":
            import requests as req_lib

            for acct in accounts:
                if acct.get("provider", "").lower() != "trello" or not acct.get("api_key") or not acct.get("api_token"):
                    continue
                key, token = acct["api_key"], acct["api_token"]
                gc = req_lib.get(
                    f"https://api.trello.com/1/cards/{ticket_id}",
                    params={"key": key, "token": token, "fields": "idList,pos"},
                    timeout=10,
                )
                if gc.status_code != 200:
                    continue
                try:
                    card_json = gc.json()
                except Exception:
                    continue
                if not isinstance(card_json, dict):
                    continue
                source_list = card_json.get("idList") or ""
                lr = req_lib.get(
                    f"https://api.trello.com/1/lists/{target_lane}/cards",
                    params={"key": key, "token": token, "fields": "id,pos"},
                    timeout=15,
                )
                if lr.status_code != 200:
                    raise HTTPException(lr.status_code, f"Trello list cards failed: {lr.text[:500]}")
                try:
                    list_cards = lr.json()
                except Exception:
                    raise HTTPException(502, "Trello returned invalid JSON for list cards")
                if not isinstance(list_cards, list):
                    list_cards = []
                rows = []
                for c in list_cards:
                    if not isinstance(c, dict):
                        continue
                    cid = c.get("id")
                    if not cid:
                        continue
                    if cid == ticket_id and source_list == target_lane:
                        continue
                    try:
                        rows.append({"id": cid, "pos": float(c.get("pos", 0))})
                    except (TypeError, ValueError):
                        rows.append({"id": cid, "pos": 0.0})
                rows.sort(key=lambda x: x["pos"])
                n = len(rows)
                pos_idx = max(0, min(int(payload.position), n))
                if n == 0:
                    new_pos = 16384.0
                elif pos_idx == 0:
                    new_pos = max(0.5, rows[0]["pos"] / 2.0)
                elif pos_idx >= n:
                    new_pos = rows[-1]["pos"] + 16384.0
                else:
                    lo, hi = rows[pos_idx - 1]["pos"], rows[pos_idx]["pos"]
                    new_pos = (lo + hi) / 2.0 if hi > lo else lo + 1.0
                put_params = {"key": key, "token": token, "idList": target_lane, "pos": new_pos}
                pr = req_lib.put(
                    f"https://api.trello.com/1/cards/{ticket_id}",
                    params=put_params,
                    timeout=15,
                )
                if pr.status_code != 200:
                    raise HTTPException(pr.status_code, f"Trello move failed: {pr.text[:500]}")
                _invalidate_external_board_detail_cache(provider, board_id)
                return JSONResponse({"success": True})
            raise HTTPException(
                404,
                "No Trello account could access this card. Check that the correct Trello account is connected.",
            )

        # Jira
        import requests as req_lib
        from requests.auth import HTTPBasicAuth

        move_fail_http: Optional[Tuple[int, str]] = None

        for acct in accounts:
            if acct.get("provider", "").lower() != "jira" or not acct.get("email") or not acct.get("api_token"):
                continue
            try:
                domain = acct.get("domain") or ""
                if not domain:
                    server_url = (acct.get("server_url") or "").strip().rstrip("/")
                    if server_url:
                        domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                if not domain:
                    continue
                auth = HTTPBasicAuth(acct["email"], acct["api_token"])
                base_url = f"https://{domain}" if not domain.startswith("http") else domain
                cr = req_lib.get(
                    f"{base_url}/rest/agile/1.0/board/{board_id}/configuration",
                    auth=auth,
                    headers={"Accept": "application/json"},
                    timeout=10,
                )
                if cr.status_code != 200:
                    continue
                try:
                    cfg = cr.json()
                except Exception:
                    logger.warning(
                        "Jira board configuration JSON parse failed (HTTP %s, body prefix=%r)",
                        cr.status_code,
                        (cr.text or "")[:200],
                        exc_info=True,
                    )
                    continue
                if not isinstance(cfg, dict):
                    continue
                target_status_ids = set()
                target_status_names_lower = set()
                column_found = False
                target_lower = target_lane.lower()
                for col in _jira_board_column_config_columns(cfg):
                    if not isinstance(col, dict):
                        continue
                    if (col.get("name") or "").strip().lower() != target_lower:
                        continue
                    column_found = True
                    statuses = col.get("statuses")
                    if not isinstance(statuses, list):
                        statuses = []
                    for st in statuses:
                        if not isinstance(st, dict):
                            continue
                        sid = st.get("id")
                        if sid is not None:
                            target_status_ids.add(str(sid))
                        sname = (st.get("name") or "").strip().lower()
                        if sname:
                            target_status_names_lower.add(sname)
                    break
                if not column_found:
                    move_fail_http = (400, f"Unknown board column: {target_lane}")
                    continue

                chosen_transition_id = None
                for api_ver in ("3", "2"):
                    tr = req_lib.get(
                        f"{base_url}/rest/api/{api_ver}/issue/{ticket_id}/transitions",
                        auth=auth,
                        headers={"Accept": "application/json"},
                        timeout=10,
                    )
                    if tr.status_code != 200:
                        continue
                    try:
                        tr_body = tr.json()
                    except Exception:
                        continue
                    if not isinstance(tr_body, dict):
                        continue
                    transitions = tr_body.get("transitions")
                    if not isinstance(transitions, list):
                        continue
                    for t in transitions:
                        if not isinstance(t, dict):
                            continue
                        to_raw = t.get("to")
                        to = to_raw if isinstance(to_raw, dict) else {}
                        tid = str(to.get("id", ""))
                        tnm = (to.get("name") or "").strip().lower()
                        if tid in target_status_ids or tnm in target_status_names_lower or tnm == target_lower:
                            chosen_transition_id = t.get("id")
                            break
                    if chosen_transition_id:
                        break
                if not chosen_transition_id:
                    move_fail_http = (
                        400,
                        "No workflow transition available into that column for this issue (check permissions or workflow).",
                    )
                    continue
                try:
                    pr = req_lib.post(
                        f"{base_url}/rest/api/2/issue/{ticket_id}/transitions",
                        json={"transition": {"id": str(chosen_transition_id)}},
                        auth=auth,
                        headers={"Accept": "application/json", "Content-Type": "application/json"},
                        timeout=15,
                    )
                except req_lib.exceptions.RequestException as e:
                    logger.warning("Jira transition request failed: %s", e)
                    continue
                if pr.status_code not in (200, 204):
                    move_fail_http = (pr.status_code, f"Jira transition failed: {pr.text[:500]}")
                    continue
                _invalidate_external_board_detail_cache(provider, board_id)
                return JSONResponse({"success": True})
            except HTTPException:
                raise
            except Exception:
                logger.exception(
                    "Jira move unexpected error (board_id=%s ticket_id=%s account=%s)",
                    board_id,
                    ticket_id,
                    acct.get("email"),
                )
                continue

        if move_fail_http:
            raise HTTPException(move_fail_http[0], move_fail_http[1])
        raise HTTPException(404, "No valid Jira account found")

    @router.post("/tickets/external-boards/{provider}/{ext_ticket_id}/attach")
    async def attach_to_external_ticket(provider: str, ext_ticket_id: str, file: UploadFile = File(...)):
        """Upload a file attachment to a Trello card or Jira issue."""
        if provider not in ("trello", "jira"):
            raise HTTPException(400, "Provider must be 'trello' or 'jira'")
        try:
            from distr.core.settings import load_settings_from_db
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            import json
            accounts = json.loads(raw) if isinstance(raw, str) else (raw if isinstance(raw, list) else [])
        except Exception:
            accounts = []

        file_content = await file.read()
        file_name = file.filename or "attachment"

        if provider == "trello":
            for acct in accounts:
                if acct.get("provider", "").lower() == "trello" and acct.get("api_key") and acct.get("api_token"):
                    import requests as req_lib
                    r = req_lib.post(
                        f"https://api.trello.com/1/cards/{ext_ticket_id}/attachments",
                        params={"key": acct["api_key"], "token": acct["api_token"]},
                        files={"file": (file_name, file_content, file.content_type or "application/octet-stream")},
                        timeout=30
                    )
                    if r.status_code not in (200, 201):
                        raise HTTPException(r.status_code, f"Trello attachment error: {r.text}")
                    return JSONResponse({"success": True, "attachment": r.json()})
            raise HTTPException(404, "No valid Trello account found")

        elif provider == "jira":
            for acct in accounts:
                if acct.get("provider", "").lower() == "jira" and acct.get("email") and acct.get("api_token"):
                    import requests as req_lib
                    from requests.auth import HTTPBasicAuth
                    domain = acct.get("domain") or ""
                    if not domain:
                        server_url = (acct.get("server_url") or "").strip().rstrip("/")
                        if server_url:
                            domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                    if not domain:
                        continue
                    auth = HTTPBasicAuth(acct["email"], acct["api_token"])
                    base_url = f"https://{domain}" if not domain.startswith("http") else domain
                    # Jira uses multipart for attachments
                    r = req_lib.post(
                        f"{base_url}/rest/api/2/issue/{ext_ticket_id}/attachments",
                        auth=auth,
                        headers={"X-Atlassian-Token": "no-check"},
                        files={"file": (file_name, file_content, file.content_type or "application/octet-stream")},
                        timeout=30
                    )
                    if r.status_code not in (200, 201):
                        raise HTTPException(r.status_code, f"Jira attachment error: {r.text}")
                    return JSONResponse({"success": True})
            raise HTTPException(404, "No valid Jira account found")

        raise HTTPException(400, "Unsupported provider")

    @router.get("/tickets/external-boards/{provider}/proxy-image")
    async def proxy_external_image(provider: str, url: str = ""):
        """Proxy an external image URL that requires authentication (Jira attachments)."""
        loop = asyncio.get_running_loop()
        try:
            body, content_type = await loop.run_in_executor(
                None,
                lambda: _proxy_external_image_sync(provider, url),
            )
        except HTTPException:
            raise
        return Response(content=body, media_type=content_type)

    # ── Linkable entities (for linking tickets to workflows/projects/etc.) ──

    @router.get("/tickets/linkable")
    async def get_linkable_entities():
        """Return lists of workflows, projects, actions for linking.

        Workflow options match **Workflows** page lists: same ``list_workflows`` helper,
        which excludes internal chat audit workflows (``workflow_type == 'audit'``).
        """
        from distr.core.workflow.service import list_workflows

        auto_rows = list_workflows(limit=500, search=None, workflow_type=None)
        workflows = [
            {"id": w["id"], "title": w["name"] or f"Workflow #{w['id']}"}
            for w in auto_rows
        ]
        with get_session() as s:
            from distr.core.db import Action
            from distr.core.db.projects import Project
            projects = [{"id": p.id, "name": p.name} for p in s.query(Project).all()]
            actions = [{"id": a.id, "title": a.title or f"Action #{a.id}"} for a in s.query(Action).all()]
            return JSONResponse({"workflows": workflows, "projects": projects, "actions": actions})

    # ── External boards (Trello / Jira) ──

    @router.get("/tickets/external-boards")
    async def get_external_boards():
        """Fetch Trello and Jira boards from connected accounts, enriched with local config."""
        trello_boards = []
        jira_boards = []
        try:
            from distr.core.settings import load_settings_from_db
            import json as _json
            settings = load_settings_from_db()
            raw = settings.get("connected_accounts") or "[]"
            if isinstance(raw, str):
                try:
                    accounts = _json.loads(raw)
                except Exception:
                    accounts = []
            else:
                accounts = raw if isinstance(raw, list) else []
            if not accounts:
                logger.info("External boards: no connected accounts found")
                return JSONResponse({"trello": [], "jira": []})
            # Load local config for external boards
            local_configs = {}
            with get_session() as s:
                external_config_boards = s.query(KanbanBoard).filter(KanbanBoard.source.in_(["trello", "jira"])).all()
                project_ids = {int(b.default_project_id) for b in external_config_boards if b.default_project_id}
                projects = {
                    p.id: p
                    for p in s.query(Project).filter(Project.id.in_(project_ids)).all()
                } if project_ids else {}
                for b in external_config_boards:
                    key = f"{b.source}:{b.external_board_id}"
                    default_project = projects.get(b.default_project_id)
                    local_configs[key] = {
                        "local_id": b.id,
                        "has_local_config": True,
                        "modified_date": _external_board_activity_iso(b.modified_date),
                        "default_project_id": b.default_project_id,
                        **_project_context_payload(default_project, "default"),
                        "default_workflow_id": b.default_workflow_id,
                        "color": b.color,
                        "can_create_ticket": True,
                    }
            logger.info("External boards: found %d connected accounts", len(accounts))
            for acct in accounts:
                provider = acct.get("provider", "").lower()
                if provider == "trello" and acct.get("api_key") and acct.get("api_token") and acct.get("is_valid", True):
                    try:
                        import requests
                        resp = requests.get(
                            "https://api.trello.com/1/members/me/boards",
                            params={"key": acct["api_key"], "token": acct["api_token"], "fields": "name,url,closed"},
                            timeout=10,
                        )
                        if resp.status_code == 200:
                            for b in resp.json():
                                if not b.get("closed", False):
                                    config = local_configs.get(f"trello:{b['id']}", {})
                                    board_data = {"id": b["id"], "name": b["name"], "url": b.get("url", ""), "can_create_ticket": True}
                                    board_data.update(config)
                                    trello_boards.append(board_data)
                    except Exception as e:
                        logger.warning("Trello board fetch failed: %s", e)
                elif provider == "jira" and acct.get("email") and acct.get("api_token") and acct.get("is_valid", True):
                    domain = acct.get("domain") or ""
                    if not domain:
                        server_url = (acct.get("server_url") or "").strip().rstrip("/")
                        if server_url:
                            domain = server_url.replace("https://", "").replace("http://", "").split("/")[0]
                    if not domain:
                        continue
                    try:
                        import requests
                        from requests.auth import HTTPBasicAuth
                        base_url = f"https://{domain}"
                        resp = requests.get(
                            f"{base_url}/rest/agile/1.0/board",
                            auth=HTTPBasicAuth(acct["email"], acct["api_token"]),
                            headers={"Accept": "application/json"},
                            timeout=10,
                        )
                        if resp.status_code == 200:
                            for b in resp.json().get("values", []):
                                config = local_configs.get(f"jira:{b['id']}", {})
                                board_data = {
                                    "id": str(b["id"]), "name": b["name"],
                                    "url": f"https://{domain}/jira/software/projects/{b.get('location', {}).get('projectKey', '')}/boards/{b['id']}",
                                    "can_create_ticket": True,
                                }
                                board_data.update(config)
                                jira_boards.append(board_data)
                    except Exception as e:
                        logger.warning("Jira board fetch failed: %s", e)
        except Exception as e:
            logger.warning("External board fetch error: %s", e)
        return JSONResponse({
            "trello": _sort_external_board_list(trello_boards),
            "jira": _sort_external_board_list(jira_boards),
        })

    @router.post("/tickets/external-boards/{provider}/{ext_board_id}/touch")
    async def touch_external_board(provider: str, ext_board_id: str):
        """Record recent activity for a configured external board (sidebar ordering)."""
        if provider not in ("trello", "jira"):
            raise HTTPException(400, "Provider must be 'trello' or 'jira'")
        with get_session() as s:
            board = (
                s.query(KanbanBoard)
                .filter(
                    KanbanBoard.source == provider,
                    KanbanBoard.external_board_id == ext_board_id,
                )
                .first()
            )
            if not board:
                return JSONResponse({"success": False})
            board.modified_date = datetime.utcnow()
            s.flush()
            return JSONResponse({
                "success": True,
                "local_id": board.id,
                "modified_date": _external_board_activity_iso(board.modified_date),
            })

    @router.get("/tickets/external-boards/{provider}/{board_id}/local-config")
    async def get_external_board_local_config(provider: str, board_id: str):
        """Return local DB config for an external board without remote API calls."""
        if provider not in ("trello", "jira"):
            raise HTTPException(400, "Provider must be 'trello' or 'jira'")

        with get_session() as s:
            local_board = (
                s.query(KanbanBoard)
                .filter(
                    KanbanBoard.source == provider,
                    KanbanBoard.external_board_id == board_id,
                )
                .first()
            )
            if not local_board:
                return JSONResponse(
                    {
                        "provider": provider,
                        "external_board_id": board_id,
                        "local_id": None,
                        "name": None,
                        "default_project_id": None,
                        "default_project_name": None,
                        "default_project_folder": None,
                        "default_workflow_id": None,
                        "color": None,
                    }
                )
            default_project = orm_get_by_id(s, Project, local_board.default_project_id) if local_board.default_project_id else None

            return JSONResponse(
                {
                    "provider": provider,
                    "external_board_id": board_id,
                    "local_id": local_board.id,
                    "name": local_board.name,
                    "default_project_id": local_board.default_project_id,
                    **_project_context_payload(default_project, "default"),
                    "default_workflow_id": local_board.default_workflow_id,
                    "color": local_board.color,
                }
            )

    def _merge_external_board_local_config(provider: str, board_id: str, body: dict) -> dict:
        out = dict(body or {})
        with get_session() as s:
            lb = (
                s.query(KanbanBoard)
                .filter(
                    KanbanBoard.source == provider,
                    KanbanBoard.external_board_id == board_id,
                )
                .first()
            )
            if lb:
                default_project = orm_get_by_id(s, Project, lb.default_project_id) if lb.default_project_id else None
                out.update(
                    {
                        "local_id": lb.id,
                        "default_project_id": lb.default_project_id,
                        **_project_context_payload(default_project, "default"),
                        "default_workflow_id": lb.default_workflow_id,
                        "color": lb.color,
                    }
                )
        return out

    @router.get("/tickets/external-boards/{provider}/{board_id}")
    async def get_external_board_detail(
        provider: str,
        board_id: str,
        force_refresh: bool = False,
    ):
        """Return cached external board immediately; refresh from Trello/Jira in a background thread when stale.

        Set force_refresh=true (query) when the user hits Re-sync: fetches and returns a fresh snapshot so
        board counts/cards match the provider after the request completes.
        """
        if provider not in ("trello", "jira"):
            raise HTTPException(400, "Provider must be 'trello' or 'jira'")
        key = _external_board_detail_cache_key(provider, board_id)
        if force_refresh:
            _invalidate_external_board_detail_cache(provider, board_id)
            body = await asyncio.to_thread(_build_external_board_detail_payload, provider, board_id)
            with _BOARD_DETAIL_LOCK:
                _BOARD_DETAIL_CACHE[key] = {"ready": True, "t": time.time(), "body": body}
            out = _merge_external_board_local_config(provider, board_id, body)
            out["cache_ready"] = True
            out["cache_stale"] = False
            return JSONResponse(out)
        now = time.time()
        with _BOARD_DETAIL_LOCK:
            ent = _BOARD_DETAIL_CACHE.get(key)
        if ent and ent.get("ready"):
            out = _merge_external_board_local_config(provider, board_id, ent["body"])
            age = now - ent["t"]
            out["cache_ready"] = True
            out["cache_stale"] = age > _BOARD_DETAIL_STALE_AFTER_SEC
            if out["cache_stale"]:
                _schedule_external_board_detail_refresh(provider, board_id, key)
            return JSONResponse(out)

        _schedule_external_board_detail_refresh(provider, board_id, key)
        loading = {"name": "", "url": "", "lanes": [], "can_create_ticket": True, "cache_ready": False}
        return JSONResponse(_merge_external_board_local_config(provider, board_id, loading))

    class EngageOrchestratorRequest(BaseModel):
        chat_id: int
        ticket: dict
        is_local: bool = True
        board_id: Optional[int] = None
        local_board_id: Optional[int] = None
        source: str = "database"
        board_name: str = ""

    @router.post("/tickets/tickets/engage-orchestrator")
    async def engage_ticket_orchestrator(body: EngageOrchestratorRequest):
        """Send a ticket to the chat orchestrator with a brief visible line and a hidden agent prompt."""
        chat_id = int(body.chat_id)
        if chat_id < 1:
            raise HTTPException(400, "chat_id is required")

        board_data: dict = {}
        local_board_id = body.local_board_id or body.board_id
        if local_board_id:
            with get_session() as s:
                board = orm_get_by_id(s, KanbanBoard, int(local_board_id))
                if board:
                    board_data = {
                        "name": board.name or "",
                        "default_project_id": board.default_project_id,
                    }
                    if board.default_project_id:
                        project = orm_get_by_id(s, Project, board.default_project_id)
                        if project:
                            board_data["default_project_name"] = project.name or ""
                            board_data["default_project_folder"] = project.folder_location or ""

        from distr.core.kanban.ticket_orchestrator_engagement import (
            activate_engagement_context,
            build_orchestrator_messages,
            emit_ticket_engagement_memory_event,
            send_ticket_engagement_to_agent,
        )

        board_data = activate_engagement_context(
            local_board_id=int(local_board_id) if local_board_id else None,
            ticket=body.ticket,
            board_data=board_data,
        )
        board_label = body.board_name or board_data.get("name") or board_data.get("activated_board_name") or ""

        display_message, agent_message = build_orchestrator_messages(
            body.ticket,
            is_local=bool(body.is_local),
            board_label=board_label,
            source=(body.source or "database").strip() or "database",
            board_data=board_data,
        )
        try:
            from distr.core.settings import load_settings_from_db, save_settings_to_db

            settings = load_settings_from_db()
            settings["last_chat_id"] = chat_id
            settings["agent_current_chat_id"] = chat_id
            save_settings_to_db(settings)

            project_id = (
                body.ticket.get("linked_project_id")
                or board_data.get("activated_project_id")
                or board_data.get("default_project_id")
            )
            emit_ticket_engagement_memory_event(
                ticket=body.ticket,
                is_local=bool(body.is_local),
                board_id=int(local_board_id) if local_board_id else None,
                project_id=int(project_id) if project_id else None,
                display_message=display_message,
            )

            send_ticket_engagement_to_agent(
                chat_id,
                display_message,
                agent_message,
                speak=True,
                board_label=board_label,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.exception("engage-orchestrator failed chat_id=%s", chat_id)
            raise HTTPException(500, "Could not send ticket to orchestrator") from exc

        return JSONResponse(
            {
                "sent": True,
                "chat_id": chat_id,
                "display_message": display_message,
            }
        )

    # ── Send ticket to project (.tickets folder) ──

    @router.post("/tickets/tickets/{ticket_id}/send-to-project")
    async def send_ticket_to_project(ticket_id: int):
        """Create a .tickets/ticket_*.md file in the linked project's folder from a Ticket Board ticket."""
        logger.info("send-to-project: request ticket_id=%s", ticket_id)
        try:
            with get_session() as s:
                return _send_ticket_to_project_impl(s, ticket_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("send-to-project: unhandled failure ticket_id=%s", ticket_id)
            raise HTTPException(
                500,
                "Send to project failed (see server log). "
                f"{type(e).__name__}: {e}",
            ) from e

    def _send_ticket_to_project_impl(s, ticket_id: int) -> JSONResponse:
        t = orm_get_by_id(s, KanbanTicket, ticket_id)
        if not t:
            logger.warning("send-to-project: ticket not found ticket_id=%s", ticket_id)
            raise HTTPException(404, "Ticket not found")

        # Resolve project: ticket-level first, then board-level default
        project_id = t.linked_project_id
        if not project_id:
            lane = orm_get_by_id(s, KanbanLane, t.lane_id)
            if lane:
                board = orm_get_by_id(s, KanbanBoard, lane.board_id)
                if board:
                    project_id = board.default_project_id

        if not project_id:
            logger.warning(
                "send-to-project: no project linked ticket_id=%s lane_id=%s",
                ticket_id,
                t.lane_id,
            )
            raise HTTPException(400, "No project linked to this ticket or its board")

        from distr.core.db.projects import Project
        project = orm_get_by_id(s, Project, project_id)
        if not project:
            logger.warning("send-to-project: project row missing ticket_id=%s project_id=%s", ticket_id, project_id)
            raise HTTPException(404, "Linked project not found")
        if not project.folder_location:
            logger.warning(
                "send-to-project: project has no folder_location ticket_id=%s project_id=%s name=%s",
                ticket_id,
                project_id,
                project.name,
            )
            raise HTTPException(400, f"Project '{project.name}' has no folder location set")

        proj_root = os.path.abspath(os.path.expanduser(project.folder_location.strip()))
        if not os.path.isdir(proj_root):
            logger.warning(
                "send-to-project: project folder not a directory ticket_id=%s path=%r",
                ticket_id,
                proj_root,
            )
            raise HTTPException(
                400,
                f"Project folder is missing or not a directory: {proj_root!r}. "
                "Fix the project path in Projects, then try again.",
            )

        # Build the markdown ticket file
        tickets_folder = os.path.join(proj_root, ".tickets")
        try:
            os.makedirs(tickets_folder, exist_ok=True)
        except OSError as e:
            logger.exception("send-to-project: cannot create .tickets under %s", proj_root)
            raise HTTPException(500, f"Cannot create .tickets folder: {e}") from e

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ticket_filename = f"ticket_{timestamp}.md"
        ticket_path = os.path.join(tickets_folder, ticket_filename)

        # Gather sub-items (null-safe strings for markdown)
        todos_md = ""
        if t.todos:
            todos_md = "\n## Checklist\n"
            for td in t.todos:
                mark = "x" if td.done else " "
                todos_md += f"- [{mark}] {(td.text or '').strip()}\n"

        links_md = ""
        if t.links:
            links_md = "\n## Links\n"
            for lk in t.links:
                lt = (lk.title or "").strip() or "link"
                lu = (lk.url or "").strip()
                links_md += f"- [{lt}]({lu})\n"

        files_md = ""
        if t.files:
            files_md = "\n## Attached Files\n"
            for fl in t.files:
                fn = (fl.filename or "").strip() or "file"
                fp = (fl.file_path or "").strip()
                files_md += f"- {fn} (`{fp}`)\n"

        desc_raw = t.description or ""
        try:
            desc_body = (
                _html_to_plain_ticket_description(desc_raw)
                if "<" in desc_raw
                else (desc_raw.strip() or "(no description)")
            )
        except Exception as e:
            logger.warning("send-to-project: description plain-text failed: %s", e)
            desc_body = desc_raw.strip() or "(no description)"
        import_md = ""
        import_warn: Optional[str] = None
        ext_src = (t.external_source or "").lower().strip()
        ext_id = (t.external_id or "").strip()
        if ext_src in ("jira", "trello") and ext_id:
            try:
                if ext_src == "jira":
                    import_md, import_warn = _download_jira_issue_attachments_for_project(
                        proj_root, timestamp, ext_id
                    )
                else:
                    import_md, import_warn = _download_trello_card_attachments_for_project(
                        proj_root, timestamp, ext_id
                    )
            except Exception as e:
                logger.exception("send-to-project: attachment import failed (continuing) ticket_id=%s", ticket_id)
                import_md = f"\n## Attachments\n_(import failed before ticket write: {e})_\n"
        if import_warn:
            logger.warning(
                "send-to-project: attachment import note ticket_id=%s ext=%s/%s: %s",
                ticket_id,
                ext_src,
                ext_id,
                import_warn,
            )

        content = f"""---
id: ticket_{timestamp}
title: {_yaml_scalar(t.title or "")}
project: {_yaml_scalar(project.name or "")}
created: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
priority: {_yaml_scalar(t.priority or "medium")}
status: open
source: kanban_ticket_{t.id}
---

## Description
{desc_body}
{import_md}{todos_md}{links_md}{files_md}
## Context
- **Project:** {project.name} (ID: {project.id})
- **Folder:** `{proj_root}`
- **Kanban Ticket ID:** {t.id}

---
*Sent from Ticket Board via DecisionsAI*
"""

        try:
            with open(ticket_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.exception("send-to-project: failed to write %s", ticket_path)
            raise HTTPException(500, f"Failed to write ticket file: {e}") from e

        logger.info(
            "send-to-project: ok ticket_id=%s project_id=%s path=%s content_bytes=%s",
            t.id,
            project.id,
            ticket_path,
            len(content.encode("utf-8")),
        )
        return JSONResponse({
            "success": True,
            "file_path": ticket_path,
            "project_name": project.name,
        })

    # ── Send ticket to CLI ──

    @router.post("/tickets/tickets/{ticket_id}/send-to-workflow")
    async def send_ticket_to_workflow(ticket_id: int, payload: SendToWorkflowRequest):
        """Start the selected/default workflow for a ticket."""
        from distr.core.workflow.service import start_workflow_run

        with get_session() as s:
            t = orm_get_by_id(s, KanbanTicket, ticket_id)
            if not t:
                raise HTTPException(404, "Ticket not found")

            lane = orm_get_by_id(s, KanbanLane, t.lane_id) if t.lane_id else None
            board = orm_get_by_id(s, KanbanBoard, lane.board_id) if lane else None
            # Keep only scalar identifiers/names to avoid detached ORM access outside session scope.
            board_id_value = (lane.board_id if lane else None)
            board_name_value = board.name if board else None
            board_default_workflow_id = board.default_workflow_id if board else None

            workflow_id = payload.workflow_id or t.linked_workflow_id or board_default_workflow_id
            if not workflow_id:
                raise HTTPException(400, "No workflow linked to this ticket or board")

            if payload.workflow_id:
                t.linked_workflow_id = payload.workflow_id
                s.flush()

            project_id_value = str(t.linked_project_id) if t.linked_project_id else None
            context = f"Ticket: {t.title}"
            workflow_brief = None
            try:
                from distr.core.kanban.ticket_workflow_brief import (
                    build_ticket_workflow_brief,
                    render_ticket_workflow_brief,
                )
                workflow_brief = build_ticket_workflow_brief(
                    s,
                    t.id,
                    board_id=board_id_value,
                    board_name=board_name_value,
                    project_id=project_id_value,
                )
                context = render_ticket_workflow_brief(workflow_brief)
            except Exception:
                logger.debug("send-to-workflow: structured brief failed", exc_info=True)
                if t.description:
                    context += f"\n\nDescription: {t.description}"

            run_metadata = {
                "source_type": "ticket_send_to_workflow",
                "board_id": board_id_value,
                "board_name": board_name_value,
                "ticket_id": t.id,
                "ticket_title": t.title or "",
                "project_id": project_id_value,
                "project_name": None,
                "phase": "planning",
            }
            if workflow_brief:
                run_metadata["ticket_workflow_brief"] = workflow_brief

            run_result = start_workflow_run(
                workflow_id,
                context=context,
                board_id=board_id_value,
                ticket_id=ticket_id,
                run_metadata=run_metadata,
            )
            if "error" in run_result:
                raise HTTPException(400, run_result["error"])
        return JSONResponse({
            "success": True,
            "message": f"Ticket #{ticket_id} sent to workflow.",
            "workflow_id": workflow_id,
            "run_id": run_result.get("run_id"),
        })

    @router.get("/tickets/tickets/{ticket_id}/active-run")
    async def get_ticket_active_run(ticket_id: int):
        """Return the active workflow run for a ticket, or {active: false} if none."""
        from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep, AutoWorkflow as _WF
        with get_session() as s:
            run = (
                s.query(AutoWorkflowRun)
                .filter(
                    AutoWorkflowRun.ticket_id == ticket_id,
                    AutoWorkflowRun.status.in_(["running", "waiting"]),
                )
                .order_by(AutoWorkflowRun.started_at.desc())
                .first()
            )
            if not run:
                return JSONResponse({"active": False})
            step_name = None
            if run.current_step_id:
                step = s.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.id == run.current_step_id).first()
                if step:
                    step_name = step.name
            wf = s.query(_WF).filter(_WF.id == run.workflow_id).first()
            run_data = {}
            try:
                run_data = json.loads(run.run_data or "{}")
            except Exception:
                pass
            return JSONResponse({
                "active": True,
                "run_id": run.id,
                "workflow_id": run.workflow_id,
                "workflow_name": wf.name if wf else None,
                "status": run.status,
                "current_step_name": step_name,
                "phase": run_data.get("phase"),
            })

    def _resolve_ticket_cli_context(s, ticket_id: int):
        t = orm_get_by_id(s, KanbanTicket,ticket_id)
        if not t:
            raise HTTPException(404, "Ticket not found")

        title = t.title
        description = t.description or ""
        tid = t.id

        project_id = t.linked_project_id
        if not project_id:
            lane = orm_get_by_id(s, KanbanLane,t.lane_id)
            if lane:
                board = orm_get_by_id(s, KanbanBoard,lane.board_id)
                if board:
                    project_id = board.default_project_id

        if not project_id:
            raise HTTPException(400, "No project linked to this ticket or its board")

        from distr.core.db.projects import Project
        project = orm_get_by_id(s, Project,project_id)
        if not project or not project.folder_location:
            raise HTTPException(400, "Project has no folder location set")

        folder = project.folder_location
        project_name = project.name
        complexity = normalize_ticket_complexity(t.complexity)

        from distr.core.kanban.ticket_cli_context import build_kanban_ticket_cli_instruction

        instruction = build_kanban_ticket_cli_instruction(
            s,
            tid,
            project_name=project_name,
            project_folder=folder or "",
            project_id=project_id,
        )
        return {
            "ticket": t,
            "title": title,
            "description": description,
            "ticket_id": tid,
            "project": project,
            "project_id": project_id,
            "project_name": project_name,
            "folder": folder,
            "complexity": complexity,
            "instruction": instruction,
            "board": (
                orm_get_by_id(s, KanbanBoard, lane.board_id)
                if (lane := orm_get_by_id(s, KanbanLane, t.lane_id)) and lane.board_id
                else None
            ),
        }

    def _resolve_ticket_execution_route(s, ctx: dict) -> dict:
        from distr.core.hermes_orchestrator import resolve_execution_route

        decision = resolve_execution_route(
            project=ctx["project"],
            ticket=ctx.get("ticket"),
            board=ctx.get("board"),
            complexity=ctx.get("complexity"),
            emit_event=False,
        )
        return decision.to_route_dict()

    @router.get("/tickets/tickets/{ticket_id}/cli-context")
    async def get_ticket_cli_context(ticket_id: int):
        """Return the resolved project/backend context and generated CLI instruction for a ticket."""
        with get_session() as s:
            ctx = _resolve_ticket_cli_context(s, ticket_id)
            project = ctx["project"]
            from distr.core.project_cli_backends import get_backend_statuses, get_project_backend_id

            route = _resolve_ticket_execution_route(s, ctx)
            active_backend = route.get("backend") or get_project_backend_id(project)
            return JSONResponse({
                "ticket_id": ctx["ticket_id"],
                "title": ctx["title"],
                "project_id": ctx["project_id"],
                "project_name": ctx["project_name"],
                "project_folder": ctx["folder"],
                "complexity": ctx["complexity"],
                "instruction": ctx["instruction"],
                "backend_id": active_backend,
                "model": route.get("model") or "auto",
                "route_source": route.get("source") or "policy",
                "route_rationale": route.get("rationale") or "",
                "requires_approval": bool(route.get("requires_approval")),
                "codex_reasoning_effort": route.get("codex_reasoning_effort") or "",
                "codex_service_tier": route.get("codex_service_tier") or "",
                **get_backend_statuses(active_backend),
            })

    @router.post("/tickets/tickets/{ticket_id}/send-to-cli")
    async def send_ticket_to_cli(ticket_id: int, payload: Optional[SendToCliRequest] = None):
        """Send a ticket's instruction to the selected project coding backend."""

        payload = payload or SendToCliRequest()
        with get_session() as s:
            ctx = _resolve_ticket_cli_context(s, ticket_id)
            title = ctx["title"]
            tid = ctx["ticket_id"]
            project_id = ctx["project_id"]
            project_name = ctx["project_name"]
            complexity = ctx["complexity"]
            instruction = (payload.instruction or "").strip() or ctx["instruction"]

        # Create audit trail using AutoWorkflow models
        from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep
        audit_id = step_id = None
        try:
            with get_session() as s:
                audit = AutoWorkflow(
                    name=f"[Project: {project_name}] Ticket #{tid}: {title}",
                    status="in_progress", workflow_type="project_cli",
                )
                s.add(audit)
                s.flush()
                step = AutoWorkflowStep(
                    workflow_id=audit.id, position=0,
                    name=f"Ticket #{tid}", instruction=instruction[:500],
                    status="running", tool_used="project_cli",
                )
                s.add(step)
                s.commit()
                audit_id, step_id = audit.id, step.id
        except Exception:
            pass

        from distr.core.db.projects import Project
        from distr.core.project_cli_backends.registry import run_project_task

        try:
            with get_session() as s:
                project = orm_get_by_id(s, Project, project_id)
                if not project:
                    raise HTTPException(400, "Project no longer exists")
                route = _resolve_ticket_execution_route(s, ctx)
            backend_override = (payload.backend_id or "").strip() or route.get("backend")
            model_override = (payload.model or "").strip() or route.get("model", "")
            if (model_override or "").lower() in ("", "auto"):
                model_override = None
            codex_reasoning_effort = (
                (payload.codex_reasoning_effort or "").strip()
                or route.get("codex_reasoning_effort")
                or None
            )
            codex_service_tier = (
                (payload.codex_service_tier or "").strip()
                or route.get("codex_service_tier")
                or None
            )
            with get_session() as s:
                project = orm_get_by_id(s, Project, project_id)
                if not project:
                    raise HTTPException(400, "Project no longer exists")
                result = await run_project_task(
                    project,
                    instruction,
                    audit_id=audit_id,
                    workflow_id=payload.workflow_id,
                    step_id=step_id,
                    ticket_id=tid,
                    ticket_complexity=complexity,
                    origin="kanban_ticket",
                    backend_id_override=backend_override,
                    model_override=model_override,
                    codex_reasoning_effort_override=codex_reasoning_effort,
                    codex_service_tier_override=codex_service_tier,
                )
        except Exception as e:
            logger.error(f"Failed to send ticket to project backend: {e}")
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        return JSONResponse({
            "success": result.success,
            "message": f"Ticket #{tid} sent to the project backend for '{project_name}'. Check the ticket execution trail for progress.",
            "audit_id": audit_id,
            "execution_session_id": result.execution_session_id,
            "backend_id": result.backend_id,
            "engine": result.engine,
            "error": result.error,
        })

    # ── WhatsApp ↔ Board Integration ──

    @router.get("/tickets/boards/{board_id}/whatsapp-links")
    async def get_whatsapp_links(board_id: int):
        """Get WhatsApp phone numbers linked to this board."""
        from distr.core.db import WhatsAppPhoneLink
        with get_session() as s:
            links = s.query(WhatsAppPhoneLink).filter_by(board_id=board_id).all()
            return JSONResponse([{
                "id": l.id,
                "board_id": l.board_id,
                "phone_jid": l.phone_jid,
                "phone_number": l.phone_number or "",
                "contact_name": l.contact_name or "",
                "auto_snapshot": l.auto_snapshot or False,
            } for l in links])

    @router.post("/tickets/boards/{board_id}/whatsapp-snapshot-ticket")
    async def create_board_whatsapp_snapshot_ticket(board_id: int, payload: dict):
        """Create a board ticket from the unticketed messages in its linked WhatsApp chat."""
        link_id = payload.get("link_id")
        message_ids = payload.get("message_ids") if isinstance(payload.get("message_ids"), list) else None
        try:
            limit = max(1, min(int(payload.get("limit") or 500), 500))
        except Exception:
            limit = 500

        with get_session() as s:
            snapshot = _resolve_board_whatsapp_snapshot(s, board_id, link_id=link_id, limit=limit, message_ids=message_ids)
            board = snapshot["board"]
            link = snapshot["link"]
            messages = snapshot["messages"]
            lane = snapshot["lane"]
            requested_lane_id = payload.get("lane_id")
            if requested_lane_id:
                try:
                    requested_lane_id = int(requested_lane_id)
                except Exception:
                    requested_lane_id = None
                if requested_lane_id:
                    requested_lane = s.query(KanbanLane).filter_by(id=requested_lane_id, board_id=board.id).first()
                    if requested_lane:
                        lane = requested_lane

            enrichment = _ensure_whatsapp_messages_enriched(messages)
            draft = _build_whatsapp_ticket_draft(messages)
            media_count = len([m for m in messages if getattr(m, "media_type", None)])
            title = (payload.get("title") or draft.get("title") or "WhatsApp request").strip()
            description = (payload.get("description") or draft.get("description") or "").strip()
            priority = (payload.get("priority") or draft.get("priority") or _infer_whatsapp_ticket_priority(title, description)).strip()
            complexity = payload.get("complexity") or draft.get("complexity") or infer_ticket_complexity(title, description, file_count=media_count)
            quality = _validate_whatsapp_ticket_quality(title, description, messages, enrichment)
            if not quality["passed"]:
                return JSONResponse({
                    "success": False,
                    "detail": "WhatsApp ticket draft does not meet intake quality standards.",
                    "quality": quality,
                }, status_code=422)
            max_pos = max([t.position for t in lane.tickets], default=-1)
            first_msg = messages[0]
            last_msg = messages[-1]
            source_contact = (
                link.contact_name
                or _whatsapp_message_sender(first_msg)
                or link.phone_number
                or link.phone_jid
                or "WhatsApp"
            )
            ticket = KanbanTicket(
                lane_id=lane.id,
                title=title,
                description=description,
                priority=priority,
                complexity=normalize_ticket_complexity(complexity),
                position=max_pos + 1,
                linked_workflow_id=board.default_workflow_id,
                linked_project_id=board.default_project_id,
                linked_snippet_id=board.default_snippet_id,
                linked_action_id=board.default_action_id,
                whatsapp_message_id=last_msg.id,
                whatsapp_message_wa_id=last_msg.message_id,
                source_provider="whatsapp",
                source_external_id=last_msg.message_id,
                source_thread_id=last_msg.jid or link.phone_jid or link.phone_number,
                source_contact=source_contact,
                source_label="WhatsApp",
            )
            s.add(ticket)
            s.flush()

            snapshot_group = _whatsapp_snapshot_group_for_ticket(board.id, ticket.id)
            attached_count = 0
            source_message_ids = []
            for msg in messages:
                source_message_ids.append(int(msg.id))
                msg.processed = True
                msg.processed_date = datetime.utcnow()
                msg.snapshot_group = snapshot_group
                wa_disk = resolve_whatsapp_media_disk_path(msg.media_local_path or "")
                if wa_disk and os.path.exists(wa_disk):
                    safe_name = os.path.basename(wa_disk)
                    s.add(KanbanTicketFile(
                        ticket_id=ticket.id,
                        filename=msg.media_filename or safe_name,
                        file_path=wa_disk,
                        description=f"WhatsApp {msg.media_type}: {safe_name}" if msg.media_type else safe_name,
                    ))
                    attached_count += 1
            s.add(KanbanTicketAuditEntry(
                ticket_id=ticket.id,
                execution_lane="whatsapp",
                status="created",
                final_verdict="passed" if quality["passed"] else "failed",
                summary=f"WhatsApp intake ticket created from {len(messages)} message(s).",
                details=json.dumps({
                    "source": "whatsapp",
                    "board_id": board.id,
                    "board_name": board.name,
                    "link_id": link.id,
                    "source_message_ids": source_message_ids,
                    "snapshot_group": snapshot_group,
                    "quality": quality,
                    "media_enrichment": enrichment,
                }, ensure_ascii=False),
            ))
            s.flush()
            _emit_ticket_channel_intake(
                ticket,
                board=board,
                channel="whatsapp",
                extra_payload={
                    "link_id": link.id,
                    "message_count": len(messages),
                    "source_message_ids": source_message_ids,
                },
            )
            return JSONResponse({
                "success": True,
                "id": ticket.id,
                "lane_id": lane.id,
                "lane_name": lane.name,
                "board_id": board.id,
                "message_count": len(messages),
                "message_ids": source_message_ids,
                "file_count": attached_count,
                "contact_name": source_contact,
                "quality": quality,
            })

    @router.post("/tickets/boards/{board_id}/whatsapp-snapshot-preview")
    async def preview_board_whatsapp_snapshot_ticket(board_id: int, payload: dict):
        """Preview the unticketed WhatsApp messages that would become a board ticket."""
        link_id = payload.get("link_id")
        message_ids = payload.get("message_ids") if isinstance(payload.get("message_ids"), list) else None
        scope = payload.get("scope") or "new_since_last_ticket"
        try:
            limit = max(1, min(int(payload.get("limit") or 500), 500))
        except Exception:
            limit = 500
        try:
            since_hours = max(0, min(int(payload.get("since_hours") or 48), 24 * 30))
        except Exception:
            since_hours = 48

        with get_session() as s:
            snapshot = _resolve_board_whatsapp_snapshot(
                s,
                board_id,
                link_id=link_id,
                limit=limit,
                message_ids=message_ids,
                scope=scope,
                since_hours=since_hours,
                allow_empty=True,
            )
            board = snapshot["board"]
            link = snapshot["link"]
            messages = snapshot["messages"]
            lane = snapshot["lane"]
            intake_stats = snapshot.get("intake_stats") or {}
            if snapshot.get("empty"):
                return JSONResponse({
                    "success": True,
                    "empty": True,
                    "empty_reason": snapshot.get("empty_reason") or "no_unticketed_messages",
                    "board_id": board.id,
                    "board_name": board.name,
                    "lane_id": lane.id if lane else None,
                    "lane_name": lane.name if lane else "",
                    "link_id": link.id,
                    "contact_name": link.contact_name or link.phone_number or link.phone_jid or "WhatsApp",
                    "message_count": 0,
                    "message_ids": [],
                    "title": "",
                    "description": "",
                    "priority": "medium",
                    "complexity": "medium",
                    "media": [],
                    "quality": {"passed": False, "score": 0, "issues": [], "warnings": []},
                    "intake_stats": intake_stats,
                })
            enrichment = _ensure_whatsapp_messages_enriched(messages)
            draft = _build_whatsapp_ticket_draft(messages)
            quality = _validate_whatsapp_ticket_quality(
                draft.get("title") or "WhatsApp request",
                draft.get("description") or "",
                messages,
                enrichment,
            )
            s.flush()
            return JSONResponse({
                "success": True,
                "empty": False,
                "board_id": board.id,
                "board_name": board.name,
                "lane_id": lane.id,
                "lane_name": lane.name,
                "link_id": link.id,
                "contact_name": link.contact_name or link.phone_number or link.phone_jid or "WhatsApp",
                "message_count": len(messages),
                "message_ids": [m.id for m in messages],
                "title": draft.get("title") or "WhatsApp request",
                "description": draft.get("description") or "",
                "priority": draft.get("priority") or "medium",
                "complexity": normalize_ticket_complexity(draft.get("complexity") or "medium"),
                "media": _whatsapp_media_items(messages, enrichment),
                "media_enrichment": enrichment,
                "quality": quality,
                "raw_text": draft.get("raw_text") or "",
                "intake_stats": intake_stats,
            })

    @router.post("/tickets/boards/{board_id}/whatsapp-links")
    async def add_whatsapp_link(board_id: int, payload: dict):
        """Link a WhatsApp phone number to this board."""
        from distr.core.db import WhatsAppPhoneLink
        phone_jid = payload.get("phone_jid", "")
        if not phone_jid:
            raise HTTPException(400, "phone_jid is required")
        with get_session() as s:
            # Prevent duplicate links
            existing = s.query(WhatsAppPhoneLink).filter_by(board_id=board_id, phone_jid=phone_jid).first()
            if existing:
                return JSONResponse({"success": True, "id": existing.id, "message": "Already linked"})
            link = WhatsAppPhoneLink(
                board_id=board_id,
                phone_jid=phone_jid,
                phone_number=payload.get("phone_number", phone_jid.split("@")[0].split(":")[0]),
                contact_name=payload.get("contact_name", ""),
                auto_snapshot=payload.get("auto_snapshot", False),
            )
            s.add(link)
            s.flush()
            return JSONResponse({"success": True, "id": link.id})

    @router.delete("/tickets/boards/{board_id}/whatsapp-links/{link_id}")
    async def delete_whatsapp_link(board_id: int, link_id: int):
        """Unlink a WhatsApp phone number from this board."""
        from distr.core.db import WhatsAppPhoneLink
        with get_session() as s:
            link = s.query(WhatsAppPhoneLink).filter_by(id=link_id, board_id=board_id).first()
            if not link:
                raise HTTPException(404, "Link not found")
            s.delete(link)
            return JSONResponse({"success": True})

    @router.patch("/tickets/boards/{board_id}/whatsapp-links/{link_id}")
    async def update_whatsapp_link(board_id: int, link_id: int, payload: dict):
        """Update a WhatsApp link (e.g. toggle auto_snapshot)."""
        from distr.core.db import WhatsAppPhoneLink
        with get_session() as s:
            link = s.query(WhatsAppPhoneLink).filter_by(id=link_id, board_id=board_id).first()
            if not link:
                raise HTTPException(404, "Link not found")
            if "auto_snapshot" in payload:
                link.auto_snapshot = payload["auto_snapshot"]
            if "contact_name" in payload:
                link.contact_name = payload["contact_name"]
            return JSONResponse({"success": True})

    @router.get("/tickets/whatsapp/messages")
    async def get_whatsapp_messages(jid_phone: str = "", limit: int = 50, offset: int = 0, unprocessed_only: bool = False, sort: str = "asc"):
        """Get WhatsApp messages stored in the local database."""
        try:
            from PyQt6.QtWidgets import QApplication
            _app = QApplication.instance()
            whatsapp_manager = getattr(_app, 'whatsapp_manager', None) if _app else None
            if not whatsapp_manager:
                return JSONResponse({"messages": [], "total": 0, "error": "WhatsApp not connected"})
            result = whatsapp_manager.get_stored_messages(
                jid_phone=jid_phone or None,
                limit=limit,
                offset=offset,
                unprocessed_only=unprocessed_only,
                sort=sort,
            )
            return JSONResponse(result)
        except Exception as e:
            logger.error(f"WhatsApp message query error: {e}")
            return JSONResponse({"messages": [], "total": 0, "error": str(e)})

    @router.post("/tickets/whatsapp/sync")
    async def sync_whatsapp_messages():
        """Sync messages from the relay server into the local DB."""
        try:
            from distr.core.kanban.whatsapp_relay_sync import (
                announce_whatsapp_sync,
                sync_whatsapp_from_relay,
            )

            result = sync_whatsapp_from_relay(mark_processed=False)
            announce_whatsapp_sync(result)
            status = 500 if result.get("error") and not int(result.get("synced") or 0) else 200
            return JSONResponse(result, status_code=status)
        except Exception as e:
            logger.error(f"WhatsApp sync error: {e}")
            return JSONResponse({"synced": 0, "error": str(e)}, status_code=500)
    @router.get("/tickets/whatsapp/linked-board")
    async def get_whatsapp_linked_board(phone: str):
        """Return the board linked to this WhatsApp phone number, if any."""
        with get_session() as s:
            link = s.query(WhatsAppPhoneLink).filter(
                WhatsAppPhoneLink.phone_number == phone
            ).first()
            if link:
                board = s.query(KanbanBoard).filter(KanbanBoard.id == link.board_id).first()
                board_name = board.name if board else None
                return JSONResponse({"board_id": link.board_id, "board_name": board_name})
            return JSONResponse({"board_id": None, "board_name": None})



    @router.post("/tickets/whatsapp/compose-ticket")
    async def compose_whatsapp_ticket(request: Request):
        """Use the configured LLM to compose a detailed, actionable ticket from WhatsApp messages, voice transcriptions, and media."""
        body = await request.json()
        message_ids = body.get("message_ids", [])
        if not message_ids:
            return JSONResponse({"error": "No message IDs provided"}, status_code=400)

        with get_session() as s:
            messages = s.query(WhatsAppMessage).filter(
                WhatsAppMessage.id.in_(message_ids)
            ).order_by(WhatsAppMessage.whatsapp_timestamp.asc()).all()

            if not messages:
                return JSONResponse({"error": "No messages found"}, status_code=404)

            enrichment = _ensure_whatsapp_messages_enriched(messages)
            draft = _build_whatsapp_ticket_draft(messages)
            raw_text = draft["raw_text"]
            media_items = _whatsapp_media_items(messages, enrichment)
            s.flush()

        # Call the LLM to polish the deterministic draft. If this fails, the
        # modal still gets the draft immediately instead of blocking creation.
        try:
            from distr.core.utils import load_settings_from_db
            from distr.core.llm_factory import resolve_settings_keys, create_stream, normalize_provider

            settings = load_settings_from_db()
            provider, model = resolve_settings_keys(settings)
            quality_instructions = _whatsapp_ticket_quality_instructions(len(messages), len(media_items))

            prompt = f"""You are a project manager writing a detailed, actionable ticket from WhatsApp messages, voice notes, and media.

Here are the messages and transcriptions:
---
{raw_text}
---

{quality_instructions}

Write a thorough ticket with:
1. TITLE: A clear, specific title (max 80 chars) that captures exactly what needs to happen
2. DESCRIPTION: A comprehensive, detailed description that:
   - States exactly what the user needs done — be explicit and specific
   - Weaves in every detail from voice transcriptions ([Transcription] sections) as if the user said it directly
   - Includes all names, dates, numbers, places, and specifics mentioned
   - Breaks down complex requests into numbered steps or bullet points
   - Notes media attachments as evidence linked to the relevant message or caption
   - Flags any ambiguity or missing info that should be clarified
   - Is written so someone who has NEVER seen these messages can pick up the work immediately
   - Do NOT just paraphrase — write full, complete sentences that explain the what, why, and how
   - Include context: who sent it, what they were responding to, what outcome they expect
   - Do NOT mention OCR, bounding boxes, extraction status, image processing, or internal analysis. Images are attachments; voice/video transcriptions are usable message text.

The description should be long enough that a developer or team member can start working without needing to read the original messages.

Respond in this exact format:
TITLE: [your title here]
DESCRIPTION: [your full description here]"""

            # Collect the full response from the stream
            full_response = ""
            for token in create_stream(provider, model, [
                {"role": "system", "content": "You are a project manager who writes thorough, actionable tickets from messages and voice notes. Be detailed and specific."},
                {"role": "user", "content": prompt}
            ], settings):
                full_response += token

            # Parse title and description from the response
            title = ""
            description = ""
            lines = full_response.strip().split("\n")
            in_desc = False
            for line in lines:
                if line.startswith("TITLE:"):
                    title = line[6:].strip()
                elif line.startswith("DESCRIPTION:"):
                    in_desc = True
                    description = line[12:].strip()
                elif in_desc:
                    description += "\n" + line

            if not title:
                # Fallback: use first line as title
                title = lines[0].strip() if lines else "WhatsApp Ticket"
                description = "\n".join(lines[1:]) if len(lines) > 1 else full_response
            priority = _infer_whatsapp_ticket_priority(title, description)
            complexity = infer_ticket_complexity(title, description, file_count=len(media_items))
            quality = _validate_whatsapp_ticket_quality(title, description, messages, enrichment)

            return JSONResponse({
                "title": title,
                "description": description,
                "priority": priority,
                "complexity": complexity,
                "media": media_items,
                "media_enrichment": enrichment,
                "quality": quality,
                "raw_text": raw_text,
                "success": True
            })
        except Exception as e:
            from distr.core.llm_errors import format_model_error

            compose_error = format_model_error(
                e,
                provider=provider if "provider" in locals() else "",
                model=model if "model" in locals() else "",
                operation="compose a WhatsApp ticket",
            )
            logger.error(f"LLM distill error: {compose_error}", exc_info=True)
            return JSONResponse({
                "title": draft["title"],
                "description": draft["description"],
                "priority": draft["priority"],
                "complexity": draft["complexity"],
                "media": media_items,
                "media_enrichment": enrichment if "enrichment" in locals() else {},
                "quality": _validate_whatsapp_ticket_quality(
                    draft["title"],
                    draft["description"],
                    messages if "messages" in locals() else [],
                    enrichment if "enrichment" in locals() else {},
                ),
                "raw_text": raw_text,
                "success": True,
                "fallback": True,
                "error": compose_error,
                "compose_error": compose_error,
            })


    register_whatsapp_routes(
        router=router,
        relay_auth_headers=_relay_auth_headers,
        load_or_create_device_identity=_load_or_create_device_identity,
    )

    @router.websocket("/tickets/ws/boards")
    async def kanban_boards_websocket(websocket: WebSocket):
        """WebSocket stream for realtime board/ticket/workflow check-in updates."""
        origin = websocket.headers.get("origin")
        if origin and not is_allowed_local_origin(origin):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        await websocket.accept()
        loop = asyncio.get_event_loop()
        from distr.gui.web.kanban_events import register_kb_websocket, unregister_kb_websocket
        register_kb_websocket(websocket, loop)
        try:
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    await websocket.send_text('{"type":"ping"}')
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            unregister_kb_websocket(websocket)

    return router
