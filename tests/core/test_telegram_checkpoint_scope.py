"""Telegram decisions must belong to the current durable workflow checkpoint."""
import json
from pathlib import Path

import pytest
from sqlalchemy import text

from .test_proposal_continuity_integration import intake
from distr.core.db.workflow import AutoWorkflow,AutoWorkflowRun,AutoWorkflowStep
from distr.core.workflow import interactions,dispatcher
from distr.core.workflow import service
from distr.core.workflow.development_threads import ensure_development_thread


@pytest.fixture
def checkpoint(intake,monkeypatch):
    import socket
    import traceback
    import requests
    attempted=[]
    def no_transport(*args,**kwargs):
        if not attempted:
            frames=traceback.extract_stack(limit=45)
            path=' <- '.join(f"{frame.filename.rsplit('/',1)[-1]}:{frame.name}:{frame.lineno}" for frame in frames[:-1])
            print('ISOLATED_NETWORK_ATTEMPT',path)
        attempted.append(True)
        raise OSError('External transport blocked by isolated test')
    monkeypatch.setattr(requests.sessions.Session,'request',no_transport)
    monkeypatch.setattr(socket.socket,'connect',no_transport)
    monkeypatch.setattr(socket.socket,'connect_ex',no_transport)
    monkeypatch.setattr('distr.core.workspace_memory.paths.WORKSPACES_ROOT',Path.home()/'.decisions/workspaces')
    monkeypatch.setattr('distr.core.mempalace.wiring.dual_write_workspace_event',lambda **kwargs:None)
    monkeypatch.setattr('distr.core.mempalace.wiring.dual_write_steering',lambda **kwargs:None)
    monkeypatch.setattr('distr.core.workflow.standards_memory.capture_feedback_as_memory',lambda *args,**kwargs:None)
    session,_,ids=intake
    from distr.core import db as db_module
    with session() as db:
        test_engine=db.get_bind()
    monkeypatch.setattr(interactions,'engine',test_engine)
    monkeypatch.setattr(dispatcher,'get_session',session)
    monkeypatch.setattr(service,'get_session',session)
    from distr.core.workflow import router,run_briefing
    monkeypatch.setattr(router,'get_session',session)
    monkeypatch.setattr(run_briefing,'get_session',session)
    monkeypatch.setattr('distr.core.workflow.steering_memory.get_session',session)
    monkeypatch.setattr('distr.core.human_engagement.mark_workflow_engagement_answered',lambda *args:None)
    monkeypatch.setattr('distr.core.project_cli_backends.registry.terminate_backend_process',lambda *args,**kwargs:False)
    chat_id=ensure_development_thread(title='Scoped checkpoint',project_id=ids[0])
    with session() as db:
        workflow=AutoWorkflow(name='Explicit Telegram workflow');db.add(workflow);db.flush()
        steps=[AutoWorkflowStep(workflow_id=workflow.id,name=f'Approval {i}',position=i,status='waiting') for i in range(2)]
        db.add_all(steps);db.flush()
        run=AutoWorkflowRun(workflow_id=workflow.id,chat_id=chat_id,status='waiting',current_step_id=steps[0].id,
            run_data=json.dumps({'project_id':ids[0],'chat_id':chat_id,'waiting_kind':'approval','waiting_prompt':'Approve first scope'}))
        db.add(run);db.commit()
        scope={'workflow_id':workflow.id,'run_id':run.id,'step_id':steps[0].id,'kind':'approval','telegram_chat_id':123}
        next_step=steps[1].id
    item=interactions.create_workflow_interaction(**scope)
    yield session,item,scope,next_step,ids
    assert not attempted,'Isolated checkpoint test attempted external transport'


def test_delayed_stop_cannot_cancel_a_different_waiting_step(checkpoint):
    session,item,scope,next_step,_=checkpoint
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);run.current_step_id=next_step
        data=json.loads(run.run_data);data['waiting_prompt']='Approve second scope';run.run_data=json.dumps(data);db.commit()
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:stop",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True,result
    with session() as db:
        assert db.get(AutoWorkflowRun,scope['run_id']).status=='waiting'


