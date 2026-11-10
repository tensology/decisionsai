"""Verified original copies, independent of temporary channel/provider caches."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def remember(record):
    target = Path(record['path']).with_name(Path(record['path']).name + '.record.json')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, suffix='.part', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream)
        temporary.replace(target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def verified(path, identity, item):
    path = Path(path)
    try:
        record = json.loads(path.with_name(path.name + '.record.json').read_text())
        if record.get('id') != hashlib.sha256(identity.encode()).hexdigest()[:24]:
            return None
        if path.stat().st_size != record['size'] or digest(path) != record['sha256']:
            return None
        if item.get('sha256') and item['sha256'] != record['sha256']:
            return None
        if item.get('size') and int(item['size']) != record['size']:
            return None
        return {**record, 'path': str(path.resolve())}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def copy_original(source, destination):
    """A failed copy must not truncate a previously verified original."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.part', delete=False) as output:
            temporary = Path(output.name)
            with Path(source).open('rb') as original:
                shutil.copyfileobj(original, output)
        temporary.replace(destination)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def retained_original(proposal, item, project_id, identity, get_session):
    """Recover only a verified file retained by this same proposal and project."""
    from distr.core.chat import _chat_params
    from distr.core.db import Chat, WhatsAppMessage
    from .proposals import _proposal_handoff_key
    source_id = str(item.get('source_message_id') or '')
    expected_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    key = _proposal_handoff_key(proposal)
    location = str(item.get('api_path') or '')
    if location:
        from .media import _root, _destination
        proxy_identity = 'proxy:' + location
        proxy_path = _destination(_root(None, 'tensology-proxy-cache'), proxy_identity, 'attachment.bin')
        proxy = verified(proxy_path, proxy_identity, item)
        if proxy:
            from .transcription import with_transcript
            return with_transcript(proxy)
    with get_session() as db:
        roots = db.query(Chat.params).filter(Chat.project_id == project_id, Chat.parent_id.is_(None)).yield_per(100)
        for (encoded,) in roots:
            params = _chat_params(encoded)
            if (params.get('incoming_proposal_handoff') or {}).get('key') != key:
                continue
            for record in params.get('incoming_attachments') or []:
                if not (record.get('id') == expected_id or (source_id and record.get('source_message_id') == source_id)):
                    continue
                try:
                    path = Path(record['path'])
                    if record.get('sha256') and digest(path) == record['sha256']:
                        from .transcription import with_transcript
                        return with_transcript(record)
                except (KeyError, OSError):
                    continue
        # A canonical source reference may have arrived before its provider cache
        # was copied. Only use a local row belonging to the original conversation.
        if source_id and proposal.get('source') == 'whatsapp':
            from .source_reply import source_choices
            conversations = {source['thread_id'] for source in source_choices(proposal)
                             if source['channel'] == 'whatsapp' and source_id in source['message_ids']}
            row = db.query(WhatsAppMessage).filter(WhatsAppMessage.message_id == source_id,
                                                 WhatsAppMessage.jid.in_(conversations)).first()
            if row and row.media_local_path:
                from distr.core.integrations.whatsapp.paths import resolve_whatsapp_media_disk_path
                path = Path(resolve_whatsapp_media_disk_path(row.media_local_path))
                if path.is_file():
                    return {'path': str(path), 'mime_type': row.media_mime_type or ''}
    return None


def replicate(client, location, record):
    """Restore the canonical cache when only the desktop copy survived.

    Failure leaves the verified desktop copy usable and a durable retry marker.
    An unconfirmed upload is never described as a second durable copy.
    """
    if record.get('remote_sha256') == record['sha256']:
        return
    try:
        result = client.restore_attachment(location, Path(record['path']),
                                           sha256=record['sha256'], mime_type=record['mime_type'])
        if not isinstance(result, dict) or result.get('success') is not True or result.get('sha256') != record['sha256']:
            raise ValueError('Canonical media copy was not verified.')
        record['remote_sha256'] = record['sha256']
        record.pop('replication_error', None)
    except Exception:
        record['replication_error'] = 'The original is safe locally; its Tensology copy is awaiting synchronization.'
    remember(record)
