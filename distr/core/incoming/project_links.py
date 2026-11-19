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


def _store_links(rows):
    path = _cache_path()
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


def project_links(*, refresh=False, force=False, require_fresh=False):
    global _checked_at
    path = _cache_path()
    with _lock:
        if require_fresh or force or (refresh and time.monotonic() - _checked_at > 30):
            _checked_at = time.monotonic()
            try:
                from distr.core.tensology_client import configured_tensology_client
                client = configured_tensology_client(source='incoming-project-links')
                rows = client.get('incoming/project-links')
                if isinstance(rows, dict):
                    rows = rows.get('data')
                if isinstance(rows, list):
                    _store_links(rows)
                    if any(isinstance(row, dict) and row.get('source') == 'whatsapp' and 'link_explicit' in row for row in rows):
                        from .proposal_sync import _mapped_projects
                        from .whatsapp_links import apply_remote_whatsapp_links
                        apply_remote_whatsapp_links(rows, mapped_project_ids=set(_mapped_projects(client)))
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


def _sender_email(value):
    from email.utils import parseaddr
    address = parseaddr(str(value or ''))[1].strip().lower()
    return address if re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', address) else ''


def _sender_projects(rows):
    return {row['source_thread_id']: row.get('projects') or [] for row in rows
            if isinstance(row, dict) and row.get('source') == 'mail_sender'
            and (row.get('projects') or row.get('link_explicit') is not False)}


def _effective_projects(row, senders):
    if row.get('is_archived'):
        return []
    sender = row.get('sender_email')
    if sender in senders and 'direct_projects' in row:
        return [*(row.get('direct_projects') or []), *senders[sender]]
    return row.get('projects') or []


def _sender_id_index(senders):
    from distr.core.initiative.attention import address_links
    ids = {key.removeprefix('email:'): set(row.get('project_ids') or [])
           for key, row in address_links().items() if key.startswith('email:')}
    ids.update({email: {row.get('decisions_project_id') for row in projects} for email, projects in senders.items()})
    return ids


def mail_project_links():
    from distr.core.db import IncomingMailProjectLink, get_session
    from distr.core.db.projects import Project

    with get_session() as db:
        result = {
            (row.source, row.thread_id): (row.project_id, row.message_id)
            for row in db.query(IncomingMailProjectLink).join(Project, Project.id == IncomingMailProjectLink.project_id)
            .filter(Project.deleted_at.is_(None), Project.agent_ignored.is_not(True))
        }
        available = {row.id for row in db.query(Project).filter(Project.deleted_at.is_(None), Project.agent_ignored.is_not(True))}
    rows = project_links()
    senders = _sender_projects(rows)
    from distr.core.initiative.attention import learned_thread_links
    learned = learned_thread_links(_sender_id_index(senders))
    for key in list(result):
        if key in learned:
            ids = learned[key] & available
            if len(ids) == 1:
                result[key] = (next(iter(ids)), result[key][1])
            else:
                result.pop(key)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get('projects') and row.get('link_explicit') is False and not row.get('is_archived'):
            continue
        key = (row.get('source'), row.get('source_thread_id'))
        if key not in result:
            continue
        ids = {project.get('decisions_project_id') for project in _effective_projects(row, senders)
               if isinstance(project, dict) and type(project.get('decisions_project_id')) is int
               and project['decisions_project_id'] in available}
        if len(ids) == 1:
            result[key] = (ids.pop(), result[key][1])
        else:
            result.pop(key)  # Removed/ambiguous canonical links must not retarget work to an old local project.
    return result


def verified_mail_sender(source, thread_id, message_id):
    from distr.core.incoming.proposal_conversation import _mail_conversation
    messages, _ = _mail_conversation(source, thread_id, [message_id])
    message = next((row for row in messages if row.get('source_message_id') == message_id), None)
    sender = _sender_email(message.get('sender')) if message else ''
    if not sender or message.get('from_me') or 'SENT' in (message.get('labels') or []):
        raise ValueError('Choose a received message with a valid sender email address.')
    return sender


