"""Public triage approval must retain scope, provenance and actual outcomes."""
import contextlib
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, WhatsAppMessage, WhatsAppPhoneLink
from distr.core.db.projects import Project
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
from distr.core.initiative.draft_queue import DraftQueue
from distr.core.initiative import draft_execute
from distr.core.orchestrator_daily_triage import build_daily_triage, enqueue_triage_candidates


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "isolated.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    @contextlib.contextmanager
    def session():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
    monkeypatch.setattr('distr.core.db.get_session', session)
    monkeypatch.setattr('distr.core.orchestrator.emit_event', Mock())
    with session() as db:
        db.add_all([Project(id=1, name='One'), Project(id=2, name='Other')])
        db.add_all([KanbanBoard(id=1, name='One board', default_project_id=1), KanbanBoard(id=2, name='Other board', default_project_id=2)])
        db.add_all([KanbanLane(id=1, board_id=1, name='Inbox'), KanbanLane(id=2, board_id=2, name='Inbox')])
        db.add(WhatsAppPhoneLink(phone_jid='client@s.whatsapp.net', board_id=1))
        db.add(WhatsAppMessage(id=1, message_id='wa-1', jid='client@s.whatsapp.net', text='Fix the page', whatsapp_timestamp=1))
    return factory


def proposal(provenance=None):
    payload = {'source': 'whatsapp', 'message_ids': [1], 'linked_board_id': 1, 'linked_project_id': 1, 'jid': 'client@s.whatsapp.net'}
    if provenance:
        payload['provenance'] = provenance
    return {'action_type': 'message_triage', 'description': 'Fix this project bug', 'payload': payload}


def queued(tmp_path, scan, suffix=''):
    queue = DraftQueue(path=str(tmp_path / f'queue{suffix}.json'))
    candidates = build_daily_triage(work_scan=scan)['candidates']
    assert enqueue_triage_candidates(queue, candidates) == 1
    return queue, queue.get_all()[0]


@pytest.mark.parametrize('provenance', ['project_oversight', 'persisted_planning'])
def test_derived_read_only_proposal_cannot_create_work_through_public_approval(store, tmp_path, provenance):
    queue, entry = queued(tmp_path, {'proposals': [proposal(provenance)]})
    assert entry.execute_payload['provenance'] == provenance
    assert not draft_execute.approve_draft_in_queue(queue, entry.id)
    retained = DraftQueue(path=str(tmp_path / 'queue.json')).get_by_id(entry.id)
    assert 'read-only project recommendation' in retained.reason
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


def test_raw_grouped_message_candidate_creates_the_requested_scoped_ticket(store, tmp_path):
    queue, entry = queued(tmp_path, {'messages': {'whatsapp': [{'id': 1, 'project_id': 1, 'linked_board_id': 1,
        'source_thread_id': 'client@s.whatsapp.net', 'text_preview': 'Fix the page'}]}})
    assert draft_execute.approve_draft_in_queue(queue, entry.id)
    with store() as db:
        ticket = db.query(KanbanTicket).one()
        assert ticket.lane_id == 1 and ticket.linked_project_id == 1


def test_changed_board_link_rejects_stale_approval_and_retains_draft(store, tmp_path):
    queue, entry = queued(tmp_path, {'proposals': [proposal()]})
    with store() as db:
        db.query(WhatsAppPhoneLink).one().board_id = 2
        db.commit()
    assert not draft_execute.approve_draft_in_queue(queue, entry.id)
    assert queue.get_by_id(entry.id) is not None
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


def test_repeated_source_approval_reuses_ticket_instead_of_duplicate_work(store, tmp_path):
    queue, entry = queued(tmp_path, {'proposals': [proposal()]})
    assert draft_execute.approve_draft_in_queue(queue, entry.id)
    queue2, entry2 = queued(tmp_path, {'proposals': [proposal()]}, 'second')
    assert draft_execute.approve_draft_in_queue(queue2, entry2.id)
    with store() as db:
        assert db.query(KanbanTicket).count() == 1