def test_deleted_run_token_is_expired_and_cannot_report_success(checkpoint):
    session,item,scope,_,_=checkpoint
    with session() as db:
        db.delete(db.get(AutoWorkflowRun,scope['run_id']));db.commit()
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:stop",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True,result
    with interactions.engine.connect() as conn:
        assert conn.execute(text('SELECT status FROM workflow_interactions WHERE token=:token'),{'token':item['token']}).scalar()=='expired'


@pytest.mark.parametrize('change',['project','thread_project','board','ticket','question','kind'])
def test_control_expires_when_approved_context_changes(checkpoint,change):
    from distr.core.db import Chat
    session,item,scope,_,ids=checkpoint
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        if change=='project':data['project_id']=ids[1]
        elif change=='thread_project':db.get(Chat,run.chat_id).project_id=ids[1]
        elif change=='board':run.board_id=9123
        elif change=='ticket':run.ticket_id=9223
        elif change=='question':data['waiting_prompt']='A materially different approval'
        elif change=='kind':data['waiting_kind']='worker_needs_input'
        run.run_data=json.dumps(data);db.commit()
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:stop",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True,result
    with session() as db:assert db.get(AutoWorkflowRun,scope['run_id']).status=='waiting'


def test_changed_provider_options_require_new_controls(checkpoint):
    session,_,scope,_,_=checkpoint
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        data.update(waiting_kind='provider_preflight',provider_free_candidates=[{'model':'original-model'}])
        run.run_data=json.dumps(data);db.commit()
    first=interactions.create_workflow_interaction(**{**scope,'kind':'provider_preflight'})
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        data['provider_free_candidates']=[{'model':'replacement-model'}];run.run_data=json.dumps(data);db.commit()
    result=interactions.handle_telegram_workflow_reply(f"wf:{first['token']}:model_0",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True,result
    second=interactions.create_workflow_interaction(**{**scope,'kind':'provider_preflight'})
    assert second['token']!=first['token']
    assert json.loads(second['allowed_actions'])==['model_0','stop']


def test_duplicate_stop_claims_once_and_other_chat_cannot_replay_success(checkpoint,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    session,item,scope,_,_=checkpoint
    barrier=threading.Barrier(2);calls=[]
    original=dispatcher.cancel_run
    def cancel(*args,**kwargs):
        calls.append(args[0]);return original(*args,**kwargs)
    monkeypatch.setattr(dispatcher,'cancel_run',cancel)
    def press():
        barrier.wait(timeout=5)
        return interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:stop",chat_id=123,source='telegram_callback')
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(lambda _:press(),range(2)))
    assert all(row.get('success') for row in results),results
    assert calls==[scope['run_id']]
    for chat in (999,None):
        denied=interactions.resolve_interaction(token=item['token'],action='stop',chat_id=chat,source='telegram_callback')
        assert denied['status_code']==403


def test_background_claim_rechecks_scope_before_transition(checkpoint,monkeypatch):
    session,item,scope,next_step,_=checkpoint
    queued=[]
    class DeferredThread:
        def __init__(self,*,target,kwargs,**rest):self.target,self.kwargs=target,kwargs
        def start(self):queued.append(self)
    monkeypatch.setattr(interactions.threading,'Thread',DeferredThread)
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:stop",chat_id=123,source='telegram_callback',background=True)
    assert result['queued'] and len(queued)==1
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);run.current_step_id=next_step;db.commit()
    applied=queued[0].target(**queued[0].kwargs)
    assert applied['status_code']==409
    with session() as db:assert db.get(AutoWorkflowRun,scope['run_id']).status=='waiting'


def test_transition_false_cannot_be_reported_as_applied(checkpoint,monkeypatch):
    _,item,_,_,_=checkpoint
    monkeypatch.setattr(dispatcher,'continue_waiting_step',lambda *args,**kwargs:{'success':False,'status':'blocked','message':'Required prerequisite is incomplete'})
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:approve",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True
    assert result['error']=='Required prerequisite is incomplete'
    assert 'couldn\'t apply' in interactions.workflow_reply_message(result)


def test_delivery_receipt_cannot_rebind_an_existing_control(checkpoint):
    _,item,_,_,_=checkpoint
    assert not interactions.record_telegram_delivery(token=item['token'],telegram_chat_id=999,telegram_message_id=55,reply_markup_sent=True)
    with interactions.engine.connect() as conn:
        assert conn.execute(text('SELECT telegram_chat_id FROM workflow_interactions WHERE token=:token'),{'token':item['token']}).scalar()=='123'