def set_mail_project_link(source, thread_id, message_id, project_id, *, remove=False):
    from sqlalchemy.dialects.sqlite import insert
    from distr.core.db import IncomingMailProjectLink, get_session
    from distr.core.db.projects import Project
    from distr.core.incoming.proposals import invalidate_proposals_list_cache

    if source not in _MAIL_SOURCES or any(not re.fullmatch(r"[\w.@:+-]{1,255}", value or "") for value in (thread_id, message_id)):
        raise ValueError("Choose a Gmail or Mailshot conversation.")
    with get_session() as db:
        project = db.get(Project, project_id)
        if not project or project.deleted_at is not None or (project.agent_ignored and not remove):
            raise ValueError("Choose an active project that is available to Incoming.")
        result = {"project_id": project.id, "project_name": project.name}
    sender = verified_mail_sender(source, thread_id, message_id)
    result['sender_email'] = sender
    from distr.core.initiative import attention
    from .link_exchange import sync_project_delta
    changed = sync_project_delta(source, thread_id, project_id, sender=sender, remove=remove)
    result['synced'] = changed['synced']
    existing_ids, _ = attention.address_project_ids(sender)
    ids = changed.get('project_ids', sorted(set(existing_ids) - {project_id} if remove else set(existing_ids) | {project_id}))
    attention.set_sender_projects(sender, ids)
    attention.learn_thread_link(source, thread_id, ids, 'email:' + sender)
    with get_session() as db:
        if remove:
            # Canonical cache tombstones also cover older local conversation rows.
            learned = attention.load()['learned_thread_links']
            keys = {(channel, thread) for key, row in learned.items()
                    for channel, _, thread in [key.partition('|')] if row.get('via') == 'email:' + sender}
            keys.add((source, thread_id))
            for channel, thread in keys:
                db.query(IncomingMailProjectLink).filter_by(source=channel, thread_id=thread, project_id=project_id).delete()
        else:
            values = {"project_id": project_id, "message_id": message_id}
            db.execute(insert(IncomingMailProjectLink).values(source=source, thread_id=thread_id, **values)
                       .on_conflict_do_update(index_elements=["source", "thread_id"], set_=values))
    result.update(project_ids=ids, removed=remove)
    invalidate_proposals_list_cache()
    return result


def sync_address_projects(email, project_ids, *, remove=False):
    """Keep address-only UI/tool edits on the same exchange as Incoming edits."""
    from distr.core.settings import load_settings_from_db
    if not load_settings_from_db().get('tensology_enabled'):
        return {'synced': False}
    rows = project_links(force=True, require_fresh=True)
    if rows is None:
        raise ValueError('Tensology project links could not be refreshed. No local link was changed.')
    # A canonical sender identity is sufficient. Never invent a conversation ID
    # for an address-only control or use a recipient as a received sender.
    anchor = next((row for row in rows if row.get('source') in _MAIL_SOURCES
                   and row.get('sender_email') == email and not row.get('is_archived')), None)
    if anchor is None:
        from distr.core.db import IncomingMailProjectLink, get_session
        with get_session() as db:
            candidates = [(row.source, row.thread_id, row.message_id) for row in db.query(IncomingMailProjectLink)]
        for source, thread, message in candidates:
            try:
                if verified_mail_sender(source, thread, message) == email:
                    anchor = {'source': source, 'source_thread_id': thread}
                    break
            except ValueError:
                continue
    if anchor is None:
        raise ValueError('Open a received Gmail or Mailshot conversation from this sender and manage its projects there so the link can synchronize.')
    if project_ids is None:
        from distr.core.initiative import attention
        local, _ = attention.address_project_ids(email)
        sender = next((row for row in rows if row.get('source') == 'mail_sender' and row.get('source_thread_id') == email), {})
        project_ids = sorted(local | {project['decisions_project_id'] for project in sender.get('projects') or []
                                     if type(project.get('decisions_project_id')) is int})
    if not project_ids:
        return {'synced': True, 'project_ids': []}
    from .link_exchange import sync_project_deltas
    return sync_project_deltas(anchor['source'], anchor['source_thread_id'], project_ids, sender=email, remove=remove)


def _norm_subject(value):
    return re.sub(r'\s+', ' ', _SUBJECT_PREFIX_RE.sub('', str(value or ''))).strip().lower()