def test_unsupported_requested_execution_does_not_disappear_as_acknowledged(store, tmp_path):
    queue, entry = queued(tmp_path, {'proposals': [{'action_type': 'workflow_start', 'description': 'Run this linked workflow', 'payload': {'workflow_id': 99, 'project_id': 1}}]})
    assert not draft_execute.approve_draft_in_queue(queue, entry.id)
    assert queue.get_by_id(entry.id) is not None


@pytest.mark.parametrize('mutation', ['archived_board', 'deleted_project', 'changed_board_project', 'missing_message', 'mixed_conversations', 'already_processed'])
def test_current_scope_and_complete_source_batch_are_verified_before_creation(store, tmp_path, mutation):
    item = proposal()
    if mutation in {'missing_message', 'mixed_conversations'}:
        item['payload']['message_ids'].append(2)
    queue, entry = queued(tmp_path, {'proposals': [item]})
    with store() as db:
        if mutation == 'archived_board':
            db.get(KanbanBoard, 1).archived = True
        elif mutation == 'deleted_project':
            from datetime import datetime, timezone
            db.get(Project, 1).deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        elif mutation == 'changed_board_project':
            db.get(KanbanBoard, 1).default_project_id = 2
        elif mutation == 'mixed_conversations':
            db.add(WhatsAppMessage(id=2, message_id='wa-2', jid='other@s.whatsapp.net', text='Other project source'))
        elif mutation == 'already_processed':
            db.get(WhatsAppMessage, 1).processed = True
        db.commit()
    assert not draft_execute.approve_draft_in_queue(queue, entry.id)
    assert queue.get_by_id(entry.id) is not None
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


@pytest.mark.parametrize('key,value', [('linked_project_id', None), ('linked_project_id', True), ('linked_project_id', 1.5), ('message_ids', [1, True]), ('message_ids', [1, '1other']), ('jid', None)])
def test_missing_or_malformed_scope_does_not_fall_back(store, tmp_path, key, value):
    item = proposal()
    item['payload'][key] = value
    queue, entry = queued(tmp_path, {'proposals': [item]})
    assert not draft_execute.approve_draft_in_queue(queue, entry.id)
    with store() as db:
        assert db.query(KanbanTicket).count() == 0


def test_concurrent_public_approvals_create_at_most_one_source_ticket(store, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    queue, entry = queued(tmp_path, {'proposals': [proposal()]})
    barrier = Barrier(2)
    original = draft_execute._create_whatsapp_snapshot_ticket
    def synchronized(**kwargs):
        barrier.wait(timeout=5)
        return original(**kwargs)
    monkeypatch.setattr(draft_execute, '_create_whatsapp_snapshot_ticket', synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: draft_execute.approve_draft_in_queue(queue, entry.id), range(2)))
    assert sum(results) == 1
    with store() as db:
        assert db.query(KanbanTicket).count() == 1


def test_ticket_commit_then_event_failure_is_safe_to_retry(store, tmp_path, monkeypatch):
    queue, entry = queued(tmp_path, {'proposals': [proposal()]})
    monkeypatch.setattr('distr.core.orchestrator.emit_event', Mock(side_effect=RuntimeError('Temporary event storage failure')))
    assert not draft_execute.approve_draft_in_queue(queue, entry.id)
    assert queue.get_by_id(entry.id)
    monkeypatch.setattr('distr.core.orchestrator.emit_event', Mock())
    assert draft_execute.approve_draft_in_queue(queue, entry.id)
    with store() as db:
        assert db.query(KanbanTicket).count() == 1


@pytest.mark.parametrize('tag', ['project_oversight', 'persisted_planning'])
@pytest.mark.parametrize('wrapper', ['root', 'action', 'candidate', 'proposal'])
def test_public_payload_guard_catches_root_and_nested_reference_only_actions(monkeypatch, tag, wrapper):
    execute = Mock()
    monkeypatch.setattr('distr.core.initiative.action_handlers.execute_initiative_action', execute)
    payload = {'kind': 'initiative_action', 'action': {'action_type': 'workflow_start', 'payload': {}}}
    if wrapper == 'root':
        payload['provenance'] = tag
    elif wrapper == 'action':
        payload['action']['payload']['source'] = tag
    else:
        payload[wrapper] = {'payload': {'provenance': tag}}
    with pytest.raises(ValueError, match='read-only project recommendation'):
        draft_execute.run_execute_payload(payload)
    execute.assert_not_called()


