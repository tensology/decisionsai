"""Proposal files: lenient fetch at accept, and thread files flowing to the linked ticket's Files tab.

Runs only against the temp DB from tests/conftest.py (DECISIONS_DB_DIR=mktemp)."""
import json
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from distr.core.db import Chat, get_session
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, KanbanTicketFile
from distr.core.db.projects import Project
from distr.core.incoming import media
from distr.core.incoming import proposals as proposals_mod


@pytest.fixture
def uploads(monkeypatch, tmp_path):
    folder = tmp_path / 'kanban_uploads'
    monkeypatch.setattr(media, '_kanban_uploads_dir', lambda: folder)
    return folder


def _thread_with_files(tmp_path, items):
    with get_session() as db:
        chat = Chat(title='Proposal thread', params=json.dumps({'incoming_attachments': items}))
        db.add(chat)
        db.commit()
        return int(chat.id)


def _ticket(chat_id=None):
    with get_session() as db:
        board = KanbanBoard(name='Board')
        db.add(board)
        db.flush()
        lane = KanbanLane(board_id=board.id, name='Todo')
        db.add(lane)
        db.flush()
        ticket = KanbanTicket(lane_id=lane.id, title='T', source_chat_id=chat_id)
        db.add(ticket)
        db.commit()
        return int(ticket.id)


def _files(ticket_id):
    with get_session() as db:
        return [(row.filename, Path(row.file_path)) for row in db.query(KanbanTicketFile).filter_by(ticket_id=ticket_id).all()]


def _item(tmp_path, name, mime, body=b'data'):
    path = tmp_path / name
    path.write_bytes(body)
    return {'id': f'att-{name}', 'name': name, 'mime_type': mime, 'path': str(path)}


def test_thread_files_including_audio_flow_to_linked_ticket_once(tmp_path, uploads):
    items = [_item(tmp_path, 'spec.pdf', 'application/pdf'), _item(tmp_path, 'voice-note.ogg', 'audio/ogg')]
    chat_id = _thread_with_files(tmp_path, items)
    ticket_id = _ticket(chat_id)
    report = media.sync_thread_files_to_tickets(chat_id)
    assert report['failed'] == [] and report['ticket_ids'] == [ticket_id]
    files = _files(ticket_id)
    assert sorted(name for name, _ in files) == ['spec.pdf', 'voice-note.ogg']
    for _, path in files:
        assert path.parent == uploads / str(ticket_id) and path.read_bytes() == b'data'
    # Sources stay on the thread (copy, not move); a second sync adds nothing.
    assert all(Path(item['path']).is_file() for item in items)
    media.sync_thread_files_to_tickets(chat_id)
    assert len(_files(ticket_id)) == 2


def test_missing_file_is_reported_and_does_not_block_the_rest(tmp_path, uploads):
    good = _item(tmp_path, 'ok.png', 'image/png')
    gone = {'id': 'att-gone', 'name': 'gone.pdf', 'path': str(tmp_path / 'gone.pdf')}
    failed = {'id': 'att-fail', 'name': 'remote.mp3', 'download_error': 'HTTP 404'}
    chat_id = _thread_with_files(tmp_path, [gone, good, failed])
    ticket_id = _ticket(chat_id)
    report = media.sync_thread_files_to_tickets(chat_id)
    assert [row['name'] for row in report['failed']] == ['gone.pdf', 'remote.mp3']
    assert [name for name, _ in _files(ticket_id)] == ['ok.png']


def test_unsafe_names_stay_inside_the_ticket_folder(tmp_path, uploads):
    item = _item(tmp_path, 'evil.txt', 'text/plain')
    item['name'] = '../../etc/passwd'
    chat_id = _thread_with_files(tmp_path, [item])
    ticket_id = _ticket(chat_id)
    media.sync_thread_files_to_tickets(chat_id)
    [(name, path)] = _files(ticket_id)
    assert name == 'passwd' and path.parent == uploads / str(ticket_id)


def test_linking_an_existing_ticket_later_copies_thread_files(tmp_path, uploads):
    from distr.core.workflow.development_threads import mark_development_thread

    chat_id = _thread_with_files(tmp_path, [_item(tmp_path, 'later.pdf', 'application/pdf')])
    ticket_id = _ticket(None)
    mark_development_thread(chat_id, ticket_id=ticket_id, source_type='incoming_proposal')
    assert [name for name, _ in _files(ticket_id)] == ['later.pdf']


