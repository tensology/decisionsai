"""Seven ticket regressions. All records live in pytest's isolated database."""
import json
from datetime import timedelta
from unittest.mock import Mock

import pytest

from distr.core.db import get_session
from distr.core.db.time import utc_now_naive
from distr.core.db.workflow import AutoWorkflow, AutoWorkflowRun, AutoWorkflowStep
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.workflow import dispatcher, service
from distr.core.workflow.run_health import run_health, check_worker_heartbeats
from distr.core.workflow.blueprint_adherence import power_budget_is_exhausted
from distr.core.workflow.step_executor import StepExecutorMixin


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    from tests.core.test_workflow_crash_guard import _factory, _real_session
    factory = _factory()
    session = lambda: _real_session(factory)
    monkeypatch.setattr(__name__ + '.get_session', session)
    for module in ('distr.core.db', 'distr.core.workflow.dispatcher',
                   'distr.core.workflow.service', 'distr.core.workflow.step_executor', 'distr.core.workflow.router'):
        monkeypatch.setattr(module + '.get_session', session)
    monkeypatch.setattr(dispatcher, 'record_workflow_chat_event', Mock())


def seed(action='send_to_project_cli', **data):
    with get_session() as db:
        wf = AutoWorkflow(name='Ticket improvements')
        db.add(wf)
        db.flush()
        step = AutoWorkflowStep(workflow_id=wf.id, name='Heavy session', instruction='Resume the saved test work', action_type=action, status='running')
        db.add(step)
        db.flush()
        run = AutoWorkflowRun(workflow_id=wf.id, current_step_id=step.id, status='running',
                              started_at=utc_now_naive() - timedelta(minutes=12),
                              run_data=json.dumps({'project_name': 'JackCat', 'ticket_title': 'Find part IDs', 'ticket_id': 179, **data}))
        db.add(run)
        db.commit()
        return wf.id, run.id, step.id


@pytest.mark.parametrize('meter', [{}, {'power_budget': {'max_turns': 1, 'turns_used': 1}}])
def test_restart_automatically_dispatches_saved_step_once(monkeypatch, meter):
    _, rid, sid = seed(**meter)
    dispatch = Mock(return_value={'success': True})
    monkeypatch.setattr(dispatcher, '_dispatch_workflow_step', dispatch)
    monkeypatch.setattr(dispatcher, 'record_workflow_chat_event', Mock())
    dispatcher._cleanup_orphaned_runs_on_startup()
    dispatcher._auto_resume_interrupted_runs(background=False)
    dispatch.assert_called_once_with(rid, sid)
    with get_session() as db:
        run = db.get(AutoWorkflowRun, rid)
        data = json.loads(run.run_data)
        assert run.status == 'running'
        assert data['restart_recovery']['auto_resume_counts'][str(sid)] == 1
    dispatcher._auto_resume_interrupted_runs(background=False)
    dispatch.assert_called_once()


@pytest.mark.parametrize('action,extra,reason', [
    ('http_request', {}, 'side effects'),
    ('send_to_project_cli', {'pending_route_approval': {'backend': 'codex'}}, 'approval'),
])
def test_blocked_recovery_is_listed_with_reason(monkeypatch, action, extra, reason):
    wid, rid, _ = seed(action, **extra)
    dispatch = Mock()
    monkeypatch.setattr(dispatcher, '_dispatch_workflow_step', dispatch)
    dispatcher._cleanup_orphaned_runs_on_startup()
    dispatcher._auto_resume_interrupted_runs(background=False)
    dispatch.assert_not_called()
    row = next(r for r in service.get_active_runs(workflow_id=wid) if r['id'] == rid)
    assert reason.lower() in row['status_summary'].lower()
    assert 'Safe default' in row['waiting_prompt']


def test_restart_auto_retry_limit_survives_second_restart(monkeypatch):
    _, rid, sid = seed()
    dispatch = Mock(return_value={'success': True})
    monkeypatch.setattr(dispatcher, '_dispatch_workflow_step', dispatch)
    monkeypatch.setattr(dispatcher, 'record_workflow_chat_event', Mock())
    for _ in range(3):
        dispatcher._cleanup_orphaned_runs_on_startup()
        dispatcher._auto_resume_interrupted_runs(background=False)
    assert dispatch.call_count == 2
    with get_session() as db:
        data = json.loads(db.get(AutoWorkflowRun, rid).run_data)
        assert data['restart_recovery']['auto_resume_counts'][str(sid)] == 2
        assert 'retry limit' in data['restart_recovery']['blocked_reason']