def sync_whatsapp_project_link(board_id, thread_id, *, remove=False):
    from distr.core.settings import load_settings_from_db
    if not load_settings_from_db().get('tensology_enabled'):
        return False
    from distr.core.db import get_session
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project
    with get_session() as db:
        board = db.get(KanbanBoard, board_id)
        if board is None:
            raise ValueError('Choose an existing project board.')
        projects = db.query(Project).filter(Project.deleted_at.is_(None)).all()
        ids = {row.id for row in projects if row.kanban_board_id == board_id}
        if board.default_project_id in {row.id for row in projects}:
            ids = {board.default_project_id}
        if len(ids) != 1:
            raise ValueError('Choose a board linked to exactly one project before syncing WhatsApp.')
        project_id = next(iter(ids))
        from .whatsapp_links import whatsapp_alias_pairs, whatsapp_conversation_aliases
        aliases = whatsapp_conversation_aliases([{'source': 'whatsapp', 'source_thread_id': thread_id, 'projects': []}],
                                               alias_pairs=whatsapp_alias_pairs(db)).get(thread_id, {thread_id})
    from .link_exchange import sync_project_delta
    result = sync_project_delta('whatsapp', thread_id, project_id, remove=remove, aliases=aliases)
    from .proposals import invalidate_proposals_list_cache
    invalidate_proposals_list_cache()
    return result['synced']


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
    from distr.core.incoming.telegram_topics import split_telegram_source

    index = source_project_index(refresh=refresh)
    senders = _sender_projects(project_links())
    try:
        from distr.core.initiative import attention
    except Exception:  # pragma: no cover - attention is part of the same package tree
        attention = None
    first_subject = {}
    linked_subjects, linked_senders = {}, {}
    flags = []
    for item in items:
        source, thread = str(item.get('source')), str(item.get('source_thread_id'))
        linked_thread = split_telegram_source(thread)[0] if source == "telegram" else thread
        ids = set(index.get((source, linked_thread), set()))
        via = 'thread' if ids else ''
        if not ids and (source, linked_thread) not in index and source in _MAIL_SOURCES and attention is not None:
            try:
                sender = _sender_email(item.get('sender')) if not item.get('from_me') else ''
                if sender in senders:
                    ids = {row.get('decisions_project_id') for row in senders[sender]
                           if type(row.get('decisions_project_id')) is int}
                    via = 'email:' + sender
                else:
                    ids, via = attention.address_project_ids(sender) if sender else (set(), '')
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
    links = [row for row in project_links(refresh=refresh) if isinstance(row, dict)
             and (row.get('projects') or row.get('link_explicit') is not False or row.get('is_archived'))]
    senders = _sender_projects(links)
    with get_session() as session:
        projects = session.query(Project).filter(Project.deleted_at.is_(None)).all()
        from .whatsapp_links import whatsapp_alias_pairs
        alias_pairs = whatsapp_alias_pairs(session)
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
        ids = [int(row['decisions_project_id']) for row in _effective_projects(link, senders)
               if isinstance(row, dict) and type(row.get('decisions_project_id')) is int and row['decisions_project_id'] in existing]
        index[(str(link.get('source')), str(link.get('source_thread_id')))] = set(ids)
    from .whatsapp_links import canonical_whatsapp_scopes
    for thread, ids in canonical_whatsapp_scopes(links, alias_pairs=alias_pairs).items():
        index[('whatsapp', thread)] = set(ids) & existing
    # Threads routed by a local email-address link made in DecisionsAI. A non-empty server
    # link always wins; an empty server link is filled by the user's explicit address link.
    try:
        from distr.core.initiative.attention import learned_thread_links
        sender_ids = _sender_id_index(senders)
        for key, ids in learned_thread_links(sender_ids).items():
            ids = {pid for pid in ids if pid in existing}
            if key not in index:
                index[key] = ids
    except Exception:
        pass
    server_keys = {(str(row.get('source')), str(row.get('source_thread_id'))) for row in links if isinstance(row, dict)}
    for key, (project_id, _) in mail_project_links().items():
        if key not in server_keys and key not in index:
            index[key] = {project_id}
    return index


def proposal_in_scope(proposal, index=None, *, allow_review=False):
    from distr.core.incoming.telegram_topics import split_telegram_source

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

    def linked_projects(ref):
        if not isinstance(ref, dict):
            return set()
        channel = str(ref.get('source') or ref.get('channel') or '')
        conversation = str(ref.get('source_thread_id') or '')
        key = channel, conversation
        if channel == 'telegram' and key not in index:
            conversation, _ = split_telegram_source(conversation)
            key = channel, conversation
        return index.get(key, set())

    selected = proposal.get('project_id')
    if allow_review and selected is None and proposal.get('needs_project') is True:
        return all(linked_projects(ref) for ref in refs)
    return type(selected) is int and bool(refs) and all(selected in linked_projects(ref) for ref in refs)
