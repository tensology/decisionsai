"""Proposal continuity through real durable lifecycle and Development dispatch."""
import contextlib
import asyncio
import importlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db.projects import Project
from distr.core.incoming import proposals
from distr.core.workflow import development_harness as harness


@pytest.fixture
def intake(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path/'continuity.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    for name in ('distr.core.db', 'distr.core.chat', 'distr.core.chat_turns',
                 'distr.core.workflow.development_threads', 'distr.core.workflow.development_harness',
                 'distr.core.incoming.media'):
        monkeypatch.setattr(importlib.import_module(name), 'get_session', session)
    home = tmp_path/'home'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: home))
    monkeypatch.setattr('distr.gui.web.workflow_events.increment_workflow_updated', lambda: None)
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', lambda **kwargs: None)
    monkeypatch.setattr('distr.core.project_cli_backends.catalog_probe.probe_cli_backend',
                        lambda *args, **kwargs: {'status': {'ready': True}})
    roots = [tmp_path/'first', tmp_path/'second']
    for path in roots: path.mkdir()
    with session() as db:
        projects = [Project(name=name, folder_location=str(root), coding_backend='codex', coding_backend_model='auto')
                    for name, root in zip(('First','Second'), roots)]
        db.add_all(projects); db.commit()
        ids = [p.id for p in projects]
    links = home/'.decisions/cache/incoming-project-links.json'
    links.parent.mkdir(parents=True)
    links.write_text(json.dumps([{'source':'gmail','source_thread_id':'source-a',
                                 'projects':[{'decisions_project_id':pid} for pid in ids]}]))
    proposal = {'id':'local:gmail:fixture-a', 'dedupe_key':'gmail:source-a', 'origin':'local',
                'source':'gmail', 'source_thread_id':'source-a', 'message_ids':['message-a'],
                'project_id':ids[0], 'project_name':'First', 'title':'Report current configuration',
                'description':'Read the configuration and report the observed value.',
                'tasks':['Read the configuration'], 'attachments':[]}
    yield session, proposal, ids
    engine.dispose()


@pytest.mark.parametrize('project_changed', [False, True])
def test_handoff_save_preserves_concurrent_execution_and_project_scope(intake, monkeypatch, project_changed):
    session, proposal, projects = intake
    with session() as db:
        chat = Chat(title='Handoff race', project_id=projects[0], params='{}', is_archived=True)
        db.add(chat)
        db.commit()
        chat_id = chat.id
    serialize = proposals.proposal_brief_json
    changed = False
    latest = {'development': {'execution': {'status': 'running', 'job_id': 'worker-owned'}},
              'telegram_proposal_update': {'sent': 'earlier:done'}}

    def update_during_serialization(value):
        nonlocal changed
        if not changed:
            changed = True
            with session() as db:
                chat = db.get(Chat, chat_id)
                chat.params = json.dumps(latest)
                if project_changed:
                    chat.project_id = projects[1]
                db.commit()
        return serialize(value)

    monkeypatch.setattr(proposals, 'proposal_brief_json', update_during_serialization)
    if project_changed:
        with pytest.raises(ValueError, match='another project'):
            proposals._record_proposal_handoff(chat_id, proposal, project_id=projects[0], started=True)
    else:
        proposals._record_proposal_handoff(chat_id, proposal, project_id=projects[0], started=True)
    with session() as db:
        chat = db.get(Chat, chat_id)
        params = json.loads(chat.params)
        assert params['development'] == latest['development']
        assert params['telegram_proposal_update'] == latest['telegram_proposal_update']
        if project_changed:
            assert chat.project_id == projects[1] and chat.is_archived
            assert 'incoming_proposal_handoff' not in params
        else:
            assert params['incoming_proposal_handoff']['started'] is True
            assert not chat.is_archived


