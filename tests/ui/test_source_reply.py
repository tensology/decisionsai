"""Real Development UI, isolated proposal sources and outbound transports."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect
from tests.ui.test_development_harness_playwright import _install_api, URL
from tests.ui.test_incoming_dismiss_undo import fixture_base
from tests.core.test_proposal_continuity_integration import intake  # noqa: F401

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
            if state.get('damaged_history'):
                route.fulfill(status=409, json={'detail': state['damaged_history'], 'code': 'reply_history_unavailable'})
                return
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
def test_damaged_reply_history_has_an_actionable_error_and_can_close(page, width, fixture_base):
    page.set_viewport_size({'width': width, 'height': 1000})
    state = install(page, sources=1, url=f'{fixture_base}/development/threads/17/')
    state['damaged_history'] = ('A saved reply record could not be verified. Check the original conversation '
                                'and restore the reply history before sending again.')
    opener = page.get_by_role('button', name='Message Client', exact=True)
    opener.click()
    dialog = page.get_by_role('dialog', name='Message Client')
    status = dialog.get_by_role('status')
    expect(status).to_have_text(state['damaged_history'])
    expect(status).to_be_focused()
    expect(dialog.get_by_label('To:', exact=True)).to_be_hidden()
    expect(dialog.locator('label[for="source-reply-source"]')).to_be_hidden()
    expect(dialog.get_by_role('button', name='Approve')).to_be_disabled()
    expect(dialog.get_by_role('button', name='Rewrite')).to_be_disabled()
    assert not state['drafts'] and not state['sent']
    shot(page, f'{width}-damaged-history')
    dialog.get_by_role('button', name='Close message').click()
    expect(opener).to_be_focused()


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_merged_reply_keeps_independent_recipient_selectable(page, width, fixture_base):
    page.set_viewport_size({'width': width, 'height': 900})
    state = install(page, url=f'{fixture_base}/development/threads/17/')
    state['choices'] = [
        {'key': 'whatsapp:group', 'channel': 'whatsapp', 'label': 'Client project group',
         'reply_blocked_reason': 'The workflow already sent a reply to this conversation. Check the original conversation before sending again.'},
        {'key': 'gmail:thread', 'channel': 'gmail', 'label': 'Client billing email'},
    ]
    page.get_by_role('button', name='Message Client', exact=True).click()
    dialog = page.get_by_role('dialog', name='Message Client')
    recipient = dialog.get_by_label('To:', exact=True)
    recipient.select_option('whatsapp:group')
    expect(dialog.get_by_role('status')).to_contain_text('The workflow already sent a reply')
    expect(dialog.get_by_role('button', name='Approve')).to_be_disabled()
    expect(recipient).to_be_enabled()
    assert state['drafts'] == 0
    shot(page, f'{width}-merged-reply-blocked')
    recipient.select_option('gmail:thread')
    expect(dialog.get_by_label('Reply', exact=True)).to_be_enabled()
    shot(page, f'{width}-merged-reply-available')
    dialog.get_by_role('button', name='Approve').click()
    expect(page.locator('#studio-toast.success')).to_have_text('Reply sent')
    assert state['drafts'] == 1 and len(state['sent']) == 1
    state['retained'] = [{
        'id': 'draft-1', 'source_key': 'gmail:thread', 'response_id': 18, 'status': 'sent',
        'text': state['sent'][0]['text'],
        'target': {'channel': 'gmail', 'to': ['client@example.com'], 'cc': [], 'subject': 'Re: Billing request'},
    }]
    page.get_by_role('button', name='Message Client', exact=True).click()
    recipient.select_option('gmail:thread')
    expect(dialog.get_by_text('Previously sent.', exact=True)).to_be_visible()
    expect(dialog.get_by_role('button', name='Approve')).to_be_disabled()
    shot(page, f'{width}-merged-reply-reopened')
    assert state['drafts'] == 1 and len(state['sent']) == 1


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('surface', ['desktop', 'paired'])
def test_merged_reply_isolates_unreadable_claim(page, width, surface, fixture_base, intake, monkeypatch):
    import json
    from unittest.mock import Mock
    from urllib.parse import urlsplit

    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient
    from distr.core.db import Chat, SourceReplyDraft
    from distr.core.incoming import source_reply as replies
    from distr.core.orchestrator_proactive import _add_proactive_turn_event, PROACTIVE_RESULT_HELD
    from distr.gui.web.routes import chat as chat_routes
    from distr.gui.web.routes.development.thread_tools import register_routes
    from tests.core.test_chat_development_surface_isolation import _client

    app_view = os.environ.get('DECISIONS_REMOTE_APP_TEST_URL')
    if surface == 'paired' and not app_view:
        pytest.skip('Set DECISIONS_REMOTE_APP_TEST_URL to the local App View development server.')
    session, proposal, projects = intake
    proposal['source_scope'] = [
        {'source': 'whatsapp', 'source_thread_id': 'first@g.us', 'message_ids': ['one']},
        {'source': 'whatsapp', 'source_thread_id': 'second@g.us', 'message_ids': ['two']},
    ]
    with session() as db:
        root = Chat(id=17, title='Merged proposal', project_id=projects[0],
                    params=json.dumps({'incoming_proposal_handoff': {'proposal': proposal}}))
        db.add(root)
        db.flush()
        response = Chat(parent_id=root.id, response='The work is ready.')
        db.add(response)
        db.flush()
        chat_id, response_id = root.id, response.id
        db.commit()
    monkeypatch.setattr(replies, 'resolve_target', lambda source: {
        'channel': 'whatsapp', 'jid': source['thread_id'], 'label': source['label'],
    })
    monkeypatch.setattr(replies, 'generate_draft', lambda *a, **kw: ('The work is ready.', {}))
    sender = Mock()
    move_during_preflight = []

    def send(target, text, draft_id, *, before_send):
        if move_during_preflight:
            move_during_preflight.clear()
            with session() as db:
                root = db.get(Chat, chat_id)
                root.params = json.dumps({**json.loads(root.params), 'development': {
                    'source_type': 'proposal', 'board_provider': 'tensology', 'board_key': 'replacement-board',
                    'board_ticket_key': 'replacement-ticket',
                }})
                db.commit()
        before_send()
        return sender(target, text, draft_id)

    monkeypatch.setattr(replies, 'send_reply', send)
    first = replies.create_draft(chat_id, response_id, 'whatsapp:first@g.us')
    replies.act_on_draft(chat_id, first['id'], 'approve', 'Already delivered.')
    sender.reset_mock()
    with session() as db:
        damaged = db.get(SourceReplyDraft, first['id'])
        damaged.payload = '{broken'
        damaged.claimed_response_id = None
        db.commit()

    api_app, router = FastAPI(), APIRouter()
    register_routes(router, None)
    api_app.include_router(router, prefix='/api')
    api = TestClient(api_app)
    path = f'/api/workflows/studio/tasks/{chat_id}/source-replies'
    chat = {
        'id': chat_id, 'title': 'Merged proposal', 'project_id': 7, 'development': True,
        'development_board_key': 'decisions:70', 'has_reply_source': True, 'archived': False,
        'messages': [{'role': 'assistant', 'content': 'The work is ready.', 'chat_row_id': response_id}],
    }
    rejected = api.post(f"{path}/{first['id']}/approve", json={'approved': True, 'text': 'Duplicate.'})
    assert rejected.status_code == 409
    assert rejected.json()['code'] == 'reply_history_unavailable'
    sender.assert_not_called()

    overlapping_approvals = []
    execution = {'status': 'completed', 'direct': True}

    def repeat_while_sending(_target, _text, draft_id):
        overlapping_approvals.append(api.post(f'{path}/{draft_id}/approve',
                                             json={'approved': True, 'text': 'Concurrent duplicate.'}))

    sender.side_effect = repeat_while_sending

    def bridge(route):
        request = route.request
        url = urlsplit(request.url)
        result = api.request(request.method, f'{url.path}?{url.query}', json=request.post_data_json)
        route.fulfill(status=result.status_code, json=result.json())

    page.set_viewport_size({'width': width, 'height': 900})
    dialog_root = page
    if surface == 'paired':
        def relay(socket):
            def handle(raw):
                frame = json.loads(raw)['body']
                if frame['type'] == 'ping':
                    socket.send(json.dumps({'type': 'pong'}))
                    return
                command = frame['command']
                status, response = 200, {'screens': []}
                if command == 'api_relay':
                    data = frame['data']
                    request_path = data['path']
                    if request_path.startswith('/api/workflows/studio/incoming/proposals/review?'):
                        response = {'status': 'thread', 'chat_id': chat_id}
                    elif request_path == f'/api/chats/{chat_id}?surface=development':
                        response = chat
                    elif request_path == f'/api/workflows/studio/tasks/{chat_id}/execution':
                        response = execution
                    else:
                        result = api.request(data['method'], request_path, json=data.get('body'))
                        status, response = result.status_code, result.json()
                socket.send(json.dumps({
                    'type': 'remote_control_response', 'command': command,
                    'request_id': frame.get('request_id'), 'data': {'status': status, 'response': response},
                }))
            socket.on_message(handle)

        page.route_web_socket('**/ws/telegram?*', relay)
        page.goto(f"{app_view}?channel=fixture&proposal_key={'a' * 64}")
        dialog_root = page.frame_locator('iframe[title="Development source reply"]')
    else:
        _install_api(page, [], current_board_linked=True, direct_execution=True, terminal_session=False)
        page.route(f'**/api/chats/{chat_id}**', lambda route: route.fulfill(json=chat))
        page.route(f'**/api/workflows/studio/tasks/{chat_id}/execution', lambda route: route.fulfill(json=execution))
        page.route(f'**{path}**', bridge)
        page.goto(f'{fixture_base}/development/threads/{chat_id}/')

    opener = page.get_by_role('button', name='Message Client', exact=True)
    opener.click()
    dialog = dialog_root.get_by_role('dialog', name='Message Client')
    recipient = dialog.get_by_label('To:', exact=True)
    approve = dialog.get_by_role('button', name='Approve', exact=True)
    assert recipient.evaluate("select => getComputedStyle(select.parentElement, '::after').content") == '""'
    recipient.select_option('whatsapp:first@g.us')
    expect(recipient.locator('option:checked')).to_have_text('WhatsApp · first@g.us (blocked)')
    expect(dialog.get_by_role('status')).to_contain_text('Check the original conversation')
    expect(approve).to_be_disabled()
    sender.assert_not_called()
    shot(page, f'{width}-{surface}-unreadable-claim-blocked')
    recipient.select_option('whatsapp:second@g.us')
    expect(recipient.locator('option:checked')).to_have_text('WhatsApp · second@g.us')
    expect(approve).to_be_enabled()
    shot(page, f'{width}-{surface}-unreadable-claim-independent')
    dialog.get_by_label('Reply', exact=True).fill('Independent reply.')
    move_during_preflight.append(True)
    approve.click()
    expect(dialog.get_by_role('status')).to_contain_text('board or ticket changed')
    expect(dialog.get_by_label('Reply', exact=True)).to_have_value('Independent reply.')
    expect(approve).to_be_disabled()
    sender.assert_not_called()
    shot(page, f'{width}-{surface}-changed-reply-scope')
    dialog.get_by_role('button', name='Rewrite', exact=True).click()
    expect(approve).to_be_enabled()
    dialog.get_by_label('Reply', exact=True).fill('Independent reply.')
    approve.click()
    toast = (page.get_by_role('status').get_by_text('Reply sent', exact=True)
             if surface == 'paired' else page.locator('#studio-toast.success'))
    expect(toast).to_have_text('Reply sent')
    assert len(overlapping_approvals) == 1
    assert overlapping_approvals[0].status_code == 409
    assert 'sending' in overlapping_approvals[0].json()['detail']
    draft_id = sender.call_args.args[2]
    duplicate = api.post(f'{path}/{draft_id}/approve', json={'approved': True, 'text': 'Duplicate.'})
    assert duplicate.status_code == 200 and duplicate.json()['already_sent']
    sender.assert_called_once()
    assert sender.call_args.args[0]['jid'] == 'second@g.us'
    opener.click()
    recipient.select_option('whatsapp:second@g.us')
    expect(dialog.get_by_text('Previously sent.', exact=True)).to_be_visible()
    expect(approve).to_be_disabled()
    shot(page, f'{width}-{surface}-unreadable-claim-reopened')
    recipient.select_option('whatsapp:first@g.us')
    expect(dialog.get_by_role('status')).to_contain_text('Check the original conversation')
    expect(approve).to_be_disabled()
    sender.assert_called_once()
    with session() as db:
        original = db.get(SourceReplyDraft, first['id'])
        assert (original.status, original.payload, original.claimed_source_key) == (
            'sent', '{broken', 'whatsapp:first@g.us',
        )
        root = db.get(Chat, chat_id)
        root.params = json.dumps({**json.loads(root.params), "development": {"source_type": "proposal"}})
        warning = Chat(parent_id=chat_id, response=PROACTIVE_RESULT_HELD)
        db.add(warning)
        db.flush()
        warning_id = warning.id
        _add_proactive_turn_event(db, chat_id, warning_id, "turn_failed", "failed", PROACTIVE_RESULT_HELD)
        db.commit()
    monkeypatch.setattr(chat_routes, "get_session", session)
    transcript = _client().get(f'/api/chats/{chat_id}?surface=development')
    assert transcript.status_code == 200
    chat['messages'] = transcript.json()['messages']
    execution.update(status='idle')
    page.reload()
    messages = page.locator('#message-list' if surface == 'desktop' else '.av-messages')
    expect(messages.get_by_text(PROACTIVE_RESULT_HELD, exact=True)).to_be_visible()
    expect(opener).to_have_count(0)
    shot(page, f'{width}-{surface}-internal-reply-warning')
    refused = api.post(path, json={'source_key': 'whatsapp:second@g.us', 'response_id': warning_id})
    assert refused.status_code == 422
    sender.assert_called_once()

    waiting_text = 'I need a decision about the billing units before continuing.'
    with session() as db:
        waiting = Chat(parent_id=chat_id, response=waiting_text)
        db.add(waiting)
        db.flush()
        waiting_id = waiting.id
        _add_proactive_turn_event(db, chat_id, waiting_id, "turn_completed", "waiting", waiting_text)
        db.commit()
    transcript = _client().get(f'/api/chats/{chat_id}?surface=development')
    assert transcript.status_code == 200
    chat['messages'] = transcript.json()['messages']
    execution.update(status='blocked', waiting_kind='human_review', can_stop=False,
                     origin='proactive_orchestrator', turn_id=waiting_id)
    page.reload()
    expect(messages.get_by_text(waiting_text, exact=True)).to_be_visible()
    status = page.locator('#task-state b') if surface == 'desktop' else page.get_by_role(
        'region', name='Development execution').get_by_text('Waiting for review', exact=True)
    expect(status).to_have_text('Waiting for review')
    expect(status).to_be_visible()
    expect(opener).to_have_count(0)
    expect(page.get_by_role('button', name='Stop', exact=True)).to_have_count(0)
    if surface == 'desktop':
        page.get_by_role('textbox', name='Development instruction', exact=True).fill('Use per-byte billing.')
        expect(page.get_by_role('button', name='Send prompt', exact=True)).to_be_enabled()
    shot(page, f'{width}-{surface}-waiting-for-review')
    refused = api.post(path, json={'source_key': 'whatsapp:second@g.us', 'response_id': waiting_id})
    assert refused.status_code == 422
    sender.assert_called_once()


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
    state['retained'] = [{
        'id': 'draft-1', 'status': 'sent', 'response_id': 18, 'source_key': 'gmail:thread',
        'text': 'The wording confirmed by the first window.',
        'target': {'channel': 'gmail', 'from': 'paul@example.com', 'to': ['client@example.com'],
                   'cc': ['reviewer@example.com'], 'subject': 'Re: Billing request'},
    }]
    page.get_by_role('button', name='Check send status', exact=True).click()
    recovered = page.get_by_text('This reply was already sent. Your newer wording is under Your unsent edits.')
    expect(recovered).to_be_visible()
    expect(page.get_by_label('Reply', exact=True)).to_have_value('The wording confirmed by the first window.')
    expect(page.get_by_label('Your unsent edits', exact=True)).to_have_value('Approved wording must remain visible.')
    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_disabled()
    assert len(state['sent']) == 1
    expect(recovered).to_be_focused()
    expect(recovered).to_be_in_viewport()
    shot(page, f'{width}-recovered-send-with-unsent-edits')
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
