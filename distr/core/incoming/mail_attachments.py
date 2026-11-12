"""Download, store, and attach Gmail/Mailshot files for Incoming triage and threads."""
from __future__ import annotations

import logging
import hashlib
import stat
from pathlib import Path
from typing import Any

from . import media_cache
from .attachment_text import apply_extraction, extract_attachment_bytes, excerpt_extracted_text

logger = logging.getLogger(__name__)

MAX_ATTACHMENTS_PER_MESSAGE = 15


def _kind_for(mime: str, filename: str = "") -> str:
    mime = str(mime or "").split(";", 1)[0].strip().lower()
    suffix = Path(filename or "").suffix.lower()
    if mime.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return "image"
    if mime == "application/pdf" or suffix == ".pdf":
        return "document"
    return "file"


def collect_item_attachments(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize attachment dicts from an Incoming mail item."""
    if not isinstance(item, dict):
        return []
    source = str(item.get("source") or item.get("provider") or "").strip().lower()
    message_id = str(item.get("source_message_id") or item.get("message_id") or item.get("id") or "").strip()
    out: list[dict[str, Any]] = []
    for raw in item.get("attachments") or []:
        if not isinstance(raw, dict):
            continue
        filename = str(raw.get("filename") or raw.get("name") or "attachment").strip() or "attachment"
        mime = str(raw.get("mime_type") or raw.get("content_type") or raw.get("type") or "").strip()
        mid = str(raw.get("message_id") or message_id).strip()
        provider = str(raw.get("provider") or source).strip().lower()
        row = {
            "name": filename,
            "filename": filename,
            "mime_type": mime,
            "kind": raw.get("kind") or _kind_for(mime, filename),
            "size": raw.get("size") or 0,
            "attachment_id": str(raw.get("attachment_id") or "").strip(),
            "content_id": str(raw.get("content_id") or "").strip().strip("<>"),
            "message_id": mid,
            "source_message_id": mid,
            "provider": provider,
            "source": provider,
            "inline": bool(raw.get("inline")),
            "preview_url": str(raw.get("preview_url") or ""),
            "path": str(raw.get("path") or ""),
            "sha256": str(raw.get("sha256") or ""),
            "extracted_text": str(raw.get("extracted_text") or raw.get("transcript") or ""),
            "transcript": str(raw.get("transcript") or raw.get("extracted_text") or ""),
            "extraction_status": str(raw.get("extraction_status") or ""),
            "download_status": str(raw.get("download_status") or ("stored" if raw.get("path") else "")),
        }
        out.append(row)
        if len(out) >= MAX_ATTACHMENTS_PER_MESSAGE:
            break
    return out


def collect_conversation_attachments(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in conversation.get("items") or []:
        for row in collect_item_attachments(item if isinstance(item, dict) else {}):
            key = (row.get("message_id") or "", row.get("attachment_id") or "", row.get("name") or "")
            if key in seen:
                continue
            seen.add(key)
            collected.append(row)
    return collected


def prompt_attachments_for_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Compact attachment payload for a triage prompt message."""
    rows = []
    for raw in collect_item_attachments(item):
        rows.append({
            "name": raw.get("name") or "attachment",
            "mime_type": raw.get("mime_type") or "",
            "size": raw.get("size") or 0,
            "extracted_text": excerpt_extracted_text(str(raw.get("extracted_text") or raw.get("transcript") or ""), limit=8000),
            "extraction_status": raw.get("extraction_status") or "",
            "path": raw.get("path") or "",
        })
    return rows


def _download_mail_bytes(item: dict[str, Any]) -> tuple[bytes, str, str]:
    """Fetch one Gmail or Mailshot attachment. Callers must mock this in tests."""
    provider = str(item.get("provider") or item.get("source") or "").strip().lower()
    message_id = str(item.get("message_id") or item.get("source_message_id") or "").strip()
    attachment_id = str(item.get("attachment_id") or "").strip()
    content_id = str(item.get("content_id") or "").strip()
    filename_hint = str(item.get("filename") or item.get("name") or "").strip()
    if provider in {"mailshot", "tensology"} or str(message_id).startswith("email-"):
        from distr.core.incoming.mailshot_media import fetch_mailshot_attachment_bytes

        return fetch_mailshot_attachment_bytes(message_id, attachment_id, filename_hint=filename_hint)
    from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector

    connector = GoogleWorkspaceConnector()
    result = connector.fetch_email_attachment_bytes(
        message_id,
        attachment_id,
        content_id=content_id,
        filename_hint=filename_hint,
    )
    if not result:
        raise FileNotFoundError("Gmail attachment could not be loaded.")
    return result


def store_mail_attachment(
    item: dict[str, Any],
    *,
    project_id: int | None,
    reference: str,
    downloader=_download_mail_bytes,
) -> dict[str, Any]:
    """Download one mail file into managed storage, extract text, and write record.json."""
    from .media import _destination, _record, _root

    name = str(item.get("name") or item.get("filename") or "attachment").strip() or "attachment"
    message_id = str(item.get("message_id") or item.get("source_message_id") or "").strip()
    attachment_id = str(item.get("attachment_id") or item.get("content_id") or name).strip()
    identity = f"mail:{item.get('provider') or item.get('source') or 'gmail'}:{message_id}:{attachment_id}"
    root = _root(project_id, reference or f"mail:{message_id}")
    destination = _destination(root, identity, name)
    record = media_cache.verified(destination, identity, item)
    if record is None:
        data, mime, resolved_name = downloader(item)
        if not data:
            raise FileNotFoundError(f"{name} was empty.")
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_bytes(data)
        record = _record(
            destination,
            resolved_name or name,
            mime or str(item.get("mime_type") or ""),
            identity,
            source_message_id=message_id,
            kind=_kind_for(mime, resolved_name or name),
            attachment_id=attachment_id,
            provider=str(item.get("provider") or item.get("source") or ""),
        )
        extracted = extract_attachment_bytes(data, mime_type=record.get("mime_type") or "", filename=record.get("name") or name)
        record = apply_extraction(record, extracted)
        media_cache.remember(record)
    else:
        record = apply_extraction(record)
        if not record.get("extracted_text") and record.get("path"):
            media_cache.remember(record)
    record["download_status"] = "stored"
    return record


def restore_prepared_mail_attachment(record: dict, original: dict, project_id: int) -> Path:
    """Restore the reviewed bytes from the other cache of this exact mail attachment."""
    from .media import MAX_MEDIA_BYTES, _copy_reviewed_attachment, _destination, _root

    provider = record.get('provider') or 'gmail'
    allowed = {'gmail': {'gmail'}, 'mailshot': {'mailshot', 'tensology'}, 'email': {'email', 'gmail', 'mailshot', 'tensology'}}
    attachment_id = record.get('attachment_id')
    if not isinstance(provider, str) or provider not in allowed.get(original['source'], ()) or not isinstance(attachment_id, str) or not attachment_id:
        raise ValueError('The saved mail attachment has no matching provider identity.')
    identity = f"mail:{provider}:{record['source_message_id']}:{attachment_id}"
    size = record.get('size')
    if (record.get('id') != hashlib.sha256(identity.encode()).hexdigest()[:24]
            or type(size) is not int or not 0 < size <= MAX_MEDIA_BYTES
            or not isinstance(record.get('sha256'), str) or len(record['sha256']) != 64):
        raise ValueError('The reviewed mail attachment has no verified identity or checksum.')

    base = (Path.home() / '.decisions/workspaces/projects').resolve()
    roots = [_root(scope, original['id'], create=False).resolve() for scope in (project_id, None)]
    if not roots[0].is_relative_to(base / str(project_id)) or not roots[1].is_relative_to(base / 'inbox'):
        raise ValueError('The mail cache is outside the original project or inbox.')
    destination = Path(str(record.get('path') or ''))
    _, separator, filename = destination.name.partition('-')
    if (destination.parent not in roots or not separator or _destination(destination.parent, identity, filename) != destination
            or destination.resolve() != destination):
        raise ValueError('The saved attachment path does not match its original mail cache.')
    source = next(root for root in roots if root != destination.parent) / destination.name
    details = source.stat()
    if source.resolve() != source or not stat.S_ISREG(details.st_mode) or details.st_size != size:
        raise FileNotFoundError('The cached mail attachment is unavailable or changed.')
    cached = media_cache.verified(source, identity, record)
    if not cached or any(cached.get(key) != record.get(key) for key in ('source_message_id', 'attachment_id', 'provider')):
        raise ValueError('The cached mail attachment does not match its reviewed manifest.')
    restored = _copy_reviewed_attachment(source, destination, record)
    try:
        media_cache.remember(record)
    except OSError:
        logger.debug("Could not save the restored mail attachment manifest", exc_info=True)
    return restored


def enrich_conversation_mail_attachments(
    conversation: dict[str, Any],
    *,
    downloader=_download_mail_bytes,
    project_id: int | None = None,
) -> list[dict[str, Any]]:
    """Download and extract every mail attachment on a triage conversation (in place)."""
    source = str(conversation.get("source") or "").strip().lower()
    if source not in {"gmail", "mailshot", "email"}:
        return []
    reference = f"local:{source}:{conversation.get('thread_id') or 'thread'}"
    stored: list[dict[str, Any]] = []
    for item in conversation.get("items") or []:
        if not isinstance(item, dict):
            continue
        enriched: list[dict[str, Any]] = []
        for raw in collect_item_attachments(item):
            raw["provider"] = raw.get("provider") or source
            raw["source"] = raw.get("source") or source
            try:
                record = store_mail_attachment(
                    raw, project_id=project_id or conversation.get("project_id"),
                    reference=reference, downloader=downloader,
                )
                raw.update({
                    "path": record.get("path") or "",
                    "sha256": record.get("sha256") or "",
                    "size": record.get("size") or raw.get("size") or 0,
                    "mime_type": record.get("mime_type") or raw.get("mime_type") or "",
                    "name": record.get("name") or raw.get("name"),
                    "filename": record.get("name") or raw.get("filename"),
                    "extracted_text": record.get("extracted_text") or "",
                    "transcript": record.get("transcript") or record.get("extracted_text") or "",
                    "extraction_status": record.get("extraction_status") or "",
                    "download_status": "stored",
                    "id": record.get("id") or "",
                })
                stored.append({**raw, **record})
            except Exception:
                logger.info("Mail attachment could not be stored before triage", exc_info=True)
                raw["download_status"] = "failed"
                raw["extraction_status"] = "error"
            enriched.append(raw)
        if enriched:
            item["attachments"] = enriched
    return stored


def store_proposal_mail_attachments(
    proposal: dict[str, Any],
    project_id: int | None,
    *,
    root=None,
    strict: bool = False,
    downloader=_download_mail_bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Store proposal mail files into managed storage (used by ensure_proposal_attachments)."""
    attachments: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    reference = str(proposal.get("id") or proposal.get("dedupe_key") or "")
    for item in proposal.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        if item.get("path") and Path(str(item["path"])).is_file() and item.get("sha256") and item.get("id"):
            attachments.append(apply_extraction(item))
            continue
        name = str(item.get("name") or item.get("filename") or "attachment")
        try:
            record = store_mail_attachment(item, project_id=project_id, reference=reference, downloader=downloader)
            attachments.append(record)
        except Exception as exc:
            row = {"name": name, "error": str(exc), "download_error": str(exc), "source_message_id": item.get("source_message_id") or item.get("message_id") or ""}
            failures.append(row)
            if strict:
                raise
    return attachments, failures


def work_intake_attachments(rows: Any) -> list[Any]:
    """Map Incoming/Gmail attachment dicts onto WorkIntakeAttachment records."""
    from distr.core.work_intake import WorkIntakeAttachment

    out: list[WorkIntakeAttachment] = []
    for raw in rows or []:
        if isinstance(raw, WorkIntakeAttachment):
            out.append(raw)
            continue
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("filename") or "attachment")
        mime = str(raw.get("mime_type") or raw.get("content_type") or "")
        out.append(WorkIntakeAttachment(
            kind=str(raw.get("kind") or _kind_for(mime, name)),
            path=str(raw.get("path") or ""),
            url=str(raw.get("preview_url") or raw.get("url") or ""),
            name=name,
            mime_type=mime,
            metadata={
                "attachment_id": str(raw.get("attachment_id") or ""),
                "message_id": str(raw.get("message_id") or raw.get("source_message_id") or ""),
                "extracted_text": str(raw.get("extracted_text") or raw.get("transcript") or ""),
                "sha256": str(raw.get("sha256") or ""),
                "size": raw.get("size") or 0,
            },
        ))
    return out