def test_proposal_review_link_opens_the_exact_snapshot_or_existing_thread(intake, monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.workflow.development_threads import ensure_development_thread
    from distr.gui.web.routes.development.incoming import register_routes

    session, original, projects = intake
    original = {**original, "id": "local:gmail:original-ñ"}
    newer = {**original, "title": "New request in the same conversation", "message_ids": ["message-b"]}
    key = proposals.proposal_review_key(original)
    newer_key = proposals.proposal_review_key(newer)
    assert len(key) == 64 and original["id"] not in key
    pending = [original]
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **k: {"proposals": pending})
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    base = "/workflows/studio/incoming/proposals"
    assert client.get(base).json()["proposals"][0]["review_key"] == key
    review = client.get(base + "/review", params={"review_key": key}).json()
    assert review["status"] == "proposal" and review["proposal"]["title"] == original["title"]
    pending[:] = [{**original, "project_id": projects[1]}]
    assert client.post(base + "/thread", json={"proposal": original, "review_key": key}).status_code == 409

    pending[:] = [newer]
    assert client.get(base + "/review", params={"review_key": key}).json() == {"status": "unavailable"}
    assert client.get(base + "/review", params={"review_key": newer_key}).json()["proposal"]["title"] == newer["title"]
    assert client.post(base + "/thread", json={"proposal": newer, "review_key": key}).status_code == 409
    with session() as db:
        assert db.query(Chat).count() == 0

    chat_id = ensure_development_thread(title=original["title"], project_id=projects[0],
                                        source_type="incoming_proposal", source_ref=proposals._proposal_handoff_key(original))
    proposals._record_proposal_handoff(chat_id, original, project_id=projects[0], started=True)
    assert client.get(base + "/review", params={"review_key": key}).json() == {"status": "thread", "chat_id": chat_id}
    monkeypatch.setattr(proposals, "create_proposal_thread", lambda *a, **k: pytest.fail("Existing proposal must not dispatch again"))
    result = client.post(base + "/thread", json={"proposal": original, "review_key": key})
    assert result.json()["chat_id"] == chat_id


def test_attachment_report_preserves_concurrent_execution_state(intake):
    session, _, projects = intake
    with session() as db:
        chat = Chat(title='Report race', project_id=projects[0], params='{}')
        db.add(chat)
        db.commit()
        chat_id = chat.id
    latest = {'development': {'execution': {'status': 'running', 'job_id': 'worker-owned'}}}

    class ConcurrentReport(dict):
        def get(self, key, default=None):
            with session() as db:
                db.get(Chat, chat_id).params = json.dumps(latest)
                db.commit()
            return super().get(key, default)

    proposals._record_attachment_report(chat_id, [{'name': 'missing.pdf'}], ConcurrentReport(failed=['ticket-file']))
    with session() as db:
        params = json.loads(db.get(Chat, chat_id).params)
    assert params['development'] == latest['development']
    assert params['incoming_attachment_failures'] == [{'name': 'missing.pdf'}]
    assert params['incoming_ticket_file_failures'] == ['ticket-file']


