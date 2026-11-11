"""Open the exact proposal review link from the Development workspace."""
from pathlib import Path
from urllib.parse import urlencode

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path('/tmp/decisions-proposal-review-link')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_exact_proposal_link_reopens_the_review_or_its_thread(page, fixture_base, width):
    page.set_default_timeout(5000)
    _install_api(page, [])
    page.add_init_script("Object.defineProperty(navigator, 'clipboard', { value: { writeText: async value => { window.copiedLink = value; } } });")
    key = 'a' * 64
    proposal = {'id': 'local:gmail:fixture-ñ', 'review_key': key, 'title': 'Review the original request',
                'project_id': 7, 'project_name': 'Decisions delivery', 'source': 'gmail',
                'message_ids': ['message-a'], 'description': 'Inspect the original project request.',
                'attachments': [{'id': 'file-a', 'name': 'Original scope.txt', 'mime_type': 'text/plain'}],
                'tasks': ['Verify the original scope']}
    newer = {**proposal, 'review_key': 'b' * 64, 'title': 'A newer request', 'message_ids': ['message-b']}
    state = {'status': 'proposal', 'proposal': proposal}
    held = []
    posts = []
    prepared = []
    file_state = {'unavailable': False}
    page.route('**/api/workflows/studio/incoming/proposals?*',
               lambda route: route.fulfill(json={'proposals': [newer]}))
    page.route('**/api/workflows/studio/incoming/proposals/files/ensure',
               lambda route: prepared.append(route))
    page.route('**/api/workflows/studio/incoming/proposals/files/file-a?*',
               lambda route: route.fulfill(
                   status=404, json={'error': 'Proposal unavailable'},
               ) if file_state['unavailable'] else route.fulfill(
                   content_type='text/plain', body='Original proposal attachment content.',
               ))
    page.route('**/api/workflows/studio/incoming/proposals/details',
               lambda route: route.fulfill(json={'success': True, 'proposal': {
                   **proposal, 'task_completed_indexes': route.request.post_data_json['task_completed_indexes'],
               }}))
    page.route('**/api/workflows/studio/incoming/proposals/review?*',
               lambda route: held.append(route) if state['status'] == 'hold' else route.fulfill(json=state))
    page.route('**/api/workflows/studio/incoming/proposals/thread',
               lambda route: (posts.append(route.request.post_data_json), route.fulfill(status=503, json={'error': 'Test dispatch paused'})))
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={'items': []}))
    page.set_viewport_size({'width': width, 'height': 900})
    link = f"{fixture_base}/development/incoming/?{urlencode({'proposal_key': key})}"
    EVIDENCE.mkdir(exist_ok=True)

    page.goto(link)
    dialog = page.get_by_role('dialog', name='Review the original request')
    expect(dialog).to_be_visible()
    expect(dialog.get_by_label('Project', exact=True)).to_be_focused()
    expect(dialog.get_by_text('A newer request')).to_have_count(0)
    page.screenshot(path=EVIDENCE / f'{width}-review.png', animations='disabled')
    dialog.get_by_role('button', name='Copy review link').click()
    expect(dialog.locator('[data-proposal-copy-status]')).to_have_text('Development review link copied.')
    assert page.evaluate('window.copiedLink') == link
    dialog.get_by_role('tab', name='Files').click()
    expect(dialog.get_by_text('Loading attachments…')).to_be_visible()
    expect(dialog.get_by_role('button', name='Original Scope', exact=False)).to_have_count(0)
    page.screenshot(path=EVIDENCE / f'{width}-files-loading.png', animations='disabled')
    assert len(prepared) == 1 and prepared[0].request.post_data_json == {'review_key': key}
    prepared[0].fulfill(json={'attachments': [{
        **proposal['attachments'][0], 'mime_type': 'text/plain',
        'url': f'/api/workflows/studio/incoming/proposals/files/file-a?review_key={key}',
    }]})
    expect(dialog.get_by_role('button', name='Original Scope', exact=False)).to_be_visible()
    for label in ('Copy review link', 'Close', 'Create Thread'):
        expect(dialog.locator('footer').get_by_role('button', name=label, exact=True)).to_be_in_viewport(ratio=1)
    page.screenshot(path=EVIDENCE / f'{width}-files.png', animations='disabled')
    dialog.get_by_role('button', name='Original Scope', exact=False).click()
    preview = page.get_by_role('dialog', name='Original Scope')
    expect(preview.get_by_text('Original proposal attachment content.')).to_be_visible()
    page.screenshot(path=EVIDENCE / f'{width}-file-preview.png', animations='disabled')
    preview.get_by_role('button', name='Close', exact=True).click()
    file_state['unavailable'] = True
    dialog.get_by_role('button', name='Original Scope', exact=False).click()
    expect(preview.get_by_text('This attachment is no longer available on the linked proposal.', exact=False)).to_be_visible()
    expect(preview.get_by_role('link', name='Download')).to_be_hidden()
    page.screenshot(path=EVIDENCE / f'{width}-file-unavailable.png', animations='disabled')
    preview.get_by_role('button', name='Close', exact=True).click()
    dialog.get_by_role('tab', name='Tasks').click()
    dialog.get_by_role('checkbox', name='Mark task done').click()
    expect(dialog.get_by_role('checkbox', name='Mark task not done')).to_be_checked()
    page.screenshot(path=EVIDENCE / f'{width}-tasks.png', animations='disabled')
    dialog.get_by_role('button', name='Create Thread', exact=True).click()
    expect(page.locator('#studio-toast')).to_contain_text('Test dispatch paused')
    assert len(posts) == 1 and posts[0]['review_key'] == key
    assert posts[0]['proposal']['message_ids'] == ['message-a']

    state.clear()
    state['status'] = 'unavailable'
    page.goto(link)
    expect(page.locator('#incoming-proposal-review-status')).to_have_text('This proposal link is no longer available.')
    expect(page.get_by_text('A newer request', exact=True)).to_be_visible()
    expect(dialog).to_have_count(0)
    page.screenshot(path=EVIDENCE / f'{width}-missing.png', animations='disabled')

    state.update(status='thread', chat_id=17)
    page.goto(link)
    expect(page).to_have_url(f'{fixture_base}/development/threads/17/')
    expect(page.locator('#conversation')).to_be_visible()
    page.screenshot(path=EVIDENCE / f'{width}-thread.png', animations='disabled')
    assert len(posts) == 1

    state['status'] = 'hold'
    page.goto(link)
    expect(page.locator('#incoming-proposal-review-status')).to_have_text('Opening linked proposal…')
    if width == 390:
        page.locator('#studio-sidebar').evaluate("node => node.classList.add('open')")
    page.get_by_role('button', name='New Thread', exact=True).click()
    expect(page).to_have_url(f'{fixture_base}/development/new/')
    with page.expect_response('**/proposals/review?*') as delivered:
        held[0].fulfill(json={'status': 'proposal', 'proposal': proposal})
    delivered.value.finished()
    expect(dialog).to_have_count(0)
    expect(page).to_have_url(f'{fixture_base}/development/new/')


def test_proposal_review_actions_fit_a_short_viewport(page, fixture_base):
    _install_api(page, [])
    key = 'a' * 64
    proposal = {'id': 'local:gmail:fixture', 'review_key': key, 'title': 'Review on a short screen'}
    page.route('**/api/workflows/studio/incoming/proposals/review?*',
               lambda route: route.fulfill(json={'status': 'proposal', 'proposal': proposal}))
    page.set_viewport_size({'width': 1280, 'height': 460})
    page.goto(f'{fixture_base}/development/incoming/?proposal_key={key}')
    dialog = page.get_by_role('dialog', name=proposal['title'])
    expect(dialog).to_be_in_viewport(ratio=1)
    for label in ('Copy review link', 'Close', 'Create Thread'):
        expect(dialog.locator('footer').get_by_role('button', name=label, exact=True)).to_be_in_viewport(ratio=1)
    EVIDENCE.mkdir(exist_ok=True)
    page.screenshot(path=EVIDENCE / '1280-short-review.png', animations='disabled')
