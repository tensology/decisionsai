"""Atomic scope claims prevent moving a running or about-to-start worker."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from fastapi import APIRouter,FastAPI
from fastapi.testclient import TestClient

from .test_proposal_continuity_integration import intake, _join_new_workers
from distr.core.db import Chat
from distr.core.incoming import proposals
from distr.core.turn_runtime import TurnResult
from distr.core.workflow import development_harness as harness
from distr.core.workflow.development_threads import rebind_development_thread


def test_overlapping_starts_keep_the_original_worker_and_allow_other_projects(intake, monkeypatch):
    from distr.gui.web.routes.development.threads import register_routes
    from distr.core.workflow.development_threads import ensure_development_thread

    session, proposal, projects = intake
    chat_id = proposals.create_proposal_thread(proposal)
    other_chat = ensure_development_thread(title="Other project", project_id=projects[1])
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    entered, release = threading.Event(), threading.Event()
    calls = []

    async def backend(request, **kwargs):
        calls.append(request.scope.project_id)
        if request.scope.project_id == projects[0] and calls.count(projects[0]) == 1:
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.01)
        return TurnResult(success=False, backend_id="codex", model="auto",
                          runtime_id="cli_harness", error="Fixture result")

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", backend)
    previous = set(threading.enumerate())
    try:
        first = harness.dispatch_development_prompt(chat_id, "First instruction", dispatch_async=True)
        assert entered.wait(timeout=5)
        with pytest.raises(ValueError, match="already running"):
            harness.dispatch_development_prompt(chat_id, "Overlapping instruction", dispatch_async=False)
        response = client.post(
            f"/workflows/studio/tasks/{chat_id}/messages", json={"message": "Submission from a stale view"},
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "development_execution_busy"
        assert "already running" in response.json()["detail"]
        with session() as db:
            assert db.query(Chat).filter_by(parent_id=chat_id, input="Submission from a stale view").first() is None
        assert harness.development_execution_state(chat_id)["job_id"] == first["job_id"]
        other = harness.dispatch_development_prompt(other_chat, "Separate project", dispatch_async=False)
        assert other["status"] == "failed"
        assert calls == projects
    finally:
        release.set()
        _join_new_workers(chat_id, previous)
    assert harness.development_execution_state(chat_id)["job_id"] == first["job_id"]
    followup = harness.dispatch_development_prompt(chat_id, "Follow-up instruction", dispatch_async=False)
    assert followup["job_id"] != first["job_id"]
    assert calls == [*projects, projects[0]]


def test_finished_human_wait_allows_a_followup(intake, monkeypatch):
    session, proposal, _ = intake
    chat_id = proposals.create_proposal_thread(proposal)
    with session() as db:
        root = db.get(Chat, chat_id)
        params = json.loads(root.params)
        params["development"]["execution"] = {
            "job_id": "previous-job", "status": "waiting", "completed_at": "2026-11-11T08:00:00",
        }
        root.params = json.dumps(params)
        db.commit()

    async def backend(request, **kwargs):
        return TurnResult(success=False, backend_id="codex", model="auto",
                          runtime_id="cli_harness", error="Fixture result")

    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", backend)
    followup = harness.dispatch_development_prompt(chat_id, "Use the selected value", dispatch_async=False)
    assert followup["job_id"] != "previous-job"
    assert followup["status"] == "failed"


def test_simultaneous_studio_submissions_save_only_the_claimed_turn(intake, monkeypatch):
    from distr.core.chat import ChatService
    from distr.gui.web.routes.development.threads import register_routes

    session, proposal, _ = intake
    chat_id = proposals.create_proposal_thread(proposal)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    contenders = threading.Barrier(2)
    release_worker = threading.Event()
    calls = []
    update_execution = harness._update_execution

    def synchronized_claim(*args, **kwargs):
        if kwargs.get("expected_scope") is not None:
            contenders.wait(timeout=8)
        return update_execution(*args, **kwargs)

    async def backend(request, **kwargs):
        calls.append(request)
        while not release_worker.is_set():
            await asyncio.sleep(.01)
        return TurnResult(success=False, backend_id="codex", model="auto",
                          runtime_id="cli_harness", error="Fixture result")

    monkeypatch.setattr(harness, "_update_execution", synchronized_claim)
    monkeypatch.setattr("distr.core.turn_runtime.execute_turn", backend)
    monkeypatch.setattr("distr.core.workflow.development_control.resume_thread_time", lambda *_: None)
    messages = ["Inspect the first setting", "Inspect the second setting"]
    previous = set(threading.enumerate())
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(
                lambda message: client.post(f"/workflows/studio/tasks/{chat_id}/messages", json={"message": message}),
                messages,
            ))
        assert sorted(response.status_code for response in responses) == [200, 409]
        accepted = next(response for response in responses if response.status_code == 200)
        winner = messages[responses.index(accepted)]
        payload = accepted.json()
        with session() as db:
            rows = db.query(Chat).filter(Chat.parent_id == chat_id, Chat.input.in_(messages)).all()
            assert len(rows) == 1
            assert rows[0].input == winner
            assert rows[0].id == payload["turn_id"] == payload["execution"]["turn_id"]
            params = json.loads(db.get(Chat, chat_id).params)
            assert params["active_turn_chat_row_id"] == rows[0].id
            assert str(rows[0].id) in params["development"]["turn_routes"]
        unrelated_turn = ChatService.add_user_message(chat_id, "An instruction saved by another surface")
    finally:
        release_worker.set()
        _join_new_workers(chat_id, previous)
    assert len(calls) == 1
    assert calls[0].scope.turn_id == payload["turn_id"]
    with session() as db:
        assert db.get(Chat, payload["turn_id"]).response == "Fixture result"
        assert db.get(Chat, unrelated_turn).response is None
        routes = json.loads(db.get(Chat, chat_id).params)["development"]["turn_routes"]
        assert str(unrelated_turn) not in routes


def test_studio_claim_rechecks_autonomy_before_saving_the_prompt(intake, monkeypatch):
    from distr.gui.web.routes.development.threads import register_routes

    session, proposal, _ = intake
    chat_id = proposals.create_proposal_thread(proposal)
    update_execution = harness._update_execution

    def require_approval_before_claim(*args, **kwargs):
        if kwargs.get("expected_scope") is not None:
            with session() as db:
                db.get(Chat, chat_id).autonomy_level = "approval"
                db.commit()
        return update_execution(*args, **kwargs)

    monkeypatch.setattr(harness, "_update_execution", require_approval_before_claim)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    message = "Inspect after the approval setting changed"
    response = TestClient(app).post(f"/workflows/studio/tasks/{chat_id}/messages", json={"message": message})
    assert response.status_code == 409, response.text
    with session() as db:
        assert db.query(Chat).filter_by(parent_id=chat_id, input=message).first() is None
        execution = json.loads(db.get(Chat, chat_id).params)["development"].get("execution")
        assert not execution


def test_claim_then_rebind_http_rejects_scope_but_allows_settings(intake,monkeypatch):
    from distr.gui.web.routes.development.threads import register_routes
    session,proposal,ids=intake
    entered,release=threading.Event(),threading.Event()
    calls=[]
    async def backend(request,**kwargs):
        calls.append(request);entered.set()
        while not release.is_set(): await asyncio.sleep(.01)
        return TurnResult(success=False,backend_id='codex',model='auto',runtime_id='cli_harness',error='Fixture result')
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn',backend)
    chat_id=proposals.create_proposal_thread(proposal)
    app,router=FastAPI(),APIRouter();register_routes(router,None);app.include_router(router)
    client=TestClient(app)
    previous=set(threading.enumerate())
    try:
        harness.dispatch_development_prompt(chat_id,'Read the configuration',dispatch_async=True)
        assert entered.wait(timeout=5)
        response=client.patch(f'/workflows/studio/tasks/{chat_id}',json={'title':'Attempted move','project_id':ids[1]})
        assert response.status_code==409,response.text
        assert 'Stop or finish' in response.json()['detail']
        renamed=client.patch(f'/workflows/studio/tasks/{chat_id}',json={'title':'Renamed safely','remote_continuation':True})
        assert renamed.status_code==200,renamed.text
        assert renamed.json()['project_id']==ids[0]
    finally:
        release.set();_join_new_workers(chat_id,previous)
    with session() as db:
        root=db.get(Chat,chat_id)
        assert root.project_id==ids[0] and root.title=='Renamed safely'
        assert 'expected_scope' not in json.loads(root.params)['development']['execution']
    assert len(calls)==1 and calls[0].scope.project_id==ids[0]
    # Explicit reassignment after the worker terminates remains supported.
    assert rebind_development_thread(chat_id,project_id=ids[1],board_key=None,board_provider=None)['project_id']==ids[1]


def test_rebind_wins_before_claim_dispatch_rejects_before_provider(intake,monkeypatch):
    session,proposal,ids=intake
    reached,release=threading.Event(),threading.Event()
    calls=[];errors=[]
    original_route=harness._route
    def held_route(*args,**kwargs):
        reached.set()
        assert release.wait(timeout=5),'Held route did not release'
        return original_route(*args,**kwargs)
    async def backend(request,**kwargs):
        calls.append(request)
        raise AssertionError('A stale-scope worker must never start')
    monkeypatch.setattr(harness,'_route',held_route)
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn',backend)
    chat_id=proposals.create_proposal_thread(proposal)
    def dispatch():
        try: harness.dispatch_development_prompt(chat_id,'Read configuration',dispatch_async=False)
        except Exception as exc: errors.append(exc)
    worker=threading.Thread(target=dispatch,name='scope-claim-audit')
    worker.start()
    try:
        assert reached.wait(timeout=5),'Dispatcher did not reach its preclaim route'
        rebind_development_thread(chat_id,project_id=ids[1],board_key=None,board_provider=None)
    finally:
        release.set();worker.join(timeout=5)
        assert not worker.is_alive(),'Test-owned dispatch did not stop'
    assert calls==[]
    assert len(errors)==1 and isinstance(errors[0],ValueError)
    assert 'changed before work started' in str(errors[0])
    with session() as db:
        root=db.get(Chat,chat_id)
        assert root.project_id==ids[1]
        assert not json.loads(root.params)['development'].get('execution')


@pytest.mark.parametrize('status',['initializing','queued','running','waiting','pending','dispatching','paused'])
@pytest.mark.parametrize('change',['project','board','ticket'])
def test_pending_and_active_aliases_block_actual_scope_changes(intake,status,change):
    session,proposal,ids=intake
    chat_id=proposals.create_proposal_thread(proposal)
    with session() as db:
        root=db.get(Chat,chat_id);params=json.loads(root.params)
        params['development']['execution']={'status':status}
        root.params=json.dumps(params);db.commit()
    target={'project_id':ids[0],'board_key':None,'board_provider':None}
    if change=='project':target['project_id']=ids[1]
    elif change=='board':target.update(board_key='trello:another-board',board_provider='trello')
    else:target.update(board_ticket_key='another-source-ticket')
    with pytest.raises(ValueError,match='Stop or finish'):
        rebind_development_thread(chat_id,**target)
    with session() as db:
        root=db.get(Chat,chat_id)
        assert root.project_id==ids[0]
        assert json.loads(root.params)['development']['execution']['status']==status


@pytest.mark.parametrize('status',['completed','completed_noop','failed','blocked','cancelled'])
def test_terminal_execution_allows_explicit_scope_correction(intake,status):
    session,proposal,ids=intake
    chat_id=proposals.create_proposal_thread(proposal)
    with session() as db:
        root=db.get(Chat,chat_id);params=json.loads(root.params)
        params['development']['execution']={'status':status};root.params=json.dumps(params);db.commit()
    assert rebind_development_thread(chat_id,project_id=ids[1],board_key=None,board_provider=None)['project_id']==ids[1]


@pytest.mark.parametrize('kind',['workflow','session','dispatching_command'])
def test_other_durable_work_blocks_move_even_with_old_terminal_metadata(intake,kind):
    from distr.core.db.workflow import AutoWorkflow,AutoWorkflowRun,DevelopmentCommand
    from distr.core.db.kanban import ProjectExecutionSession
    session,proposal,ids=intake
    chat_id=proposals.create_proposal_thread(proposal)
    with session() as db:
        root=db.get(Chat,chat_id);params=json.loads(root.params)
        execution={'status':'failed'}
        if kind=='workflow':
            workflow=AutoWorkflow(name='Queued fixture');db.add(workflow);db.flush()
            db.add(AutoWorkflowRun(workflow_id=workflow.id,chat_id=chat_id,status='queued'))
        elif kind=='session':
            pending=ProjectExecutionSession(project_id=ids[0],route_backend='codex',status='queued')
            db.add(pending);db.flush();execution['execution_session_id']=pending.id
        else:db.add(DevelopmentCommand(chat_id=chat_id,content='Pending fixture',status='dispatching'))
        params['development']['execution']=execution;root.params=json.dumps(params);db.commit()
    with pytest.raises(ValueError,match='Stop or finish'):
        rebind_development_thread(chat_id,project_id=ids[1],board_key=None,board_provider=None)


def test_unlinked_project_uses_matching_synthetic_claim_identity(intake,monkeypatch):
    _,proposal,_=intake
    chat_id=proposals.create_proposal_thread(proposal)
    rebind_development_thread(chat_id,project_id=None,board_key=None,board_provider=None)
    calls=[]
    monkeypatch.setattr(harness,'_route',lambda *args,**kwargs:('codex','auto',{},'low'))
    async def backend(request,**kwargs):
        calls.append(request)
        return TurnResult(success=False,backend_id='codex',model='auto',runtime_id='cli_harness',error='Fixture result')
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn',backend)
    result=harness.dispatch_development_prompt(chat_id,'Read configuration',dispatch_async=False)
    assert len(calls)==1 and calls[0].scope.project_id==-chat_id
    assert result['status']=='failed'


@pytest.mark.parametrize('invalid',[['unknown'],'broken',42])
def test_malformed_legacy_execution_blocks_scope_change_readably(intake,invalid):
    session,proposal,ids=intake
    chat_id=proposals.create_proposal_thread(proposal)
    with session() as db:
        root=db.get(Chat,chat_id);params=json.loads(root.params)
        params['development']['execution']=invalid;root.params=json.dumps(params);db.commit()
    with pytest.raises(ValueError,match='invalid Development execution state'):
        rebind_development_thread(chat_id,project_id=ids[1],board_key=None,board_provider=None)
    with session() as db:
        root=db.get(Chat,chat_id)
        assert root.project_id==ids[0]
        assert json.loads(root.params)['development']['execution']==invalid