def test_thread_without_files_or_ticket_is_unchanged(tmp_path, uploads):
    chat_id = _thread_with_files(tmp_path, [])
    assert media.sync_thread_files_to_tickets(chat_id) == {'attached': [], 'failed': [], 'ticket_ids': []}
    assert not uploads.exists()


def test_strict_false_ensure_returns_failures_without_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(media, '_root', lambda project_id, reference: tmp_path)
    proposal = {'id': 'local:x', 'attachments': [{'name': 'a.pdf', 'url': 'http://127.0.0.1:9/a.pdf'}]}
    result = media.ensure_proposal_attachments(proposal, None, strict=False)
    assert isinstance(result['failures'], list) and isinstance(result['attachments'], list)


def _accept(monkeypatch, tmp_path, ensured):
    with get_session() as db:
        project = Project(name='Proposal files fixture', folder_location=str(tmp_path))
        db.add(project)
        db.commit()
        project_id = int(project.id)
    calls = {}
    def fake_ensure(proposal, project_id, client=None, chat_id=None, strict=True):
        calls['strict'] = strict
        return ensured
    monkeypatch.setattr('distr.core.incoming.media.ensure_proposal_attachments', fake_ensure)
    monkeypatch.setattr('distr.core.incoming.project_links.source_project_index',
                        lambda **kwargs: {('gmail', str(tmp_path)): {project_id}})
    registered = []
    monkeypatch.setattr('distr.core.incoming.media.register_thread_attachments',
                        lambda cid, rows: registered.append(rows) or {'failed': []})
    proposal = {'id': f'local:files-{len(ensured["attachments"])}-{len(ensured["failures"])}', 'origin': 'local',
                'source': 'gmail', 'source_thread_id': str(tmp_path), 'project_id': project_id, 'title': 'Files proposal', 'message_ids': [str(tmp_path)]}
    chat_id = proposals_mod.create_proposal_thread(proposal, start_work=False)
    return chat_id, calls, registered


A = {'name': 'a.pdf', 'path': '/x/a.pdf', 'id': 'a'}
B = {'name': 'voice.ogg', 'mime_type': 'audio/ogg', 'path': '/x/voice.ogg', 'id': 'b'}


def test_accept_one_failed_file_still_creates_thread_and_reports(monkeypatch, tmp_path):
    chat_id, calls, registered = _accept(monkeypatch, tmp_path, {
        'attachments': [A, B], 'failures': [{'name': 'big.mov', 'download_error': 'timeout'}]})
    assert calls['strict'] is False
    assert chat_id and registered == [[A, B]]
    report = proposals_mod.proposal_thread_attachment_report(chat_id)
    assert report['failed_attachments'] == [{'name': 'big.mov', 'error': 'timeout'}]


def test_accept_all_files_failed_still_creates_thread(monkeypatch, tmp_path):
    chat_id, _, registered = _accept(monkeypatch, tmp_path, {
        'attachments': [], 'failures': [{'name': 'a.pdf', 'error': 'HTTP 500'}, {'name': 'b.ogg', 'error': 'HTTP 404'}]})
    assert chat_id and registered == [[]]
    names = [row['name'] for row in proposals_mod.proposal_thread_attachment_report(chat_id)['failed_attachments']]
    assert names == ['a.pdf', 'b.ogg']


def test_accept_all_files_succeed_is_unchanged(monkeypatch, tmp_path):
    chat_id, _, registered = _accept(monkeypatch, tmp_path, {'attachments': [A, B], 'failures': []})
    assert registered == [[A, B]]
    assert proposals_mod.proposal_thread_attachment_report(chat_id)['failed_attachments'] == []


def test_thread_route_returns_failed_attachments(monkeypatch):
    from distr.gui.web.routes.development.incoming import register_routes
    monkeypatch.setattr(proposals_mod, 'create_proposal_thread', lambda proposal, start_work: 55)
    monkeypatch.setattr(proposals_mod, 'proposal_thread_attachment_report',
                        lambda chat_id: {'failed_attachments': [{'name': 'x.mp3', 'error': 'gone'}], 'ticket_file_failures': []})
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    with TestClient(app) as client:
        body = client.post('/workflows/studio/incoming/proposals/thread', json={'proposal': {'title': 'T'}}).json()
    assert body == {'success': True, 'chat_id': 55, 'failed_attachments': [{'name': 'x.mp3', 'error': 'gone'}], 'ticket_file_failures': []}