def test_restart_holds_only_the_step_with_an_unconfirmed_checkpoint_decision(monkeypatch):
    _, blocked_run, blocked_step = seed()
    _, other_run, other_step = seed()
    dispatch = Mock(return_value={"success": True})
    monkeypatch.setattr(dispatcher, "_dispatch_workflow_step", dispatch)
    dispatcher._cleanup_orphaned_runs_on_startup()
    dispatcher._auto_resume_interrupted_runs(
        background=False, unconfirmed_checkpoint_steps={(blocked_run, blocked_step)},
    )
    dispatch.assert_called_once_with(other_run, other_step)
    with get_session() as db:
        run = db.get(AutoWorkflowRun, blocked_run)
        assert run.status == "waiting"
        assert "checkpoint decision" in json.loads(run.run_data)["restart_recovery"]["blocked_reason"]


def test_restart_dispatch_failure_remains_visible(monkeypatch):
    wid, rid, _ = seed()
    monkeypatch.setattr(dispatcher, '_dispatch_workflow_step', Mock(side_effect=RuntimeError('worker unavailable')))
    monkeypatch.setattr(dispatcher, 'record_workflow_chat_event', Mock())
    dispatcher._cleanup_orphaned_runs_on_startup()
    dispatcher._auto_resume_interrupted_runs(background=False)
    row = next(r for r in service.get_active_runs(workflow_id=wid) if r['id'] == rid)
    assert row['status'] == 'waiting' and 'worker unavailable' in row['status_summary']


def test_recovery_failure_does_not_resurrect_a_cancelled_run(monkeypatch):
    _, rid, _ = seed()
    def cancelled(*args):
        with get_session() as db:
            db.get(AutoWorkflowRun, rid).status = 'cancelled'
            db.commit()
        raise RuntimeError('cancelled during recovery')
    monkeypatch.setattr(dispatcher, '_dispatch_workflow_step', cancelled)
    dispatcher._cleanup_orphaned_runs_on_startup()
    dispatcher._auto_resume_interrupted_runs(background=False)
    with get_session() as db:
        assert db.get(AutoWorkflowRun, rid).status == 'cancelled'


def test_interrupted_runs_are_never_hidden_by_active_list_limit():
    _, interrupted, _ = seed()
    dispatcher._cleanup_orphaned_runs_on_startup()
    seed()
    assert interrupted in {item['id'] for item in service.get_active_runs(limit=1)}


def test_all_three_prompts_name_project_ticket_and_actual_action():
    from distr.core.workflow.router import _effective_wait_prompt
    from distr.core.workflow.run_prompts import run_label, restart_prompt
    data = {'project_name': 'JackCat', 'ticket_title': 'Find part IDs', 'ticket_id': 179}
    restart = restart_prompt(run_label(data))
    interrupt = _effective_wait_prompt({**data, 'waiting_kind': 'control_interrupt',
        'interrupt_context': {'question': 'Confirm the updated scope?'}}, default_prompt='', result='')
    question = _effective_wait_prompt({**data, 'waiting_kind': 'worker_needs_input', 'worker_question': 'Which supplier?'}, default_prompt='', result='')
    for text in (restart, interrupt, question):
        assert 'JackCat #179 Find part IDs' in text and 'Safe default if you do nothing' in text
    assert 'Continue re-runs' in restart
    assert 'Confirm the updated scope?' in interrupt
    assert 'Continue sends your answer' in interrupt
    assert 'Which supplier?' in question and 'Continue sends your answer' in question


def test_restart_runs_real_dispatch_and_router_to_completion(monkeypatch):
    from distr.core.db.workflow import AutoWorkflowStepResult

    _, rid, sid = seed('agent_instruction')
    execute = Mock(return_value={'output': 'RESTART_RECOVERY_OK', 'passed': True, 'skip_wait': True})
    monkeypatch.setattr(dispatcher.StepDispatcher, '_execute', execute)
    dispatcher._cleanup_orphaned_runs_on_startup()
    dispatcher._auto_resume_interrupted_runs(background=False)
    execute.assert_called_once()
    with get_session() as db:
        assert db.get(AutoWorkflowRun, rid).status == 'completed'
        rows = db.query(AutoWorkflowStepResult).filter_by(run_id=rid, step_id=sid).all()
        assert any('RESTART_RECOVERY_OK' in row.agent_response for row in rows)


