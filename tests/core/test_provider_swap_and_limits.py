"""Deliberate provider swaps, auto-accept limits/trigger, and one-interval stall checks.

All records live in pytest's isolated database (see test_ticket_improvements)."""
import inspect
import json
from datetime import timedelta
from unittest.mock import MagicMock, Mock

import pytest

from distr.core.db.time import utc_now_naive
from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
from distr.core.workflow import run_prompts as rp
from distr.core.workflow.run_health import (ACTIVITY_TIMEOUT_SECONDS, HEARTBEAT_CHECK_SECONDS,
                                            HEARTBEAT_TIMEOUT_SECONDS, run_health)
from tests.core.test_ticket_improvements import isolated_database, seed  # noqa: F401  (autouse fixture)
import tests.core.test_ticket_improvements as _ti


def get_session():
    """The isolated session the autouse fixture patched into test_ticket_improvements."""
    return _ti.get_session()


# ------------------------------------------------------------- approver / reason

@pytest.mark.parametrize('value,expected', [
    ('', ''), (None, ''), ('operator via route approval', ''), ('system fallback', ''),
    ('Unknown Approver', ''), ('legacy recorded approval', ''),
    ('telegram user 42 via telegram_callback', 'telegram user 42 via telegram_callback'),
])
def test_real_approver_rejects_placeholders(value, expected):
    assert rp.real_approver(value) == expected


def test_interaction_approver_uses_resolver_identity():
    from distr.core.workflow.interactions import interaction_approver
    assert interaction_approver('telegram_callback', '42') == 'telegram user 42 via telegram_callback'
    assert interaction_approver('web', 'paul') == 'paul via web'
    assert interaction_approver('telegram_text', '') == ''


def test_web_approver_names_local_user():
    assert rp.web_approver('Paul', '127.0.0.1') == 'Paul via web UI'
    assert rp.web_approver('', '127.0.0.1').startswith('web UI user ')


def test_legacy_replacement_without_approver_is_not_applied():
    from distr.core.work_intake.execution_policy import apply_approved_provider_replacements_to_route
    route = {'backend': 'pi', 'model': 'deepseek/deepseek-v4.1-flash', 'model_provider': 'openrouter'}
    legacy = [{'from_backend': 'pi', 'from_model': 'deepseek/deepseek-v4.1-flash',
               'to_backend': 'codex', 'to_model': 'auto'}]
    assert apply_approved_provider_replacements_to_route(route, legacy) == route
    no_reason = [{**legacy[0], 'approved_by': 'telegram user 42 via telegram_callback'}]
    assert apply_approved_provider_replacements_to_route(route, no_reason) == route


def _waiting_preflight_run(**extra):
    from distr.core.db.workflow import AutoWorkflowRun, AutoWorkflowStep
    _, rid, sid = seed()
    with get_session() as db:
        run = db.get(AutoWorkflowRun, rid)
        run.status = 'waiting'
        db.get(AutoWorkflowStep, sid).status = 'waiting'
        data = json.loads(run.run_data)
        data.update({'waiting_kind': 'provider_preflight',
                     'execution_route': {'backend': 'pi', 'model': 'deepseek/deepseek-v4.1-flash',
                                         'model_provider': 'openrouter',
                                         'rationale': 'Inherited the thread composer route'},
                     'pending_route_approval': {'backend': 'codex', 'model': 'auto',
                                                'rationale': 'Use this alternative route instead.'},
                     **extra})
        run.run_data = json.dumps(data)
        db.commit()
    return rid


@pytest.fixture
def no_side_effects(monkeypatch):
    from distr.core.workflow import service
    monkeypatch.setattr(service, 'get_session', get_session)
    monkeypatch.setattr(service, 'increment_workflow_updated', MagicMock())
    monkeypatch.setattr('distr.core.workflow.dispatcher._dispatch_workflow_step', Mock())
    events = Mock()
    monkeypatch.setattr('distr.core.orchestration_events.emit_orchestration_event', events)
    return events


