"""Review the orchestrator's saved draft inside the Development ticket."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path('/tmp/decisions-ticket-workflow-draft')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_workflow_draft_review_stays_in_the_ticket(page, fixture_base, width):
    page.set_default_timeout(5000)
    _install_api(page, [])
    steps = ['Reproduce the checkout error', 'Repair the checkout request', 'Verify the payment handoff']
    description = '## Implementation steps\n' + '\n'.join(
        f'{index}. {step}' for index, step in enumerate(steps, 1)
    )
    page.route('**/api/tickets/boards/70', lambda route: route.fulfill(json={
        'id': 70, 'name': 'Development', 'source': 'database',
        'lanes': [{'id': 701, 'name': 'Backlog', 'tickets': [{
            'id': 42, 'title': 'Repair checkout', 'description': description, 'linked_project_id': 7,
            'priority': 'high', 'complexity': 'complex', 'source_chat_id': 17,
        }]}],
    }))
    response = {'status': 'awaiting_review', 'project_name': 'Decisions delivery', 'candidate_id': 5,
                'draft': {'reason': 'The ticket description lists 3 distinct implementation steps.',
                          'checklist_revision': 'a' * 64,
                          'steps': [{'instruction': step} for step in steps], 'omitted_steps': 0}}
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
    dialog.screenshot(path=EVIDENCE / f'{width}-collapsed.png', animations='disabled')
    panel.get_by_text('Workflow draft', exact=True).click()
    status = panel.get_by_role('status')
    expect(status).to_have_text('Awaiting review for Decisions delivery.')
    expect(panel.get_by_text(response['draft']['reason'], exact=True)).to_be_visible()
    expect(panel.get_by_role('listitem')).to_have_text(steps)
    panel.get_by_role('button', name='Refresh draft').scroll_into_view_if_needed()
    assert panel.evaluate("""node => node.getBoundingClientRect().top >=
        document.getElementById('kanban-ticket-description').getBoundingClientRect().bottom""")
    dialog.screenshot(path=EVIDENCE / f'{width}-current.png', animations='disabled')

    with page.expect_request('**/workflow-draft/approve') as submitted:
        panel.get_by_role('button', name='Approve and launch').click()
    assert submitted.value.post_data_json == {'approved': True, 'candidate_id': 5, 'revision': 'a' * 64}
    expect(status).to_have_text('Launching approved workflow…')
    expect(panel.get_by_role('button', name='Approve and launch')).to_be_hidden()
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
    panel.get_by_text('Workflow draft', exact=True).click()
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
    panel.get_by_role('button', name='Refresh draft').click()
    expect(panel.get_by_role('link', name='Open Development thread')).to_have_attribute('href', '/development/threads/17/')
    expect(panel.get_by_role('button', name='Approve and launch')).to_be_hidden()
    assert len(approvals) == 2
    panel.get_by_role('link', name='Open Development thread').click()
    expect(page).to_have_url(f'{fixture_base}/development/threads/17/')
    expect(page.locator('#conversation')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
