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

    modules = [importlib.import_module(name) for name in (
        'distr.core.db', 'distr.core.chat', 'distr.core.chat_turns',
        'distr.core.workflow.development_threads', 'distr.core.workflow.development_harness',
        'distr.core.workflow.development_control', 'distr.core.workflow.dispatcher',
        'distr.core.workflow.service', 'distr.core.workflow.work_dispatch',
        'distr.core.incoming.media',
    )]
    for module in modules:
        monkeypatch.setattr(module, 'get_session', session)
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


@pytest.mark.parametrize("source,queue_interrupted,verified", [
    ("gmail", False, True), ("whatsapp", True, True), ("gmail", False, False),
])
def test_native_telegram_proposal_outcome_survives_disconnect_and_later_work(
        intake, monkeypatch, source, queue_interrupted, verified):
    from sqlalchemy.exc import OperationalError
    from distr.core import tensology_client
    from distr.core.incoming import telegram_updates as updates
    from distr.core.initiative import outcome_flow
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramMessages, DummyTelegramSender

    session, proposal, projects = intake
    if source == "whatsapp":
        proposal.update(id="local:whatsapp:fixture-a", dedupe_key="whatsapp:fixture@g.us",
                        source=source, source_thread_id="fixture@g.us")
        links = Path.home() / ".decisions/cache/incoming-project-links.json"
        links.write_text(json.dumps([{"source": source, "source_thread_id": "fixture@g.us",
                                      "projects": [{"decisions_project_id": projects[0]}]}]))
    stage = "done" if verified else "failed"
    sender = DummyTelegramSender()
    connection = {"sender": None}
    monkeypatch.setattr(updates, "get_session", session)
    monkeypatch.setattr(updates, "_manager", lambda: connection["sender"])
    monkeypatch.setattr(tensology_client, "_ticket_push_paused", lambda: True)
    monkeypatch.setattr(outcome_flow, "_pending_proposals", lambda **_: [proposal])
    monkeypatch.setattr(outcome_flow, "audit", lambda *a, **kw: None)
    workers = []
    queue = updates._queue_execution_update
    interrupt = {"enabled": queue_interrupted}

    def enqueue(*args, **kwargs):
        if interrupt["enabled"]:
            raise OperationalError("enqueue", {}, RuntimeError("database is locked"))
        return queue(*args, **kwargs)

    async def worker(*, chat_id, job_id, **kwargs):
        with session() as db:
            params = json.loads(db.get(Chat, chat_id).params)
        assert params["incoming_proposal_handoff"]["telegram_approval"] == {
            "chat_id": 12345, "job_id": job_id, "project_id": projects[0],
        }
        if not queue_interrupted:
            updates.queue_execution_update({"source_metadata": {
                "approval_source": "telegram", "approval_telegram_chat_id": 54321,
            }}, chat_id, {"job_id": job_id, "summary": "Stale remote result."}, "failed")
            with session() as db:
                assert updates._KEY not in json.loads(db.get(Chat, chat_id).params)
        harness._update_execution(chat_id, job_id=job_id, status="completed", summary="Approved result.",
                                  outcome={"verification": {"final_verdict": "pass"} if verified else {}})
        workers.append((chat_id, job_id))

    monkeypatch.setattr(updates, "_queue_execution_update", enqueue)
    monkeypatch.setattr(harness, "_run_execution", worker)
    inbound = DummyTelegramMessages()
    callback = f"ip:{outcome_flow._proposal_key_hash(proposal)}:accept"
    previous = set(threading.enumerate())
    inbound._handle_telegram_message({"data": {
        "message_id": "native-proposal", "chat_id": 12345, "chat": {"type": "private"},
        "from": {"id": 12345}, "callback_data": callback,
    }})
    assert len(inbound.sent) == 1 and "started work" in inbound.sent[0]
    chat_id = proposals._existing_proposal_thread(proposal, projects[0])
    assert chat_id is not None
    _join_new_workers(chat_id, previous)
    [(finished_chat, job_id)] = workers
    assert finished_chat == chat_id
    with session() as db:
        params = json.loads(db.get(Chat, chat_id).params)
    assert params["incoming_proposal_handoff"]["started"] is True
    assert params["incoming_proposal_handoff"]["telegram_approval"]["outcome"]["stage"] == stage
    assert (updates._KEY not in params) == queue_interrupted

    harness._update_execution(chat_id, job_id="later", status="initializing", started_at="2026-11-12")
    harness._update_execution(chat_id, job_id="later", status="completed",
                              summary="[DECISIONS_STAGE: done] Unrelated follow-up.")
    interrupt["enabled"] = False
    sender.telegram_user_id = 54321
    connection["sender"] = sender
    if not queue_interrupted:
        # A stale remote mirror must not replace the locally verified result or its owner.
        updates.queue_execution_update({"source_metadata": {
            "approval_source": "telegram", "approval_telegram_chat_id": 54321,
        }}, chat_id, {"job_id": job_id, "summary": "Stale remote result."}, "failed")
    tensology_client.sync_tensology_execution_states(force=True)
    assert sender._message_queue.empty() and not sender.websocket_attempts

    sender.telegram_user_id = 12345
    tensology_client.sync_tensology_execution_states(force=True)
    sender._process_message_queue()
    [delivery] = sender.websocket_attempts
    assert delivery["chat_id"] == 12345
    assert "Approved result." in delivery["text"]
    assert all(incorrect not in delivery["text"] for incorrect in ("Unrelated", "Stale remote result"))
    assert ("Final verification evidence is missing." in delivery["text"]) is not verified
    token = delivery["client_message_id"]
    assert updates.record_delivery(token, telegram_message_id=71, telegram_chat_id=12345)
    updates.queue_execution_update({"source_metadata": {
        "approval_source": "telegram", "approval_telegram_chat_id": 12345,
    }}, chat_id, {"job_id": "later", "summary": "Unrelated follow-up."}, "done")
    tensology_client.sync_tensology_execution_states(force=True)
    sender._process_message_queue()
    assert sender.websocket_attempts == [delivery]
    with session() as db:
        receipt = json.loads(db.get(Chat, chat_id).params)[updates._KEY]
    assert receipt["sent"] == f"{job_id}:{stage}"