def test_review_link_attachments_follow_the_exact_proposal(intake, monkeypatch, tmp_path):
    import base64
    import hashlib
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.incoming import media
    from distr.core.integrations.whatsapp import RELAY_MEDIA_CHUNK_BYTES
    from distr.gui.web.routes.development.incoming import register_routes
    from tests.core.test_remote_voice_transcribe_paths import _Host

    _, original, _ = intake
    attachment_id = "a" * 24
    content = b"Original attachment" * (RELAY_MEDIA_CHUNK_BYTES // 16)
    file = tmp_path / "scope.txt"
    file.write_bytes(content)
    record = {"id": attachment_id, "name": file.name, "mime_type": "text/plain",
              "sha256": hashlib.sha256(content).hexdigest(), "path": str(file)}
    original = {**original, "attachments": [{"name": file.name}]}
    newer = {**original, "message_ids": ["message-b"], "attachments": [{"name": "new.txt"}]}
    pending = [original]
    prepared = []
    downloaded = []
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **kwargs: {"proposals": pending})

    def ensure(snapshot, project_id, *, strict):
        prepared.append(snapshot["attachments"])
        return {"attachments": [record], "failures": []}

    def resolve_file(snapshot, identifier, project_id):
        downloaded.append(snapshot["attachments"])
        assert identifier == attachment_id
        return file, record

    monkeypatch.setattr(media, "ensure_proposal_attachments", ensure)
    monkeypatch.setattr(media, "resolve_proposal_managed_attachment", resolve_file)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    endpoint = "/api/workflows/studio/incoming/proposals/files"
    key = proposals.proposal_review_key(original)
    response = client.post(endpoint + "/ensure", json={
        "proposal": {**original, "attachments": [{"name": "different.txt"}]}, "review_key": key,
    })
    assert response.status_code == 200
    assert prepared == [original["attachments"]]
    url = response.json()["attachments"][0]["url"]
    assert url == f"{endpoint}/{attachment_id}?review_key={key}"
    assert "path" not in response.json()["attachments"][0]
    partial = client.get(url, headers={"Range": "bytes=0-7"})
    assert partial.status_code == 206
    assert partial.content == content[:8]
    assert partial.headers["etag"] == '"' + record["sha256"] + '"'
    assert downloaded == [original["attachments"]]

    monkeypatch.setattr("distr.gui.web.server.get_unified_server", lambda: None)
    monkeypatch.setattr("requests.get", lambda url, **kwargs: client.get(
        url.removeprefix("http://127.0.0.1:8765"), headers=kwargs["headers"],
    ))
    host = _Host()
    restored = bytearray()
    for offset in range(0, len(content), RELAY_MEDIA_CHUNK_BYTES):
        host._dispatch_api_relay({"request_id": str(offset), "data": {
            "method": "GET", "path": url, "response_type": "base64", "media_offset": offset,
        }})
        frame = host.responses.pop()["data"]
        assert frame["status"] == 206
        chunk = frame["response"]
        assert chunk["size_bytes"] <= RELAY_MEDIA_CHUNK_BYTES
        assert chunk["etag"] == partial.headers["etag"]
        restored.extend(base64.b64decode(chunk["body_base64"]))
    assert restored == content

    prior_downloads = downloaded[:]
    pending[:] = [newer]
    assert client.post(endpoint + "/ensure", json={"review_key": key}).status_code == 409
    assert client.get(url).status_code == 404
    assert prepared == [original["attachments"]]
    assert downloaded == prior_downloads


@pytest.mark.parametrize('project_changed', [False, True])
def test_merge_receipt_preserves_concurrent_execution_and_project_scope(intake, monkeypatch, project_changed):
    session, proposal, projects = intake
    with session() as db:
        chat = Chat(title='Merge race', project_id=projects[0], params='{}')
        db.add(chat)
        db.commit()
        chat_id = chat.id
    receipt = {'key': 'merged-request', 'merged': proposal, 'pending_originals': []}
    serialize = json.dumps
    latest = {'development': {'execution': {'status': 'running', 'job_id': 'worker-owned'}}}
    changed = False

    def change_during_serialization(value, *args, **kwargs):
        nonlocal changed
        if not changed and isinstance(value, dict) and value.get('incoming_merge') is receipt:
            changed = True
            with session() as db:
                chat = db.get(Chat, chat_id)
                chat.params = serialize(latest)
                if project_changed:
                    chat.project_id = projects[1]
                db.commit()
        return serialize(value, *args, **kwargs)

    monkeypatch.setattr(proposals.json, 'dumps', change_during_serialization)
    if project_changed:
        with pytest.raises(ValueError, match='another project'):
            proposals._write_merge_receipt(chat_id, receipt)
    else:
        proposals._write_merge_receipt(chat_id, receipt)
    with session() as db:
        params = json.loads(db.get(Chat, chat_id).params)
    assert params['development'] == latest['development']
    assert ('incoming_merge' in params) is not project_changed