def test_health_monitor_detects_loss_and_clears_on_heartbeat_without_retries():
    wid, rid, sid = seed()
    with get_session() as db:
        execution = ProjectExecutionSession(project_id=999, run_id=rid, step_id=sid, status='running',
                                             started_at=utc_now_naive() - timedelta(minutes=12))
        db.add(execution)
        db.flush()
        eid = execution.id
        db.add(ProjectExecutionEvent(session_id=eid, event_type='heartbeat', created_at=utc_now_naive() - timedelta(seconds=120)))
        # Activity does not impersonate a worker heartbeat.
        db.add(ProjectExecutionEvent(session_id=eid, event_type='tool_execution_start', message='Checking part IDs'))
        db.commit()
    check_worker_heartbeats()
    with get_session() as db:
        run = db.get(AutoWorkflowRun, rid)
        assert run.status == 'running'
        assert json.loads(run.run_data)['worker_health']['state'] == 'stalled'
    row = next(r for r in service.get_active_runs(workflow_id=wid) if r['id'] == rid)
    assert row['activity_state'] == 'stalled'
    assert 'Stalled' in row['status_summary'] and 'heartbeat stopped' in row['waiting_prompt']
    with get_session() as db:
        db.add(ProjectExecutionEvent(session_id=eid, event_type='heartbeat'))
        db.add(ProjectExecutionEvent(session_id=eid, event_type='file_changed', payload=json.dumps({'path': 'app.py'})))
        db.commit()
    check_worker_heartbeats()
    row = next(r for r in service.get_active_runs(workflow_id=wid) if r['id'] == rid)
    assert not row['stalled'] and '1 file changed' in row['status_summary']
    assert '12 minutes in' in row['status_summary']
    with get_session() as db:
        run = db.get(AutoWorkflowRun, rid)
        assert json.loads(run.run_data)['worker_health']['state'] == 'active'
        run.status = 'waiting'
        db.commit()
        assert not run_health(db, run, {})['stalled']


def test_run_file_changes_survive_moving_to_the_next_step():
    wid, rid, sid = seed()
    with get_session() as db:
        first = ProjectExecutionSession(project_id=999, run_id=rid, step_id=sid, status='completed')
        db.add(first); db.flush()
        db.add(ProjectExecutionEvent(session_id=first.id, event_type='item.completed',
            payload=json.dumps({'item': {'type': 'file_change', 'changes': [{'path': 'lookup.py'}]}})))
        step = AutoWorkflowStep(workflow_id=wid, name='Review', status='running')
        db.add(step); db.flush()
        db.get(AutoWorkflowRun, rid).current_step_id = step.id
        second = ProjectExecutionSession(project_id=999, run_id=rid, step_id=step.id, status='running')
        db.add(second); db.flush()
        db.add(ProjectExecutionEvent(session_id=second.id, event_type='heartbeat'))
        db.commit()
    row = next(item for item in service.get_active_runs(workflow_id=wid) if item['id'] == rid)
    assert '1 file changed' in row['status_summary'] and not row['stalled']


@pytest.mark.parametrize('tokens,cost,turns', [(101, .99, 1), (101, 1., 1), (1000, 0., 1), (1, 0., 80)])
def test_power_budget_exhaustion_never_gates(tokens, cost, turns):
    assert power_budget_is_exhausted({'max_turns': 80, 'turns_used': turns, 'max_tokens': 100,
                                      'tokens_used': tokens, 'estimated_cost_usd': cost}) is False


@pytest.mark.parametrize('incoming,outgoing,cached,expected', [(1000, 30, 900, 130), (1000, 0, 1000, 0), (1000, 30, 2000, 30)])
def test_cached_conversation_is_never_charged_again(incoming, outgoing, cached, expected):
    _, rid, sid = seed(power_budget={'max_turns': 80, 'max_tokens': 320000})
    StepExecutorMixin()._consume_power_budget_after_step(rid, {'id': sid},
        {'tokens_in': incoming, 'tokens_out': outgoing, 'usage': {'cacheRead': cached}, 'model_provider': 'codex'})
    with get_session() as db:
        budget = json.loads(db.get(AutoWorkflowRun, rid).run_data)['power_budget']
        assert budget['tokens_used'] == expected and budget['turns_used'] == 1