def test_web_approval_returns_actual_retained_scope_error(store, tmp_path, monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.settings.initiative import register_routes
    queue, entry = queued(tmp_path, {'proposals': [proposal()]})
    monkeypatch.setattr('distr.core.initiative.draft_queue._DEFAULT_DRAFT_PATH', str(tmp_path / 'queue.json'))
    with store() as db:
        db.query(WhatsAppPhoneLink).one().board_id = 2
        db.commit()
    router = APIRouter()
    register_routes(router, None)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(f'/initiative/drafts/{entry.id}/approve')
    assert response.status_code == 400
    assert 'changed its linked board or project' in response.json()['error']
    assert 'install step' not in response.json()['error']
    persisted = DraftQueue(path=str(tmp_path / 'queue.json')).get_by_id(entry.id)
    assert persisted.reason == response.json()['error']


def test_public_approval_preserves_full_text_cached_transcript_and_media_for_downstream_reads(store, tmp_path, monkeypatch):
    # No transport, model, transcription or media fetch is permitted by this
    # regression. Only existing database text and cached references are used.
    import requests
    monkeypatch.setattr(requests, 'get', Mock(side_effect=AssertionError('No external or relay reads')))
    monkeypatch.setattr(requests, 'post', Mock(side_effect=AssertionError('No external or relay writes')))
    tail = 'TAIL REQUIREMENT: keep the customer-selected currency.'
    text = 'Detailed original requirement. ' * 40 + tail
    transcript = '[Transcription]\n' + 'Saved voice detail. ' * 40 + 'VOICE TAIL: never erase the edited response.'
    cached_reference = 'whatsapp_media/existing-voice.ogg'
    with store() as db:
        message = db.get(WhatsAppMessage, 1)
        message.text = text
        message.caption = 'Separate saved caption.'
        db.add(WhatsAppMessage(id=2, message_id='wa-voice-2', jid='client@s.whatsapp.net',
            text='Voice introduction.', caption=transcript, media_type='voice', media_mime_type='audio/ogg',
            media_filename='existing-voice.ogg', media_local_path=cached_reference, whatsapp_timestamp=2))
        db.commit()
    item = proposal()
    item['payload']['message_ids'] = [1, 2]
    queue, entry = queued(tmp_path, {'proposals': [item]})
    assert draft_execute.approve_draft_in_queue(queue, entry.id)
    with store() as db:
        ticket = db.query(KanbanTicket).one()
        ticket_id = ticket.id
        assert text in ticket.description and 'Separate saved caption.' in ticket.description
        assert transcript in ticket.description and 'Voice introduction.' in ticket.description
        assert 'WhatsApp message ID: wa-1' in ticket.description and 'WhatsApp message ID: wa-voice-2' in ticket.description
        assert cached_reference in ticket.description and 'audio/ogg' in ticket.description
        assert 'not fetched or inspected' in ticket.description
        assert {row.snapshot_group for row in db.query(WhatsAppMessage)} == {f'{ticket_id}_{ticket.lane_id}'}
        from distr.core.kanban.ticket_cli_context import build_kanban_ticket_cli_instruction
        instruction = build_kanban_ticket_cli_instruction(db, ticket_id, project_id=1)
        assert tail in instruction and transcript in instruction and cached_reference in instruction
    from distr.core.integrations.whatsapp.manager import WhatsAppWebSocketManager
    # This local getter does not need a manager or WebSocket connection for
    # private-chat rows, and does not fetch or open the referenced media file.
    retrieved = WhatsAppWebSocketManager.get_stored_messages(None, limit=10)
    assert not retrieved.get('error')
    rows = {row['id']: row for row in retrieved['messages']}
    assert rows[1]['text'] == text and rows[1]['caption'] == 'Separate saved caption.'
    assert rows[2]['caption'] == transcript and rows[2]['media_local_path'] == cached_reference
    assert rows[2]['media_filename'] == 'existing-voice.ogg'
    assert rows[2]['message_id'] == 'wa-voice-2'
    assert all(row['has_ticket'] and row['ticket_id'] == ticket_id for row in rows.values())
