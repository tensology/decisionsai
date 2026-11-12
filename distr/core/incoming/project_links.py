"""Cache canonical source/project candidate links without copying source messages."""
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import time

_lock = threading.Lock()
_checked_at = 0.0


def _cache_path():
    return Path.home() / '.decisions/cache/incoming-project-links.json'


def project_links(*, refresh=False, force=False, require_fresh=False):
    global _checked_at
    path = _cache_path()
    with _lock:
        if require_fresh or force or (refresh and time.monotonic() - _checked_at > 30):
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
                    if require_fresh:
                        return rows
            except Exception:
                pass  # Offline/older servers retain the last successful link snapshot.
        if require_fresh:
            return None
        try:
            rows = json.loads(path.read_text(encoding='utf-8'))
            return rows if isinstance(rows, list) else []
        except (OSError, ValueError):
            return []


def telegram_group_snapshot():
    """Return current explicit group links to live local projects, or None when unavailable."""
    from distr.core.db import get_session
    from distr.core.db.projects import Project
    from distr.core.integrations.telegram.chat_send import is_group_chat_id

    rows = project_links(require_fresh=True)
    if rows is None:
        return None
    with get_session() as db:
        projects = {project_id for (project_id,) in db.query(Project.id).filter(Project.deleted_at.is_(None))}
    return sorted({
        int(row['source_thread_id']) for row in rows
        if isinstance(row, dict) and row.get('source') == 'telegram' and is_group_chat_id(row.get('source_thread_id'))
        and isinstance(row.get('projects'), list)
        and any(isinstance(project, dict) and type(project.get('decisions_project_id')) is int
                and project['decisions_project_id'] in projects for project in row['projects'])
    })


_MAIL_SOURCES = {'gmail', 'mailshot'}
_SUBJECT_PREFIX_RE = re.compile(r'^\s*((re|fw|fwd|aw|wg)\s*(\[\d+\])?\s*:\s*)+', re.I)


def _norm_subject(value):
    return re.sub(r'\s+', ' ', _SUBJECT_PREFIX_RE.sub('', str(value or ''))).strip().lower()


def _mail_addresses(item):
    return [item.get('sender'), item.get('reply_to'), item.get('recipient')]


