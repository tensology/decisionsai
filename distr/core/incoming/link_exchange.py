"""Remote-first project-link deltas shared by mail and WhatsApp controls."""
import hashlib
import json


def sync_project_delta(source, thread_id, project_id, *, sender=None, remove=False, aliases=()):
    return sync_project_deltas(source, thread_id, [project_id], sender=sender, remove=remove, aliases=aliases)


def sync_project_deltas(source, thread_id, project_ids, *, sender=None, remove=False, aliases=()):
    """Apply a selected set atomically, including sender-wide remove-all."""
    from distr.core.settings import load_settings_from_db
    if not load_settings_from_db().get('tensology_enabled'):
        return {'synced': False}
    from distr.core.tensology_client import configured_tensology_client, TensologyApiError
    from .proposal_sync import _mapped_projects
    from . import project_links

    client = configured_tensology_client(source='incoming-project-link-change')
    mapped = _mapped_projects(client)
    if not project_ids or any(project_id not in mapped for project_id in project_ids):
        raise ValueError('Link this project to Tensology before changing its conversation links.')
    selected = {mapped[project_id] for project_id in project_ids}
    rows = client.get('incoming/project-links')
    if not isinstance(rows, list):
        raise TensologyApiError('The current project links could not be confirmed.')
    rows = [row for row in rows if isinstance(row, dict)]
    current = next((row for row in rows if row.get('source') == source and row.get('source_thread_id') == thread_id), None)
    if not any('sender_revision' in row or row.get('source') == 'mail_sender' for row in rows):
        identity = client.get('identity')
        if not isinstance(identity, dict) or type(identity.get('project_links_version')) is not int or identity['project_links_version'] < 2:
            raise TensologyApiError('Update Tensology to support bidirectional project links before saving this change.')
    if current is not None and 'sender_revision' not in current:
        raise TensologyApiError('Update Tensology to support bidirectional project links before saving this change.')
    body = {'source': source, 'source_thread_id': thread_id, 'project_ids': sorted(selected),
            'scope': 'sender' if sender else 'conversation', 'action': 'remove' if remove else 'add',
            'revision': (current or {}).get('revision', '')}
    if source == 'whatsapp':
        alias_rows = [row for row in rows if row.get('source') == source and row.get('source_thread_id') in aliases
                      and row.get('source_thread_id') != thread_id
                      and (row.get('projects') or row.get('link_explicit') is not False or row.get('is_archived'))]
        if alias_rows:
            identity = client.get('identity')
            if not isinstance(identity, dict) or type(identity.get('project_links_version')) is not int or identity['project_links_version'] < 3:
                raise TensologyApiError('Update Tensology to support atomic WhatsApp alias linking before saving this change.')
            body['alias_revisions'] = {row['source_thread_id']: row.get('revision') for row in alias_rows}
            if any(not isinstance(revision, str) for revision in body['alias_revisions'].values()):
                raise TensologyApiError('Tensology did not provide the WhatsApp alias revisions. Refresh before retrying.')
    if sender:
        sender_row = next((row for row in rows if row.get('source') == 'mail_sender' and row.get('source_thread_id') == sender), None)
        body.update(sender_email=sender, sender_revision=(sender_row or {}).get('revision', (current or {}).get('sender_revision', '')))
        if body['sender_revision'] is None:
            raise TensologyApiError('Tensology needs the project-link update before this change can sync.')
    suffix = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    saved = client.patch('incoming/project-links', body, idempotency_key=f'project-link-{suffix}')
    confirmed = saved.get('links', [saved]) if isinstance(saved, dict) else None
    if not isinstance(confirmed, list) or any(not isinstance(row, dict) for row in confirmed):
        raise TensologyApiError('Tensology did not confirm the project-link change.')
    target_key = ('mail_sender', sender) if sender else (source, thread_id)
    target = next((row for row in confirmed if (row.get('source'), row.get('source_thread_id')) == target_key), None)
    ids = {row.get('id') for row in (target or {}).get('projects') or [] if isinstance(row, dict)}
    if target is None or (bool(selected & ids) if remove else not selected <= ids):
        raise TensologyApiError('Tensology did not confirm the project-link change.')
    for alias in body.get('alias_revisions', {}):
        row = next((row for row in confirmed if row.get('source') == source and row.get('source_thread_id') == alias), None)
        projects = {project.get('id') for project in (row or {}).get('projects') or []}
        if row is None or (bool(selected & projects) if remove else not selected <= projects):
            raise TensologyApiError('Tensology did not confirm the change for every WhatsApp conversation alias.')
    if remove and sender:
        affected = [row for row in confirmed if row.get('source') in {'gmail', 'mailshot'}
                    and (row.get('sender_email') == sender or (row.get('source'), row.get('source_thread_id')) == (source, thread_id))]
        if any(selected & {project.get('id') for project in row.get('projects') or []} for row in affected):
            raise TensologyApiError('Tensology has not removed this project from every conversation for the sender.')
    with project_links._lock:
        keys = {(row.get('source'), row.get('source_thread_id')) for row in confirmed}
        combined = [row for row in rows if (row.get('source'), row.get('source_thread_id')) not in keys] + confirmed
        project_links._store_links(combined)
    return {'synced': True, 'links': confirmed,
            'project_ids': sorted({row['decisions_project_id'] for row in target.get('projects') or []
                                   if isinstance(row, dict) and type(row.get('decisions_project_id')) is int})}
