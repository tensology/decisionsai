"""Editable queued submissions and their explicitly attached files."""
import json
import time
from pathlib import Path

from distr.core.chat_turns import redact_text
from distr.core.db import Chat
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflowRun, DevelopmentCommand
from distr.core.db.time import utc_now_naive


def submission_metadata(chat_id, metadata):
    from . import development_control as control
    from .development_harness import _safe_turn_attachments
    raw_metadata = dict(metadata or {})
    result = control.redact_metadata({key: value for key, value in raw_metadata.items() if key not in {'composer', 'attachments'}})
    with control.get_session() as db:
        chat = control._require_development_root(db, chat_id)
        project = db.get(Project, chat.project_id) if chat.project_id else None
        folder = project.folder_location if project else ''
    raw_files = raw_metadata.get('attachments') or []
    files = _safe_turn_attachments(raw_files, project_folder=folder or '')
    if len(files) != len(raw_files):
        raise ValueError('An attached file is missing or outside this project. Reattach it before submitting.')
    result['attachments'] = files
    draft = raw_metadata.get('composer')
    if isinstance(draft, dict):
        items = []
        for raw in (draft.get('attachments') or [])[:80]:
            if not isinstance(raw, dict):
                continue
            item = {key: raw[key] for key in ('kind', 'key', 'label', 'reference', 'mime_type', 'size', 'source', 'sourceLabel', 'skill_id', 'suppress_prompt_context', 'hidden_chip') if key in raw}
            item['text'] = redact_text(raw.get('text') or '', limit=48000, preserve_paths=True)
            if raw.get('transcript'):
                item['transcript'] = redact_text(raw['transcript'], limit=48000, preserve_paths=True)
            if item.get('kind') == 'file' and not any(row['path'] == str(Path(str(item.get('reference') or '')).expanduser().resolve()) for row in files):
                raise ValueError('The composer file does not match an attached file.')
            items.append(item)
        result['composer'] = {'text': redact_text(draft.get('text') or '', limit=48000, preserve_paths=True),
                              'attachments': items, 'use_playwright': bool(draft.get('use_playwright'))}
    return result


def command_file(command_id, index):
    from . import development_control as control
    command = control.list_commands_for_id(command_id)
    if not command:
        raise FileNotFoundError('Queued item not found.')
    metadata = submission_metadata(command['chat_id'], command['metadata'])
    files = metadata['attachments']
    if index < 0 or index >= len(files):
        raise FileNotFoundError('Attachment not found.')
    return files[index]


def record_submission_message(chat_id, command_id, content):
    """A retry starts the same queued turn without duplicating its user message."""
    from . import development_control as control
    from distr.core.chat import ChatService
    with control.get_session() as db:
        messages = db.query(Chat).filter(Chat.parent_id == chat_id).all()
        if any(control._json(row.params, {}).get('development_turn', {}).get('queued_command_id') == command_id for row in messages):
            return
    ChatService.add_user_message(chat_id, content, metadata={'queued_command_id': command_id})


def run_submission(command_id, *, new_thread=False):
    """Claim once, then interrupt-and-steer or start a separate project thread."""
    from . import development_control as control
    from . import development_harness as harness
    from .development_threads import ensure_development_thread
    from distr.core.turn_runtime import active_turn_registered

    with control.get_session() as db:
        row = db.get(DevelopmentCommand, command_id)
        if row is None:
            raise LookupError('Queued item not found.')
        if row.status == 'delivered':
            return {'chat_id': control._json(row.metadata_json, {}).get('started_chat_id') or row.chat_id, 'already_started': True}
        changed = db.query(DevelopmentCommand).filter_by(id=command_id, status='queued').update({'status': 'dispatching'})
        if not changed:
            raise ValueError('This item is being edited or has already changed. Refresh the queue.')
        metadata = control._json(row.metadata_json, {})
        chat_id, content = row.chat_id, row.content
        root = control._require_development_root(db, chat_id)
        project_id = root.project_id
        preferences = {key: getattr(root, key) for key in ('route_mode', 'execution_profile', 'autonomy_level', 'provider', 'model_name')}
        source_settings = control._json(root.params, {}).get('development') or {}
        if not new_thread and source_settings.get("proactive_dispatch"):
            db.rollback()
            raise ValueError("This item stays queued while the approved work is unresolved.")
        inherited_settings = {key: source_settings[key] for key in ('model_route', 'permission_profile') if key in source_settings}
        active_workflow = db.query(AutoWorkflowRun.id).filter(AutoWorkflowRun.chat_id == chat_id, AutoWorkflowRun.status.in_(['running', 'waiting'])).first() is not None
        db.commit()
    try:
        metadata = submission_metadata(chat_id, metadata)
        target = chat_id
        if new_thread:
            target = ensure_development_thread(title=content.split('\n')[0][:120], project_id=project_id,
                starting_question=content, source_type='queued_instruction', source_ref=str(command_id))
            with control.get_session() as db:
                fork = db.get(Chat, target)
                for key, value in preferences.items():
                    setattr(fork, key, value)
                params = control._json(fork.params, {})
                params['development'] = {**params.get('development', {}), **inherited_settings}
                fork.params = json.dumps(params)
                row = db.get(DevelopmentCommand, command_id)
                row.metadata_json = json.dumps({**metadata, 'started_chat_id': target})
                db.commit()
        elif harness.development_execution_active(chat_id) or active_workflow:
            harness.stop_development_execution(chat_id)
            deadline = time.monotonic() + 10
            while active_turn_registered(chat_id):
                if time.monotonic() >= deadline:
                    raise ValueError('The worker is still stopping. Your item remains queued; try Steer again shortly.')
                time.sleep(.05)
        active = harness.development_execution_active(target)
        if active and not new_thread:
            raise ValueError('The worker is still running. Your item remains queued.')
        if not active:
            if not new_thread:
                record_submission_message(target, command_id, content)
            harness.dispatch_development_prompt(target, content, attachments=metadata['attachments'],
                skill_ids=metadata.get('skill_ids') or [], use_playwright=bool(metadata.get('composer', {}).get('use_playwright')))
        with control.get_session() as db:
            row = db.get(DevelopmentCommand, command_id)
            row.status = 'delivered'
            row.delivered_at = utc_now_naive()
            row.result_summary = 'Started in a new thread.' if new_thread else 'Applied as the next turn in this thread.'
            db.commit()
        return {'chat_id': target, 'started': True}
    except Exception:
        with control.get_session() as db:
            row = db.get(DevelopmentCommand, command_id)
            if row and row.status == 'dispatching':
                row.status = 'queued'
                db.commit()
        raise
