"""Proposal review and navigation must use the same source identity."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from playwright.sync_api import expect

from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
from distr.core.incoming import proposals as intake
from distr.gui.web.routes.development.incoming import register_routes
from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
EVIDENCE = Path('/tmp/decisions-proposal-source-navigation')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_proposal_source_never_substitutes_another_conversation(page, fixture_base, width):
    page.set_default_timeout(5000)
    _install_api(page, [])
    proposals = [
        {'id': 'tensology:101', 'source': 'gmail', 'source_thread_id': 'original',
         'title': 'Review original request', 'message_ids': ['shared-id']},
        {'id': 'tensology:102', 'source': 'gmail', 'source_thread_id': 'missing',
         'title': 'Review older request', 'message_ids': ['old-id']},
    ]
    items = [
        ('mailshot', 'mailshot-thread', 'shared-id', 'Unrelated Mailshot message'),
        ('gmail', 'other-thread', 'old-id', 'Unrelated Gmail message'),
        ('gmail', 'original', 'shared-id', 'Exact proposal source message'),
        ('gmail', 'original', 'shared-id-2', 'Later conversation context'),
    ]
    page.route('**/api/workflows/studio/incoming/proposals?*',
               lambda route: route.fulfill(json={'proposals': proposals}))
    page.route('**/api/workflows/studio/incoming/proposals/files/ensure',
               lambda route: route.fulfill(json={'attachments': []}))
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={
        'items': [{'key': f'{channel}:{thread}:{message_id}', 'source': channel,
                   'source_thread_id': thread, 'source_message_id': message_id, 'text': text,
                   'subject': f'Conversation {thread}', 'sender': 'Sender',
                   'created_at': '2026-11-11T10:25:00Z'} for channel, thread, message_id, text in items],
    }))
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'{fixture_base}/development/incoming/')
    EVIDENCE.mkdir(exist_ok=True)

    older = page.get_by_role('button', name='Open evidence for Review older request')
    older.press('Enter')
    dialog = page.get_by_role('dialog', name='Review older request')
    dialog.get_by_role('tab', name='Source').click()
    expect(dialog.get_by_text('Unrelated Gmail message')).to_have_count(0)
    dialog.get_by_role('button', name='View in conversation').click()
    expect(dialog.get_by_role('status')).to_contain_text('The original conversation is not available in Incoming.')
    expect(dialog.get_by_role('button', name='View in conversation')).to_be_focused()
    page.screenshot(path=EVIDENCE / f'{width}-unavailable.png', animations='disabled')
    page.keyboard.press('Escape')
    expect(older).to_be_focused()

    page.get_by_role('button', name='Open evidence for Review original request').press('Enter')
    dialog = page.get_by_role('dialog', name='Review original request')
    dialog.get_by_role('tab', name='Source').click()
    expect(dialog.locator('.proposal-message')).to_have_count(1)
    expect(dialog.locator('.proposal-message')).to_contain_text('Exact proposal source message')
    expect(dialog.get_by_text('Unrelated Mailshot message')).to_have_count(0)
    expect(dialog.get_by_text('Later conversation context')).to_have_count(0)
    page.screenshot(path=EVIDENCE / f'{width}-exact.png', animations='disabled')
    dialog.get_by_role('button', name='View in conversation').click()
    reader = page.locator('#incoming-gmail-reader')
    expect(reader.get_by_text('Exact proposal source message')).to_be_visible()
    expect(reader.get_by_text('Later conversation context')).to_be_visible()
    expect(reader.locator('[data-mail-id="shared-id"]')).to_be_focused()
    page.screenshot(path=EVIDENCE / f'{width}-opened.png', animations='disabled')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_desktop_merged_proposal_reads_exact_sources_outside_the_recent_list(page, fixture_base, monkeypatch, width):
    _install_api(page, [])
    first = {'id': 'local:gmail:dispatch', 'source': 'gmail', 'source_thread_id': 'dispatch',
             'message_ids': ['10'], 'participant': 'Dispatch', 'title': 'Review delivery'}
    second = {**first, 'id': 'local:gmail:operations', 'source_thread_id': 'operations',
              'message_ids': ['11'], 'participant': 'Operations', 'title': 'Review capacity'}
    merged = intake._combine_frac_proposals(first, second)
    key = intake.proposal_review_key(merged)
    proposal = {**merged, 'review_key': key}
    page.route('**/api/workflows/studio/incoming/proposals?*',
               lambda route: route.fulfill(json={'proposals': [proposal]}))
    page.route('**/api/workflows/studio/incoming/proposals/files/ensure',
               lambda route: route.fulfill(json={'attachments': []}))
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={
        'items': [{'key': 'wrong', 'source': 'gmail', 'source_thread_id': 'dispatch',
                   'source_message_id': '11', 'text': 'Recent list collision', 'subject': 'Recent list'}],
    }))
    monkeypatch.setattr(intake, 'resolve_proposal_review', lambda review_key: (
        {'status': 'proposal', 'proposal': proposal} if review_key == key else {'status': 'unavailable'}))
    monkeypatch.setattr(GoogleWorkspaceConnector, '__init__', lambda self: None)
    available = []
    monkeypatch.setattr(GoogleWorkspaceConnector, 'get_email_thread', lambda self, thread: [
        {'id': '10', 'threadId': thread, 'subject': 'Operations conversation', 'body': 'Operations context'},
        *([{'id': '11', 'threadId': thread, 'subject': 'Operations conversation', 'body': 'Operations original'}]
          if available else []),
    ] if thread == 'operations' else [
        {'id': '10', 'threadId': 'dispatch', 'subject': 'Dispatch conversation', 'body': 'Dispatch original'},
    ])
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix='/api')
    client = TestClient(app)
    held = []

    def source(route):
        if 'source_key=' in route.request.url:
            held.append(route)
        else:
            result = client.get(route.request.url)
            route.fulfill(status=result.status_code, json=result.json())

    def deliver():
        assert len(held) == 1
        route = held.pop()
        result = client.get(route.request.url)
        route.fulfill(status=result.status_code, json=result.json())

    page.route('**/api/workflows/studio/incoming/proposals/source?*', source)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'{fixture_base}/development/incoming/')
    EVIDENCE.mkdir(exist_ok=True)
    opener = page.get_by_role('button', name='Open evidence for Review delivery + Review capacity')
    opener.press('Enter')
    dialog = page.get_by_role('dialog', name='Review delivery + Review capacity')
    dialog.get_by_role('tab', name='Source').click()
    expect(dialog.get_by_text('Recent list collision')).to_have_count(0)
    jump = dialog.get_by_role('button', name='View in conversation')
    jump.click()
    picker = dialog.get_by_label('Original conversation', exact=True)
    expect(picker).to_be_focused()
    expect(jump).to_be_disabled()
    page.screenshot(path=EVIDENCE / f'{width}-merged-picker.png', animations='disabled')
    picker.select_option(label='Gmail · Operations')
    jump.click()
    expect(dialog.get_by_text('Opening the original conversation…')).to_be_visible()
    page.screenshot(path=EVIDENCE / f'{width}-merged-loading.png', animations='disabled')
    deliver()
    expect(dialog.get_by_role('status')).to_contain_text('The original message is no longer available')
    expect(jump).to_be_focused()
    page.screenshot(path=EVIDENCE / f'{width}-merged-unavailable.png', animations='disabled')
    available.append(True)
    jump.click()
    expect(dialog.get_by_text('Opening the original conversation…')).to_be_visible()
    deliver()
    original = dialog.locator('[data-original-message]')
    expect(original).to_contain_text('Operations original')
    expect(original).to_be_focused()
    expect(dialog.get_by_text('Not scheduled', exact=True)).to_have_count(0)
    expect(dialog.get_by_text('Operations context', exact=True)).to_be_visible()
    expect(dialog.get_by_text('Dispatch original', exact=True)).to_have_count(0)
    expect(dialog.get_by_text('Recent list collision', exact=True)).to_have_count(0)
    page.screenshot(path=EVIDENCE / f'{width}-merged-original.png', animations='disabled')
    dialog.get_by_role('button', name='Back to source evidence').click()
    expect(jump).to_be_focused()
    expect(picker.locator('option:checked')).to_have_text('Gmail · Operations')
    page.screenshot(path=EVIDENCE / f'{width}-merged-return.png', animations='disabled')
    picker.select_option(label='Gmail · Dispatch')
    jump.click()
    expect(dialog.get_by_text('Opening the original conversation…')).to_be_visible()
    deliver()
    expect(original).to_contain_text('Dispatch original')
    dialog.get_by_role('button', name='Back to source evidence').click()
    jump.click()
    expect(dialog.get_by_text('Opening the original conversation…')).to_be_visible()
    dialog.get_by_role('button', name='Close', exact=True).last.click()
    deliver()
    expect(opener).to_be_focused()
    expect(dialog).to_have_count(0)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_submitted_thread_opens_its_saved_source_and_cancels_when_switching(page, fixture_base, monkeypatch, width):
    _install_api(page, [])
    first = {'id': 'local:gmail:dispatch', 'source': 'gmail', 'source_thread_id': 'dispatch',
             'message_ids': ['10'], 'participant': 'Dispatch', 'title': 'Review delivery'}
    second = {**first, 'id': 'local:gmail:operations', 'source_thread_id': 'operations',
              'message_ids': ['11'], 'participant': 'Operations', 'title': 'Review capacity'}
    proposals = {17: intake._combine_frac_proposals(first, second), 19: first}
    keys = {chat_id: intake.proposal_review_key(proposal) for chat_id, proposal in proposals.items()}
    titles = {17: 'Submitted merge', 18: 'Other Development thread', 19: 'Submitted email'}

    def chat_route(route):
        chat_id = int(urlparse(route.request.url).path.rsplit('/', 1)[1])
        route.fulfill(json={'id': chat_id, 'project_id': 7, 'title': titles[chat_id], 'messages': [], 'development': True,
                            'proposal_source_review_key': keys.get(chat_id)})

    page.route('**/api/chats/*?surface=development', chat_route)
    monkeypatch.setattr(intake, 'resolve_proposal_review', lambda review_key, submitted_only=False: next((
        {'status': 'thread', 'chat_id': chat_id, 'proposal': proposals[chat_id]}
        for chat_id, key in keys.items() if submitted_only and key == review_key
    ), {'status': 'unavailable'}))
    monkeypatch.setattr(GoogleWorkspaceConnector, '__init__', lambda self: None)
    unavailable = ['operations']
    monkeypatch.setattr(GoogleWorkspaceConnector, 'get_email_thread', lambda self, thread: [
        {'id': '10' if thread == 'dispatch' else '11', 'threadId': thread,
         'subject': f'{thread.title()} conversation', 'body': f'{thread.title()} original'},
    ] if thread not in unavailable else [])
    app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    app.include_router(router, prefix='/api')
    client = TestClient(app)
    held, calls = [], []
    hold = []

    def reply(route):
        response = client.get(route.request.url)
        route.fulfill(status=response.status_code, json=response.json())

    def source(route):
        params = parse_qs(urlparse(route.request.url).query)
        chat_id = int(params['chat_id'][0])
        assert params['review_key'] == [keys[chat_id]]
        calls.append(chat_id)
        if hold:
            held.append(route)
        else:
            reply(route)

    def switch_thread(chat_id):
        page.evaluate("""id => {
            history.pushState(null, '', '/development/threads/' + id + '/');
            dispatchEvent(new PopStateEvent('popstate'));
        }""", chat_id)
        expect(page.locator('#header-task')).to_have_text(titles[chat_id])

    page.route('**/api/workflows/studio/incoming/proposals/source?*', source)
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'{fixture_base}/development/threads/17/')
    expect(page.locator('#header-task')).to_have_text(titles[17])
    menu = page.get_by_role('button', name='Thread menu', exact=True)
    action = page.get_by_role('menuitem', name='View original conversation')
    dialog = page.get_by_role('dialog', name='Original conversation')
    picker = dialog.get_by_label('Original conversation', exact=True)
    open_source = dialog.get_by_role('button', name='View original conversation')
    anchor = dialog.locator('[data-original-message]')
    EVIDENCE.mkdir(exist_ok=True)
    page.locator('#task-prompt').fill('Keep this unsent draft')
    menu.click()
    expect(action).to_be_visible()
    page.screenshot(path=EVIDENCE / f'{width}-submitted-menu.png', animations='disabled')
    action.click()
    expect(picker).to_be_focused()
    page.screenshot(path=EVIDENCE / f'{width}-submitted-picker.png', animations='disabled')
    picker.select_option(label='Gmail · Operations')
    open_source.click()
    expect(dialog.get_by_role('status')).to_contain_text('The original message is no longer available')
    page.screenshot(path=EVIDENCE / f'{width}-submitted-unavailable.png', animations='disabled')
    unavailable.clear()
    open_source.click()
    expect(anchor).to_contain_text('Operations original')
    expect(anchor).to_be_focused()
    page.screenshot(path=EVIDENCE / f'{width}-submitted-original.png', animations='disabled')
    dialog.get_by_role('button', name='Back to conversations').click()
    expect(open_source).to_be_focused()
    expect(picker.locator('option:checked')).to_have_text('Gmail · Operations')
    page.screenshot(path=EVIDENCE / f'{width}-submitted-return.png', animations='disabled')
    dialog.get_by_role('button', name='Close').click()
    expect(menu).to_be_focused()
    expect(page.locator('#task-prompt')).to_have_value('Keep this unsent draft')
    menu.click()
    hold.append(True)
    with page.expect_request('**/api/workflows/studio/incoming/proposals/source?*'):
        action.click()
    expect(dialog.get_by_role('status')).to_have_text('Opening the original conversation…')
    page.screenshot(path=EVIDENCE / f'{width}-submitted-loading.png', animations='disabled')
    switch_thread(18)
    expect(dialog).to_have_count(0)
    assert len(held) == 1
    reply(held.pop())
    menu.click()
    expect(action).to_have_count(0)
    expect(dialog).to_have_count(0)
    hold.clear()
    switch_thread(19)
    menu.click()
    action.click()
    expect(anchor).to_contain_text('Dispatch original')
    expect(dialog.get_by_role('button', name='Back to conversations')).to_have_count(0)
    page.screenshot(path=EVIDENCE / f'{width}-submitted-single.png', animations='disabled')
    assert calls == [17, 17, 17, 17, 19]
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