@pytest.mark.parametrize("first_status,interrupted_at", [
    ("failed", "enqueue"), ("blocked", "receipt"), ("failed", None),
])
def test_native_telegram_retry_preserves_order_across_queue_failures(intake, monkeypatch, first_status, interrupted_at):
    from sqlalchemy.exc import OperationalError
    from distr.core import tensology_client
    from distr.core.incoming import telegram_updates as updates
    from distr.core.initiative import outcome_flow
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramMessages, DummyTelegramSender

    session, proposal, projects = intake
    sender = DummyTelegramSender()
    sender.telegram_user_id = 54321
    monkeypatch.setattr(updates, "get_session", session)
    monkeypatch.setattr(updates, "_manager", lambda: sender)
    monkeypatch.setattr(tensology_client, "_ticket_push_paused", lambda: True)
    monkeypatch.setattr(outcome_flow, "_pending_proposals", lambda **_: [proposal])
    monkeypatch.setattr(outcome_flow, "audit", lambda *a, **kw: None)
    interrupted_writer = {
        "enqueue": "_queue_execution_update", "receipt": "_write_receipt",
    }.get(interrupted_at)

    def unavailable(*args, **kwargs):
        raise OperationalError("enqueue", {}, RuntimeError("database is locked"))

    if interrupted_writer:
        writer = getattr(updates, interrupted_writer)
        monkeypatch.setattr(updates, interrupted_writer, unavailable)
    jobs = []

    async def worker(*, chat_id, job_id, **kwargs):
        first = not jobs
        jobs.append(job_id)
        harness._update_execution(chat_id, job_id=job_id, status=first_status if first else "completed",
                                  summary="Original run needs review." if first else "Retry finished.",
                                  outcome={"verification": {"final_verdict": "pass"}})

    monkeypatch.setattr(harness, "_run_execution", worker)
    inbound = DummyTelegramMessages()
    callback = f"ip:{outcome_flow._proposal_key_hash(proposal)}:accept"
    for index in range(2):
        previous = set(threading.enumerate())
        inbound._handle_telegram_message({"data": {
            "message_id": f"retry-{index}", "chat_id": 12345, "chat": {"type": "private"},
            "from": {"id": 12345}, "callback_data": callback,
        }})
        chat_id = proposals._existing_proposal_thread(proposal, projects[0])
        assert chat_id is not None and "started work" in inbound.sent[-1]
        _join_new_workers(chat_id, previous)
    assert len(jobs) == len(set(jobs)) == 2
    with session() as db:
        stale_params = json.loads(db.get(Chat, chat_id).params)
    if interrupted_writer:
        previous = stale_params["incoming_proposal_handoff"]["telegram_previous_approvals"]
        assert [approval["job_id"] for approval in previous] == jobs[:1]
        assert updates._KEY not in stale_params
        assert updates.public_proposal_update_status(stale_params)["state"] == "queued"
        monkeypatch.setattr(updates, interrupted_writer, writer)
    tensology_client.sync_tensology_execution_states(force=True)
    assert sender._message_queue.empty() and not sender.websocket_attempts

    sender.telegram_user_id = 12345
    tensology_client.sync_tensology_execution_states(force=True)
    for index, expected in enumerate(("Original run needs review.", "Retry finished.")):
        sender._process_message_queue()
        assert len(sender.websocket_attempts) == index + 1
        delivery = sender.websocket_attempts[-1]
        assert delivery["chat_id"] == 12345 and expected in delivery["text"]
        assert updates.record_delivery(delivery["client_message_id"], telegram_message_id=70 + index,
                                       telegram_chat_id=12345)
    updates.queue_local_proposal_outcome(chat_id, stale_params)
    tensology_client.sync_tensology_execution_states(force=True)
    sender._process_message_queue()
    assert len(sender.websocket_attempts) == 2
    with session() as db:
        saved = json.loads(db.get(Chat, chat_id).params)
    assert "telegram_previous_approvals" not in saved["incoming_proposal_handoff"]
    assert saved[updates._KEY]["delivered_keys"] == [f"{jobs[0]}:{first_status}", f"{jobs[1]}:done"]
    assert updates.public_proposal_update_status(saved)["state"] == "sent"


