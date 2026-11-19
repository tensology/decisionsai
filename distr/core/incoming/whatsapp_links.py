"""Project canonical WhatsApp conversation links into local project boards."""
from distr.core.kanban.whatsapp_jid import is_whatsapp_group_jid, whatsapp_jid_match_keys, whatsapp_jid_local_part


def _canonical_whatsapp_data(rows, alias_pairs):
    parent = {}

    def root(key):
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def join(keys):
        keys = [key for key in keys if key]
        if keys:
            first = root(keys[0])
            for key in keys[1:]:
                parent[root(key)] = first

    for jid, phone in alias_pairs:
        if not jid or is_whatsapp_group_jid(jid) or is_whatsapp_group_jid(phone):
            continue
        keys = whatsapp_jid_match_keys(jid, phone)
        if phone and '@' not in phone:
            keys.extend([phone + '@s.whatsapp.net', phone + '@lid'])
        join(keys)
    records, scopes, families = [], {}, {}
    for row in rows:
        if not isinstance(row, dict) or row.get('source') != 'whatsapp':
            continue
        thread = str(row.get('source_thread_id') or '').strip()
        if not thread:
            continue
        ids = {project['decisions_project_id'] for project in row.get('projects') or []
               if isinstance(project, dict) and type(project.get('decisions_project_id')) is int}
        if not ids and row.get('link_explicit') is False and not row.get('is_archived'):
            continue
        if row.get('is_archived'):
            ids = set()
        if is_whatsapp_group_jid(thread):
            scopes[thread] = ids
            families[thread] = {thread}
        else:
            join(whatsapp_jid_match_keys(thread))
            records.append((thread, ids))
    memberships = {}
    for thread, ids in records:
        group = root(thread)
        memberships[group] = memberships.get(group, ids) & ids
    components = {}
    for key in parent:
        components.setdefault(root(key), set()).add(key)
    for group, ids in memberships.items():
        for key in components[group]:
            scopes[key] = ids
            families[key] = components[group]
    return scopes, families


def canonical_whatsapp_scopes(rows, *, alias_pairs=()):
    """Conflicting private alias rows use their common grants, never revive removals."""
    return _canonical_whatsapp_data(rows, alias_pairs)[0]


def whatsapp_conversation_aliases(rows, *, alias_pairs=()):
    """Same-conversation identities for presentation, without group-member aliases."""
    return _canonical_whatsapp_data(rows, alias_pairs)[1]


def whatsapp_alias_pairs(session):
    from distr.core.db import WhatsAppMessage, WhatsAppPhoneLink
    return [*session.query(WhatsAppMessage.jid, WhatsAppMessage.jid_phone).distinct().all(),
            *session.query(WhatsAppPhoneLink.phone_jid, WhatsAppPhoneLink.phone_number).all()]


def apply_remote_whatsapp_links(rows, *, mapped_project_ids):
    """Only change explicitly supplied source scopes on mapped, active local boards."""
    from distr.core.db import get_session, WhatsAppPhoneLink
    from distr.core.db.projects import Project
    from distr.core.db.kanban import KanbanBoard
    from distr.core.kanban.lifecycle import ensure_delivery_lanes
    from distr.core.kanban.whatsapp_jid import upsert_whatsapp_phone_link

    mapped = {value for value in mapped_project_ids if type(value) is int}
    with get_session() as db:
        scopes, families = _canonical_whatsapp_data(rows, whatsapp_alias_pairs(db))
        available = {project.id: project for project in db.query(Project).filter(Project.deleted_at.is_(None))}
        projects = {pid: project for pid, project in available.items() if pid in mapped and not project.agent_ignored}
        boards = {board.id: board for board in db.query(KanbanBoard).filter(KanbanBoard.archived.is_not(True))
                  if board.source in (None, '', 'database', 'local')}
        board_projects = {}
        represented = set()
        for board in boards.values():
            ids = {pid for pid, project in available.items() if project.kanban_board_id == board.id or board.default_project_id == pid}
            represented.update(ids)
            # One physical board link cannot encode separate decisions for
            # several projects. Preserve ambiguous/local-only board ownership.
            if len(ids) == 1 and ids <= projects.keys():
                board_projects[board.id] = ids
        wanted_projects = set().union(*scopes.values()) & projects.keys() if scopes else set()
        for pid in sorted(wanted_projects - represented):
            project = projects[pid]
            board = KanbanBoard(name=project.name or 'Untitled', source='database', default_project_id=pid, archived=False)
            db.add(board)
            db.flush()
            project.kanban_board_id = board.id
            ensure_delivery_lanes(db, board.id)
            boards[board.id] = board
            board_projects[board.id] = {pid}
        links = db.query(WhatsAppPhoneLink).all()
        for link in list(links):
            if link.board_id not in board_projects:
                continue
            key = link.phone_jid if link.phone_jid in scopes else None
            if key is None and not is_whatsapp_group_jid(link.phone_jid) and link.phone_number in scopes:
                key = link.phone_number
            if key is not None and not scopes[key].intersection(board_projects[link.board_id]):
                db.delete(link)
                links.remove(link)
        for row in rows:
            if not isinstance(row, dict) or row.get('source') != 'whatsapp':
                continue
            thread = str(row.get('source_thread_id') or '')
            if thread not in scopes:
                continue
            aliases = families[thread]
            for board_id, ids in board_projects.items():
                if not scopes[thread].intersection(ids):
                    continue
                existing = next((link for link in links if link.board_id == board_id
                                 and (link.phone_jid in aliases or (not is_whatsapp_group_jid(link.phone_jid) and link.phone_number in aliases))), None)
                if existing:
                    if not existing.contact_name and row.get('label'):
                        existing.contact_name = str(row['label'])
                    continue
                link, _ = upsert_whatsapp_phone_link(db, board_id=board_id, phone_jid=thread,
                    phone_number=whatsapp_jid_local_part(thread), contact_name=row.get('label') or '',
                    auto_snapshot=False, board_scoped=True)
                links.append(link)
