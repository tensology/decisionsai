"""Review the orchestrator's saved draft inside the Development ticket."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from distr.core.orchestrator_proactive import PROJECT_HIDDEN_FROM_AGENT, _workflow_draft
from distr.core.workflow.draft_launch import WORKFLOW_APPROVAL_SCOPE_CHANGED
from tests.core.test_development_harness import scoped_direct_ticket  # noqa: F401
from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path('/tmp/decisions-ticket-workflow-draft')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_generated_ticket_shows_plan_source_revisions_on_open(page, fixture_base, width):
    requests = []
    _install_api(page, requests)
    page.route('**/api/tickets/boards/70', lambda route: route.fulfill(json={
        'id': 70, 'name': 'Development', 'source': 'database',
        'lanes': [{'id': 701, 'name': 'Backlog', 'tickets': [{
            'id': 42, 'title': 'Repair checkout', 'linked_project_id': 7,
            'source_provider': 'plan', 'source_external_id': 'plan:1:task:checkout',
        }]}],
    }))
    response = {'status': 'changed', 'changes': [
        {'item_id': 7, 'title': 'Checkout requirements', 'built_revision': 1, 'current_revision': 3},
        {'item_id': 8, 'title': None, 'built_revision': 2, 'current_revision': None},
    ]}
    page.route('**/api/tickets/tickets/42', lambda route: route.fulfill(json={
        'id': 42, 'board_id': 70, 'plan_sources': response,
    }))
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/boards/decisions/70/kanban/?ticket_id=42')
    dialog = page.get_by_role('dialog')
    notice = dialog.get_by_label('Plan sources', exact=True)
    expect(notice.get_by_role('status')).to_have_text('Plan sources changed since this ticket was built.')
    expect(notice.get_by_role('listitem')).to_have_text([
        'Checkout requirements: revision 1 to 3', 'Source item #8 is unavailable (built from revision 2).',
    ])
    link = notice.get_by_role('link', name='Open Plan in a new tab')
    expect(link).to_have_attribute('href', '/development/boards/decisions/70/plan/')
    expect(dialog.get_by_role('button', name='Run with Codex')).to_be_enabled()
    EVIDENCE.mkdir(exist_ok=True)
    dialog.screenshot(path=EVIDENCE / f'{width}-plan-sources-changed.png', animations='disabled')
    for state, message in [
        ('current', 'This ticket uses the current saved Plan revisions.'),
        ('unavailable', 'The saved Plan revisions could not be verified.'),
    ]:
        dialog.get_by_role('button', name='Close ticket details').click()
        response.update(status=state, changes=[])
        page.get_by_role('button', name='Open Repair checkout', exact=True).press('Enter')
        expect(notice.get_by_role('status')).to_have_text(message)
        expect(link).to_be_hidden()
        dialog.screenshot(path=EVIDENCE / f'{width}-plan-sources-{state}.png', animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('draft_type', ['short', 'large', 'outcomes', 'assigned', 'builtin'])
def test_workflow_draft_review_stays_in_the_ticket(page, fixture_base, width, draft_type):
    page.set_default_timeout(5000)
    _install_api(page, [])
    steps = ['Reproduce the checkout error', 'Repair the checkout request', 'Verify the payment handoff']
    large = draft_type == 'large'
    if large:
        steps += [f'Check the confirmation message on storefront {number}' for number in range(1, 13)]
    description = (
        'Checkout currently drops the customer back to the basket after submitting the payment form.\n\n'
        'First, Reproduce the checkout error. Then, Repair the checkout request. Finally, Verify the payment handoff.'
    )
    if draft_type == 'outcomes':
        steps = [
            'The checkout must retain the basket after a declined payment',
            'Order history needs to show each declined payment attempt',
            'The support view must show the payment reference',
        ]
        description = '. '.join(steps) + '. The workflow must not contact customers.'
    draft = _workflow_draft(
        {'title': 'Repair checkout', 'description': description, 'project_id': 7, 'board_id': 70, 'ticket_id': 42},
        steps if large else None, complete=True,
    )
    if draft_type in {'assigned', 'builtin'}:
        draft = {**draft, 'workflow_id': 9, 'name': 'Checkout acceptance', 'assigned_by': 'board',
                 'reason': 'Assigned as the board default.', 'start_step_position': 1}
        draft['steps'][0]['detail'] = 'Record the checkout response before editing.'
    if draft_type == 'builtin':
        draft.update(assigned_by='builtin', reason='The request asks to map an existing project into the plan.')
    page.route('**/api/tickets/boards/70', lambda route: route.fulfill(json={
        'id': 70, 'name': 'Development', 'source': 'database',
        'lanes': [{'id': 701, 'name': 'Backlog', 'tickets': [{
            'id': 42, 'title': 'Repair checkout', 'description': description, 'linked_project_id': 7,
            'priority': 'high', 'complexity': 'complex', 'source_chat_id': 17,
        }]}],
    }))
    response = {'status': 'awaiting_review', 'project_name': 'Decisions delivery', 'candidate_id': 5, 'draft': draft}
    held = []
    approvals = []

    def fetch_draft(route):
        if response['status'] == 'hold':
            held.append(route)
        elif response['status'] == 'fail':
            route.fulfill(status=503, json={'detail': 'Temporary error'})
        else:
            route.fulfill(json=response)

    page.route('**/api/workflows/studio/boards/70/tickets/42/workflow-draft', fetch_draft)
    page.route('**/api/workflows/studio/boards/70/tickets/42/workflow-draft/approve',
               lambda route: approvals.append(route))
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/boards/decisions/70/kanban/?ticket_id=42')
    dialog = page.locator('#kanban-ticket-dialog')
    expect(dialog).to_be_visible()
    expect(dialog.locator('#kanban-ticket-complexity')).to_have_value('complex')
    panel = dialog.locator('#kanban-ticket-workflow-draft')
    EVIDENCE.mkdir(exist_ok=True)
    if draft_type != 'short':
        width = f'{width}-{draft_type}'
    dialog.screenshot(path=EVIDENCE / f'{width}-collapsed.png', animations='disabled')
    panel.get_by_text('Workflow review', exact=True).click()
    status = panel.get_by_role('status')
    expect(status).to_have_text('Awaiting review for Decisions delivery.')
    expect(panel.get_by_text(response['draft']['reason'], exact=True)).to_be_visible()
    rows = panel.get_by_role('listitem')
    if draft_type in {'assigned', 'builtin'}:
        expect(panel.get_by_text('Checkout acceptance', exact=True)).to_be_visible()
        expect(rows).to_have_count(3)
        expect(rows.first.get_by_text(draft['steps'][0]['detail'], exact=True)).to_be_visible()
        expect(rows.nth(1)).to_have_text(f'{steps[1]} (Starts here)')
        expect(panel.get_by_text('Record the check and result for each step.', exact=True)).to_have_count(0)
    else:
        expect(rows).to_have_text(steps)
    if large:
        assert not approvals
        for index in (0, len(steps) // 2, len(steps) - 1):
            row = rows.nth(index)
            row.scroll_into_view_if_needed()
            expect(row).to_be_in_viewport()
            dialog.screenshot(path=EVIDENCE / f'{width}-step-{index + 1}.png', animations='disabled')
    panel.get_by_role('button', name='Refresh draft').scroll_into_view_if_needed()
    assert panel.evaluate("""node => node.getBoundingClientRect().top >=
        document.getElementById('kanban-ticket-description').getBoundingClientRect().bottom""")
    dialog.screenshot(path=EVIDENCE / f'{width}-current.png', animations='disabled')

    with page.expect_request('**/workflow-draft/approve') as submitted:
        panel.get_by_role('button', name='Approve and launch').click()
    assert submitted.value.post_data_json == {
        'approved': True, 'candidate_id': 5, 'revision': draft['checklist_revision'],
    }
    expect(status).to_have_text('Launching approved workflow…')
    launching = panel.get_by_role('button', name='Launching…')
    expect(launching).to_be_disabled()
    expect(launching).to_be_in_viewport()
    dialog.screenshot(path=EVIDENCE / f'{width}-approving.png', animations='disabled')
    approvals[0].fulfill(status=409, json={'detail': 'This draft is out of date. Refresh it before approving.'})
    expect(status).to_have_text('This draft is out of date. Refresh it before approving.')
    expect(panel.get_by_role('listitem')).to_have_count(0)
    dialog.screenshot(path=EVIDENCE / f'{width}-approval-conflict.png', animations='disabled')

    response['status'] = 'stale'
    panel.get_by_role('button', name='Refresh draft').click()
    expect(status).to_contain_text('This draft is out of date.')
    expect(panel.get_by_role('listitem')).to_have_count(0)
    dialog.screenshot(path=EVIDENCE / f'{width}-stale.png', animations='disabled')

    response['status'] = 'fail'
    panel.get_by_role('button', name='Refresh draft').click()
    expect(status).to_have_text('Could not load the workflow draft. Try again.')
    dialog.screenshot(path=EVIDENCE / f'{width}-error.png', animations='disabled')
    response['status'] = 'missing'
    panel.get_by_role('button', name='Refresh draft').click()
    expect(status).to_have_text('There is no saved workflow draft for this ticket.')
    dialog.screenshot(path=EVIDENCE / f'{width}-missing.png', animations='disabled')
    if draft_type == 'assigned':
        response.update(status='blocked', error='The assigned workflow has no steps.')
        panel.get_by_role('button', name='Refresh draft').click()
        expect(status).to_have_text(response['error'])
        expect(panel.get_by_role('button', name='Approve and launch')).to_be_hidden()
        dialog.screenshot(path=EVIDENCE / f'{width}-blocked.png', animations='disabled')
        response['error'] = PROJECT_HIDDEN_FROM_AGENT
        panel.get_by_role('button', name='Refresh draft').click()
        expect(status).to_have_text(PROJECT_HIDDEN_FROM_AGENT)
        expect(panel.get_by_role('button', name='Approve and launch')).to_be_hidden()
        dialog.screenshot(path=EVIDENCE / f'{width}-hidden-project.png', animations='disabled')
    response['status'] = 'unavailable'
    panel.get_by_role('button', name='Refresh draft').click()
    expect(status).to_have_text('Link this ticket to an active project to review a workflow draft.')
    dialog.screenshot(path=EVIDENCE / f'{width}-unavailable.png', animations='disabled')

    response['status'] = 'hold'
    with page.expect_request('**/workflow-draft'):
        panel.get_by_role('button', name='Refresh draft').click()
    expect(status).to_have_text('Loading draft…')
    dialog.screenshot(path=EVIDENCE / f'{width}-loading.png', animations='disabled')
    dialog.get_by_role('button', name='Close ticket details').click()
    expect(dialog).to_be_hidden()
    with page.expect_response('**/workflow-draft') as delivered:
        held[0].fulfill(json={**response, 'status': 'awaiting_review'})
    delivered.value.finished()
    expect(panel.locator('#kanban-ticket-workflow-draft-status')).to_have_text('Loading draft…')
    response['status'] = 'awaiting_review'
    page.locator('[data-kanban-ticket="decisions:42"]').press('Enter')
    expect(dialog).to_be_visible()
    panel.get_by_text('Workflow review', exact=True).click()
    expect(status).to_have_text('Awaiting review for Decisions delivery.')
    with page.expect_request('**/workflow-draft/approve'):
        panel.get_by_role('button', name='Approve and launch').click()
    expect(status).to_have_text('Launching approved workflow…')
    receipt = {'status': 'launched', 'run_id': 19, 'run_status': 'running', 'phase': 'initializing', 'chat_id': 17}
    approvals[1].fulfill(json=receipt)
    expect(status).to_contain_text('Workflow run #19 · Starting')
    thread = panel.get_by_role('link', name='Open Development thread')
    expect(thread).to_have_attribute('href', '/development/threads/17/')
    expect(thread).to_be_in_viewport()
    dialog.screenshot(path=EVIDENCE / f'{width}-launched.png', animations='disabled')
    response.update(receipt)
    if draft_type == 'assigned':
        response.update(run_status='failed', warning='The workflow or a linked Action changed after approval. Scan this ticket again and review before launching.')
    panel.get_by_role('button', name='Refresh draft').click()
    if draft_type == 'assigned':
        expect(status).to_have_text(response['warning'])
        dialog.screenshot(path=EVIDENCE / f'{width}-definition-changed.png', animations='disabled')
        from distr.core.workflow.draft_launch import WORKFLOW_APPROVAL_FOLDER_CHANGED

        response['warning'] = WORKFLOW_APPROVAL_FOLDER_CHANGED
        panel.get_by_role('button', name='Refresh draft').click()
        expect(status).to_have_text(WORKFLOW_APPROVAL_FOLDER_CHANGED)
        dialog.screenshot(path=EVIDENCE / f'{width}-approval-folder-stopped.png', animations='disabled')
    expect(panel.get_by_role('link', name='Open Development thread')).to_have_attribute('href', '/development/threads/17/')
    expect(panel.get_by_role('button', name='Approve and launch')).to_be_hidden()
    if draft_type == 'short':
        response['status'] = 'unconfirmed'
        panel.get_by_role('button', name='Refresh draft').click()
        expect(status).to_have_text('A workflow was approved, but its run could not be confirmed.')
        expect(thread).to_be_hidden()
        expect(panel.get_by_role('button', name='Approve and launch')).to_be_hidden()
        dialog.screenshot(path=EVIDENCE / f'{width}-receipt-scope-unconfirmed.png', animations='disabled')
        response.update(receipt, run_status='failed', warning=WORKFLOW_APPROVAL_SCOPE_CHANGED)
        panel.get_by_role('button', name='Refresh draft').click()
        expect(status).to_have_text(WORKFLOW_APPROVAL_SCOPE_CHANGED)
        expect(thread).to_be_visible()
        dialog.screenshot(path=EVIDENCE / f'{width}-approval-scope-stopped.png', animations='disabled')
    assert len(approvals) == 2
    panel.get_by_role('link', name='Open Development thread').click()
    expect(page).to_have_url(f'{fixture_base}/development/threads/17/')
    expect(page.locator('#conversation')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_ticket_footer_checks_current_thread_before_navigation(page, fixture_base, width):
    requests = []
    _install_api(page, requests)
    page.route('**/api/tickets/boards/70', lambda route: route.fulfill(json={
        'id': 70, 'name': 'Development', 'source': 'database',
        'lanes': [{'id': 701, 'name': 'Backlog', 'tickets': [{
            'id': 42, 'title': 'Repair checkout', 'linked_project_id': 7, 'source_chat_id': 17,
        }]}],
    }))
    if width in (390, 1440):
        page.route('**/api/chats', lambda route: route.fulfill(json={'chats': []}))
    stale = True
    lookups = []

    def linked_thread(route):
        lookups.append(route.request.url)
        if stale:
            route.fulfill(status=409, json={'detail': "The linked Development thread is outside the ticket's current project."})
        else:
            route.fulfill(json={'chat_id': 17})

    page.route('**/api/tickets/boards/70/tickets/42/development-thread?*', linked_thread)
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/boards/decisions/70/kanban/?ticket_id=42')
    dialog = page.get_by_role('dialog')
    opener = dialog.get_by_role('button', name='Open Thread', exact=True)
    title = dialog.locator('#kanban-ticket-title')
    title.fill('An unsaved edit')
    opener.click()
    expect(dialog.get_by_role('alert')).to_contain_text("outside the ticket's current project")
    expect(title).to_have_value('An unsaved edit')
    expect(opener).to_be_focused()
    EVIDENCE.mkdir(exist_ok=True)
    dialog.screenshot(path=EVIDENCE / f'{width}-ticket-thread-conflict.png', animations='disabled')

    stale = False
    opener.click()
    expect(dialog).to_be_hidden()
    expect(page).to_have_url(f'{fixture_base}/development/threads/17/')
    assert len(lookups) == 2 and all(url.endswith('?expected_chat_id=17') for url in lookups)
    assert not any(path == '/api/tickets/thread-draft' for _, path in requests)
    page.screenshot(path=EVIDENCE / f'{width}-ticket-thread-opened.png', animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_local_ticket_draft_keeps_review_and_submission_in_assigned_project(page, fixture_base, width):
    requests = []
    _install_api(page, requests)
    page.route('**/api/tickets/boards/70', lambda route: route.fulfill(json={
        'id': 70, 'name': 'Decisions delivery', 'source': 'database', 'default_project_id': 7,
        'lanes': [{'id': 701, 'name': 'Backlog', 'tickets': [{
            'id': 42, 'title': 'Repair checkout', 'linked_project_id': 8,
        }]}],
    }))
    page.route('**/api/tickets/boards/70/tickets/42/development-thread',
               lambda route: route.fulfill(json={'chat_id': None}))
    preparations = []
    submissions = []
    conflict = "The ticket's project changed. Refresh the board and reopen the ticket."

    def prepare(route):
        preparations.append(route.request.post_data_json)
        if len(preparations) == 1:
            route.fulfill(status=409, json={'detail': conflict})
        else:
            route.fulfill(json={'project_id': 8, 'prompt': 'Repair checkout', 'attachments': [
                {'name': 'scope-evidence.txt', 'path': '/tmp/assigned-project/scope-evidence.txt', 'mime_type': 'text/plain'},
            ]})

    def submit(route):
        submissions.append(route.request.post_data_json)
        route.fulfill(status=409, json={'detail': conflict})

    page.route('**/api/tickets/thread-draft', prepare)
    page.route('**/api/workflows/studio/tasks', submit)
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/boards/decisions/70/kanban/?ticket_id=42')
    dialog = page.get_by_role('dialog')
    opener = dialog.get_by_role('button', name='Start Thread', exact=True)
    title = dialog.locator('#kanban-ticket-title')
    title.fill('Unsaved title')
    opener.click()
    expect(dialog.get_by_role('alert')).to_contain_text(conflict)
    expect(title).to_have_value('Unsaved title')
    EVIDENCE.mkdir(exist_ok=True)
    dialog.screenshot(path=EVIDENCE / f'{width}-ticket-draft-project-conflict.png', animations='disabled')

    opener.click()
    expect(dialog).to_be_hidden()
    prompt = page.locator('#task-prompt')
    expect(prompt).to_have_value('Repair checkout')
    expect(page.locator('#header-project')).to_contain_text('Empty project')
    attachment = page.locator('#composer-attachment-strip [data-tooltip$=": scope-evidence.txt"]')
    expect(attachment).to_be_visible()
    assert attachment.evaluate("""element => {
        const icon = element.getBoundingClientRect();
        const strip = element.closest('.composer-attachment-strip').getBoundingClientRect();
        return icon.left >= strip.left && icon.right <= strip.right;
    }""")
    if width <= 640:
        assert page.locator('.composer-toolbar').evaluate("""toolbar => {
            const rect = selector => toolbar.querySelector(selector).getBoundingClientRect();
            const strip = rect('.composer-attachment-strip');
            const attach = rect('#attach-button');
            const send = rect('#send-button');
            return strip.bottom <= attach.top && Math.abs(send.y - attach.y) < 1
                && send.right <= toolbar.getBoundingClientRect().right;
        }""")
    assert [draft['project_id'] for draft in preparations] == [8, 8]
    assert not submissions
    page.screenshot(path=EVIDENCE / f'{width}-ticket-draft-assigned-project.png', animations='disabled')

    prompt.fill('Keep this reviewed draft.')
    page.locator('#send-button').click()
    expect(page.locator('#studio-toast')).to_contain_text(conflict)
    expect(prompt).to_have_value('Keep this reviewed draft.')
    assert len(submissions) == 1
    assert submissions[0]['project_id'] == 8
    assert submissions[0]['ticket_id'] == 42
    assert submissions[0]['board_key'] == 'decisions:70'
    assert submissions[0]['attachments'][0]['name'] == 'scope-evidence.txt'
    page.screenshot(path=EVIDENCE / f'{width}-ticket-submit-project-conflict.png', animations='disabled')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_moved_ticket_blocks_direct_send_and_preserves_draft(page, fixture_base, scoped_direct_ticket, monkeypatch, width):
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.db import Chat
    from distr.gui.web.routes.development.threads import register_routes

    scope = scoped_direct_ticket
    scope.move('board')
    monkeypatch.setattr('distr.core.db.get_session', scope.session)
    worker_calls = []
    monkeypatch.setattr('distr.core.turn_runtime.execute_turn', lambda *a, **kw: worker_calls.append(a))
    router = APIRouter()
    register_routes(router, None)
    app = FastAPI()
    app.include_router(router, prefix='/api')
    api = TestClient(app)
    _install_api(page, [], current_board_linked=True, direct_execution=True, run_status='completed', terminal_session=False)
    calls = []

    def send(route):
        response = api.post(f'/api/workflows/studio/tasks/{scope.chat_id}/messages', json=route.request.post_data_json)
        calls.append((response.status_code, response.json()))
        route.fulfill(status=response.status_code, json=response.json())

    page.route('**/api/workflows/studio/tasks/17/messages', send)
    page.set_viewport_size({'width': width, 'height': 850})
    page.goto(f'{fixture_base}/development/threads/17/')
    prompt = page.locator('#task-prompt')
    prompt.fill('Preserve this reviewed instruction.')
    page.get_by_role('button', name='Send prompt').click()
    expect(page.locator('#studio-toast')).to_contain_text('changed before work started')
    expect(prompt).to_have_value('Preserve this reviewed instruction.')
    assert len(calls) == 1 and calls[0][0] == 409
    assert not worker_calls
    with scope.session() as db:
        assert db.query(Chat).filter_by(parent_id=scope.chat_id).count() == 1
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=EVIDENCE / f'{width}-direct-ticket-scope-changed.png', animations='disabled')
