"""Generated-task prerequisites across real dispatch entry points, providers stubbed."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from distr.core.db import Chat
from distr.core.db.kanban import KanbanBoard, KanbanLane, KanbanTicket, ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep, DevelopmentWorkItem, PlanItemRevision
from distr.core.planning import build, prerequisites as gate
from tests.core.test_plan_build import store, task


@pytest.fixture
def graph(store, monkeypatch):
    monkeypatch.setattr(gate, 'get_session', store)
    result=build.build_tasks(1,tasks=[task('foundation'),task('dependent',depends_on=['foundation'])],expected_revisions={'1':1})
    ids={row['key']:row['id'] for row in result['tasks']}
    with store() as db:
        db.add(KanbanLane(id=3,board_id=1,name='Complete'))
        db.add(KanbanLane(id=4,board_id=1,name='QA'))
        db.add(AutoWorkflow(id=10,name='Explicit workflow'))
        db.add(AutoWorkflowStep(id=10,workflow_id=10,name='Implementation',position=0,action_type='run_command',code='Never run',status='queued'))
        db.add(Chat(id=42,project_id=1,autonomy_level='full',params='{}'))
        db.commit()
    return store, ids


def accepted(graph, *, status=None):
    store, ids=graph
    with store() as db:
        dep=db.get(KanbanTicket,ids['foundation']);dep.lane_id=3;dep.workflow_status=status
        db.commit()


@pytest.mark.parametrize('status',[None,'queued','running','waiting','failed','cancelled','completed'])
def test_unaccepted_dependency_blocks_regardless_of_worker_status(graph,status):
    store, ids=graph
    with store() as db:
        db.get(KanbanTicket,ids['foundation']).workflow_status=status;db.commit()
    with pytest.raises(gate.PlanPrerequisiteError,match='prerequisites are incomplete'):
        gate.require_plan_prerequisites(ids['dependent'])


@pytest.mark.parametrize('lane',['Complete','Done','Completed','Closed'])
@pytest.mark.parametrize('old_status',['failed','cancelled','completed'])
def test_human_acceptance_supersedes_old_terminal_worker_status(graph,lane,old_status):
    store, ids=graph
    accepted(graph,status=old_status)
    with store() as db:
        db.get(KanbanLane,3).name=lane;db.commit()
    gate.require_plan_prerequisites(ids['dependent'],project_id=1,board_id=1)


@pytest.mark.parametrize('execution',['queued','running','waiting'])
def test_accepted_label_does_not_override_current_execution(graph,execution):
    store,ids=graph;accepted(graph)
    with store() as db:
        db.add(ProjectExecutionSession(project_id=1,ticket_id=ids['foundation'],status=execution));db.commit()
    with pytest.raises(gate.PlanPrerequisiteError,match='active work'):
        gate.require_plan_prerequisites(ids['dependent'])


def test_agent_done_lane_qa_is_not_human_acceptance(graph):
    store,ids=graph
    with store() as db:
        db.get(KanbanBoard,1).agent_done_lane='QA'
        db.get(KanbanTicket,ids['foundation']).lane_id=4;db.commit()
    with pytest.raises(gate.PlanPrerequisiteError,match='accepted Complete'):
        gate.require_plan_prerequisites(ids['dependent'])


@pytest.mark.parametrize('change',['missing','project','board','different_identity'])
def test_dependency_cannot_disappear_or_change_scope(graph,change):
    store,ids=graph;accepted(graph)
    with store() as db:
        dep=db.get(KanbanTicket,ids['foundation'])
        if change=='missing':db.delete(dep)
        elif change=='project':dep.linked_project_id=2
        elif change=='board':dep.lane_id=2
        else:dep.source_external_id='plan:1:task:some-other-task'
        db.commit()
    with pytest.raises(gate.PlanPrerequisiteError,match='missing or belongs'):
        gate.require_plan_prerequisites(ids['dependent'])


def test_changed_contract_fails_closed_but_legacy_valid_contract_is_checked(graph):
    store,ids=graph;accepted(graph)
    with store() as db:
        ticket=db.get(KanbanTicket,ids['dependent']);notes=json.loads(ticket.context_notes)
        notes.pop('original_digest');ticket.context_notes=json.dumps(notes);db.commit()
    gate.require_plan_prerequisites(ids['dependent'])
    with store() as db:
        ticket=db.get(KanbanTicket,ids['dependent']);ticket.context_notes='not a contract';db.commit()
    with pytest.raises(gate.PlanPrerequisiteError,match='contract is missing or invalid'):
        gate.require_plan_prerequisites(ids['dependent'])


def test_source_revision_changes_are_informational_not_new_execution_policy(graph):
    store,ids=graph;accepted(graph)
    with store() as db:
        db.add(PlanItemRevision(item_id=1,revision=2,title='New descriptive note'));db.commit()
    gate.require_plan_prerequisites(ids['dependent'])


def test_ordinary_tickets_legacy_artifacts_and_general_prompts_are_unchanged(graph):
    store,ids=graph
    with store() as db:
        db.add(KanbanTicket(id=700,lane_id=1,source_provider='manual',title='Normal work',context_notes='arbitrary notes'))
        db.add(KanbanTicket(id=701,lane_id=1,source_provider='plan',source_external_id='legacy-artifact-hash',title='Approved document'))
        db.commit()
    gate.require_plan_prerequisites(None)
    gate.require_plan_prerequisites(700)
    gate.require_plan_prerequisites(701)
    gate.require_plan_prerequisites(ids['foundation'])  # A generated task without dependencies.


def test_cli_public_dispatch_blocks_before_backend_or_execution_session(graph,monkeypatch):
    from distr.core.project_cli_backends import registry
    store,ids=graph
    backend=Mock(side_effect=AssertionError('Provider must not run'))
    monkeypatch.setattr(registry,'get_backend',backend)
    result=asyncio.run(registry.run_project_task(SimpleNamespace(id=1),'Implement task',ticket_id=ids['dependent']))
    assert result.success is False and result.engine=='plan_prerequisites'
    backend.assert_not_called()
    with store() as db:assert db.query(ProjectExecutionSession).count()==0


def test_cli_public_dispatch_can_continue_after_dependencies_accepted(graph,monkeypatch):
    from distr.core.project_cli_backends import registry
    _,ids=graph;accepted(graph)
    backend=Mock(side_effect=RuntimeError('Reached backend readiness seam'))
    monkeypatch.setattr(registry,'get_backend',backend)
    with pytest.raises(RuntimeError,match='Reached backend readiness'):
        asyncio.run(registry.run_project_task(SimpleNamespace(id=1,coding_backend='pi'),'Implement task',ticket_id=ids['dependent']))
    backend.assert_called_once()


def test_native_development_public_submit_blocks_before_thread_execution(graph,monkeypatch):
    from distr.core.workflow import development_harness as harness
    store,ids=graph
    monkeypatch.setattr(harness,'get_session',store)
    monkeypatch.setattr(harness,'_project_and_scope',lambda chat_id:(SimpleNamespace(id=1),{},ids['dependent'],1))
    monkeypatch.setattr(harness,'development_execution_state',lambda chat_id:{})
    monkeypatch.setattr('distr.core.incoming.media.required_thread_attachments',lambda chat_id:[])
    execution=Mock(side_effect=AssertionError('No executor'))
    monkeypatch.setattr(harness,'_update_execution',execution)
    with pytest.raises(gate.PlanPrerequisiteError):
        harness.dispatch_development_prompt(42,'Implement the generated task')
    execution.assert_not_called()


def test_workflow_gateway_blocks_before_creating_or_mutating_thread(graph,monkeypatch):
    from distr.core.workflow import work_dispatch
    store,ids=graph
    monkeypatch.setattr(work_dispatch,'get_session',store)
    ensure=Mock(side_effect=AssertionError('No thread creation'))
    monkeypatch.setattr('distr.core.workflow.development_threads.ensure_development_thread',ensure)
    result=work_dispatch.dispatch_work_item(workflow_id=10,ticket_id=ids['dependent'],project_id=1,board_id=1)
    assert result['success'] is False and result['status']=='blocked'
    ensure.assert_not_called()
    with store() as db:assert db.get(KanbanTicket,ids['dependent']).source_chat_id is None


@pytest.mark.parametrize('surface',['dispatcher','service'])
def test_raw_named_workflow_start_blocks_before_run_creation(graph,monkeypatch,surface):
    from distr.core.workflow import dispatcher,service
    store,ids=graph
    module=dispatcher if surface=='dispatcher' else service
    monkeypatch.setattr(module,'get_session',store)
    result=module.start_workflow_run(10,ticket_id=ids['dependent'],board_id=1,run_metadata={'project_id':1})
    assert result['status']=='blocked' and result['success'] is False
    with store() as db:assert db.query(AutoWorkflowRun).count()==0


@pytest.mark.parametrize('surface',['dispatcher','service'])
def test_typed_workflow_context_cannot_hide_generated_scope(graph,monkeypatch,surface):
    from distr.core.workflow import dispatcher,service
    from distr.core.workflow_engine.context_assembly import WorkflowRunContext
    store,ids=graph
    module=dispatcher if surface=='dispatcher' else service
    monkeypatch.setattr(module,'get_session',store)
    result=module.start_workflow_run(10,run_ctx=WorkflowRunContext(ticket_id=ids['dependent'],board_id=1,active_project_id=1))
    assert result['success'] is False and result['status']=='blocked'
    with store() as db:assert db.query(AutoWorkflowRun).count()==0


def test_workflow_resume_pauses_same_step_and_rechecks_before_continuation(graph,monkeypatch):
    from distr.core.workflow import dispatcher
    store,ids=graph
    monkeypatch.setattr(dispatcher,'get_session',store)
    with store() as db:
        db.add(AutoWorkflowRun(id=10,workflow_id=10,ticket_id=ids['dependent'],board_id=1,
            current_step_id=10,status='running',run_data=json.dumps({'project_id':1})));db.commit()
    execution=Mock(side_effect=AssertionError('No step execution'))
    monkeypatch.setattr(dispatcher.StepDispatcher,'_execute',execution)
    blocked=dispatcher.StepDispatcher().run_in_workflow(10,10)
    assert blocked['status']=='waiting' and blocked['waiting_kind']=='plan_prerequisites'
    assert dispatcher.continue_waiting_step(10)['status']=='waiting'
    execution.assert_not_called()
    accepted(graph)
    resume=Mock(return_value={'success':True,'status':'running'})
    monkeypatch.setattr(dispatcher.StepDispatcher,'run_in_workflow',resume)
    assert dispatcher.continue_waiting_step(10)['success'] is True
    resume.assert_called_once_with(10,10)


@pytest.mark.parametrize('status',['initializing','queued','running','waiting'])
def test_native_dependency_execution_is_active_even_without_cli_session(graph,status):
    store,ids=graph;accepted(graph)
    with store() as db:
        db.add(Chat(id=50,project_id=1,params=json.dumps({'development':{'execution':{'status':status}}})))
        db.add(DevelopmentWorkItem(chat_id=50,local_ticket_id=ids['foundation'],project_id=1,identity_key='ticket:local:foundation'))
        db.commit()
    with pytest.raises(gate.PlanPrerequisiteError,match='active work'):
        gate.require_plan_prerequisites(ids['dependent'])


@pytest.mark.parametrize('explicit',[None,700,'other_plan'])
def test_canonical_thread_owner_cannot_be_bypassed_by_missing_or_other_ticket(graph,explicit):
    store,ids=graph
    with store() as db:
        db.add(KanbanTicket(id=700,lane_id=1,title='Other normal task',source_provider='manual'))
        db.add(DevelopmentWorkItem(chat_id=42,local_ticket_id=ids['dependent'],project_id=1,identity_key='ticket:local:dependent'))
        db.commit()
    explicit=ids['foundation'] if explicit=='other_plan' else explicit
    with pytest.raises(gate.PlanPrerequisiteError):
        gate.require_plan_prerequisites(explicit,chat_id=42)


def test_public_cli_derives_generated_owner_and_rejects_conflicting_explicit_scope(graph,monkeypatch):
    from distr.core.project_cli_backends import registry
    store,ids=graph
    with store() as db:
        db.add(KanbanTicket(id=700,lane_id=1,title='Normal task',source_provider='manual'))
        db.add(DevelopmentWorkItem(chat_id=42,local_ticket_id=ids['dependent'],project_id=1,identity_key='ticket:local:dependent'))
        db.commit()
    backend=Mock(side_effect=AssertionError('No provider'))
    monkeypatch.setattr(registry,'get_backend',backend)
    for explicit in (None,700):
        result=asyncio.run(registry.run_project_task(SimpleNamespace(id=1),'Implement task',ticket_id=explicit,chat_id=42))
        assert result.success is False and result.engine=='plan_prerequisites'
    backend.assert_not_called()


@pytest.mark.parametrize('mutate',['workspace_bool','oversized'])
def test_generated_contract_rejects_wrong_scalar_types_and_oversized_dependencies(graph,mutate):
    store,ids=graph;accepted(graph)
    with store() as db:
        row=db.get(KanbanTicket,ids['dependent']);contract=json.loads(row.context_notes)['plan_build']
        if mutate=='workspace_bool':contract['workspace_id']=True
        else:
            contract['depends_on']=list(range(1000,1201));contract['task']['depends_on']=[str(n) for n in range(201)]
        row.context_notes=build._notes(contract);db.commit()
    with pytest.raises(gate.PlanPrerequisiteError):gate.require_plan_prerequisites(ids['dependent'])


@pytest.mark.parametrize('endpoint',['send-to-cli','send-to-workflow'])
def test_public_http_cli_rejection_is_readable_and_has_no_execution_or_lane_write(graph,monkeypatch,endpoint):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from distr.gui.web.routes import kanban
    store,ids=graph
    monkeypatch.setattr(kanban,'get_session',store)
    app=FastAPI();app.include_router(kanban.create_routes(),prefix='/api')
    response=TestClient(app).post(f"/api/tickets/tickets/{ids['dependent']}/{endpoint}",json={'workflow_id':10} if endpoint=='send-to-workflow' else {})
    assert response.status_code==409
    assert 'prerequisites are incomplete' in response.json()['detail']
    assert response.json()['success'] is False
    with store() as db:
        assert db.get(KanbanTicket,ids['dependent']).lane_id==1
        assert db.query(ProjectExecutionSession).count()==db.query(AutoWorkflowRun).count()==0


def test_public_ticket_tool_reports_blocker_without_creating_audit_or_moving_ticket(graph,monkeypatch):
    from distr.core.agent.tools.integrations.kanban_ticket import KanbanTicketTool
    store,ids=graph
    monkeypatch.setattr(KanbanTicketTool,'_get_session',lambda self:store())
    message=KanbanTicketTool()._action_send_to_cli(ids['dependent'])
    assert 'prerequisites are accepted' in message
    assert 'prerequisites are incomplete' in message
    with store() as db:
        assert db.get(KanbanTicket,ids['dependent']).lane_id==1
        assert db.query(AutoWorkflow).count()==1  # Only the explicit test fixture, no dispatch audit.


@pytest.mark.parametrize('explicit',[None,700,'other_plan'])
def test_canonical_run_owner_cannot_be_bypassed(graph,explicit):
    store,ids=graph
    with store() as db:
        db.add(KanbanTicket(id=700,lane_id=1,title='Other normal task',source_provider='manual'))
        db.add(AutoWorkflowRun(id=55,workflow_id=10,ticket_id=ids['dependent'],status='waiting',run_data='{}'))
        db.commit()
    explicit=ids['foundation'] if explicit=='other_plan' else explicit
    with pytest.raises(gate.PlanPrerequisiteError):gate.require_plan_prerequisites(explicit,run_id=55)


def test_large_valid_dependency_check_batches_reads(graph):
    from sqlalchemy import event
    store,ids=graph;accepted(graph)
    with store() as db:
        target=db.get(KanbanTicket,ids['dependent']);contract=json.loads(target.context_notes)['plan_build']
        for number in range(1000,1198):
            key=f'prerequisite-{number}'
            db.add(KanbanTicket(id=number,lane_id=3,title=key,linked_project_id=1,
                source_provider='plan',source_external_id=f'plan:1:task:{key}'))
            contract['depends_on'].append(number);contract['task']['depends_on'].append(key)
        target.context_notes=build._notes(contract);db.commit();engine=db.get_bind()
    queries=[]
    def count(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
    event.listen(engine,'before_cursor_execute',count)
    try:gate.require_plan_prerequisites(ids['dependent'])
    finally:event.remove(engine,'before_cursor_execute',count)
    assert len(queries)<=12


def test_workflow_gateway_rejects_explicit_normal_ticket_with_generated_chat_before_any_write(graph,monkeypatch):
    from distr.core.workflow import work_dispatch
    store,ids=graph
    monkeypatch.setattr(work_dispatch,'get_session',store)
    with store() as db:
        db.add(KanbanTicket(id=700,lane_id=1,title='Normal work',source_provider='manual'))
        db.add(DevelopmentWorkItem(chat_id=42,local_ticket_id=ids['dependent'],project_id=1,identity_key='ticket:local:dependent'))
        db.commit()
    result=work_dispatch.dispatch_work_item(workflow_id=10,chat_id=42,ticket_id=700,project_id=1,board_id=1)
    assert result['success'] is False and 'does not match' in result['error']
    with store() as db:
        assert db.get(KanbanTicket,700).source_chat_id is None
        assert db.query(AutoWorkflowRun).count()==0


@pytest.mark.parametrize('raw',['{bad','[]','null','42'])
def test_resume_and_step_dispatch_fail_closed_on_malformed_context(graph,monkeypatch,raw):
    from distr.core.workflow import dispatcher
    store,ids=graph
    monkeypatch.setattr(dispatcher,'get_session',store)
    with store() as db:
        db.add(AutoWorkflowRun(id=55,workflow_id=10,ticket_id=ids['dependent'],board_id=1,
            current_step_id=10,status='waiting',run_data=raw));db.commit()
    assert 'context is invalid' in dispatcher.continue_waiting_step(55)['error']
    step=dispatcher.StepDispatcher().run_in_workflow(10,55)
    assert step['success'] is False and step['status']=='waiting'
    assert 'context is invalid' in step['error']
    with store() as db:assert db.get(AutoWorkflowRun,55).run_data==raw
    assert 'context is invalid' in dispatcher.continue_waiting_step(55)['error']
    with store() as db:assert db.get(AutoWorkflowRun,55).run_data==raw


@pytest.mark.parametrize('status',['initializing','queued','running','waiting'])
def test_active_workflow_dependency_blocks_even_after_stale_complete_lane(graph,status):
    store,ids=graph;accepted(graph)
    with store() as db:
        db.add(AutoWorkflowRun(id=55,workflow_id=10,ticket_id=ids['foundation'],status=status));db.commit()
    with pytest.raises(gate.PlanPrerequisiteError,match='active work'):
        gate.require_plan_prerequisites(ids['dependent'])