@pytest.mark.parametrize('approver', ['', 'operator via route approval', 'system fallback'])
def test_swap_without_real_approver_is_refused_and_changes_nothing(no_side_effects, approver):
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.service import apply_run_route_approval
    rid = _waiting_preflight_run(provider_preflight={'ready': False, 'message': 'OpenRouter has $0.03 available'})
    result = apply_run_route_approval(rid, approved=True, approved_by=approver)
    assert result['refused'] is True and result['status_code'] == 403 and 'approver' in result['error']
    with get_session() as db:
        run = db.get(AutoWorkflowRun, rid)
        data = json.loads(run.run_data)
        assert run.status == 'waiting' and data['execution_route']['backend'] == 'pi'
        assert data['pending_route_approval']['backend'] == 'codex'
    no_side_effects.assert_not_called()


def test_swap_without_unready_reason_is_refused(no_side_effects):
    from distr.core.workflow.service import apply_run_route_approval
    rid = _waiting_preflight_run()
    result = apply_run_route_approval(rid, approved=True, approved_by='telegram user 42 via telegram_callback')
    assert result['refused'] is True and 'reason' in result['error']


def test_approved_swap_logs_reason_and_approver_in_record_and_timeline(no_side_effects):
    from distr.core.db.workflow import AutoWorkflowRun
    from distr.core.workflow.service import apply_run_route_approval
    why = 'OpenRouter has $0.03 available; this high route requires at least a $0.50 safety buffer.'
    rid = _waiting_preflight_run(provider_preflight={'ready': False, 'message': why})
    result = apply_run_route_approval(rid, approved=True, approved_by='telegram user 42 via telegram_callback')
    assert result['success'] is True
    with get_session() as db:
        data = json.loads(db.get(AutoWorkflowRun, rid).run_data)
    route = data['execution_route']
    assert route['backend'] == 'codex' and route['approved_by'] == 'telegram user 42 via telegram_callback'
    assert why in route['reason'] and 'Inherited' not in route['reason']
    swap = data['approved_provider_replacements'][-1]
    assert swap['unready_reason'] == why and swap['approved_by'] == 'telegram user 42 via telegram_callback'
    kwargs = no_side_effects.call_args.kwargs
    assert kwargs['event_type'] == 'route_approval_granted' and kwargs['run_id'] == rid
    assert 'telegram user 42' in kwargs['summary'] and kwargs['payload']['unready_reason'] == why


def test_model_selection_without_approver_is_refused_before_probe(monkeypatch, no_side_effects):
    from distr.core.workflow.service import apply_run_provider_model_selection
    probe = Mock()
    monkeypatch.setattr('distr.core.project_cli_backends.provider_preflight.probe_openrouter_model_readiness', probe)
    result = apply_run_provider_model_selection(1, 0)
    assert result['refused'] is True
    probe.assert_not_called()


def test_runtime_provider_fallback_is_disabled():
    from distr.core.workflow.step_executor import StepExecutorMixin
    assert StepExecutorMixin._runtime_provider_fallback_route({}, {}) == {}
    assert StepExecutorMixin._ready_route_or_fallback({}, project=None, board=None) == {}


# ------------------------------------------------------------- auto-accept trigger

def test_listing_incoming_never_auto_accepts():
    from distr.core.incoming import proposals
    assert 'auto_accept_proposals' not in inspect.getsource(proposals.list_incoming_proposals)


def test_auto_accept_cycle_only_runs_when_enabled(monkeypatch):
    from distr.core.incoming import proposals
    settings = {'incoming_proposals_auto_accept': False}
    monkeypatch.setattr('distr.core.settings.load_settings_from_db', lambda: settings)
    listing = Mock(return_value={'proposals': [{'id': 'a'}]})
    accept = Mock(return_value=['a'])
    monkeypatch.setattr(proposals, 'list_incoming_proposals', listing)
    monkeypatch.setattr(proposals, 'auto_accept_proposals', accept)
    assert proposals.run_auto_accept_cycle() == []
    listing.assert_not_called()
    settings['incoming_proposals_auto_accept'] = True
    assert proposals.run_auto_accept_cycle() == ['a']
    accept.assert_called_once_with([{'id': 'a'}])