def test_auto_accept_enforces_own_project_spend_and_risk_limits(monkeypatch):
    from distr.core.incoming import proposals
    settings = {'incoming_proposals_auto_accept': False}
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: settings)
    monkeypatch.setattr(proposals, '_undismissed_proposals', lambda rows: rows)
    monkeypatch.setattr(proposals, '_accepted_proposal_keys', lambda **kwargs: set())
    monkeypatch.setattr('distr.core.incoming.events.publish_incoming_change', Mock())
    handoff = Mock(return_value=100)
    monkeypatch.setattr(proposals, 'create_proposal_thread', handoff)
    ok = {'id': 'local:gmail:ok', 'origin': 'local', 'project_id': 7, 'project_name': 'Jackcat',
          'title': 'Handle unknown part IDs without catalogue 500 errors'}
    rows = [ok,
            {**ok, 'id': 'local:gmail:spend', 'title': 'Pay the R1,500 hosting invoice'},
            {**ok, 'id': 'local:gmail:risk', 'title': 'Drop the old orders table in production'},
            {**ok, 'id': 'local:gmail:amount', 'estimated_cost_usd': 3.5},
            {**ok, 'id': 'local:gmail:other', 'project_name': 'Some Client Co'},
            {**ok, 'id': 'tensology:5', 'origin': 'tensology'},
            {'id': 'local:gmail:c', 'origin': 'local'}]
    assert proposals.auto_accept_proposals(rows) == []
    handoff.assert_not_called()
    settings['incoming_proposals_auto_accept'] = True
    assert proposals.auto_accept_proposals(rows) == [proposals._proposal_handoff_key(ok)]
    handoff.assert_called_once_with(ok, start_work=True)


def test_auto_accept_failure_keeps_proposal_eligible(monkeypatch):
    from distr.core.incoming import proposals
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: {'incoming_proposals_auto_accept': True})
    monkeypatch.setattr(proposals, '_undismissed_proposals', lambda rows: rows)
    monkeypatch.setattr(proposals, '_accepted_proposal_keys', lambda **kwargs: set())
    handoff = Mock(side_effect=RuntimeError('dispatch rejected'))
    monkeypatch.setattr(proposals, 'create_proposal_thread', handoff)
    assert proposals.auto_accept_proposals([{'id': 'x', 'origin': 'local', 'project_id': 7,
                                             'project_name': 'Snuza', 'title': 'Fix login copy'}]) == []
    handoff.assert_called_once()


def test_global_setting_http_persists_only_the_requested_switch():
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.db import Settings
    from distr.gui.web.routes.development.incoming import register_routes

    with get_session() as db:
        db.add(Settings(incoming_proposal_sound_enabled=False))
        db.commit()
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router)
    client = TestClient(app)
    url = '/workflows/studio/incoming/proposals/auto-accept'
    for enabled in (True, False):
        assert client.put(url, json={'enabled': enabled}).json() == {'auto_accept': enabled}
        with get_session() as db:
            settings = db.query(Settings).one()
            assert settings.incoming_proposals_auto_accept is enabled
            assert settings.incoming_proposal_sound_enabled is False
    assert client.put(url, json={'enabled': 'true'}).status_code == 422


def test_provider_swap_logs_real_failure_and_approver(caplog, monkeypatch):
    from distr.core.work_intake.execution_policy import apply_approved_provider_replacements_to_route
    _, rid, sid = seed()
    with get_session() as db:
        run = db.get(AutoWorkflowRun, rid)
        run.status = 'waiting'
        run.run_data = json.dumps({'waiting_kind': 'provider_preflight',
            'provider_preflight': {'message': 'Pi executable is unavailable', 'ready': False},
            'execution_route': {'backend': 'pi', 'model': 'auto', 'reason': 'inherited the thread route Pi'},
            'pending_route_approval': {'backend': 'codex', 'model': 'auto'},
            'step_routes': {str(sid): {'backend': 'pi', 'model': 'auto', 'reason': 'inherited Pi'}}})
        db.commit()
    monkeypatch.setattr(dispatcher, '_dispatch_workflow_step', Mock())
    with caplog.at_level('INFO'):
        service.apply_run_route_approval(rid, True, synchronous_redispatch=True, approved_by='operator via web')
    assert 'Pi executable is unavailable' in caplog.text and 'operator via web' in caplog.text
    with get_session() as db:
        data = json.loads(db.get(AutoWorkflowRun, rid).run_data)
        route = data['execution_route']
        assert route['reason'] == route['rationale']
        assert 'inherited' not in route['reason']
        replacement = data['approved_provider_replacements'][-1]
        assert replacement['unready_reason'] == 'Pi executable is unavailable'
        assert data['step_routes'][str(sid)]['reason'] == route['reason']
        carried = apply_approved_provider_replacements_to_route({'backend': 'pi', 'model': 'auto', 'reason': 'inherited Pi'}, [replacement])
        assert carried['reason'] == route['reason'] and carried['approved_by'] == 'operator via web'
