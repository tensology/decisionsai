"""Resolve and append customer follow-ups without replacing earlier work."""
from .followup_policy import rank_followups


def handoff_receipts(params):
    current = params.get('incoming_proposal_handoff')
    rows = [row for row in [*(params.get('incoming_proposal_history') or []), current]
            if isinstance(row, dict) and row.get('key')]
    context = params.get('incoming_reply_context')
    if isinstance(context, dict) and context.get('id'):
        from .proposals import _proposal_handoff_key
        key = _proposal_handoff_key(context)
        if not any(row['key'] == key for row in rows):
            rows.append({'key': key, 'proposal': context, 'started': True})
    return list({row['key']: row for row in rows}.values())


def preserve_original_context(chat_id):
    from distr.core.chat import _chat_params
    from distr.core.db import Chat, get_session
    import json
    for _ in range(5):
        with get_session() as db:
            root = db.get(Chat, chat_id)
            original = root.params
            params = _chat_params(original)
            # Website-created threads may have a reply pack without an Incoming
            # handoff receipt. Retain it before switching the active reply source.
            current_key = (params.get('incoming_proposal_handoff') or {}).get('key')
            params['incoming_proposal_history'] = [row for row in handoff_receipts(params) if row['key'] != current_key]
            if db.query(Chat).filter(Chat.id == chat_id, Chat.params == original).update(
                    {Chat.params: json.dumps(params)}, synchronize_session=False):
                db.commit()
                return
    raise ValueError('The follow-up thread changed. Refresh and retry.')


def followup_thread(proposal, *, include_accepted=False):
    from distr.core.chat import _chat_params
    from distr.core.db import Chat, get_session
    from distr.core.db.workflow import DevelopmentWorkItem
    from .proposals import _proposal_handoff_key
    project = proposal.get('project_id')
    if (type(project) is not int or proposal.get('requires_merge_receipt')
            or str(proposal.get('id') or '').startswith(('local:merge:', 'local:merged:'))):
        return None
    key = _proposal_handoff_key(proposal)
    candidates = []
    with get_session() as db:
        rows = db.query(Chat).join(DevelopmentWorkItem, DevelopmentWorkItem.chat_id == Chat.id).filter(
            Chat.parent_id.is_(None), Chat.project_id == project).all()
        for chat in rows:
            params = _chat_params(chat.params)
            receipts = handoff_receipts(params)
            accepted = next((row for row in receipts if row['key'] == key), None)
            if accepted and include_accepted and accepted.get('followup'):
                return {'thread_id': chat.id, 'status': 'queued'}
            if accepted:
                return None
            status = ((params.get('development') or {}).get('execution') or {}).get('status') or 'idle'
            if status in {'cancelled', 'canceled', 'archived'}:
                continue
            for receipt in receipts:
                original = receipt.get('proposal') or {}
                if original.get('project_id') not in (None, project):
                    continue
                candidates.append({**original, 'project_id': project, 'thread_id': chat.id,
                                   'status': status, 'original_key': receipt['key']})
    ranked = rank_followups(proposal, candidates)
    target = proposal.get('followup_work_request_id')
    if target:
        canonical = [row for row in ranked if row.get('id') == f'tensology:{target}']
        if canonical:
            return canonical[0]
    return ranked[0] if ranked else None


def append_followup(chat_id, proposal, *, start_work):
    from hashlib import sha256
    from distr.core.chat import _chat_params
    from distr.core.db import Chat, get_session
    from distr.core.workflow import development_control as control
    from distr.core.workflow.queued_submissions import record_submission_message, start_next_followup
    from distr.core.workflow.development_harness import development_execution_active
    from .proposals import _proposal_handoff_key, _proposal_prompt

    with get_session() as db:
        root = db.get(Chat, chat_id)
        if root is None or root.project_id != proposal['project_id']:
            raise ValueError('The follow-up thread belongs to another project.')
        status = ((_chat_params(root.params).get('development') or {}).get('execution') or {}).get('status')
        if status in {'cancelled', 'canceled', 'archived'}:
            raise ValueError('Archived or cancelled work cannot be reopened by a follow-up.')
        root.is_archived = False
        db.commit()
    content = 'Customer follow-up to the existing work. Retain the previous investigation and results.\n\n' + _proposal_prompt(proposal)
    command = control.enqueue_command(chat_id, content, source='incoming_followup',
        source_ref='proposal:' + sha256(_proposal_handoff_key(proposal).encode()).hexdigest(), command_type='followup', idempotent=True,
        expected_project_id=proposal['project_id'])
    record_submission_message(chat_id, command['id'], content)
    if start_work and command.get('status') == 'queued' and not development_execution_active(chat_id):
        start_next_followup(chat_id, command['id'], command['modified_at'])
    return command


def append_ticket_followups(ticket, chat_id, project_id, *, included_in_initial_prompt=False):
    """The polling and push paths consume the same accepted server amendments."""
    from .proposals import _HANDOFF_LOCK, _accepted_proposal_keys, _proposal_handoff_key, _record_proposal_handoff
    rows = ticket.get('incoming_followups') or (ticket.get('source_metadata') or {}).get('incoming_followups') or []
    for row in rows:
        if not isinstance(row, dict) or row.get('dispatched') is not True:
            continue
        proposal_id = row.get('proposal_id', row.get('id'))
        if type(proposal_id) is not int:
            continue
        evidence = list(row.get('evidence') or [])
        discussion = next((item for item in ticket.get('discussion') or []
                           if item.get('id') == row.get('discussion_message_id')), None)
        if discussion:
            evidence.append({'id': f"discussion:{discussion['id']}", 'body': discussion.get('content') or '',
                             'direction': 'inbound', 'sent_at': discussion.get('created_at') or ''})
        proposal = {'id': f"tensology:{proposal_id}", 'project_id': project_id,
                    'source': 'tensology', 'source_thread_id': f"ticket:{ticket['id']}",
                    'title': row.get('title') or 'Customer follow-up', 'description': row.get('description') or '',
                    'tasks': row.get('tasks') or [], 'message_ids': row.get('message_ids') or [str(proposal_id)],
                    'evidence': evidence, 'created_at': row.get('created_at') or ''}
        with _HANDOFF_LOCK:
            if _proposal_handoff_key(proposal) in _accepted_proposal_keys(include_terminal=True):
                continue
            if not included_in_initial_prompt:
                append_followup(chat_id, proposal, start_work=True)
            _record_proposal_handoff(chat_id, proposal, project_id=project_id, started=True, followup=True)