@pytest.mark.parametrize('surface', ['source', 'files'])
def test_source_and_file_registration_preserve_concurrent_progress(intake, monkeypatch, surface):
    from distr.core.incoming import media, source_context

    session, proposal, projects = intake
    proposal = {**proposal, 'source': 'whatsapp'}
    with session() as db:
        chat = Chat(title='Registration race', project_id=projects[0], params='{}')
        db.add(chat)
        db.commit()
        chat_id = chat.id
    serialize = json.dumps
    member = 'incoming_source_context' if surface == 'source' else 'incoming_attachments'
    latest = {'development': {'execution': {'status': 'running', 'job_id': 'worker-owned'}}}
    if surface == 'files':
        latest[member] = [{'id': 'concurrent-file', 'path': '/retained/file.txt', 'name': 'Retained file'}]
    else:
        latest[member] = {'source': 'whatsapp', 'source_thread_id': proposal['source_thread_id'], 'message_ids': ['concurrent-message']}
    changed = False

    def update_during_serialization(value, *args, **kwargs):
        nonlocal changed
        if not changed and isinstance(value, dict) and member in value:
            changed = True
            with session() as db:
                db.get(Chat, chat_id).params = serialize(latest)
                db.commit()
        return serialize(value, *args, **kwargs)

    monkeypatch.setattr(json, 'dumps', update_during_serialization)
    if surface == 'source':
        source_context.register_source_context(chat_id, proposal)
    else:
        path = Path.home() / '.decisions/workspaces/projects' / str(projects[0]) / 'request.txt'
        path.parent.mkdir(parents=True)
        path.write_text('Requested attachment.')
        monkeypatch.setattr(media, 'sync_thread_files_to_tickets', lambda *_: {})
        media.register_thread_attachments(chat_id, [{'id': 'new-file', 'sha256': 'known', 'path': str(path), 'name': path.name}])
    with session() as db:
        params = json.loads(db.get(Chat, chat_id).params)
    assert changed
    assert params['development'] == latest['development']
    if surface == 'files':
        assert {item['id'] for item in params[member]} == {'concurrent-file', 'new-file'}
    else:
        assert params[member]['message_ids'] == ['concurrent-message', 'message-a']


def test_acknowledged_remote_proposal_retry_reopens_only_its_started_thread(intake, monkeypatch):
    from unittest.mock import Mock

    session, proposal, projects = intake
    proposal = {**proposal, 'id': 'tensology:41', 'origin': 'tensology'}
    upstream = {**proposal, 'id': 41, 'status': 'pending', 'attachments': [],
                'source_conversation': {'thread_id': 'source-a'}, 'source_message_ids': ['message-a'],
                'project': {'id': 80, 'decisions_project_id': projects[0], 'name': 'First'}}
    client = Mock()
    client.get.side_effect = lambda path: dict(upstream)
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **_: client)
    monkeypatch.setattr('distr.core.tensology_client._resolve_tensology_project',
                        lambda payload: {'id': payload['decisions_project_id']})
    dispatch = Mock(return_value={'status': 'initializing', 'id': 'fixture-job'})
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)

    chat_id = proposals.create_proposal_thread(proposal, start_work=True)
    client.post.assert_called_once()
    with session() as db:
        params_before = db.get(Chat, chat_id).params
    upstream['status'] = 'sent_to_decisions'
    assert proposals.create_proposal_thread(proposal, start_work=True) == chat_id
    client.post.assert_called_once()
    dispatch.assert_called_once()
    with session() as db:
        assert db.query(Chat).filter(Chat.parent_id.is_(None)).count() == 1
        assert db.get(Chat, chat_id).params == params_before

    upstream['project'] = {**upstream['project'], 'decisions_project_id': projects[1]}
    with pytest.raises(ValueError, match='another project'):
        proposals.create_proposal_thread(proposal, start_work=True)
    upstream['project']['decisions_project_id'] = projects[0]
    upstream['status'] = 'dismissed'
    with pytest.raises(ValueError, match='no longer pending'):
        proposals.create_proposal_thread(proposal, start_work=True)
    upstream['status'] = 'sent_to_decisions'
    with session() as db:
        chat = db.get(Chat, chat_id)
        params = json.loads(chat.params)
        params['incoming_proposal_handoff']['started'] = False
        chat.params = json.dumps(params)
        db.commit()
    with pytest.raises(ValueError, match='no longer pending'):
        proposals.create_proposal_thread(proposal, start_work=True)
    upstream.update(id=42, status='sent_to_decisions')
    with pytest.raises(ValueError, match='no longer pending'):
        proposals.create_proposal_thread({**proposal, 'id': 'tensology:42'}, start_work=True)
    dispatch.assert_called_once()


