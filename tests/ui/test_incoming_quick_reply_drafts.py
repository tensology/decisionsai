"""Retained Incoming mail editor state under refresh and uncertain delivery."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import _install_api
from tests.ui.test_incoming_dismiss_undo import fixture_base

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path('/tmp/decisions-edge-fixes/quick-reply')


def install(page, fixture_base, width):
    page.set_viewport_size({'width': width, 'height': 960})
    _install_api(page, [])
    page.route('**/api/workflows/studio/incoming?*', lambda route: route.fulfill(json={
        'items': [
            {'key':'gmail:in', 'source':'gmail', 'source_thread_id':'mail-thread', 'source_message_id':'in',
             'conversation_label':'Client request', 'sender':'Client <client@example.com>',
             'reply_to':'Support <support@example.com>', 'recipient':'Paul <paul@example.com>',
             'text':'Please review the request.', 'created_at':'2026-11-10T09:00:00Z', 'attachments':[]},
            {'key':'gmail:out', 'source':'gmail', 'source_thread_id':'mail-thread', 'source_message_id':'out',
             'conversation_label':'Client request', 'sender':'Paul <paul@example.com>',
             'recipient':'Client <client@example.com>', 'from_me':True, 'labels':['SENT'],
             'text':'I will review it.', 'created_at':'2026-11-10T09:15:00Z', 'attachments':[]},
        ], 'links':[], 'channels':{'gmail':{'connected':True}},
    }))
    page.add_init_script('''window.auditSockets=[]; class FakeSocket {static OPEN=1; constructor(){this.readyState=0;this.sent=[];window.auditSockets.push(this);setTimeout(()=>{this.readyState=1;this.onopen?.()},0)} send(s){this.sent.push(JSON.parse(s))} close(){this.readyState=3}} window.WebSocket=FakeSocket;''')
    page.goto(fixture_base + '/development/incoming/')
    page.locator('#incoming-folder-gmail').click()
    page.locator('[data-incoming-gmail-thread]').first.click()
    page.locator('[data-incoming-quick-reply-open]').filter(visible=True).click()
    field = page.locator('[data-quick-reply-body]').filter(visible=True)
    expect(page.locator('[data-quick-reply-to]').filter(visible=True)).to_have_value('Support <support@example.com>')
    field.fill('Keep my exact unsent wording.')
    SHOTS.mkdir(parents=True, exist_ok=True)
    return field


def refresh(page):
    page.evaluate('''() => {const socket=window.auditSockets.find(s=>s.sent.some(m=>m.subscribe_topic==='incoming'));socket.onmessage({data:JSON.stringify({type:'proposal_changed',status:'proposal_updated',request_id:999})})}''')
    page.wait_for_timeout(650)


def reopen_after_reload(page):
    page.reload()
    gmail = page.locator('#incoming-folder-gmail')
    gmail.click()
    expect(gmail).to_have_attribute('aria-selected', 'true')
    page.locator('[data-incoming-gmail-thread]').first.click()
    panel = page.locator('[data-incoming-quick-reply]').filter(visible=True)
    if not panel.is_visible():
        page.locator('[data-incoming-quick-reply-open]').filter(visible=True).click()
    return panel


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_unsent_quick_reply_survives_full_reload(page, fixture_base, width):
    install(page, fixture_base, width)
    editor = reopen_after_reload(page)
    expect(editor.locator('[data-quick-reply-body]')).to_have_value('Keep my exact unsent wording.')
    expect(editor.locator('[data-quick-reply-send]')).to_be_enabled()
    page.screenshot(path=str(SHOTS/f'reload-unsent-{width}.png'), animations='disabled')
    editor.locator('[data-quick-reply-cancel]').click()
    editor = reopen_after_reload(page)
    expect(editor.locator('[data-quick-reply-body]')).to_have_value('')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_reload_while_sending_only_checks_receipt_and_never_replays(page, fixture_base, width):
    install(page, fixture_base, width)
    sends, checks = [], []
    outcome = ['missing']

    def hold_send(route):
        sends.append(route.request.post_data_json)

    def check_receipt(route):
        checks.append(route.request.url)
        assert route.request.method == 'GET'
        route.fulfill(json={'receipt_status': outcome[0]})

    page.route('**/api/workflows/studio/incoming/mail/reply', hold_send)
    page.route('**/api/workflows/studio/incoming/mail/reply/status?*', check_receipt)
    page.locator('[data-quick-reply-send]').filter(visible=True).click()
    expect(page.locator('[data-quick-reply-send]').filter(visible=True)).to_have_text('Sending...')
    editor = reopen_after_reload(page)
    expect(editor.locator('[data-quick-reply-body]')).to_have_value('Keep my exact unsent wording.')
    expect(editor.locator('[data-quick-reply-body]')).to_have_attribute('readonly', '')
    expect(editor.locator('[data-quick-reply-send]')).to_be_disabled()
    check = editor.get_by_role('button', name='Check status')
    check.click()
    expect(editor.locator('[data-quick-reply-status]')).to_contain_text('No send receipt was found')
    expect(editor.locator('[data-quick-reply-send]')).to_be_disabled()
    page.screenshot(path=str(SHOTS/f'reload-unconfirmed-{width}.png'), animations='disabled')
    assert len(sends) == len(checks) == 1
    assert checks[0].endswith(sends[0]['idempotency_key'])

    outcome[0] = 'sent'
    check.click()
    expect(editor).to_have_count(0)
    assert len(sends) == 1 and len(checks) == 2
    editor = reopen_after_reload(page)
    expect(editor.locator('[data-quick-reply-body]')).to_have_value('')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_unsent_draft_survives_refresh_and_success_clears_only_its_conversation(page, fixture_base, width):
    field = install(page, fixture_base, width)
    refresh(page)
    expect(field).to_have_value('Keep my exact unsent wording.')
    bounds = page.locator('[data-quick-reply-send]').filter(visible=True).bounding_box()
    assert bounds['y'] + bounds['height'] <= page.viewport_size['height']
    page.screenshot(path=str(SHOTS/f'retained-{width}.png'))
    calls = []
    def send(route):
        calls.append(route.request.post_data_json)
        route.fulfill(json={'success': True})
    page.route('**/api/workflows/studio/incoming/mail/reply', send)
    page.locator('[data-quick-reply-send]').filter(visible=True).click()
    expect(page.locator('[data-incoming-quick-reply]').filter(visible=True)).to_have_count(0)
    page.locator('[data-incoming-quick-reply-open]').filter(visible=True).click()
    expect(field).to_have_value('')
    assert len(calls) == 1 and calls[0]['body'] == 'Keep my exact unsent wording.'
    assert calls[0]['idempotency_key']
    page.screenshot(path=str(SHOTS/f'sent-{width}.png'))


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_uncertain_send_keeps_copyable_text_and_disables_repeat_send(page, fixture_base, width):
    field = install(page, fixture_base, width)
    calls=[]
    def send(route):
        calls.append(route.request.post_data_json)
        route.fulfill(status=409, json={'error': 'Send was not confirmed. Check the source.', 'receipt_status': 'unconfirmed'})
    page.route('**/api/workflows/studio/incoming/mail/reply', send)
    page.locator('[data-quick-reply-send]').filter(visible=True).click()
    expect(field).to_have_value('Keep my exact unsent wording.')
    expect(field).to_have_attribute('readonly', '')
    expect(page.locator('[data-quick-reply-send]').filter(visible=True)).to_be_disabled()
    expect(page.locator('[data-quick-reply-send]').filter(visible=True)).to_have_css('cursor', 'not-allowed')
    expect(page.locator('[data-quick-reply-status]').filter(visible=True)).to_contain_text('Send was not confirmed')
    refresh(page)
    expect(field).to_have_value('Keep my exact unsent wording.')
    expect(page.locator('[data-quick-reply-send]').filter(visible=True)).to_be_disabled()
    assert len(calls) == 1
    page.screenshot(path=str(SHOTS/f'unconfirmed-{width}.png'))
    page.locator('[data-quick-reply-cancel]').filter(visible=True).click()
    expect(page.locator('[data-incoming-quick-reply]').filter(visible=True)).to_have_count(0)
    expect(page.locator('[data-incoming-quick-reply-open]').filter(visible=True)).to_be_focused()
    refresh(page)
    page.locator('[data-incoming-quick-reply-open]').filter(visible=True).click()
    expect(field).to_have_value('Keep my exact unsent wording.')
    expect(field).to_have_attribute('readonly', '')
    expect(page.locator('[data-quick-reply-send]').filter(visible=True)).to_be_disabled()
    assert len(calls) == 1
    page.screenshot(path=str(SHOTS/f'unconfirmed-reopened-{width}.png'))


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_gmail_preflight_keeps_quick_reply_editable_for_deliberate_retry(page, fixture_base, width):
    field = install(page, fixture_base, width)
    calls = []
    message = 'Nothing was sent. The original Gmail reply reference is missing. Your text is retained.'

    def send(route):
        calls.append(route.request.post_data_json)
        if len(calls) == 1:
            route.fulfill(status=422, json={'success': False, 'error': message, 'send_attempted': False})
        else:
            route.fulfill(json={'success': True})

    page.route('**/api/workflows/studio/incoming/mail/reply', send)
    send_button = page.locator('[data-quick-reply-send]').filter(visible=True)
    send_button.click()
    expect(page.locator('[data-quick-reply-status]').filter(visible=True)).to_have_text(message)
    expect(page.get_by_text(message, exact=True)).to_have_count(1)
    expect(field).to_have_value('Keep my exact unsent wording.')
    expect(field).to_be_editable()
    expect(send_button).to_be_enabled()
    assert len(calls) == 1
    page.screenshot(path=str(SHOTS/f'preflight-{width}.png'))

    send_button.click()
    expect(page.locator('[data-incoming-quick-reply]').filter(visible=True)).to_have_count(0)
    assert len(calls) == 2 and calls[0] == calls[1]