def test_auto_accept_default_off_with_own_projects_allowlist():
    import ast
    import pathlib
    import distr.core.incoming.proposals as proposals
    tree = ast.parse(pathlib.Path('distr/core/settings.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'DEFAULT_SETTINGS')
    DEFAULT_SETTINGS = {ast.literal_eval(k): ast.literal_eval(v) for k, v in zip(node.value.keys, node.value.values)
                        if isinstance(k, ast.Constant) and str(k.value).startswith('incoming_proposals_auto_accept')}
    assert set(DEFAULT_SETTINGS['incoming_proposals_auto_accept_projects']) == set(proposals.AUTO_ACCEPT_DEFAULT_PROJECTS)
    assert DEFAULT_SETTINGS['incoming_proposals_auto_accept'] is False
    assert 'JackCat' in DEFAULT_SETTINGS['incoming_proposals_auto_accept_projects']


@pytest.mark.parametrize('extra,needle', [
    ({'origin': 'tensology'}, 'not a local'),
    ({'project_name': 'Acme Client'}, 'allowlist'),
    ({'title': 'Upgrade to the paid service tier'}, 'spend'),
    ({'title': 'Refund $20 to user'}, 'spend'),
    ({'description': 'costs about 300 ZAR'}, 'spend'),
    ({'title': 'Rotate the API keys'}, 'risk'),
    ({'title': 'Force push the rebase'}, 'risk'),
    ({'title': 'Email the client the report'}, 'risk'),
    ({'title': 'Run the migration on prod'}, 'risk'),
    ({'risk_level': 'high'}, 'risk'),
])
def test_auto_accept_skip_reasons(extra, needle):
    from distr.core.incoming.proposals import auto_accept_skip_reason
    base = {'origin': 'local', 'project_id': 3, 'project_name': 'Planmycharter', 'title': 'Tidy the search page'}
    assert auto_accept_skip_reason(base, {}) == ''
    assert needle in auto_accept_skip_reason({**base, **extra}, {})


# ------------------------------------------------------------- stall check

def _session(rid, sid, *, heartbeat_age=None, activity_age=None, started_age=1200):
    with get_session() as db:
        execution = ProjectExecutionSession(project_id=999, run_id=rid, step_id=sid, status='running',
                                            started_at=utc_now_naive() - timedelta(seconds=started_age))
        db.add(execution)
        db.flush()
        if heartbeat_age is not None:
            db.add(ProjectExecutionEvent(session_id=execution.id, event_type='heartbeat',
                                         created_at=utc_now_naive() - timedelta(seconds=heartbeat_age)))
        if activity_age is not None:
            db.add(ProjectExecutionEvent(session_id=execution.id, event_type='tool_execution_start',
                                         message='Running tests',
                                         created_at=utc_now_naive() - timedelta(seconds=activity_age)))
        db.commit()


def _health(rid, data=None):
    from distr.core.db.workflow import AutoWorkflowRun
    with get_session() as db:
        run = db.get(AutoWorkflowRun, rid)
        return run_health(db, run, data if data is not None else json.loads(run.run_data))


def test_one_missed_interval_flags_heartbeat_backend():
    assert HEARTBEAT_TIMEOUT_SECONDS == HEARTBEAT_CHECK_SECONDS
    _, rid, sid = seed()
    _session(rid, sid, heartbeat_age=HEARTBEAT_CHECK_SECONDS + 5)
    health = _health(rid)
    assert health['stalled'] and health['worker_health']['basis'] == 'heartbeat'


def test_recent_heartbeat_is_not_stalled():
    _, rid, sid = seed()
    _session(rid, sid, heartbeat_age=5)
    assert not _health(rid)['stalled']


def test_backend_without_heartbeats_uses_last_activity():
    _, rid, sid = seed()
    _session(rid, sid, activity_age=120)  # quiet but well inside the activity window
    health = _health(rid)
    assert not health['stalled'] and health['worker_health']['basis'] == 'activity'
    _, rid2, sid2 = seed()
    _session(rid2, sid2, activity_age=ACTIVITY_TIMEOUT_SECONDS + 30)
    health = _health(rid2)
    assert health['stalled'] and 'no worker activity' in health['status_summary']


@pytest.mark.parametrize('flag', [{'waiting_kind': 'provider_preflight'}, {'pending_route_approval': {'backend': 'codex'}},
                                  {'power_budget_pending': True}, {'human_intervention_state': 'needs_human_input'}])
def test_runs_waiting_on_humans_are_never_stalled(flag):
    _, rid, sid = seed()
    _session(rid, sid, heartbeat_age=600)
    assert not _health(rid, {'project_name': 'JackCat', **flag})['stalled']