@pytest.mark.parametrize('blocked',[False,True])
def test_failed_real_dispatch_does_not_allow_unusable_proposal_project_correction(intake, monkeypatch, blocked):
    from distr.core.turn_runtime import TurnResult
    session, proposal, ids = intake
    calls = []

    async def unavailable_backend(request, **kwargs):
        calls.append(request)
        return TurnResult(success=False, status='blocked' if blocked else 'failed',
                          backend_id='codex', model='auto', runtime_id='cli_harness',
                          error='Fixture worker unavailable')

    monkeypatch.setattr('distr.core.turn_runtime.execute_turn', unavailable_backend)
    chat_id = proposals.create_proposal_thread(proposal)
    result = harness.dispatch_development_prompt(chat_id, 'Report current configuration', dispatch_async=False)
    assert len(calls) == 1, result
    assert result['status'] == ('blocked' if blocked else 'failed'), result
    assert result['error'] == 'Fixture worker unavailable'
    with session() as db:
        retained = json.loads(db.get(Chat,chat_id).params)
        assert retained['incoming_reply_context']['message_ids'] == ['message-a']
        assert retained['incoming_proposal_handoff']['proposal']['description'] == proposal['description']
    # A retained thread is authoritative even after its worker fails. A source
    # assignment must not report success while making every retry unusable.
    with pytest.raises(ValueError, match='already has a thread'):
        proposals.reassign_proposal_project(proposal,ids[1])
    assert proposals.create_proposal_thread(proposal) == chat_id


def _join_new_workers(chat_id, previous):
    workers = [worker for worker in threading.enumerate()
               if worker not in previous and worker.name == f'development-agent-{chat_id}']
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive(), 'Test-owned Development worker did not stop.'


