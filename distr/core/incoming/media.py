"""Durable, scoped media for Incoming proposals and their Development threads."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import shutil
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from distr.core.db import Chat, WhatsAppMessage, get_session

MAX_MEDIA_BYTES = 512 * 1024 * 1024


def _digest(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _root(project_id: int | None, reference: str) -> Path:
    key = hashlib.sha256(reference.encode()).hexdigest()[:24]
    root = Path.home()/'.decisions/workspaces/projects'/str(project_id or 'inbox')/'attachments/incoming'/key
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _destination(root: Path, identity: str, name: str) -> Path:
    name = Path(str(name).replace('\\', '/')).name or 'attachment'
    key = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return root/(key+'-'+name[:180])


def _record(path: Path, name: str, mime: str, identity: str, **metadata) -> dict[str, Any]:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_MEDIA_BYTES:
        raise ValueError(f'Attachment {name} is missing, empty or exceeds the media limit. Retry after syncing media.')
    return {'id': hashlib.sha256(identity.encode()).hexdigest()[:24], 'name': Path(name).name,
            'path': str(path.resolve()), 'mime_type': mime or mimetypes.guess_type(name)[0] or 'application/octet-stream',
            'size': path.stat().st_size, 'sha256': _digest(path), **metadata}


def _download_whatsapp(row_id: int, message_id: str, destination: Path) -> None:
    """Use the existing authenticated relay/cache path, never a supplied filesystem path."""
    from distr.gui.web.security import INTERNAL_AUTH_HEADER, get_internal_api_token
    url = f'http://127.0.0.1:8765/api/tickets/whatsapp/relay-media/{row_id}?wa_key={urllib.parse.quote(message_id, safe="")}'
    request = urllib.request.Request(url, headers={INTERNAL_AUTH_HEADER: get_internal_api_token()})
    partial = destination.with_suffix(destination.suffix+'.part')
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open('wb') as output:
            total = 0
            while chunk := response.read(65536):
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    raise ValueError('Media exceeds the attachment limit.')
                output.write(chunk)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def list_proposal_attachment_manifest(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """Metadata-only file list for Incoming UI (no download). Works for Tensology and local WhatsApp."""
    reference = str(proposal.get('id') or proposal.get('dedupe_key') or '')
    if reference.startswith('tensology:') or str(proposal.get('origin') or '') == 'tensology':
        out = []
        for item in proposal.get('attachments') or []:
            if not isinstance(item, dict):
                continue
            out.append({
                'name': str(item.get('name') or item.get('filename') or 'attachment'),
                'mime_type': str(item.get('mime_type') or item.get('type') or ''),
                'kind': str(item.get('kind') or ''),
                'size': int(item.get('size') or 0) if str(item.get('size') or '').isdigit() or isinstance(item.get('size'), int) else 0,
                'caption': str(item.get('caption') or ''),
                'transcript': str(item.get('transcript') or ''),
                'api_path': str(item.get('api_path') or ''),
                'source_message_id': str(item.get('source_message_id') or ''),
                'download_status': str(item.get('download_status') or ''),
                'download_error': str(item.get('download_error') or ''),
            })
        return out
    if str(proposal.get('source') or '').lower() != 'whatsapp':
        return [item for item in (proposal.get('attachments') or []) if isinstance(item, dict)]
    thread_id = str(proposal.get('source_thread_id') or '')
    ids = [str(value) for value in proposal.get('message_ids') or []]
    if not thread_id:
        return []
    with get_session() as db:
        query = db.query(WhatsAppMessage).filter(WhatsAppMessage.jid == thread_id)
        if ids:
            query = query.filter(WhatsAppMessage.message_id.in_(ids))
        rows = query.order_by(WhatsAppMessage.whatsapp_timestamp.desc()).limit(250).all()
        media = []
        for row in rows:
            if not (row.media_type or row.media_local_path):
                continue
            mime = str(row.media_mime_type or '')
            extension = mimetypes.guess_extension(mime.split(';', 1)[0].strip()) or '.bin'
            name = row.media_filename or f"{row.message_id}{extension}"
            media.append({
                'name': name,
                'mime_type': mime or (mimetypes.guess_type(name)[0] or 'application/octet-stream'),
                'kind': str(row.media_type or ''),
                'size': int(row.media_file_length or 0) if row.media_file_length else 0,
                'duration': int(row.media_duration) if row.media_duration not in (None, '') else None,
                'source_message_id': str(row.message_id),
                'api_path': '',
                'download_status': 'local' if row.media_local_path else 'not_cached',
            })
    return media


def ensure_proposal_attachments(
    proposal: dict[str, Any],
    project_id: int | None = None,
    *,
    client=None,
    chat_id: int | None = None,
    strict: bool = True,
) -> dict[str, Any]:
    """Download/store proposal files locally once, optionally register on a thread.

    This is the shared entry point for Create Thread, merge, preview, and backfill.
    Returns ``{"attachments": [...], "failures": [...]}``. When ``strict`` is True,
    the first hard failure raises ValueError (same contract as ``proposal_attachments``).
    Failures are always listed so the UI can show an error row instead of a silent 502.
    Dedupes by attachment id when registering onto ``chat_id``.
    """
    reference = str(proposal.get('id') or proposal.get('dedupe_key') or '')
    root = _root(project_id, reference)
    attachments: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    def _fail(name: str, error: str, **meta: Any) -> None:
        row = {'name': name, 'error': error, 'download_error': error, **meta}
        failures.append(row)
        if strict:
            raise ValueError(error)

    if reference.startswith('tensology:'):
        proposal_id = int(reference.split(':', 1)[1])
        for item in proposal.get('attachments') or []:
            if not isinstance(item, dict):
                continue
            location = str(item.get('api_path') or '')
            name = str(item.get('name') or item.get('filename') or 'attachment')
            identity = f'tensology:{proposal_id}:{location or name}'
            if not location.startswith(f'work-requests/{proposal_id}/attachment?'):
                _fail(name, 'Proposal media is missing its scoped download link. Refresh Incoming and retry.',
                      api_path=location, mime_type=str(item.get('mime_type') or item.get('type') or ''))
                continue
            destination = _destination(root, identity, name)
            try:
                # Cache hits are for soft ensure/backfill only. Create/push (strict) always re-fetches.
                if (not strict) and destination.is_file() and 0 < destination.stat().st_size <= MAX_MEDIA_BYTES:
                    attachments.append(_record(destination, name, str(item.get('mime_type') or item.get('type') or ''), identity,
                                               source_message_id=str(item.get('source_message_id') or ''),
                                               transcript=str(item.get('transcript') or '')))
                    continue
                if client is None:
                    from distr.core.tensology_client import configured_tensology_client
                    client = configured_tensology_client(source='incoming-proposal-media')
                client.download(location, destination, max_bytes=MAX_MEDIA_BYTES)
                attachments.append(_record(destination, name, str(item.get('mime_type') or item.get('type') or ''), identity,
                                           source_message_id=str(item.get('source_message_id') or ''),
                                           transcript=str(item.get('transcript') or '')))
            except Exception as exc:
                _fail(name, f'Could not attach {name}. The proposal was not pushed. Sync its media and retry. ({exc})',
                      api_path=location, mime_type=str(item.get('mime_type') or item.get('type') or ''),
                      source_message_id=str(item.get('source_message_id') or ''))
        # WhatsApp-origin Tensology proposals sometimes omit attachment_manifest; pull local WA media by message id only.
        if str(proposal.get('source') or '').lower() == 'whatsapp' and not (proposal.get('attachments') or []):
            wa_proposal = {
                **proposal,
                'id': f"local-wa-fallback:{reference}",
                'source': 'whatsapp',
                'source_thread_id': proposal.get('source_thread_id') or '',
                'message_ids': [str(v) for v in (proposal.get('message_ids') or []) if str(v).strip()],
            }
            if not wa_proposal['source_thread_id'] and wa_proposal['message_ids']:
                with get_session() as db:
                    row = db.query(WhatsAppMessage).filter(
                        WhatsAppMessage.message_id.in_(wa_proposal['message_ids'])
                    ).first()
                    if row is not None:
                        wa_proposal['source_thread_id'] = str(row.jid or '')
            if wa_proposal['source_thread_id'] and wa_proposal['message_ids']:
                wa_result = ensure_proposal_attachments(
                    wa_proposal, project_id, client=client, chat_id=None, strict=False,
                )
                seen = {item.get('id') for item in attachments}
                for item in wa_result.get('attachments') or []:
                    if item.get('id') in seen:
                        continue
                    seen.add(item.get('id'))
                    attachments.append(item)
                for item in wa_result.get('failures') or []:
                    if not isinstance(item, dict):
                        continue
                    err = str(item.get('download_error') or item.get('error') or '')
                    if 'source WhatsApp messages are unavailable' in err or 'no source conversation' in err:
                        continue
                    failures.append(item)
        if chat_id is not None and attachments:
            register_thread_attachments(int(chat_id), attachments)
        return {'attachments': attachments, 'failures': failures}

    if str(proposal.get('source') or '').lower() != 'whatsapp':
        if proposal.get('attachments'):
            _fail('attachment', 'This proposal has media without a supported source download link. Refresh Incoming before pushing it.')
        if chat_id is not None and attachments:
            register_thread_attachments(int(chat_id), attachments)
        return {'attachments': attachments, 'failures': failures}

    thread_id = str(proposal.get('source_thread_id') or '')
    ids = [str(value) for value in proposal.get('message_ids') or []]
    if not thread_id:
        _fail('whatsapp', 'The WhatsApp proposal has no source conversation. Resync Incoming and retry.')
        return {'attachments': attachments, 'failures': failures}
    with get_session() as db:
        query = db.query(WhatsAppMessage).filter(WhatsAppMessage.jid == thread_id)
        if ids:
            query = query.filter(WhatsAppMessage.message_id.in_(ids))
        rows = query.order_by(WhatsAppMessage.whatsapp_timestamp.desc()).limit(250).all()
        found = {str(row.message_id) for row in rows}
        if ids and set(ids) - found:
            _fail('whatsapp', 'Some source WhatsApp messages are unavailable. Resync Incoming before pushing this proposal.')
            return {'attachments': attachments, 'failures': failures}
        media = [{'id': row.id, 'message_id': row.message_id, 'name': row.media_filename or '',
                  'mime': row.media_mime_type or '', 'kind': row.media_type or '',
                  'duration': int(row.media_duration) if row.media_duration not in (None, '') else None,
                  'stored': row.media_local_path or ''} for row in rows if row.media_type or row.media_local_path]
    from distr.core.integrations.whatsapp.paths import resolve_whatsapp_media_disk_path
    for item in media:
        identity = 'whatsapp:' + item['message_id']
        extension = mimetypes.guess_extension((item['mime'] or '').split(';', 1)[0].strip()) or '.bin'
        name = item['name'] or item['message_id'] + extension
        destination = _destination(root, identity, name)
        try:
            if (not strict) and destination.is_file() and 0 < destination.stat().st_size <= MAX_MEDIA_BYTES:
                attachments.append(_record(destination, name, item['mime'], identity, source_message_id=item['message_id'], kind=item.get('kind') or '', duration=item.get('duration')))
                continue
            original = Path(resolve_whatsapp_media_disk_path(item['stored'])) if item['stored'] else None
            if original and original.is_file():
                if original.stat().st_size > MAX_MEDIA_BYTES:
                    raise ValueError('Media exceeds the attachment limit.')
                shutil.copyfile(original, destination)
            else:
                _download_whatsapp(item['id'], item['message_id'], destination)
            attachments.append(_record(destination, name, item['mime'], identity, source_message_id=item['message_id'], kind=item.get('kind') or '', duration=item.get('duration')))
        except Exception as exc:
            _fail(name, f'Could not attach {name}. The proposal was not pushed. Sync WhatsApp media and retry. ({exc})',
                  source_message_id=item['message_id'], mime_type=item['mime'], kind=item.get('kind') or '')
    if chat_id is not None and attachments:
        register_thread_attachments(int(chat_id), attachments)
    return {'attachments': attachments, 'failures': failures}


def proposal_attachments(proposal: dict[str, Any], project_id: int | None, *, client=None) -> list[dict[str, Any]]:
    """Resolve source evidence before creating the thread. A known missing file fails explicitly."""
    result = ensure_proposal_attachments(proposal, project_id, client=client, strict=True)
    return result['attachments']


def cached_tensology_attachment_file(api_path: str, *, client=None) -> tuple[Path, str]:
    """Cache original media on disk so large video previews can stream and seek."""
    path = str(api_path or '').strip().lstrip('/')
    if not (path.startswith('work-requests/') or path.startswith('tickets/')):
        raise ValueError('Unsupported attachment path.')
    if client is None:
        from distr.core.tensology_client import configured_tensology_client
        client = configured_tensology_client(source='decisionsai-incoming-attachment')
    identity = f'proxy:{path}'
    root = _root(None, 'tensology-proxy-cache')
    name = Path(urllib.parse.urlsplit(path).path).name or 'attachment'
    destination = _destination(root, identity, name if name != 'attachment' else 'attachment.bin')
    mime_path = destination.with_name(destination.name + '.mime')
    mime = mimetypes.guess_type(destination.name)[0] or 'application/octet-stream'
    if not (destination.is_file() and 0 < destination.stat().st_size <= MAX_MEDIA_BYTES):
        metadata = {}
        client.download(path, destination, max_bytes=MAX_MEDIA_BYTES, metadata=metadata)
        mime = str(metadata.get('content_type') or mime).split(';', 1)[0].strip().lower()
        # Untrusted active documents must remain downloads on this app origin.
        if mime in {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif',
                    'audio/ogg', 'audio/opus', 'audio/mpeg', 'audio/mp4', 'audio/wav', 'audio/webm',
                    'video/mp4', 'video/webm', 'video/quicktime', 'video/3gpp', 'application/pdf'}:
            mime_path.write_text(mime, encoding='utf-8')
        else:
            mime = 'application/octet-stream'
    elif mime_path.is_file():
        mime = mime_path.read_text(encoding='utf-8').strip()
    if mime == 'application/octet-stream':
        # Existing caches were saved as attachment.bin without response headers.
        with destination.open('rb') as handle:
            signature = handle.read(16)
        if signature.startswith(b'\xff\xd8\xff'): mime = 'image/jpeg'
        elif signature.startswith(b'\x89PNG\r\n\x1a\n'): mime = 'image/png'
        elif signature.startswith((b'GIF87a', b'GIF89a')): mime = 'image/gif'
        elif signature.startswith(b'OggS'): mime = 'audio/ogg'
        elif signature.startswith(b'%PDF-'): mime = 'application/pdf'
        elif signature[4:8] == b'ftyp': mime = 'audio/mp4' if signature[8:12] in (b'M4A ', b'M4B ') else 'video/mp4'
        elif signature.startswith(b'RIFF') and signature[8:12] == b'WEBP': mime = 'image/webp'
    return destination, mime


def cached_tensology_attachment_bytes(api_path: str, *, client=None) -> tuple[bytes, str]:
    path, mime = cached_tensology_attachment_file(api_path, client=client)
    return path.read_bytes(), mime


def public_managed_attachments(records: list[dict[str, Any]], *, proposal_id: str = '', chat_id: int | None = None) -> list[dict[str, Any]]:
    """Strip local paths and attach preview/download URLs for proposal or thread files."""
    out = []
    for item in records:
        if not isinstance(item, dict):
            continue
        row = {key: value for key, value in item.items() if key != 'path'}
        if item.get('download_error') or item.get('error'):
            row['download_error'] = item.get('download_error') or item.get('error')
            row['url'] = ''
        elif chat_id is not None and item.get('id'):
            row['url'] = f"/api/workflows/studio/tasks/{int(chat_id)}/attachments/{item['id']}"
        elif proposal_id and item.get('id'):
            row['url'] = (
                f"/api/workflows/studio/incoming/proposals/files/{item['id']}"
                f"?proposal_id={urllib.parse.quote(str(proposal_id), safe='')}"
            )
        elif item.get('api_path'):
            row['url'] = (
                f"/api/workflows/studio/incoming/proposals/attachment"
                f"?path={urllib.parse.quote(str(item['api_path']), safe='')}"
            )
        else:
            row['url'] = ''
        out.append(row)
    return out


def resolve_proposal_managed_attachment(proposal: dict[str, Any], attachment_id: str, project_id: int | None = None) -> tuple[Path, dict[str, Any]]:
    """Resolve a previously ensured proposal file from managed storage."""
    result = ensure_proposal_attachments(proposal, project_id, strict=False)
    record = next((item for item in result['attachments'] if item.get('id') == attachment_id), None)
    if record is None:
        failure = next(
            (item for item in result['failures']
             if item.get('id') == attachment_id or str(item.get('name') or '') == attachment_id),
            None,
        )
        raise FileNotFoundError((failure or {}).get('download_error') or 'Attachment not found for this proposal.')
    path = Path(record['path']).resolve()
    allowed = (Path.home() / '.decisions/workspaces/projects').resolve()
    if not path.is_relative_to(allowed) or not path.is_file():
        raise FileNotFoundError('Proposal attachment is unavailable.')
    if path.stat().st_size != record.get('size') or _digest(path) != record.get('sha256'):
        raise FileNotFoundError('Proposal attachment changed. Resync source media and retry.')
    return path, record


def thread_attachments(chat_id: int) -> list[dict[str, Any]]:
    with get_session() as db:
        chat = db.get(Chat, int(chat_id))
        if chat is None:
            return []
        try:
            params = json.loads(chat.params or '{}')
        except (ValueError, TypeError):
            return []
        return [item for item in params.get('incoming_attachments') or [] if isinstance(item, dict)]


def register_thread_attachments(chat_id: int, attachments: list[dict[str, Any]]) -> None:
    if not attachments:
        return
    normalized = []
    allowed = (Path.home()/'.decisions/workspaces/projects').resolve()
    for item in attachments:
        path = Path(str(item.get('path') or '')).resolve()
        boards = (Path.home()/'.decisions/workspaces/boards').resolve()
        is_snapshot = path.is_relative_to(boards) and len(path.relative_to(boards).parts) > 2 and path.relative_to(boards).parts[1] == 'intake'
        if not path.is_relative_to(allowed) and not is_snapshot:
            raise ValueError('Thread attachment is outside managed attachment storage.')
        normalized.append(item if item.get('sha256') and item.get('id') else
                          _record(path, str(item.get('name') or path.name), str(item.get('mime_type') or ''), str(path)))
    with get_session() as db:
        chat = db.get(Chat, int(chat_id))
        if chat is None:
            raise ValueError('The created Development thread was not found.')
        params = json.loads(chat.params or '{}')
        merged = {item['id']: item for item in params.get('incoming_attachments') or []}
        merged.update({item['id']: item for item in normalized})
        params['incoming_attachments'] = list(merged.values())
        chat.params = json.dumps(params)
        db.commit()
    # Thread files also belong in the linked ticket's Files tab (best effort).
    return sync_thread_files_to_tickets(int(chat_id))


def public_thread_attachments(chat_id: int) -> list[dict[str, Any]]:
    out = []
    for item in thread_attachments(chat_id):
        row = {key: value for key, value in item.items() if key != 'path'}
        if item.get('download_error') or item.get('error'):
            row['download_error'] = item.get('download_error') or item.get('error')
            row['url'] = ''
        else:
            row['url'] = f'/api/workflows/studio/tasks/{int(chat_id)}/attachments/{item["id"]}'
        out.append(row)
    return out


def required_thread_attachments(chat_id: int) -> list[dict[str, Any]]:
    records = thread_attachments(chat_id)
    for item in records:
        resolve_thread_attachment(chat_id, item['id'])
    return records


def resolve_thread_attachment(chat_id: int, attachment_id: str) -> tuple[Path, dict[str, Any]]:
    records = thread_attachments(chat_id)
    record = next((item for item in records if item.get('id') == attachment_id), None)
    if record is None:
        raise FileNotFoundError('Attachment not found in this thread.')
    path = Path(record['path']).resolve()
    allowed = (Path.home()/'.decisions/workspaces/projects').resolve()
    boards = (Path.home()/'.decisions/workspaces/boards').resolve()
    is_snapshot = path.is_relative_to(boards) and len(path.relative_to(boards).parts) > 2 and path.relative_to(boards).parts[1] == 'intake'
    if (not path.is_relative_to(allowed) and not is_snapshot) or not path.is_file():
        raise FileNotFoundError('Thread attachment is unavailable.')
    if path.stat().st_size != record.get('size') or _digest(path) != record.get('sha256'):
        raise FileNotFoundError('Thread attachment changed. Resync source media and retry.')
    return path, record


# ---- Thread -> ticket Files tab ---------------------------------------------
# A Development thread's incoming files (documents, images, audio notes) are
# copied into kanban_uploads/{ticket_id} as KanbanTicketFile rows for every
# ticket linked to the thread (KanbanTicket.source_chat_id). Copies, never moves:
# the thread keeps its own files. Idempotent via a description marker. Never raises.
TICKET_FILE_MARKER = 'incoming-attachment:'


def _kanban_uploads_dir() -> Path:
    from distr.core.paths import DB_DIR
    return Path(DB_DIR) / 'kanban_uploads'


def _unique_destination(folder: Path, name: str) -> Path:
    safe = Path(str(name or 'file')).name.strip() or 'file'
    if safe in {'.', '..'}:
        safe = 'file'
    candidate = folder / safe
    stem, suffix, index = candidate.stem, candidate.suffix, 1
    while candidate.exists():
        candidate = folder / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def sync_thread_files_to_tickets(chat_id: int, ticket_id: int | None = None) -> dict[str, Any]:
    """Copy a thread's incoming files onto its linked ticket(s). Returns a report."""
    import logging
    log = logging.getLogger(__name__)
    report: dict[str, Any] = {'attached': [], 'failed': [], 'ticket_ids': []}
    try:
        from distr.core.db.kanban import KanbanTicket, KanbanTicketFile

        items = thread_attachments(int(chat_id))
        if not items:
            return report
        with get_session() as db:
            query = db.query(KanbanTicket).filter(KanbanTicket.source_chat_id == int(chat_id))
            ticket_ids = [int(row.id) for row in query.all()]
            if ticket_id is not None and int(ticket_id) not in ticket_ids and db.get(KanbanTicket, int(ticket_id)) is not None:
                ticket_ids.append(int(ticket_id))
            report['ticket_ids'] = ticket_ids
            for tid in ticket_ids:
                existing = {str(row.description or '') for row in db.query(KanbanTicketFile).filter(KanbanTicketFile.ticket_id == tid).all()}
                folder = _kanban_uploads_dir() / str(tid)
                for item in items:
                    name = str(item.get('name') or 'file')
                    marker = f"{TICKET_FILE_MARKER}{item.get('id') or item.get('sha256') or name}"
                    if marker in existing:
                        continue
                    error = item.get('download_error') or item.get('error')
                    source = Path(str(item.get('path') or ''))
                    try:
                        if error:
                            raise ValueError(str(error))
                        if not source.is_file():
                            raise FileNotFoundError('The thread file is missing on disk.')
                        folder.mkdir(parents=True, exist_ok=True)
                        destination = _unique_destination(folder, name)
                        if destination.resolve().parent != folder.resolve():
                            raise ValueError('Unsafe file name.')
                        shutil.copy2(source, destination)
                        db.add(KanbanTicketFile(ticket_id=tid, filename=destination.name,
                                                file_path=str(destination), description=marker))
                        existing.add(marker)
                        report['attached'].append({'ticket_id': tid, 'name': destination.name})
                    except Exception as exc:  # one bad file never blocks the rest
                        log.warning('Could not attach thread %s file %r to ticket %s: %s', chat_id, name, tid, exc)
                        report['failed'].append({'ticket_id': tid, 'name': name, 'error': str(exc)})
            db.commit()
    except Exception as exc:
        log.warning('Thread %s file sync to ticket failed: %s', chat_id, exc, exc_info=True)
        report['failed'].append({'name': '*', 'error': str(exc)})
    return report


def sync_thread_files_safely(chat_id: int | None, ticket_id: int | None = None) -> None:
    if chat_id is None:
        return
    try:
        sync_thread_files_to_tickets(int(chat_id), ticket_id)
        from distr.core.incoming.source_context import sync_ticket_source_context

        sync_ticket_source_context(int(chat_id), ticket_id)
    except Exception:
        pass