def test_execution_update_holds_the_reply_receipt_write_boundary(intake, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.exc import OperationalError

    session, proposal, _ = intake
    chat_id = proposals.create_proposal_thread(proposal)
    reading, release = threading.Event(), threading.Event()

    def hold_route(*args, **kwargs):
        reading.set()
        release.wait(5)

    monkeypatch.setattr(harness, "_remember_turn_route", hold_route)
    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(harness._update_execution, chat_id, status="completed", route_receipt={})
        try:
            assert reading.wait(5)
            with session() as db:
                connection = db.connection()
                connection.exec_driver_sql("PRAGMA busy_timeout = 0")
                with pytest.raises(OperationalError, match="locked"):
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
        finally:
            release.set()
        assert result.result(timeout=5)["status"] == "completed"


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


@pytest.mark.parametrize("changes", [
    {"tasks": ["A different task"]},
    {"task_completed_indexes": [0]},
    {"attachments": [{"name": "replacement.pdf", "api_path": "/api/replacement.pdf"}]},
    {"source_scope": [{"source": "gmail", "source_thread_id": "other-recipient", "message_ids": ["message-b"]}]},
])
def test_review_submit_requires_the_reviewed_work_and_sources(intake, monkeypatch, changes):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.incoming import register_routes

    _, proposal, _ = intake
    key = proposals.proposal_review_key(proposal)
    monkeypatch.setattr(proposals, "list_incoming_proposals",
                        lambda **kwargs: {"proposals": [{**proposal, **changes}]})
    attempts = []
    monkeypatch.setattr(proposals, "create_proposal_thread", lambda *a, **k: attempts.append(a) or 1)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)

    result = TestClient(app).post("/workflows/studio/incoming/proposals/thread",
                                  json={"proposal": proposal, "review_key": key})
    assert result.status_code == 409
    assert "changed" in result.json()["error"]
    assert attempts == []


@pytest.mark.parametrize(("route_name", "changes", "mutation"), [
    ("details", {"task_completed_indexes": [0]}, "distr.core.incoming.proposal_sync.update_proposal"),
    ("project", {"project_id": 2}, "distr.core.incoming.proposals.reassign_proposal_project"),
])
def test_review_edit_rejects_changed_tasks_without_advancing_the_timestamp(intake, monkeypatch, route_name, changes, mutation):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.incoming import register_routes

    _, proposal, projects = intake
    proposal["updated_at"] = "2026-11-12T12:00:00Z"
    newer = {**proposal, "tasks": ["A different task"]}
    if route_name == "project":
        changes = {"project_id": projects[1]}
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **kwargs: {"proposals": [newer]})
    writes = []
    monkeypatch.setattr(mutation, lambda *args, **kwargs: writes.append(args) or newer)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    body = {"proposal_id": proposal["id"], "message_ids": proposal["message_ids"],
            "expected_updated_at": proposal["updated_at"], "reviewed_proposal": proposal, **changes}
    api = TestClient(app)
    endpoint = f"/workflows/studio/incoming/proposals/{route_name}"
    rejected = api.put(endpoint, json=body)
    assert rejected.status_code == 409 and not writes
    assert api.put(endpoint, json={**body, "reviewed_proposal": newer}).status_code == 200
    assert len(writes) == 1
    if route_name == "details":
        assert writes[0][1] == changes