@pytest.mark.parametrize('partial_source', [False, True])
def test_retry_keeps_complete_brief_source_and_verified_file(intake, monkeypatch, tmp_path, partial_source):
    from distr.core.db import WhatsAppMessage
    from distr.core.incoming import media
    from distr.core.turn_runtime import TurnResult
    session, proposal, ids = intake
    source = tmp_path/'original.txt'
    source.write_text('source bytes must survive retry\n')
    proposal = {**proposal,'source':'whatsapp','source_thread_id':'source-wa',
                'description':'Complete durable brief. '*2500 + 'END OF BRIEF',
                'evidence':[{'id':f'evidence-{i}','body':f'Source statement {i}'} for i in range(200)]}
    proposal['message_ids'] = ['message-a', *(row['id'] for row in proposal['evidence'])]
    if partial_source:
        proposal['message_ids'].append('missing-message')
    links = Path.home()/'.decisions/cache/incoming-project-links.json'
    links.write_text(json.dumps([{'source':'whatsapp','source_thread_id':'source-wa',
                                 'projects':[{'decisions_project_id':ids[0]}]}]))
    with session() as db:
        db.add(WhatsAppMessage(jid='source-wa',message_id='message-a',media_type='document',
            media_mime_type='text/plain',media_filename='original.txt',media_local_path=str(source)))
        db.add_all(WhatsAppMessage(jid='source-wa', message_id=row['id'], text=row['body']) for row in proposal['evidence'])
        db.commit()
    calls = []
    async def backend(request, **kwargs):
        calls.append(request)
        return TurnResult(success=False,backend_id='codex',model='auto',runtime_id='cli_harness',error='Retryable worker failure')
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn',backend)
    chat_id = proposals.create_proposal_thread(proposal)
    original_files = media.thread_attachments(chat_id)
    assert len(original_files) == 1
    first = harness.dispatch_development_prompt(chat_id,'Read original attachment',dispatch_async=False)
    assert first['status'] == 'failed'
    previous = set(threading.enumerate())
    try:
        assert proposals.create_proposal_thread(proposal,start_work=True) == chat_id
    finally:
        _join_new_workers(chat_id,previous)
    assert len(calls) == 2
    assert harness.development_execution_state(chat_id)['status'] == 'failed'
    with session() as db:
        roots = db.query(Chat).filter(Chat.parent_id.is_(None)).all()
        assert [root.id for root in roots] == [chat_id]
        retained = json.loads(roots[0].params)
        handoff = retained['incoming_proposal_handoff']['proposal']
        assert handoff['description'] == proposal['description']
        assert len(handoff['evidence']) == 200
        assert retained['incoming_reply_context']['message_ids'] == proposal['message_ids']
        assert retained['incoming_source_context']['source_thread_id'] == 'source-wa'
    assert media.thread_attachments(chat_id) == original_files
    assert media.resolve_thread_attachment(chat_id,original_files[0]['id'])[0].read_bytes() == source.read_bytes()
    assert calls[-1].scope.project_id == ids[0]
    assert 'END OF BRIEF' in calls[-1].instruction
    assert 'Source statement 199' in calls[-1].instruction
    assert any(Path(row['path']).read_bytes() == source.read_bytes() for row in calls[-1].metadata['attachments'])
    failures = proposals.proposal_thread_attachment_report(chat_id)['failed_attachments']
    if partial_source:
        assert len(failures) == 1 and 'source WhatsApp messages are unavailable' in failures[0]['error']
    else:
        assert failures == []


def test_canonical_nested_project_mapping_beats_stale_row_assignment(intake, monkeypatch):
    session, proposal, ids = intake
    canonical = {**proposal,'id':811,'status':'pending','attachments':[],
                 'project':{'id':9001,'name':'First','decisions_project_id':ids[0]},
                 'decisions_project_id':ids[1],
                 'source_conversation':{'thread_id':'source-a'},'source_message_ids':['message-a']}
    client = SimpleNamespace(get=lambda path,**kwargs:dict(canonical),post=lambda *args,**kwargs:{})
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client',lambda **kwargs:client)
    chat_id = proposals.create_proposal_thread({**proposal,'id':'tensology:811','origin':'tensology'})
    with session() as db:
        root = db.get(Chat,chat_id)
        assert root.project_id == ids[0]
        retained = json.loads(root.params)
        assert retained['incoming_reply_context']['project_id'] == ids[0]
        assert retained['incoming_proposal_handoff']['proposal']['project_id'] == ids[0]


def test_cancelled_worker_keeps_proposal_thread_scope(intake, monkeypatch):
    from distr.core.turn_runtime import TurnResult
    session, proposal, ids = intake
    entered, release = threading.Event(), threading.Event()
    async def backend(request, **kwargs):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(.01)
        return TurnResult(success=False,backend_id='codex',model='auto',runtime_id='cli_harness',error='Stopped fixture')
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn',backend)
    monkeypatch.setattr('distr.core.project_cli_backends.registry.terminate_backend_process',lambda *args,**kwargs:False)
    chat_id = proposals.create_proposal_thread(proposal)
    previous = set(threading.enumerate())
    try:
        harness.dispatch_development_prompt(chat_id,'Read the configuration',dispatch_async=True)
        assert entered.wait(timeout=5),'Fixture backend did not start'
        assert harness.stop_development_execution(chat_id)['status'] == 'cancelled'
        with pytest.raises(ValueError,match='already has a thread'):
            proposals.reassign_proposal_project(proposal,ids[1])
        assert proposals.create_proposal_thread(proposal) == chat_id
    finally:
        release.set()
        _join_new_workers(chat_id,previous)
    assert harness.development_execution_state(chat_id)['status'] == 'cancelled'
    with session() as db:
        assert db.get(Chat,chat_id).project_id == ids[0]


