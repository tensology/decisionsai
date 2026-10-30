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


def proposal_attachments(proposal: dict[str, Any], project_id: int | None, *, client=None) -> list[dict[str, Any]]:
    """Resolve source evidence before creating the thread. A known missing file fails explicitly."""
    reference = str(proposal.get('id') or proposal.get('dedupe_key') or '')
    root = _root(project_id, reference)
    attachments: list[dict[str, Any]] = []
    if reference.startswith('tensology:'):
        if client is None:
            from distr.core.tensology_client import configured_tensology_client
            client = configured_tensology_client(source='incoming-proposal-media')
        proposal_id = int(reference.split(':', 1)[1])
        for item in proposal.get('attachments') or []:
            identity = f'tensology:{proposal_id}:{item.get("api_path")}'
            location = str(item.get('api_path') or '')
            if not location.startswith(f'work-requests/{proposal_id}/attachment?'):
                raise ValueError('Proposal media is missing its scoped download link. Refresh Incoming and retry.')
            name = str(item.get('name') or item.get('filename') or 'attachment')
            destination = _destination(root, identity, name)
            # Refetch authoritative bytes, not a filename-only cache hit.
            try:
                client.download(location, destination)
                attachments.append(_record(destination, name, str(item.get('mime_type') or item.get('type') or ''), identity,
                                           source_message_id=str(item.get('source_message_id') or ''),
                                           transcript=str(item.get('transcript') or '')))
            except Exception as exc:
                raise ValueError(f'Could not attach {name}. The proposal was not pushed. Sync its media and retry.') from exc
        return attachments
    if str(proposal.get('source') or '').lower() != 'whatsapp':
        if proposal.get('attachments'):
            raise ValueError('This proposal has media without a supported source download link. Refresh Incoming before pushing it.')
        return []
    thread_id = str(proposal.get('source_thread_id') or '')
    ids = [str(value) for value in proposal.get('message_ids') or []]
    if not thread_id:
        raise ValueError('The WhatsApp proposal has no source conversation. Resync Incoming and retry.')
    with get_session() as db:
        query = db.query(WhatsAppMessage).filter(WhatsAppMessage.jid == thread_id)
        if ids:
            query = query.filter(WhatsAppMessage.message_id.in_(ids))
        rows = query.order_by(WhatsAppMessage.whatsapp_timestamp.desc()).limit(250).all()
        found = {str(row.message_id) for row in rows}
        if ids and set(ids) - found:
            raise ValueError('Some source WhatsApp messages are unavailable. Resync Incoming before pushing this proposal.')
        media = [{'id': row.id, 'message_id': row.message_id, 'name': row.media_filename or '',
                  'mime': row.media_mime_type or '', 'kind': row.media_type or '',
                  'stored': row.media_local_path or ''} for row in rows if row.media_type or row.media_local_path]
    from distr.core.integrations.whatsapp.paths import resolve_whatsapp_media_disk_path
    for item in media:
        identity = 'whatsapp:'+item['message_id']
        extension = mimetypes.guess_extension(item['mime']) or '.bin'
        name = item['name'] or item['message_id']+extension
        destination = _destination(root, identity, name)
        try:
            original = Path(resolve_whatsapp_media_disk_path(item['stored'])) if item['stored'] else None
            if original and original.is_file():
                if original.stat().st_size > MAX_MEDIA_BYTES:
                    raise ValueError('Media exceeds the attachment limit.')
                shutil.copyfile(original, destination)
            else:
                _download_whatsapp(item['id'], item['message_id'], destination)
            attachments.append(_record(destination, name, item['mime'], identity, source_message_id=item['message_id']))
        except Exception as exc:
            raise ValueError(f'Could not attach {name}. The proposal was not pushed. Sync WhatsApp media and retry.') from exc
    return attachments


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


def public_thread_attachments(chat_id: int) -> list[dict[str, Any]]:
    return [{key: value for key, value in item.items() if key != 'path'} |
            {'url': f'/api/workflows/studio/tasks/{int(chat_id)}/attachments/{item["id"]}'}
            for item in thread_attachments(chat_id)]


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