def test_migrated_unscoped_token_expires_instead_of_guessing_owner(checkpoint):
    session,item,scope,_,_=checkpoint
    with interactions.engine.begin() as conn:
        conn.exec_driver_sql('ALTER TABLE workflow_interactions DROP COLUMN scope_json')
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:stop",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True,result
    with session() as db:assert db.get(AutoWorkflowRun,scope['run_id']).status=='waiting'


@pytest.mark.parametrize('ready',[False,True])
def test_provider_readiness_result_cannot_mutate_a_replaced_checkpoint(checkpoint,monkeypatch,ready):
    from types import SimpleNamespace
    session,_,scope,next_step,_=checkpoint
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        data.update(waiting_kind='provider_preflight',provider_free_candidates=[{'backend':'pi','model':'original-model','model_provider':'openrouter'}])
        run.run_data=json.dumps(data);db.commit()
    item=interactions.create_workflow_interaction(**{**scope,'kind':'provider_preflight'})
    probed=[]
    def readiness(*,model,api_key):
        probed.append(model)
        with session() as db:
            run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
            data['provider_free_candidates']=[{'backend':'pi','model':'replacement-model','model_provider':'openrouter'}]
            run.current_step_id=next_step;run.run_data=json.dumps(data);db.commit()
        return SimpleNamespace(ready=ready,message='Original option checked',http_status=200 if ready else 503,
                               to_dict=lambda:{'ready':ready,'message':'Original option checked','http_status':200 if ready else 503})
    monkeypatch.setattr('distr.core.project_cli_backends.provider_preflight.probe_openrouter_model_readiness',readiness)
    monkeypatch.setattr('distr.core.kanban.ticket_workflow_engagement.notify_ticket_workflow_progress',lambda **kwargs:None)
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:model_0",chat_id=123,resolver_id='123',source='telegram_callback')
    assert probed==['original-model']
    assert result.get('success') is not True,result
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        assert run.current_step_id==next_step
        assert data['provider_free_candidates']==[{'backend':'pi','model':'replacement-model','model_provider':'openrouter'}]


@pytest.mark.parametrize('change', ['step', 'status'])
def test_scope_change_between_claim_check_and_stop_transition_cannot_cancel_new_step(checkpoint,monkeypatch,change):
    session,item,scope,next_step,_=checkpoint
    original=dispatcher.cancel_run
    def delayed_stop(*args,**kwargs):
        with session() as db:
            run=db.get(AutoWorkflowRun,scope['run_id'])
            if change=='status':
                run.status='running'  # The next control can keep the same step and prompt.
            else:
                run.current_step_id=next_step
                data=json.loads(run.run_data);data['waiting_prompt']='Different current work';run.run_data=json.dumps(data)
            db.commit()
        return original(*args,**kwargs)
    monkeypatch.setattr(dispatcher,'cancel_run',delayed_stop)
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:stop",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True,result
    with session() as db:assert db.get(AutoWorkflowRun,scope['run_id']).status==('running' if change=='status' else 'waiting')


@pytest.mark.parametrize('kind',['approval','run_briefing','step_review','worker_needs_input','restart_recovery','plan_prerequisites','control_interrupt','pre_execution_approval'])
def test_each_continuation_transaction_rejects_a_replaced_checkpoint(checkpoint,monkeypatch,kind):
    session,_,scope,next_step,_=checkpoint
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        data.update(waiting_kind=kind,pending_first_step_id=scope['step_id'],pending_next_step_id=next_step)
        run.run_data=json.dumps(data);db.commit()
    item=interactions.create_workflow_interaction(**{**scope,'kind':kind})
    name='approve_pre_execution_step' if kind=='pre_execution_approval' else 'continue_waiting_step'
    original=getattr(dispatcher,name)
    def delayed(*args,**kwargs):
        with session() as db:
            run=db.get(AutoWorkflowRun,scope['run_id']);run.current_step_id=next_step
            data=json.loads(run.run_data);data['waiting_prompt']='New checkpoint requiring its own decision'
            run.run_data=json.dumps(data);db.commit()
        return original(*args,**kwargs)
    monkeypatch.setattr(dispatcher,name,delayed)
    action='approve' if kind in {'approval','run_briefing','pre_execution_approval'} else 'continue'
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:{action}",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True,result
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id'])
        assert run.status=='waiting' and run.current_step_id==next_step
        assert db.get(AutoWorkflowStep,next_step).status=='waiting'


