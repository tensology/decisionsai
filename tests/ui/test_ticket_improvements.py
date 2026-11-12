"""Real templates and read-only backend projections, isolated from live runs."""
from pathlib import Path
from urllib.parse import urlparse
import json

import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import _install_api, TEST_BASE_URL

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path('/tmp/decisions-ticket-improvements-qa')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_global_switch_persists_and_failed_save_restores_it(page, width):
    _install_api(page, [])
    enabled = False
    fail = False
    writes = []

    def proposals(route):
        nonlocal enabled
        if urlparse(route.request.url).path.endswith('/auto-accept'):
            writes.append(route.request.post_data_json)
            if fail:
                route.fulfill(status=500, json={'error': 'Save unavailable'})
                return
            enabled = route.request.post_data_json['enabled']
        route.fulfill(json={'proposals': [], 'counts': {}, 'auto_accept': enabled})
    page.route('**/api/workflows/studio/incoming/proposals**', proposals)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(TEST_BASE_URL + '/development/incoming/')
    switch = page.get_by_role('checkbox', name='Auto-accept proposals (all projects)')
    expect(switch).not_to_be_checked()
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=str(EVIDENCE / f'auto-accept-off-{width}.png'))
    switch.check()
    expect(switch).to_be_checked()
    expect(switch).to_be_enabled()
    page.reload()
    expect(switch).to_be_checked()
    page.screenshot(path=str(EVIDENCE / f'auto-accept-on-{width}.png'))
    fail = True
    switch.uncheck()
    expect(switch).to_be_checked()
    expect(switch).to_be_enabled()
    assert writes == [{'enabled': True}, {'enabled': False}]
    page.screenshot(path=str(EVIDENCE / f'auto-accept-failed-{width}.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width', [390, 1280])
@pytest.mark.parametrize('surface', ['development', 'legacy'])
def test_board_popover_shows_run_summary(page, width, surface):
    _install_api(page, [])
    summary = 'JackCat #179 Find part IDs: Stalled: worker heartbeat missing for 125 seconds, 12 minutes in, 1 file changed.'
    board = {'id': 70, 'name': 'Decisions delivery', 'source': 'database', 'default_project_id': 7,
             'lanes': [{'id': 701, 'name': 'In progress', 'tickets': [{'id': 42, 'title': 'Find part IDs', 'workflow_status': 'running', 'linked_project_id': 7}]}]}
    page.route('**/api/tickets/boards/70', lambda route: route.fulfill(json=board))
    page.route('**/api/tickets/tickets/42/active-run', lambda route: route.fulfill(json={
        'active': True, 'id': 99, 'workflow_id': 44, 'workflow_name': 'Development execution',
        'status': 'running', 'stalled': True, 'status_summary': summary, 'open_url': '/development/threads/17/'}))
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(TEST_BASE_URL + ('/legacy-kanban/' if surface == 'legacy' else '/development/'))
    if surface == 'legacy':
        page.locator('#kb-db-boards [data-board-id="70"] .kb-board-item-main').click()
        page.locator('.kb-wf-status-badge').click()
        popover = page.locator('.kb-run-popover')
    else:
        mobile = page.locator('#sidebar-open').is_visible()
        if mobile: page.locator('#sidebar-open').click()
        page.locator('[data-board-select="decisions:70"]').click()
        if mobile: page.wait_for_function("document.getElementById('studio-sidebar').getBoundingClientRect().right <= 1")
        page.locator('[data-ticket-run-status]').click()
        popover = page.locator('.development-run-popover')
    expect(popover).to_contain_text(summary)
    page.screenshot(path=str(EVIDENCE / f'{surface}-board-summary-{width}.png'))
    page.keyboard.press('Escape')


@pytest.mark.parametrize('width', [390, 1280])
def test_legacy_monitor_displays_summary(page, width):
    _install_api(page, [])
    for endpoint, payload in {
        '/tickets/linkable': [],
        '/tickets/boards/70/workflow-view': {'id': 70, 'name': 'Decisions delivery', 'lanes': []},
        '/workflows/presets': {'presets': []},
        '/tickets/workflows/44/tickets': {'tickets': []},
        '/projects/7/terminal/keepalive': {'success': True},
        '/tickets/workflows/44/execution-sessions': {'sessions': []},
        '/workflows/44/orchestrator-events': [],
    }.items():
        page.route('**/api' + endpoint + '*', lambda route, request, value=payload: route.fulfill(json=value))
    summary = 'JackCat #179 Find part IDs: doing Checking part IDs, 12 minutes in, 1 file changed.'
    page.route('**/api/workflows/active-runs*', lambda route: route.fulfill(json=[{
        'id': 99, 'workflow_id': 44, 'workflow_name': 'Development execution', 'status': 'running',
        'status_summary': summary, 'current_step_name': 'Heavy session', 'elapsed_seconds': 720,
        'project_name': 'JackCat', 'ticket_title': 'Find part IDs', 'execution_route': {}}]))
    page.route('**/api/workflows/44/active-run', lambda route: route.fulfill(json={
        'id': 99, 'workflow_id': 44, 'status': 'running', 'current_step_id': 1,
        'status_summary': summary, 'current_step_name': 'Heavy session', 'elapsed_seconds': 720,
        'ticket_title': 'Find part IDs', 'project_name': 'JackCat', 'execution_route': {}}))
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(TEST_BASE_URL + '/workflows/?mode=blueprint')
    if width < 800:
        page.locator('#wf-board-pane-toggle-header').click()
    page.get_by_text('Development execution', exact=True).first.click()
    expect(page.locator('.wf-loop-decision-context')).to_contain_text(summary)
    page.screenshot(path=str(EVIDENCE / f'legacy-activity-{width}.png'))
    page.locator('[data-tab="runs"]').click(timeout=3000)
    expect(page.locator('#wf-active-runs-list')).to_contain_text(summary)
    page.locator('#wf-active-runs-list').scroll_into_view_if_needed()
    assert page.locator('#wf-active-runs-list').evaluate('el => el.getBoundingClientRect().right <= innerWidth')
    page.screenshot(path=str(EVIDENCE / f'legacy-monitor-{width}.png'))


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_workflow_timeline_explains_why_the_next_ticket_is_held(page, monkeypatch, tmp_path, width):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core import orchestrator
    from distr.core.workflow.dispatcher import _record_ticket_queue_hold
    from distr.gui.web.routes.development.workflow_execution import register_routes
    from tests.core.test_workflow_crash_guard import _factory, _real_session

    factory = _factory()
    monkeypatch.setattr(orchestrator, 'get_session', lambda: _real_session(factory))
    monkeypatch.setattr(orchestrator, 'ensure_orchestrator_tables', lambda: None)
    monkeypatch.setattr(orchestrator, 'is_orchestrator_enabled', lambda: True)
    monkeypatch.setattr('distr.core.initiative.outcome_flow._data_dir', lambda: tmp_path)
    reason = 'The queued ticket is no longer assigned to its selected project.'
    _record_ticket_queue_hold(44, 99, 42, reason, board_id=70, project_id=7)
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix='/api')
    client = TestClient(app)
    path = '/api/workflows/44/orchestrator-events'
    result = client.get(path, params={'run_id': 99, 'ticket_id': 42, 'board_id': 70})
    assert result.status_code == 200
    assert len(result.json()) == 1
    assert client.get('/api/workflows/45/orchestrator-events').json() == []
    assert client.get(path, params={'board_id': 71}).json() == []

    _install_api(page, [])
    history = [{'id': 99, 'workflow_id': 44, 'status': 'completed', 'ticket_id': 41, 'board_id': 70}]
    page.route('**/api/workflows/44/runs?*', lambda route: route.fulfill(json=history))
    page.route('**/api/workflows/44', lambda route: route.fulfill(json={
        'id': 44, 'name': 'Development execution', 'steps': [], 'runs': history,
    }))
    for endpoint, payload in {
        '/tickets/linkable': [],
        '/tickets/boards/70/workflow-view': {'id': 70, 'name': 'Decisions delivery', 'lanes': []},
        '/workflows/presets': {'presets': []},
        '/tickets/workflows/44/tickets': {'tickets': []},
        '/projects/7/terminal/keepalive': {'success': True},
        '/tickets/workflows/44/execution-sessions': {'sessions': []},
    }.items():
        page.route('**/api' + endpoint + '*', lambda route, request, value=payload: route.fulfill(json=value))

    def load_events(route):
        url = urlparse(route.request.url)
        response = client.get(f'{url.path}?{url.query}')
        route.fulfill(status=response.status_code, json=response.json())

    page.route('**' + path + '*', load_events)
    page.set_viewport_size({'width': width, 'height': 900})
    page.set_default_timeout(8000)
    page.goto(TEST_BASE_URL + '/workflows/?mode=blueprint')
    if width < 800:
        page.locator('#wf-board-pane-toggle-header').click()
    page.get_by_text('Development execution', exact=True).first.click()
    page.locator('[data-tab="runs"]').click()
    page.locator('.wf-runs-subtab[data-runs-tab="timeline"]').click()
    timeline = page.locator('#wf-orchestrator-events-list')
    explanation = timeline.get_by_text(reason, exact=True)
    expect(explanation).to_be_visible()
    expect(timeline.get_by_text('Held', exact=True)).to_be_visible()
    expect(timeline.get_by_text('ticket #42 · run #99 · project #7', exact=True)).to_be_visible()
    expect(timeline.locator('time')).to_be_visible()
    assert timeline.locator('time').evaluate("el => parseFloat(getComputedStyle(el.parentElement).columnGap) >= 8")
    explanation.scroll_into_view_if_needed()
    assert explanation.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=EVIDENCE / f'workflow-queue-held-{width}.png', animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_active_run_summary_updates_stall_and_recovery_list(page, monkeypatch, width):
    from datetime import timedelta
    from distr.core.db.workflow import AutoWorkflow, AutoWorkflowStep, AutoWorkflowRun
    from distr.core.db.kanban import ProjectExecutionEvent, ProjectExecutionSession
    from distr.core.db.time import utc_now_naive
    from distr.core.workflow.service import get_active_runs
    from tests.core.test_workflow_crash_guard import _factory, _real_session

    factory = _factory()
    session = lambda: _real_session(factory)
    monkeypatch.setattr('distr.core.workflow.service.get_session', session)
    with session() as db:
        workflow = AutoWorkflow(name='Development execution')
        db.add(workflow); db.flush()
        step = AutoWorkflowStep(workflow_id=workflow.id, name='Heavy session', status='running')
        db.add(step); db.flush()
        run = AutoWorkflowRun(workflow_id=workflow.id, current_step_id=step.id, status='running',
            started_at=utc_now_naive() - timedelta(minutes=12),
            run_data=json.dumps({'project_name': 'JackCat', 'ticket_title': 'Find part IDs', 'ticket_id': 179}))
        db.add(run); db.flush()
        execution = ProjectExecutionSession(project_id=999, run_id=run.id, step_id=step.id, status='running')
        db.add(execution); db.flush()
        db.add(ProjectExecutionEvent(session_id=execution.id, event_type='heartbeat'))
        db.add(ProjectExecutionEvent(session_id=execution.id, event_type='tool_execution_start', message='Checking unknown part IDs'))
        db.commit()
        rid, eid, wid = run.id, execution.id, workflow.id
    _install_api(page, [])
    page.route('**/api/workflows/active-runs*', lambda route: route.fulfill(json=get_active_runs(workflow_id=wid)))
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(TEST_BASE_URL + '/development/')
    mobile = page.locator('#sidebar-open').is_visible()
    if mobile:
        page.locator('#sidebar-open').click()
    page.locator('#sidebar-workflows-toggle').click()
    if mobile:
        page.wait_for_function("document.getElementById('studio-sidebar').getBoundingClientRect().right <= 1")
    row = page.locator(f'[data-active-execution="{rid}"]')
    expect(row).to_contain_text('doing Checking unknown part IDs')
    expect(row).to_contain_text('12 minutes in, no files changed yet')
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=str(EVIDENCE / f'active-{width}.png'))
    with session() as db:
        db.add(ProjectExecutionEvent(session_id=eid, event_type='item.completed', message='Updated part lookup',
            payload=json.dumps({'item': {'type': 'file_change', 'status': 'completed', 'changes': [{'path': 'lookup.py'}]}})))
        db.commit()
    expect(row).to_contain_text('1 file changed', timeout=12000)
    page.screenshot(path=str(EVIDENCE / f'file-changed-{width}.png'))
    with session() as db:
        heartbeat = db.query(ProjectExecutionEvent).filter_by(session_id=eid, event_type='heartbeat').one()
        heartbeat.created_at = utc_now_naive() - timedelta(seconds=125)
        db.commit()
    expect(row).to_contain_text('Stalled', timeout=12000)
    expect(row).to_contain_text('Safe default if you do nothing')
    page.screenshot(path=str(EVIDENCE / f'stalled-{width}.png'))
    with session() as db:
        run = db.get(AutoWorkflowRun, rid)
        data = json.loads(run.run_data)
        data.update({'waiting_kind': 'restart_recovery', 'restart_recovery': {'blocked_reason': 'Automatic recovery retry limit reached'},
                     'waiting_prompt': 'The worker was interrupted by restart. Continue retries the saved step. Safe default if you do nothing: this run stays paused.'})
        run.status = 'waiting'; run.run_data = json.dumps(data)
        db.commit()
    expect(row).to_contain_text('paused after restart', timeout=12000)
    expect(row).to_contain_text('Automatic recovery retry limit reached')
    page.screenshot(path=str(EVIDENCE / f'recovery-blocked-{width}.png'))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