def apply_project_links(items, *, refresh=False):
    """Annotate Incoming items with their linked project(s).

    Thread links (Tensology server, plus threads learned from a local address link) come
    first. Mail on an unlinked thread uses a local email-address/domain link made in
    DecisionsAI. Edge cases are flagged instead of being silently dropped or guessed:
    ``project_link_conflict`` (one address or thread, several projects), ``topic_drift``
    (a linked thread changed subject) and ``needs_link`` with ``suggested_project_ids``
    (a forward or new thread that looks like a linked conversation).
    """
    index = source_project_index(refresh=refresh)
    try:
        from distr.core.initiative import attention
    except Exception:  # pragma: no cover - attention is part of the same package tree
        attention = None
    first_subject = {}
    linked_subjects, linked_senders = {}, {}
    flags = []
    for item in items:
        source, thread = str(item.get('source')), str(item.get('source_thread_id'))
        ids = set(index.get((source, thread), set()))
        via = 'thread' if ids else ''
        if not ids and source in _MAIL_SOURCES and attention is not None:
            try:
                ids, via = attention.address_project_ids(*_mail_addresses(item))
                ids = set(ids)
                if ids and thread:
                    attention.learn_thread_link(source, thread, ids, via)
            except Exception:
                ids, via = set(), ''
        item['project_ids'] = sorted(ids)
        item['project_links_authoritative'] = True
        if via and via != 'thread':
            item['project_link_via'] = via
        if len(ids) == 1:
            item['project_id'] = next(iter(ids))
        else:
            item.pop('project_id', None)
        if len(ids) > 1:
            item['project_link_conflict'] = {'kind': 'multiple_projects', 'project_ids': sorted(ids), 'via': via or 'thread'}
            flags.append({'kind': 'conflict', 'source': source, 'thread': thread, 'subject': item.get('subject') or '',
                          'project_ids': sorted(ids), 'via': via or 'thread'})
        if source in _MAIL_SOURCES:
            subject = _norm_subject(item.get('subject'))
            if ids:
                linked_subjects.setdefault(subject, set()).update(ids)
                for email in re.findall(r'[\w.%+\-\']+@[\w.\-]+\.[a-z]{2,}', str(item.get('sender') or ''), re.I):
                    linked_senders.setdefault(email.lower(), set()).update(ids)
                # The thread's topic is its earliest message (by created_at, else first seen).
                order = (str(item.get('created_at') or ''), len(first_subject))
                current = first_subject.get((source, thread))
                if current is None or (order[0] and (not current[0][0] or order[0] < current[0][0])):
                    first_subject[(source, thread)] = (order, subject)
    for item in items:
        source, thread = str(item.get('source')), str(item.get('source_thread_id'))
        if source not in _MAIL_SOURCES:
            continue
        subject = _norm_subject(item.get('subject'))
        if item.get('project_ids'):
            topic = (first_subject.get((source, thread)) or (None, None))[1]
            if topic and subject and subject != topic:
                item['topic_drift'] = True
                item['needs_project_review'] = True
                flags.append({'kind': 'topic_drift', 'source': source, 'thread': thread, 'subject': item.get('subject') or '',
                              'project_ids': item['project_ids']})
            continue
        suggested = set(linked_subjects.get(subject, set())) if subject else set()
        for email in re.findall(r'[\w.%+\-\']+@[\w.\-]+\.[a-z]{2,}', str(item.get('sender') or ''), re.I):
            suggested |= linked_senders.get(email.lower(), set())
        if suggested:
            item['needs_link'] = True
            item['suggested_project_ids'] = sorted(suggested)
            flags.append({'kind': 'needs_link', 'source': source, 'thread': thread, 'subject': item.get('subject') or '',
                          'project_ids': sorted(suggested)})
    if flags and attention is not None:
        try:
            attention.record_link_flags(flags)
        except Exception:
            pass
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
    # Threads routed by a local email-address link made in DecisionsAI. A non-empty server
    # link always wins; an empty server link is filled by the user's explicit address link.
    try:
        from distr.core.initiative.attention import learned_thread_links
        for key, ids in learned_thread_links().items():
            ids = {pid for pid in ids if pid in existing}
            if ids and not index.get(key):
                index[key] = ids
    except Exception:
        pass
    return index


def proposal_in_scope(proposal, index=None, *, allow_review=False):
    if not allow_review and proposal.get('needs_project') is True:
        return False
    index = source_project_index() if index is None else index
    source = str(proposal.get('source') or '')
    thread = str(proposal.get('source_thread_id') or (proposal.get('source_conversation') or {}).get('thread_id') or '')
    if not thread and str(proposal.get('dedupe_key') or '').startswith(source + ':'):
        thread = proposal['dedupe_key'][len(source) + 1:]
    if not source or not thread:
        return False
    refs = [*(proposal.get('source_scope') or []), *[item['original_source'] for item in proposal.get('evidence') or []
                                                  if isinstance(item, dict) and isinstance(item.get('original_source'), dict)]]
    # A merged conversation is a synthetic container, never a source that can
    # grant project access. Authorize every retained original independently.
    if thread.startswith('merged:'):
        refs = [ref for ref in refs if not isinstance(ref, dict) or not str(ref.get('source_thread_id') or '').startswith('merged:')]
        if not refs:
            return False
    else:
        refs = [{'source': source, 'source_thread_id': thread}, *refs]
    selected = proposal.get('project_id')
    if allow_review and selected is None and proposal.get('needs_project') is True:
        return all(isinstance(ref, dict) and bool(index.get((
            str(ref.get('source') or ref.get('channel') or ''), str(ref.get('source_thread_id') or '')), set())) for ref in refs)
    return type(selected) is int and bool(refs) and all(
        selected in index.get((str(ref.get('source') or ref.get('channel') or ''), str(ref.get('source_thread_id') or '')), set())
        for ref in refs if isinstance(ref, dict)) and all(isinstance(ref, dict) for ref in refs)