def test_reissuing_controls_during_same_claim_reuses_identity(checkpoint):
    _,item,scope,_,_=checkpoint
    with interactions.engine.begin() as conn:
        conn.execute(text("UPDATE workflow_interactions SET status='resolving',resolved_action='stop' WHERE token=:token"),{'token':item['token']})
    again=interactions.create_workflow_interaction(**scope)
    assert again['token']==item['token']


@pytest.mark.parametrize('change',['instruction','execution_route'])
def test_changed_step_or_execution_route_requires_new_approval(checkpoint,change):
    session,item,scope,_,_=checkpoint
    with session() as db:
        if change=='instruction':db.get(AutoWorkflowStep,scope['step_id']).instruction='Different executable work'
        else:
            run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
            data['execution_route']={'backend':'codex','model':'different-model'};run.run_data=json.dumps(data)
        db.commit()
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:stop",chat_id=123,source='telegram_callback')
    assert result.get('success') is not True,result
    with session() as db:assert db.get(AutoWorkflowRun,scope['run_id']).status=='waiting'


def test_control_interrupt_cannot_steer_a_replaced_scope_before_resume_guard(checkpoint,monkeypatch):
    session,_,scope,next_step,ids=checkpoint
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        data.update(waiting_kind='control_interrupt',execution_route={'backend':'pi','model':'fixture'})
        run.run_data=json.dumps(data);db.commit()
    interactions.create_workflow_interaction(**{**scope,'kind':'control_interrupt'})
    original=service.apply_run_harness_steer
    def delayed(*args,**kwargs):
        with session() as db:
            run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
            data['project_id']=ids[1];run.current_step_id=next_step;run.run_data=json.dumps(data);db.commit()
        return original(*args,**kwargs)
    steers=[]
    monkeypatch.setattr(service,'apply_run_harness_steer',delayed)
    monkeypatch.setattr('distr.core.project_cli_backends.harness.steer_harness',
        lambda **kwargs:steers.append(kwargs) or {'success':True,'delivered':False,'method':'queued','backend_id':'pi'})
    result=interactions.handle_telegram_workflow_reply('Please use codex',chat_id=123,source='telegram_text')
    assert result.get('success') is not True,result
    assert steers==[],steers


def test_steer_result_cannot_be_persisted_after_its_scope_changes(checkpoint,monkeypatch):
    session,_,scope,next_step,ids=checkpoint
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        data.update(waiting_kind='control_interrupt',execution_route={'backend':'pi','model':'fixture'})
        run.run_data=json.dumps(data);db.commit()
    delivered=[]
    def steer(**kwargs):
        delivered.append(kwargs['project_id'])
        # An independent mutation is possible here: no DB claim is held
        # while the external worker processes the steering instruction.
        with session() as db:
            run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
            data['project_id']=ids[1];run.current_step_id=next_step;run.run_data=json.dumps(data);db.commit()
        return {'success':True,'delivered':True,'method':'fixture'}
    monkeypatch.setattr('distr.core.project_cli_backends.harness.steer_harness',steer)
    result=service.apply_run_harness_steer(scope['run_id'],'Fixture instruction')
    assert delivered==[ids[0]]
    assert result['status_code']==409
    with session() as db:
        run=db.get(AutoWorkflowRun,scope['run_id']);data=json.loads(run.run_data)
        assert run.current_step_id==next_step and data['project_id']==ids[1]
        assert 'pending_harness_steers' not in data


def test_nested_dispatch_failure_is_not_acknowledged_as_continued(checkpoint,monkeypatch):
    _,item,_,_,_=checkpoint
    monkeypatch.setattr(dispatcher,'continue_waiting_step',lambda *args,**kwargs:{'success':True,'dispatch':{
        'success':False,'status_code':409,'message':'The recorded dispatch was blocked'}})
    result=interactions.handle_telegram_workflow_reply(f"wf:{item['token']}:approve",chat_id=123,source='telegram_callback')
    assert result=={'error':'The recorded dispatch was blocked','status_code':409}