@pytest.mark.parametrize('link_ticket', [False, True])
def test_direct_start_failure_persists_handoff_before_retry(intake, monkeypatch, tmp_path, link_ticket):
    from distr.core.db import WhatsAppMessage
    from distr.core.incoming import media

    session, proposal, project_ids = intake
    source = tmp_path / 'request.txt'
    source.write_text('Original request attachment.')
    proposal = {**proposal, 'source': 'whatsapp', 'source_thread_id': 'fixture-wa'}
    links = Path.home() / '.decisions/cache/incoming-project-links.json'
    links.write_text(json.dumps([{'source': 'whatsapp', 'source_thread_id': 'fixture-wa',
                                  'projects': [{'decisions_project_id': project_ids[0]}]}]))
    with session() as db:
        db.add(WhatsAppMessage(jid='fixture-wa', message_id='message-a', media_type='document',
                               media_filename='request.txt', media_local_path=str(source)))
        db.commit()
    attempts = []

    def dispatch(chat_id, prompt, **kwargs):
        attempts.append(chat_id)
        if len(attempts) == 1:
            raise ValueError('Fixture dispatch failed before starting.')
        return {'started': True, 'status': 'running'}

    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    with pytest.raises(ValueError, match='Fixture dispatch failed'):
        proposals.create_proposal_thread(proposal, start_work=True)

    chat_id = attempts[0]
    with session() as db:
        retained = json.loads(db.get(Chat, chat_id).params)
    handoff = retained['incoming_proposal_handoff']
    assert handoff['started'] is False
    if link_ticket:
        from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket
        from distr.core.workflow.development_threads import mark_development_thread

        with session() as db:
            board = KanbanBoard(name='Fixture board', default_project_id=project_ids[0])
            db.add(board)
            db.flush()
            lane = KanbanLane(name='Inbox', board_id=board.id, position=0)
            db.add(lane)
            db.flush()
            ticket = KanbanTicket(title='Original request', lane_id=lane.id, linked_project_id=project_ids[0])
            db.add(ticket)
            db.commit()
            ticket_id = ticket.id
        monkeypatch.setattr(media, 'sync_thread_files_safely', lambda *_: None)
        mark_development_thread(chat_id, ticket_id=ticket_id, source_type='ticket')
    assert handoff['key'] in proposals._accepted_proposal_keys(include_terminal=True)
    assert handoff['key'] not in proposals._accepted_proposal_keys()
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {
        'incoming_proposals_auto_accept': True,
        'incoming_proposals_auto_accept_projects': [proposal['project_name']],
    })
    assert proposals.auto_accept_proposals([proposal]) == []
    assert attempts == [chat_id]
    assert handoff['proposal']['description'] == proposal['description']
    assert retained['incoming_reply_context']['message_ids'] == ['message-a']
    files = media.thread_attachments(chat_id)
    assert len(files) == 1
    assert media.resolve_thread_attachment(chat_id, files[0]['id'])[0].read_bytes() == source.read_bytes()
    assert proposals.create_proposal_thread(proposal, start_work=True) == chat_id
    assert proposals.create_proposal_thread(proposal, start_work=True) == chat_id
    assert attempts == [chat_id, chat_id]
    assert handoff['key'] in proposals._accepted_proposal_keys()
    assert media.thread_attachments(chat_id) == files