def test_review_submit_rejects_a_changed_upstream_manifest_after_cache_lookup(intake, monkeypatch):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes.development.incoming import register_routes

    session, proposal, projects = intake
    row = {**proposal, "id": 811, "status": "pending", "updated_at": "2026-11-12T12:00:00Z",
           "project": {"id": 9001, "name": "First", "decisions_project_id": projects[0]},
           "source_conversation": {"thread_id": "source-a"}, "source_message_ids": ["message-a"],
           "attachments": [{"name": "scope.pdf", "sha256": "a" * 64}]}
    snapshot = proposals._from_tensology(row)
    newer = {**row, "attachments": [{"name": "scope.pdf", "sha256": "b" * 64}]}
    receipts = []
    client = SimpleNamespace(get=lambda path: newer, post=lambda *a, **k: receipts.append(a))
    monkeypatch.setattr('distr.core.tensology_client.configured_tensology_client', lambda **kwargs: client)
    pending = [snapshot]
    monkeypatch.setattr(proposals, "list_incoming_proposals", lambda **kwargs: {"proposals": pending})
    downloads, dispatches, invalidations = [], [], []
    monkeypatch.setattr(proposals, "invalidate_proposals_list_cache", lambda: invalidations.append(True))
    monkeypatch.setattr('distr.core.incoming.media.ensure_proposal_attachments',
                        lambda *a, **k: downloads.append(a) or {"attachments": []})
    monkeypatch.setattr(harness, 'dispatch_development_prompt',
                        lambda *a, **k: dispatches.append(a) or {"status": "running"})
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)

    api = TestClient(app)
    base = "/workflows/studio/incoming/proposals"
    key = proposals.proposal_review_key(snapshot)
    result = api.post(base + "/thread", json={"proposal": snapshot, "review_key": key})
    assert result.status_code == 409
    assert "changed" in result.json()["error"]
    assert not downloads and not dispatches and not receipts
    assert invalidations == [True]
    with session() as db:
        assert db.query(Chat).count() == 0
    pending[:] = [proposals._from_tensology(newer)]
    fresh = api.get(base + "/review", params={"review_key": key}).json()["proposal"]
    body = {"proposal": fresh, "review_key": key}
    started = api.post(base + "/thread", json=body)
    assert started.status_code == 200
    assert api.post(base + "/thread", json=body).json()["chat_id"] == started.json()["chat_id"]
    assert len(downloads) == len(dispatches) == len(receipts) == 1


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


@pytest.mark.parametrize('first_source', ['other_group', 'other_topic', 'gmail'])
def test_telegram_proposal_does_not_adopt_another_conversations_ticket(intake, monkeypatch, first_source):
    from distr.core.db.kanban import KanbanTicket
    from distr.core.incoming import project_links
    from distr.core.kanban.proposal_tickets import find_thread_for_proposal

    session, proposal, projects = intake
    group = '-100777'
    project = projects[0]
    first_channel = 'gmail' if first_source == 'gmail' else 'telegram'
    first_thread = {'other_group': '-100888', 'other_topic': f'{group}~topic~101', 'gmail': 'source-a'}[first_source]
    second_thread = f'{group}~topic~102' if first_source == 'other_topic' else group
    monkeypatch.setattr(project_links, 'source_project_index', lambda: {
        (first_channel, first_thread.split('~topic~')[0]): {project}, ('telegram', group): {project},
    })
    first = {**proposal, 'id': f'local:{first_channel}:{first_thread}', 'source': first_channel,
             'source_thread_id': first_thread, 'message_ids': ['55'], 'title': 'First conversation'}
    second = {**proposal, 'id': f'local:telegram:{second_thread}', 'source': 'telegram',
              'source_thread_id': second_thread, 'message_ids': ['55'], 'title': 'Second conversation'}
    first_chat = proposals.create_proposal_thread(first)
    with session() as db:
        old = db.query(KanbanTicket).filter_by(source_chat_id=first_chat).one()
        old.source_external_id = '55'
        old.source_provider, old.source_thread_id = first_channel, first_thread
        first_ticket_id = old.id
        db.commit()

    assert find_thread_for_proposal(second, project) is None
    second_chat = proposals.create_proposal_thread(second)
    assert first_chat != second_chat
    for source, chat_id in [(first, first_chat), (second, second_chat)]:
        assert proposals.create_proposal_thread(source) == chat_id
        assert proposals.resolve_proposal_review(proposals.proposal_review_key(source)) == {
            'status': 'thread', 'chat_id': chat_id,
        }
    with session() as db:
        first_ticket = db.get(KanbanTicket, first_ticket_id)
        second_ticket = db.query(KanbanTicket).filter_by(source_chat_id=second_chat).one()
        assert first_ticket.source_chat_id == first_chat and first_ticket.source_thread_id == first_thread
        assert second_ticket.id != first_ticket.id
        assert second_ticket.source_external_id == proposals._proposal_handoff_key(second)


