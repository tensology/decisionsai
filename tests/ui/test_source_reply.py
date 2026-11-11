"""Real Development UI, isolated proposal sources and outbound transports."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import _install_api, URL
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright


def install(page, *, sources=2, linked=True, failure=False, url=URL, later_messages=()):
    _install_api(page, [], current_board_linked=True, direct_execution=True, terminal_session=False)
    chat = {'id': 17, 'title': 'Client billing request', 'project_id': 7, 'development': True,
            'development_board_key': 'decisions:70', 'development_ticket_id': 42,
            'has_reply_source': linked, 'archived': False,
            'messages': [{'role': 'user', 'content': 'Please add per-byte billing.', 'chat_row_id': 17},
                         {'role': 'assistant', 'content': 'Per-byte billing is implemented. It has not been deployed.', 'chat_row_id': 18},
                         *later_messages]}
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
            route.fulfill(json={'sources': state.get('choices', choices), 'drafts': state.get('retained', [])}); return
        body = request.post_data_json
        if path.endswith('/approve'):
            state['sent'].append(body)
            if state.get('already_sent'):
                route.fulfill(json={'status': 'sent', 'already_sent': True, 'text': 'The wording actually sent from the first window.'}); return
            if state.get('send_preflight_fail'):
                route.fulfill(status=409, json={'detail': state.get('preflight_detail') or 'Nothing was sent. The sending Gmail account changed. Restore the account or rewrite and review a new draft.',
                    'code': 'reply_not_sent', 'send_attempted': False, 'draft_status': 'draft',
                    **({'reason': state['preflight_reason']} if state.get('preflight_reason') else
                       {'reason': 'source_changed'} if state.get('source_changed') else {})}); return
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
            target = {'channel': 'gmail', 'from': 'paul@example.com', 'to': ['client@example.com'],
                      'cc': state.get('gmail_cc', ['reviewer@example.com']), 'subject': 'Re: Billing request'}
        if body['source_key'].startswith('mailshot'):
            target = {'channel': 'mailshot', 'from': 'paul@example.com', 'to': ['client@example.com'], 'cc': ['reviewer@example.com'], 'subject': 'Re: Billing request'}
        route.fulfill(json={'id': f'draft-{state["drafts"]}', 'status': 'draft', 'source_key': body['source_key'],
                            'text': 'I have made the billing change. It still needs deploying.' if state['drafts'] == 1 else 'The billing change is ready for deployment.',
                            'target': target})
    page.route('**/api/workflows/studio/tasks/17/source-replies**', handle)
    page.goto(url)
    return state


def shot(page, name):
    folder = Path(os.environ.get('SOURCE_REPLY_SHOTS', '/tmp/decisions-source-reply-qa'))
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / f'{name}.png'), full_page=True)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_choose_source_edit_rewrite_approve_once(page, width, fixture_base):
    page.set_viewport_size({'width': width, 'height': 1000})
    state = install(page, url=f'{fixture_base}/development/threads/17/')
    reply = page.get_by_role('button', name='Message Client', exact=True)
    expect(reply).to_be_visible()
    assert reply.evaluate('(el) => el.previousElementSibling.hasAttribute("data-fork-current") && el.nextElementSibling === null')
    reply.scroll_into_view_if_needed()
    shot(page, f'{width}-reply-button')
    reply.click()
    expect(page.get_by_role('dialog')).to_be_visible()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    assert state['drafts'] == 0 and not state['sent']
    shot(page, f'{width}-choose-source')
    page.get_by_label('To:', exact=True).select_option('gmail:thread')
    expect(page.get_by_label('Reply', exact=True)).to_have_value('I have made the billing change. It still needs deploying.')
    expect(page.get_by_text('Cc: reviewer@example.com', exact=True)).to_be_visible()
    page.get_by_label('Reply', exact=True).fill('My edited reply to the client.')
    page.get_by_label('Rewrite guidance (optional)').fill('Shorter, keep the deployment caveat.')
    page.get_by_role('button', name='Rewrite', exact=True).click()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('The billing change is ready for deployment.')
    shot(page, f'{width}-email-draft')
    page.get_by_label('Reply', exact=True).fill('My final approved wording.')
    page.get_by_role('button', name='Approve', exact=True).click()
    expect(page.locator('#studio-toast.success')).to_have_text('Reply sent')
    assert state['sent'] == [{'approved': True, 'text': 'My final approved wording.'}]
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    shot(page, f'{width}-sent')


def test_whatsapp_cancel_escape_and_rewrite_failure_preserve_edits(page):
    state = install(page, sources=1)
    page.get_by_role('button', name='Message Client', exact=True).click()
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    expect(page.locator('#source-reply-source option:checked')).to_have_text('Client project group')
    expect(page.get_by_role('dialog')).not_to_contain_text('fixture-group@g.us')
    page.get_by_label('Reply', exact=True).fill('Keep my edited words.')
    state['fail'] = True
    page.get_by_role('button', name='Rewrite', exact=True).click()
    expect(page.get_by_text('Drafting is unavailable. Please retry.', exact=True)).to_be_visible()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('Keep my edited words.')
    shot(page, 'whatsapp-rewrite-error')
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(state['cancelled']) == 1 and not state['sent']
    expect(page.get_by_role('button', name='Message Client', exact=True)).to_be_focused()


def test_non_proposal_has_no_reply_button(page):
    install(page, linked=False)
    expect(page.locator('[data-fork-current]')).to_be_visible()
    expect(page.get_by_role('button', name='Message Client', exact=True)).to_have_count(0)


def test_message_client_only_applies_to_the_latest_response_and_hides_while_typing(page, fixture_base):
    install(page, url=f'{fixture_base}/development/threads/17/', later_messages=[
        {'role': 'user', 'content': 'Please test deployment.', 'chat_row_id': 19},
        {'role': 'assistant', 'content': 'Deployment checks passed.', 'chat_row_id': 20},
    ])
    reply = page.get_by_role('button', name='Message Client', exact=True)
    expect(reply).to_have_count(1)
    expect(page.locator('.studio-message.assistant').filter(has=reply)).to_contain_text('Deployment checks passed.')
    prompt = page.get_by_role('textbox', name='Development instruction')
    prompt.fill('One more change')
    expect(reply).to_have_count(0)
    prompt.clear()
    expect(reply).to_be_visible()


def test_message_client_is_absent_when_the_latest_turn_is_awaiting_a_response(page, fixture_base):
    install(page, url=f'{fixture_base}/development/threads/17/', later_messages=[
        {'role': 'user', 'content': 'Please test deployment.', 'chat_row_id': 19},
    ])
    expect(page.locator('#message-list').get_by_text('Please test deployment.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Message Client', exact=True)).to_have_count(0)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_unconfirmed_send_preserves_text_and_prevents_double_send(page, width, fixture_base):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    state = install(page, url=f'{fixture_base}/development/threads/17/')
    page.get_by_role('button', name='Message Client', exact=True).click()
    page.get_by_label('To:', exact=True).select_option('gmail:thread')
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    page.get_by_label('Reply', exact=True).fill('Approved wording must remain visible.')
    state['send_fail'] = True
    page.get_by_role('button', name='Approve', exact=True).click()
    expect(page.get_by_text('Send was not confirmed. Check the original conversation.', exact=True)).to_be_visible()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('Approved wording must remain visible.')
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    assert len(state['sent']) == 1
    shot(page, f'{width}-unconfirmed-send')
    page.get_by_role('button', name='Close message', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('source', ['gmail:thread', 'whatsapp:group'])
def test_preflight_no_send_retains_editable_text_and_requires_deliberate_retry(page, width, source, tmp_path, fixture_base):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    state = install(page, url=f'{fixture_base}/development/threads/17/')
    page.get_by_role('button', name='Message Client', exact=True).click()
    page.get_by_label('To:', exact=True).select_option(source)
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    page.get_by_label('Reply', exact=True).fill('My reviewed wording is still here.')
    state['send_preflight_fail'] = True
    state['preflight_detail'] = (
        'Nothing was sent. WhatsApp dry-run mode is on. Turn it off before approving again.'
        if source == 'whatsapp:group'
        else 'Nothing was sent. The original Gmail reply reference is missing. Your text is retained.'
    )
    page.get_by_role('button', name='Approve', exact=True).click()
    expect(page.get_by_text(state['preflight_detail'], exact=True)).to_be_visible()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('My reviewed wording is still here.')
    expect(page.get_by_label('Reply', exact=True)).to_be_editable()
    expect(page.get_by_role('button', name='Rewrite', exact=True)).to_be_enabled()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_enabled()
    expect(page.get_by_role('button', name='Close message', exact=True)).to_be_enabled()
    assert len(state['sent']) == 1
    page.get_by_role('dialog').screenshot(path=tmp_path / f'{width}-preflight-not-sent.png')
    state['send_preflight_fail'] = False
    page.get_by_role('button', name='Approve', exact=True).click()
    expect(page.locator('#studio-toast.success')).to_have_text('Reply sent')
    assert state['sent'] == [{'approved': True, 'text': 'My reviewed wording is still here.'}] * 2
    expect(page.get_by_role('dialog')).to_have_count(0)


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_changed_messages_require_a_rewrite_before_approval(page, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    state = install(page, sources=1)
    page.get_by_role('button', name='Message Client', exact=True).click()
    reply = page.get_by_label('Reply', exact=True)
    reply.fill('Keep my reviewed wording.')
    state.update(send_preflight_fail=True, preflight_reason='rewrite_required',
                 preflight_detail='Nothing was sent. The source messages changed since this reply was drafted. Rewrite and review a new draft.')
    approve = page.get_by_role('button', name='Approve', exact=True)
    approve.click()
    expect(page.get_by_text(state['preflight_detail'], exact=True)).to_be_visible()
    expect(reply).to_have_value('Keep my reviewed wording.')
    expect(approve).to_be_disabled()
    reply.fill('Keep these updated edits.')
    expect(approve).to_be_disabled()
    rewrite = page.get_by_role('button', name='Rewrite', exact=True)
    expect(rewrite).to_be_enabled()
    page.get_by_text(state['preflight_detail'], exact=True).scroll_into_view_if_needed()
    shot(page, f'{width}-source-messages-changed')
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/source-replies')) as sent:
        rewrite.click()
    assert sent.value.post_data_json['previous'] == 'Keep these updated edits.'
    assert sent.value.post_data_json['replaces'] == 'draft-1'
    expect(reply).to_have_value('The billing change is ready for deployment.')
    expect(approve).to_be_enabled()
    shot(page, f'{width}-source-messages-rewritten')
    assert len(state['sent']) == 1


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_gmail_alias_change_keeps_edits_and_requires_new_recipient_review(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    state = install(page, url=f'{fixture_base}/development/threads/17/')
    state['gmail_cc'] = ['reviewer@example.com', 'orders@example.com']
    page.get_by_role('button', name='Message Client', exact=True).click()
    page.get_by_label('To:', exact=True).select_option('gmail:thread')
    expect(page.get_by_text('Cc: reviewer@example.com, orders@example.com', exact=True)).to_be_visible()
    reply = page.get_by_label('Reply', exact=True)
    reply.fill('Keep the final deployment caveat.')
    notice = 'Nothing was sent. The Gmail reply recipients changed. Rewrite and review a new draft.'
    state.update(send_preflight_fail=True, preflight_reason='rewrite_required', preflight_detail=notice)
    approve = page.get_by_role('button', name='Approve', exact=True)
    approve.click()
    expect(approve).to_be_disabled()
    expect(reply).to_have_value('Keep the final deployment caveat.')
    warning = page.get_by_text(notice, exact=True)
    expect(warning).to_be_visible()
    warning.scroll_into_view_if_needed()
    shot(page, f'{width}-gmail-alias-review-blocked')

    state.update(gmail_cc=['reviewer@example.com'], send_preflight_fail=False)
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/source-replies')) as rewrite:
        page.get_by_role('button', name='Rewrite', exact=True).click()
    assert rewrite.value.post_data_json['previous'] == 'Keep the final deployment caveat.'
    expect(approve).to_be_enabled()
    expect(page.get_by_text('Cc: reviewer@example.com', exact=True)).to_be_visible()
    expect(page.get_by_text('orders@example.com', exact=False)).to_have_count(0)
    shot(page, f'{width}-gmail-alias-review-rewritten')
    assert len(state['sent']) == 1


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_reopened_draft_shows_the_changed_result_and_keeps_the_reviewed_text(page, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    state = install(page, sources=1)
    notice = 'Nothing was sent. The Development response changed since this reply was drafted. Rewrite and review a new draft.'
    state['retained'] = [{
        'id': 'saved-draft', 'status': 'draft', 'source_key': 'whatsapp:group',
        'text': 'Keep my reviewed wording.', 'rewrite_required': True, 'rewrite_notice': notice,
        'target': {'channel': 'whatsapp', 'jid': 'fixture-group@g.us', 'label': 'Client project group'},
    }]
    state['drafts'] = 1
    page.get_by_role('button', name='Message Client', exact=True).click()
    reply = page.get_by_label('Reply', exact=True)
    approve = page.get_by_role('button', name='Approve', exact=True)
    expect(reply).to_have_value('Keep my reviewed wording.')
    expect(approve).to_be_disabled()
    expect(page.get_by_text(notice, exact=True)).to_be_visible()
    page.get_by_text(notice, exact=True).scroll_into_view_if_needed()
    shot(page, f'{width}-result-changed-reopened')
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/source-replies')) as sent:
        page.get_by_role('button', name='Rewrite', exact=True).click()
    assert sent.value.post_data_json['previous'] == 'Keep my reviewed wording.'
    assert sent.value.post_data_json['replaces'] == 'saved-draft'
    expect(approve).to_be_enabled()
    shot(page, f'{width}-result-changed-rewritten')
    assert not state['sent']


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('replacement_available', [True, False])
def test_removed_source_warning_keeps_reviewed_text_visible(page, width, replacement_available):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    state = install(page, sources=1)
    state['send_preflight_fail'] = True
    state['source_changed'] = True
    state['preflight_detail'] = ('Nothing was sent. The proposal source changed since this reply was drafted. '
                                 'Reopen Reply to choose a current source and review a new draft.' if replacement_available else
                                 'Nothing was sent. This proposal no longer has a reply source. Your draft text is retained.')
    page.get_by_role('button', name='Message Client', exact=True).click()
    reply = page.get_by_label('Reply', exact=True)
    reply.fill('My reviewed wording is still here.')
    page.get_by_role('button', name='Approve', exact=True).click()
    expect(page.get_by_text(state['preflight_detail'], exact=True)).to_be_visible()
    expect(reply).to_have_value('My reviewed wording is still here.')
    expect(reply).not_to_be_editable()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    expect(page.get_by_role('button', name='Rewrite', exact=True)).to_be_disabled()
    expect(page.get_by_label('To:', exact=True)).to_be_disabled()
    close = page.get_by_role('button', name='Close message', exact=True)
    expect(close).to_be_visible()
    shot(page, f'{width}-removed-source-warning' if replacement_available else f'{width}-last-source-removed')
    close.click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert not state['cancelled']
    assert len(state['sent']) == 1
    if not replacement_available:
        return

    state['choices'] = [{'key': 'gmail:thread', 'channel': 'gmail', 'label': 'Replacement email'}]
    page.get_by_role('button', name='Message Client', exact=True).click()
    expect(page.get_by_label('To:', exact=True)).to_have_value('gmail:thread')
    expect(page.get_by_text('Gmail: Reply all', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_enabled()
    shot(page, f'{width}-replacement-source-review')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_already_sent_shows_authoritative_text_and_preserves_unsent_edits(page, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    state = install(page, sources=1)
    page.get_by_role('button', name='Message Client', exact=True).click()
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    page.get_by_label('Reply', exact=True).fill('My newer wording from the second window.')
    state['already_sent'] = True
    page.get_by_role('button', name='Approve', exact=True).click()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('The wording actually sent from the first window.')
    expect(page.get_by_label('Reply', exact=True)).not_to_be_editable()
    expect(page.get_by_label('Your unsent edits', exact=True)).to_have_value('My newer wording from the second window.')
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    expect(page.get_by_role('button', name='Close message', exact=True)).to_be_in_viewport()
    assert len(state['sent']) == 1
    page.locator('.source-reply-content').evaluate('(el) => { el.scrollTop = 0; }')
    shot(page, f'{width}-already-sent-authoritative-top')
    page.get_by_label('Your unsent edits', exact=True).scroll_into_view_if_needed()
    page.locator('.source-reply-status').scroll_into_view_if_needed()
    shot(page, f'{width}-already-sent-unsent-edits')


def test_mailshot_reply_all_preview_and_cancel_without_send(page):
    page.set_viewport_size({'width': 390, 'height': 844})
    state = install(page, sources=3)
    page.get_by_role('button', name='Message Client', exact=True).click()
    page.get_by_label('To:', exact=True).select_option('mailshot:thread')
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    expect(page.get_by_text('Mailshot: Reply all', exact=True)).to_be_visible()
    expect(page.get_by_text('From: paul@example.com', exact=True)).to_be_visible()
    expect(page.get_by_text('Cc: reviewer@example.com', exact=True)).to_be_visible()
    shot(page, 'mailshot-phone')
    page.get_by_role('button', name='Close message', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(state['cancelled']) == 1 and not state['sent']


def test_empty_sources_and_close_button_send_nothing(page):
    state = install(page, sources=0)
    page.get_by_role('button', name='Message Client', exact=True).click()
    expect(page.get_by_text('No original source is available for this proposal.', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    shot(page, 'source-unavailable')
    page.get_by_role('button', name='Close message', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert state['drafts'] == 0 and not state['sent']


def test_change_source_cancels_previous_draft_and_backdrop_closes(page):
    state = install(page)
    page.get_by_role('button', name='Message Client', exact=True).click()
    page.get_by_label('To:', exact=True).select_option('whatsapp:group')
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    page.get_by_label('Reply', exact=True).fill('   ')
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    page.get_by_label('To:', exact=True).select_option('gmail:thread')
    expect(page.get_by_text('Gmail: Reply all', exact=True)).to_be_visible()
    assert len(state['cancelled']) == 1
    page.mouse.click(5, 5)
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(state['cancelled']) == 2 and not state['sent']


@pytest.mark.parametrize('width', [390, 1440])
def test_close_during_generation_leaves_shared_draft_available(page, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    state = install(page)
    pending = []
    draft = {
        'id': 'shared-draft', 'source_key': 'whatsapp:group', 'status': 'draft',
        'text': 'Saved for review.',
        'target': {'channel': 'whatsapp', 'jid': 'fixture-group@g.us', 'label': 'Client project group'},
    }

    def hold(route):
        if route.request.method == 'POST':
            pending.append(route)
        else:
            route.fallback()

    page.route('**/api/workflows/studio/tasks/17/source-replies', hold)
    page.get_by_role('button', name='Message Client', exact=True).click()
    page.get_by_label('To:', exact=True).select_option('whatsapp:group')
    expect(page.get_by_text('Drafting reply...', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    shot(page, f'{width}-shared-draft-loading')
    page.get_by_role('button', name='Close message', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)

    other = page.context.new_page()
    other.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    other_state = install(other, sources=1)
    other.route('**/api/workflows/studio/tasks/17/source-replies?*', lambda route: route.fulfill(json={
        'sources': [{'key': draft['source_key'], 'channel': 'whatsapp', 'label': 'Client project group'}],
        'drafts': [draft],
    }))
    other.get_by_role('button', name='Message Client', exact=True).click()
    expect(other.get_by_label('Reply', exact=True)).to_have_value(draft['text'])

    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/source-replies')):
        pending[0].fulfill(json=draft)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert not state['cancelled'] and not state['sent']
    expect(other.get_by_role('button', name='Approve', exact=True)).to_be_enabled()
    shot(other, f'{width}-shared-draft-review')
    assert not other_state['drafts'] and not other_state['sent']
    other.close()


@pytest.mark.parametrize('width', [390, 1280])
@pytest.mark.parametrize('status', ['sending', 'unconfirmed', 'sent'])
def test_reopen_resumes_durable_message_without_regeneration_or_resend(page, width, status):
    page.set_viewport_size({'width': width, 'height': 1000})
    state = install(page, sources=1)
    retained = {'id': 'protected-draft', 'source_key': 'whatsapp:group', 'response_id': 18,
                'status': status, 'text': 'My exact reviewed wording must survive reopening.',
                'target': {'channel': 'whatsapp', 'jid': 'fixture-group@g.us', 'label': 'Client project group'}}
    def resume(route):
        if route.request.method == 'GET':
            route.fulfill(json={'sources': [{'key': 'whatsapp:group', 'channel': 'whatsapp',
                                             'label': 'Client project group'}], 'drafts': [retained]})
        else:
            route.fallback()
    page.route('**/api/workflows/studio/tasks/17/source-replies**', resume)
    for _ in range(2):
        page.get_by_role('button', name='Message Client', exact=True).click()
        expect(page.get_by_label('Reply', exact=True)).to_have_value(retained['text'])
        expect(page.get_by_label('Reply', exact=True)).to_have_attribute('readonly', '')
        expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
        expect(page.get_by_role('button', name='Rewrite', exact=True)).to_be_disabled()
        assert state['drafts'] == 0 and not state['sent']
        shot(page, f'{width}-resumed-{status}')
        page.get_by_role('button', name='Close message', exact=True).click()
        expect(page.get_by_role('dialog')).to_have_count(0)


def test_switching_back_to_cancelled_source_generates_a_fresh_editable_draft(page):
    state = install(page)
    page.get_by_role('button', name='Message Client', exact=True).click()
    source = page.get_by_label('To:', exact=True)
    source.select_option('whatsapp:group')
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    source.select_option('gmail:thread')
    expect(page.get_by_text('Gmail: Reply all', exact=True)).to_be_visible()
    source.select_option('whatsapp:group')
    expect(page.locator('#source-reply-source option:checked')).to_have_text('Client project group')
    expect(page.get_by_label('Reply', exact=True)).to_be_enabled()
    assert state['drafts'] == 3
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_enabled()
