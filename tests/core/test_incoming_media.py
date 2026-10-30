"""Proposal media identity, durable bytes, scope, retries and actual thread download."""
import base64
import contextlib
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base, Chat, WhatsAppMessage
from distr.core.incoming import media

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4z0AAAAASUVORK5CYII=')
PDF = b'%PDF-1.4\nsource PDF acceptance bytes\n%%EOF'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def sessions():
        with factory() as db:
            yield db
            db.commit()

    monkeypatch.setattr(media, 'get_session', sessions)
    with sessions() as db:
        root = Chat(title='Media proposal', project_id=7)
        db.add(root)
        db.flush()
        cid = root.id
    return sessions, cid


def test_whatsapp_all_files_same_names_durable_and_thread_scoped(store, tmp_path, monkeypatch):
    sessions, cid = store
    source = tmp_path/'relay'
    source.mkdir()
    expected = {'image-a': PNG, 'image-b': PNG+b'other-image', 'pdf-c': PDF}
    with sessions() as db:
        for key, data in expected.items():
            path = source/key
            path.write_bytes(data)
            db.add(WhatsAppMessage(message_id=key, jid='client@g.us', media_type='document' if key=='pdf-c' else 'image',
                   media_filename='spec.pdf' if key=='pdf-c' else 'screenshot.png', media_mime_type='application/pdf' if key=='pdf-c' else 'image/png', media_local_path=str(path)))
        db.add(WhatsAppMessage(message_id='wrong-conversation', jid='other@g.us', media_type='image', media_local_path=str(source/'image-a')))
    proposal = {'id': 'local:whatsapp:test', 'source': 'whatsapp', 'source_thread_id': 'client@g.us', 'message_ids': list(expected)}
    attachments = media.proposal_attachments(proposal, 7)
    assert len(attachments) == 3
    assert len({item['path'] for item in attachments}) == 3
    assert {item['source_message_id'] for item in attachments} == set(expected)
    for item in attachments:
        assert Path(item['path']).read_bytes() == expected[item['source_message_id']]
    media.register_thread_attachments(cid, attachments)
    media.register_thread_attachments(cid, attachments)
    assert len(media.thread_attachments(cid)) == 3
    for path in source.iterdir():
        path.unlink()
    for item in media.thread_attachments(cid):
        path, record = media.resolve_thread_attachment(cid, item['id'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256']
    assert len(media.public_thread_attachments(cid)) == 3
    assert all('path' not in item for item in media.public_thread_attachments(cid))
    with pytest.raises(FileNotFoundError):
        media.resolve_thread_attachment(cid+1, attachments[0]['id'])
    with pytest.raises(ValueError, match='source WhatsApp messages'):
        media.proposal_attachments({**proposal, 'message_ids': ['wrong-conversation']}, 7)


def test_missing_whatsapp_media_fails_before_thread_creation(store, monkeypatch):
    sessions, _ = store
    from distr.core.db.projects import Project
    monkeypatch.setattr('distr.core.db.get_session', sessions)
    with sessions() as db:
        db.add(Project(id=7, name='Media test project'))
        db.add(WhatsAppMessage(message_id='missing', jid='client@g.us', media_type='document', media_filename='brief.pdf'))
    monkeypatch.setattr(media, '_download_whatsapp', Mock(side_effect=OSError('gone')))
    from distr.core.incoming import proposals
    create = Mock()
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread', create)
    with pytest.raises(ValueError, match='brief.pdf'):
        proposals.create_proposal_thread({'id': 'local:missing', 'source': 'whatsapp', 'project_id': 7,
                                         'source_thread_id': 'client@g.us', 'message_ids': ['missing']})
    create.assert_not_called()


def test_remote_multiple_files_on_same_snapshot_identity_and_scoped_urls(store):
    _, cid = store
    client = Mock()
    def download(location, path):
        path.write_bytes(PDF if 'attachment_index=1' in location else PNG)
    client.download.side_effect = download
    entries = [dict(name='same-name', mime_type=mime, snapshot_id=8, api_path=f'work-requests/45/attachment?snapshot_id=8&attachment_index={index}')
               for index, mime in enumerate(['image/png', 'application/pdf'])]
    proposal = {'id': 'tensology:45', 'attachments': entries}
    attachments = media.proposal_attachments(proposal, 7, client=client)
    assert len({item['id'] for item in attachments}) == 2
    assert len({item['path'] for item in attachments}) == 2
    assert {Path(item['path']).read_bytes() for item in attachments} == {PNG, PDF}
    media.register_thread_attachments(cid, attachments)
    with pytest.raises(ValueError, match='scoped download'):
        media.proposal_attachments({**proposal, 'attachments': [dict(entries[0], api_path='work-requests/46/attachment?snapshot_id=8')]}, 7, client=client)
    client.download.side_effect = OSError('not available')
    with pytest.raises(ValueError, match='not pushed'):
        media.proposal_attachments(proposal, 7, client=client)


def test_actual_download_api_returns_exact_bytes_and_rejects_missing_changed_file(store, tmp_path):
    _, cid = store
    root = tmp_path/'.decisions/workspaces/projects/7/attachments'
    root.mkdir(parents=True)
    path = root/'brief.pdf'
    path.write_bytes(PDF)
    media.register_thread_attachments(cid, [{'path': str(path), 'name': 'brief.pdf', 'mime_type': 'application/pdf'}])
    public = media.public_thread_attachments(cid)[0]
    from distr.gui.web.routes.development.threads import register_routes
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix='/api')
    client = TestClient(app)
    response = client.get(public['url'])
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers['content-type'] == 'application/pdf'
    assert client.get(public['url'].replace('/tasks/'+str(cid)+'/', '/tasks/'+str(cid+1)+'/')).status_code == 404
    path.write_bytes(PDF+b'changed')
    assert client.get(public['url']).status_code == 404


def test_registered_media_is_in_harness_inputs_and_rejects_arbitrary_paths(store, tmp_path):
    _, cid = store
    snapshot = tmp_path/'.decisions/workspaces/boards/18/intake/ticket-1'
    snapshot.mkdir(parents=True)
    path = snapshot/'snapshot.pdf'
    path.write_bytes(PDF)
    media.register_thread_attachments(cid, [{'path': str(path), 'name': 'snapshot.pdf', 'mime_type': 'application/pdf'}])
    from distr.core.workflow.development_harness import _safe_turn_attachments
    assert _safe_turn_attachments(media.thread_attachments(cid))[0]['path'] == str(path)
    outside = tmp_path/'not-an-attachment'
    outside.write_bytes(PDF)
    with pytest.raises(ValueError, match='outside managed'):
        media.register_thread_attachments(cid, [{'path': str(outside)}])


def test_existing_tensology_handoff_reuses_proposal_and_refreshes_media(store, monkeypatch):
    from distr.core import tensology_client as tc
    from distr.core.db.workflow import DevelopmentWorkItem
    sessions, cid = store
    with sessions() as db:
        db.add(DevelopmentWorkItem(chat_id=cid, identity_key='source:incoming_proposal:tensology:45', source_type='incoming_proposal'))
    monkeypatch.setattr('distr.core.db.get_session', sessions)
    ticket = {'id': 17, 'project_id': 90, 'reference': 'MEDIA-17', 'title': 'Media acceptance',
              'source_metadata': {'incoming_proposal_id': 45},
              'attachments': [{'name': 'brief.pdf', 'type': 'application/pdf', 'api_path': 'tickets/17/attachment?url=brief.pdf'}]}
    assert tc._existing_tensology_thread(ticket) == cid
    monkeypatch.setattr(tc, '_resolve_tensology_project', lambda ticket: {'id': 7, 'name': 'Media project'})
    client = Mock()
    content = PDF
    def download(location, destination):
        destination.write_bytes(content)
        return len(content)
    client.download.side_effect = download
    assert tc._process_tensology_ticket(client, ticket)
    registered = media.thread_attachments(cid)
    assert len(registered) == 1 and Path(registered[0]['path']).read_bytes() == PDF
    content = PDF+b'new-source-version'
    assert tc._process_tensology_ticket(client, ticket)
    registered = media.thread_attachments(cid)
    assert len(registered) == 1 and Path(registered[0]['path']).read_bytes() == content
    assert client.download.call_count == 2
    client.download.side_effect = OSError('source missing')
    assert tc._process_tensology_ticket(client, ticket) is False
    assert client.patch.call_args.args[1]['status'] == 'failed'


def test_tensology_mirrored_ticket_keeps_native_attachment_records(store, monkeypatch, tmp_path):
    from distr.core import tensology_client as tc
    from distr.core.db.kanban import KanbanTicketFile
    sessions, _ = store
    monkeypatch.setattr('distr.core.db.get_session', sessions)
    path = tmp_path/'.decisions/workspaces/projects/7/attachments/brief.pdf'
    path.parent.mkdir(parents=True)
    path.write_bytes(PDF)
    ticket = {'id': 17, 'reference': 'MEDIA-17', 'project_id': 90, 'title': 'Media acceptance',
              'attachments': [{'name': 'brief.pdf', 'type': 'application/pdf', 'local_path': str(path)}]}
    project = {'id': 7, 'name': 'Media project'}
    first = tc._ensure_tensology_kanban_ticket(ticket, project, 433)
    second = tc._ensure_tensology_kanban_ticket(ticket, project, 433)
    assert first == second
    with sessions() as db:
        records = db.query(KanbanTicketFile).filter_by(ticket_id=first).all()
        assert len(records) == 1 and Path(records[0].file_path).read_bytes() == PDF


def test_tensology_dispatch_includes_structured_media_not_just_prompt(store, monkeypatch, tmp_path):
    from distr.core import tensology_client as tc
    path = tmp_path/'.decisions/workspaces/projects/7/attachments/brief.pdf'
    path.parent.mkdir(parents=True)
    path.write_bytes(PDF)
    ticket = {'id': 17, 'reference': 'MEDIA-17', 'project_id': 90, 'title': 'Media acceptance',
              'attachments': [{'name': 'brief.pdf', 'type': 'application/pdf', 'local_path': str(path)}]}
    monkeypatch.setattr(tc, '_tensology_project_route', lambda project: {'backend': 'pi', 'provider': 'ollama', 'model': 'local-test'})
    monkeypatch.setattr(tc, '_ensure_tensology_kanban_ticket', lambda *args: 123)
    monkeypatch.setattr(tc, '_execution_skill_ids', lambda *args: ([], False))
    monkeypatch.setattr('distr.core.workflow.phases.route_ticket_workflow_id', lambda *args, **kwargs: 433)
    post = Mock(return_value={'task': {'id': 77}})
    monkeypatch.setattr(tc, '_local_api_post', post)
    assert tc._create_tensology_thread(ticket, {'id': 7, 'name': 'Media project'})[0] == 77
    assert post.call_args.args[1]['attachments'] == [{'name': 'brief.pdf', 'path': str(path), 'mime_type': 'application/pdf', 'size': len(PDF)}]


def test_thread_download_forces_untrusted_active_content_to_download(store, tmp_path):
    _, cid = store
    path=tmp_path/'.decisions/workspaces/projects/7/attachments/payload.html'
    path.parent.mkdir(parents=True)
    path.write_text('<script>throw new Error("untrusted")</script>')
    media.register_thread_attachments(cid,[{'path':str(path),'name':'payload.html','mime_type':'text/html'}])
    from distr.gui.web.routes.development.threads import register_routes
    app,router=FastAPI(),APIRouter()
    register_routes(router,None)
    app.include_router(router,prefix='/api')
    response=TestClient(app).get(media.public_thread_attachments(cid)[0]['url'])
    assert response.status_code==200
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['x-content-type-options']=='nosniff'
    path.unlink()
    with pytest.raises(FileNotFoundError):
        media.required_thread_attachments(cid)
