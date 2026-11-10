"""Real Development UI, isolated proposal sources and outbound transports."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import _install_api, URL

pytestmark = pytest.mark.e2e_playwright


def install(page, *, sources=2, linked=True, failure=False):
    _install_api(page, [], current_board_linked=True, direct_execution=True, terminal_session=False)
    chat = {'id': 17, 'title': 'Client billing request', 'project_id': 7, 'development': True,
            'development_board_key': 'decisions:70', 'development_ticket_id': 42,
            'has_reply_source': linked, 'archived': False,
            'messages': [{'role': 'user', 'content': 'Please add per-byte billing.', 'chat_row_id': 17},
                         {'role': 'assistant', 'content': 'Per-byte billing is implemented. It has not been deployed.', 'chat_row_id': 18}]}
    page.route('**/api/chats/17', lambda route: route.fulfill(json=chat))
    page.route('**/api/chats/17?*', lambda route: route.fulfill(json=chat))
    state = {'drafts': 0, 'sent': [], 'cancelled': [], 'fail': failure}
    choices = [{'key': 'whatsapp:group', 'channel': 'whatsapp', 'label': 'Client project group'},
               {'key': 'gmail:thread', 'channel': 'gmail', 'label': 'Client billing email'},
               {'key': 'mailshot:thread', 'channel': 'mailshot', 'label': 'Client Mailshot email'}][:sources]
    def handle(route):
        request = route.request
        path = request.url.split('?')[0]
        if request.method == 'GET':
            route.fulfill(json={'sources': choices}); return
        body = request.post_data_json
        if path.endswith('/approve'):
            state['sent'].append(body)
            if state.get('send_fail'):
                route.fulfill(status=502, json={'detail': 'Send was not confirmed. Check the original conversation.'}); return
            route.fulfill(json={'status': 'sent'}); return
        if path.endswith('/cancel'):
            state['cancelled'].append(path)
            route.fulfill(json={'status': 'cancelled'}); return
        if state['fail']:
            route.fulfill(status=502, json={'detail': 'Drafting is unavailable. Please retry.'}); return
        state['drafts'] += 1
        target = {'channel': 'whatsapp', 'jid': 'fixture-group@g.us', 'label': 'Client project group'}
        if body['source_key'].startswith('gmail'):
            target = {'channel': 'gmail', 'from': 'paul@example.com', 'to': ['client@example.com'], 'cc': ['reviewer@example.com'], 'subject': 'Re: Billing request'}
        if body['source_key'].startswith('mailshot'):
            target = {'channel': 'mailshot', 'from': 'paul@example.com', 'to': ['client@example.com'], 'cc': ['reviewer@example.com'], 'subject': 'Re: Billing request'}
        route.fulfill(json={'id': f'draft-{state["drafts"]}', 'status': 'draft', 'source_key': body['source_key'],
                            'text': 'I have made the billing change. It still needs deploying.' if state['drafts'] == 1 else 'The billing change is ready for deployment.',
                            'target': target})
    page.route('**/api/workflows/studio/tasks/17/source-replies**', handle)
    page.goto(URL)
    return state


def shot(page, name):
    folder = Path(os.environ.get('SOURCE_REPLY_SHOTS', '/tmp/decisions-source-reply-qa'))
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f'{name}.png'), full_page=True)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_choose_source_edit_rewrite_approve_once(page, width):
    page.set_viewport_size({'width': width, 'height': 1000})
    state = install(page)
    reply = page.get_by_role('button', name='Reply', exact=True)
    expect(reply).to_be_visible()
    assert reply.evaluate('(el) => el.nextElementSibling.hasAttribute("data-fork-current")')
    reply.scroll_into_view_if_needed()
    shot(page, f'{width}-reply-button')
    reply.click()
    expect(page.get_by_role('dialog')).to_be_visible()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    assert state['drafts'] == 0 and not state['sent']
    shot(page, f'{width}-choose-source')
    page.get_by_label('Source', exact=True).select_option('gmail:thread')
    expect(page.get_by_label('Reply', exact=True)).to_have_value('I have made the billing change. It still needs deploying.')
    expect(page.get_by_text('Cc: reviewer@example.com', exact=True)).to_be_visible()
    page.get_by_label('Reply', exact=True).fill('My edited reply to the client.')
    page.get_by_label('Rewrite guidance (optional)').fill('Shorter, keep the deployment caveat.')
    page.get_by_role('button', name='Rewrite', exact=True).click()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('The billing change is ready for deployment.')
    shot(page, f'{width}-email-draft')
    page.get_by_label('Reply', exact=True).fill('My final approved wording.')
    page.get_by_role('button', name='Approve', exact=True).click()
    expect(page.get_by_text('Reply sent.', exact=True)).to_be_visible()
    assert state['sent'] == [{'approved': True, 'text': 'My final approved wording.'}]
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    shot(page, f'{width}-sent')
    page.get_by_role('button', name='Close', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)


def test_whatsapp_cancel_escape_and_rewrite_failure_preserve_edits(page):
    state = install(page, sources=1)
    page.get_by_role('button', name='Reply', exact=True).click()
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    expect(page.get_by_text('WhatsApp: Client project group', exact=True)).to_be_visible()
    page.get_by_label('Reply', exact=True).fill('Keep my edited words.')
    state['fail'] = True
    page.get_by_role('button', name='Rewrite', exact=True).click()
    expect(page.get_by_text('Drafting is unavailable. Please retry.', exact=True)).to_be_visible()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('Keep my edited words.')
    shot(page, 'whatsapp-rewrite-error')
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(state['cancelled']) == 1 and not state['sent']
    expect(page.get_by_role('button', name='Reply', exact=True)).to_be_focused()


def test_non_proposal_has_no_reply_button(page):
    install(page, linked=False)
    expect(page.locator('[data-fork-current]')).to_be_visible()
    expect(page.get_by_role('button', name='Reply', exact=True)).to_have_count(0)


def test_unconfirmed_send_preserves_text_and_prevents_double_send(page):
    state = install(page, sources=1)
    page.get_by_role('button', name='Reply', exact=True).click()
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    page.get_by_label('Reply', exact=True).fill('Approved wording must remain visible.')
    state['send_fail'] = True
    page.get_by_role('button', name='Approve', exact=True).click()
    expect(page.get_by_text('Send was not confirmed. Check the original conversation.', exact=True)).to_be_visible()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('Approved wording must remain visible.')
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    assert len(state['sent']) == 1
    shot(page, 'unconfirmed-send')
    page.get_by_role('button', name='Close', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)


def test_mailshot_reply_all_preview_and_cancel_without_send(page):
    page.set_viewport_size({'width': 390, 'height': 844})
    state = install(page, sources=3)
    page.get_by_role('button', name='Reply', exact=True).click()
    page.get_by_label('Source', exact=True).select_option('mailshot:thread')
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    expect(page.get_by_text('Mailshot: Reply all', exact=True)).to_be_visible()
    expect(page.get_by_text('From: paul@example.com', exact=True)).to_be_visible()
    expect(page.get_by_text('Cc: reviewer@example.com', exact=True)).to_be_visible()
    shot(page, 'mailshot-phone')
    page.get_by_role('button', name='Cancel', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(state['cancelled']) == 1 and not state['sent']


def test_empty_sources_and_close_button_send_nothing(page):
    state = install(page, sources=0)
    page.get_by_role('button', name='Reply', exact=True).click()
    expect(page.get_by_text('No original source is available for this proposal.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    shot(page, 'source-unavailable')
    page.get_by_role('button', name='Close reply', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert state['drafts'] == 0 and not state['sent']


def test_change_source_cancels_previous_draft_and_backdrop_closes(page):
    state = install(page)
    page.get_by_role('button', name='Reply', exact=True).click()
    page.get_by_label('Source', exact=True).select_option('whatsapp:group')
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    page.get_by_label('Reply', exact=True).fill('   ')
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    page.get_by_label('Source', exact=True).select_option('gmail:thread')
    expect(page.get_by_text('Gmail: Reply all', exact=True)).to_be_visible()
    assert len(state['cancelled']) == 1
    page.mouse.click(5, 5)
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(state['cancelled']) == 2 and not state['sent']


def test_cancel_during_generation_cancels_late_draft_without_send(page):
    state = install(page)
    pending = []
    def hold(route):
        if route.request.method == 'POST': pending.append(route)
        else: route.fallback()
    page.route('**/api/workflows/studio/tasks/17/source-replies', hold)
    page.get_by_role('button', name='Reply', exact=True).click()
    page.get_by_label('Source', exact=True).select_option('whatsapp:group')
    expect(page.get_by_text('Drafting reply...', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    shot(page, 'draft-loading')
    page.get_by_role('button', name='Cancel', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    with page.expect_response(lambda response: response.url.endswith('/late-draft/cancel')):
        pending[0].fulfill(json={'id': 'late-draft', 'status': 'draft', 'text': 'Never send me.'})
    assert len(state['cancelled']) == 1 and not state['sent']