def test_starting_a_mixed_merge_keeps_its_own_ticket_and_both_sources(intake, monkeypatch):
    from unittest.mock import Mock
    from distr.core.db.kanban import KanbanTicket
    from distr.core.incoming import project_links, source_reply

    session, proposal, projects = intake
    project = projects[0]
    group = '-100777'
    topic = f'{group}~topic~101'
    monkeypatch.setattr(project_links, 'source_project_index', lambda: {
        ('telegram', group): {project}, ('gmail', 'source-a'): {project},
    })
    first = {**proposal, 'id': 'local:gmail:original', 'message_ids': ['81']}
    second = {**proposal, 'id': f'local:telegram:{topic}', 'source': 'telegram',
              'source_thread_id': topic, 'message_ids': ['55']}
    unrelated = proposals.create_proposal_thread({**proposal, 'id': 'local:gmail:unrelated', 'message_ids': ['55']})
    merged = proposals.merge_proposals(first, second, complete=lambda *args: '{}')
    dispatch = Mock(return_value={'status': 'initializing'})
    monkeypatch.setattr(harness, 'dispatch_development_prompt', dispatch)
    assert proposals.create_proposal_thread(merged['merged'], start_work=True) == merged['chat_id']
    assert proposals.create_proposal_thread(merged['merged'], start_work=True) == merged['chat_id']
    dispatch.assert_called_once()
    with session() as db:
        tickets = {row.source_chat_id: row for row in db.query(KanbanTicket).all()}
        assert set(tickets) == {unrelated, merged['chat_id']}
        assert tickets[unrelated].source_external_id == '55'
        assert tickets[merged['chat_id']].source_external_id == proposals._proposal_handoff_key(merged['merged'])
        context = json.loads(db.get(Chat, merged['chat_id']).params)['incoming_reply_context']
    assert {choice['key'] for choice in source_reply.source_choices(context)} == {'gmail:source-a', f'telegram:{topic}'}


def test_gmail_proposal_ignores_a_legacy_telegram_card_with_the_same_message_number(intake, monkeypatch):
    from distr.core.db.kanban import KanbanTicket
    from distr.core.incoming import project_links

    session, proposal, projects = intake
    group = '-100777'
    monkeypatch.setattr(project_links, 'source_project_index', lambda: {
        ('telegram', group): {projects[0]}, ('gmail', 'source-a'): {projects[0]},
    })
    telegram = {**proposal, 'id': f'local:telegram:{group}', 'source': 'telegram',
                'source_thread_id': group, 'message_ids': ['55']}
    telegram_chat = proposals.create_proposal_thread(telegram)
    with session() as db:
        old = db.query(KanbanTicket).filter_by(source_chat_id=telegram_chat).one()
        old.source_external_id = '55'
        db.commit()

    gmail_chat = proposals.create_proposal_thread({**proposal, 'message_ids': ['55']})
    assert gmail_chat != telegram_chat
    with session() as db:
        assert {row.source_chat_id for row in db.query(KanbanTicket).all()} == {telegram_chat, gmail_chat}


def test_telegram_proposal_topic_uses_only_its_explicit_link_or_its_groups_link():
    from distr.core.incoming.project_links import proposal_in_scope

    topic = '-100777~topic~101'
    proposal = {'source': 'telegram', 'source_thread_id': topic, 'project_id': 9}
    group_link = {('telegram', '-100777'): {9}}
    assert proposal_in_scope(proposal, group_link)
    assert not proposal_in_scope(proposal, {('telegram', '-100888'): {9}})
    assert not proposal_in_scope(proposal, {**group_link, ('telegram', topic): {8}})
    review = {**proposal, 'project_id': None, 'needs_project': True}
    assert proposal_in_scope(review, group_link, allow_review=True)
    assert not proposal_in_scope(review, {**group_link, ('telegram', topic): set()}, allow_review=True)


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
