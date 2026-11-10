"""Cache canonical source/project candidate links without copying source messages."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

_lock = threading.Lock()
_checked_at = 0.0


def _cache_path():
    return Path.home() / '.decisions/cache/incoming-project-links.json'


def project_links(*, refresh=False, force=False):
    global _checked_at
    path = _cache_path()
    with _lock:
        if force or (refresh and time.monotonic() - _checked_at > 30):
            _checked_at = time.monotonic()
            try:
                from distr.core.tensology_client import configured_tensology_client
                rows = configured_tensology_client(source='incoming-project-links').get('incoming/project-links')
                if isinstance(rows, dict):
                    rows = rows.get('data')
                if isinstance(rows, list):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
                            temporary = Path(handle.name)
                            json.dump(rows, handle)
                        os.replace(temporary, path)
                    finally:
                        if temporary:
                            temporary.unlink(missing_ok=True)
            except Exception:
                pass  # Offline/older servers retain the last successful link snapshot.
        try:
            rows = json.loads(path.read_text(encoding='utf-8'))
            return rows if isinstance(rows, list) else []
        except (OSError, ValueError):
            return []


def apply_project_links(items, *, refresh=False):
    index = source_project_index(refresh=refresh)
    for item in items:
        ids = index.get((str(item.get('source')), str(item.get('source_thread_id'))), set())
        item['project_ids'] = sorted(ids)
        item['project_links_authoritative'] = True
        if len(ids) == 1:
            item['project_id'] = next(iter(ids))
        else:
            item.pop('project_id', None)
    return items


def source_project_index(*, refresh=False):
    """Explicit conversation links only. A group never inherits its sender's links."""
    from distr.core.db import get_session
    from distr.core.db import WhatsAppPhoneLink, WhatsAppMessage
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    links = project_links(refresh=refresh)
    with get_session() as session:
        projects = session.query(Project).filter(Project.deleted_at.is_(None)).all()
        existing = {int(row.id) for row in projects}
        boards = {row.id: row for row in session.query(KanbanBoard).all()}
        index = {}
        for link in session.query(WhatsAppPhoneLink).all():
            board = boards.get(link.board_id)
            ids = {row.id for row in projects if row.kanban_board_id == link.board_id}
            if board and board.default_project_id in existing:
                ids.add(board.default_project_id)
            if board and len(ids) == 1 and link.phone_jid:
                index.setdefault(('whatsapp', str(link.phone_jid)), set()).update(ids)
        # Phone and LID are aliases of the same private conversation. Only use
        # conversation addresses here; a group's sender must never grant access.
        private = {thread for source, thread in index if source == 'whatsapp' and not thread.endswith('@g.us')}
        if private:
            from sqlalchemy import or_
            for jid, phone in session.query(WhatsAppMessage.jid, WhatsAppMessage.jid_phone).filter(
                    or_(WhatsAppMessage.jid.in_(private), WhatsAppMessage.jid_phone.in_(private))).distinct():
                if jid and not str(jid).endswith('@g.us'):
                    ids = index.get(('whatsapp', str(jid)), set()) | index.get(('whatsapp', str(phone)), set())
                    index.setdefault(('whatsapp', str(jid)), set()).update(ids)
                    if phone:
                        index.setdefault(('whatsapp', str(phone)), set()).update(ids)
    for link in links:
        if not isinstance(link, dict):
            continue
        ids = [int(row['decisions_project_id']) for row in link.get('projects') or []
               if isinstance(row, dict) and type(row.get('decisions_project_id')) is int and row['decisions_project_id'] in existing]
        index[(str(link.get('source')), str(link.get('source_thread_id')))] = set(ids)
    return index


def proposal_in_scope(proposal, index=None):
    index = source_project_index() if index is None else index
    source = str(proposal.get('source') or '')
    thread = str(proposal.get('source_thread_id') or (proposal.get('source_conversation') or {}).get('thread_id') or '')
    if not thread and str(proposal.get('dedupe_key') or '').startswith(source + ':'):
        thread = proposal['dedupe_key'][len(source) + 1:]
    if not source or not thread:
        return False
    refs = proposal.get('source_scope') or [item['original_source'] for item in proposal.get('evidence') or []
                                           if isinstance(item, dict) and isinstance(item.get('original_source'), dict)]
    refs = [{'source': source, 'source_thread_id': thread}, *refs]
    selected = proposal.get('project_id')
    return type(selected) is int and bool(refs) and all(
        selected in index.get((str(ref.get('source') or ref.get('channel') or ''), str(ref.get('source_thread_id') or '')), set())
        for ref in refs if isinstance(ref, dict)) and all(isinstance(ref, dict) for ref in refs)
