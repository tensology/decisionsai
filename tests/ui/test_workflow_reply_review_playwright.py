"""Real approval cards, mocked workflow reviews and decision HTTP only."""
import os
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from playwright.sync_api import expect

from tests.ui.test_development_harness_playwright import _install_api, URL
from tests.core.test_workflow_reply_review import decide, review, request_review
from distr.core.workflow.reply_delivery import execute_approved_workflow_reply
from distr.gui.web.routes.dev_approvals import _post_card as real_post_card

pytestmark = pytest.mark.e2e_playwright
SHOTS = Path(os.environ.get('WORKFLOW_REPLY_SHOTS', '/tmp/decisions-edge-audit/workflow-reply-review'))
LABELS = {'pending': 'Waiting for you', 'sending': 'Sending reply', 'sent': 'Reply sent',
          'unconfirmed': 'Send not confirmed', 'rejected': 'Reply not sent', 'denied': 'Denied', 'expired': 'Expired'}


def install(page, state='pending', long=False, generic=False, unavailable=False, projected=None, decide_request=None):
    body = ('The requested changes are ready for review.\nDeployment still needs your approval.' if not long else
            '\n'.join(f'Checked item {index}: source text <img src=x onerror=alert(1)> stays readable and exact.' for index in range(1, 50)))
    to = 'client@example.com' if not long else 'project-owner-with-a-long-mailbox@customer-with-a-long-domain.example.com'
    details = {'ticket_id': 42, 'channel': 'email', 'body': body,
        'workflow': {'run_id': 703, 'workflow_id': 12, 'chat_id': 17, 'turn_id': 19},
        'delivery': {'recipient': ['WRONG-FALLBACK@example.com'], 'thread_id': 'original-thread-123',
            'source_contact': 'Misleading label', 'subject': 'Wrong fallback subject',
            'resolved_target': {'from': 'paul@example.com', 'to': [to, 'reviewer@example.com'],
                'cc': ['project-team@example.com'], 'subject': 'Re: Original customer request',
                'thread_id': 'authoritative-thread-456'}}}
    if state == 'sent':
        details['delivery_outcome'] = {'delivered': True, 'delivery_status': 'sent', 'result': {'id': 'provider-confirmed'}}
    elif state not in {'pending', 'sending'}:
        details['delivery_outcome'] = {'delivered': False,
            'reason': {'unconfirmed': 'The app restarted before reply delivery was confirmed. Check the original conversation before sending again.',
                       'rejected': 'Project changed. Nothing was sent.', 'denied': 'You denied this closing reply.',
                       'expired': 'This approval expired. Nothing was sent.'}[state]}
    request = {'request_id': 'req-review', 'tool': 'reply_delivery', 'action': 'email', 'status': state,
        'summary': 'Send this closing reply to the original customer conversation?',
        'expires_at': '2099-01-01T12:00:00Z', 'details': details}
    if state == 'unconfirmed':
        request['summary'] = 'The closing reply was not confirmed.'
    elif state == 'sent':
        request['summary'] = 'The approved closing reply was sent.'
    if unavailable:
        request['review_unavailable'] = True
    if generic:
        request = {**request, 'tool': 'files', 'action': 'trash',
                   'details': {'before': {'path': '/tmp/example.txt'}, 'after': {}}}
    event = {'event_id': 'review-event', 'event_type': 'tool_started' if state == 'pending' else 'tool_completed',
             'status': 'pending' if state == 'pending' else 'completed', 'title': 'Closing reply review',
             'metadata': {'tool_name': 'reply_delivery', 'approval_request': request}}
    if projected:
        event = projected
        request = event['metadata']['approval_request']
        body = request['details']['body']
        envelope = request['details']['delivery']
        to = envelope['resolved_target']['to'][0] if envelope.get('resolved_target') else envelope['recipient']
    _install_api(page, [], current_board_linked=True, direct_execution=True,
                 completed_activity=True, assistant_markdown=True, terminal_session=False, activity_events=[event])
    actions = []
    def decision(route):
        actions.append((route.request.url.rsplit('/', 1)[-1], route.request.headers))
        status = 'sent' if actions[-1][0] == 'approve' else 'denied'
        if decide_request is not None:
            response = decide_request(request['request_id'], actions[-1][0])
            route.fulfill(status=response.status_code, json=response.json())
            return
        route.fulfill(json={**request, 'status': status})
    page.route(f'**/api/dev-approvals/{request["request_id"]}/*', decision)
    page.goto(URL)
    expect(page.get_by_text('Implemented', exact=True).first).to_be_visible()
    card = page.locator(f'[data-dev-approval="{request["request_id"]}"]')
    if not card.is_visible():
        page.locator('.turn-activity-disclosure > summary').last.click()
    expect(card).to_be_visible()
    return card, body, to, actions


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f'{name}.png'), full_page=True)


def reveal(locator):
    # The conversation uses smooth scrolling. Capture its settled deliberate
    # position, rather than a frame while it is still sliding under the composer.
    locator.evaluate('(el) => el.scrollIntoView({block: "center", behavior: "instant"})')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_telegram_closing_reply_waits_for_verified_receipt(page, width, review, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.integrations.telegram.manager import TelegramWebSocketManager
    from distr.core.integrations.telegram import receipt_writes
    from distr.core.workflow import reply_delivery
    from tests.telegram.test_telegram_delivery_stability import DummyTelegramSender

    api, factory, details, _ = review
    sender = DummyTelegramSender()
    sender._closing_reply_receipts = True
    monkeypatch.setattr(receipt_writes, '_journal_path', lambda: tmp_path / 'receipts')
    monkeypatch.setattr('distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app', lambda: sender)
    monkeypatch.setattr(reply_delivery, 'execute_approved_workflow_reply', execute_approved_workflow_reply)
    with factory.begin() as db:
        ticket = db.get(KanbanTicket, details['ticket_id'])
        ticket.source_provider, ticket.source_thread_id = 'telegram', str(sender.telegram_user_id)
        envelope = reply_delivery._delivery_envelope(db, ticket, reply_delivery.resolve_reply_target(ticket), '')
    request = request_review(review, {**details, 'channel': 'telegram', 'delivery': envelope}).json()
    path = f"/api/dev-approvals/{request['request_id']}"
    def receipt(route):
        response = api.get(path)
        route.fulfill(status=response.status_code, json=response.json())

    event = {'event_id': 'review-event', 'event_type': 'tool_started', 'status': 'pending',
             'title': 'Closing reply review', 'metadata': {'tool_name': 'reply_delivery', 'approval_request': request}}
    page.set_viewport_size({'width': width, 'height': 900})
    card, body, _, actions = install(page, projected=event, decide_request=lambda rid, action: decide(review, rid, action))
    page.route(f'**{path}', receipt)
    approve = card.get_by_role('button', name='Approve', exact=True)
    reveal(approve)
    approve.click()
    expect(card.locator('.dev-approval-status')).to_have_text('Sending reply')
    expect(card.get_by_label('Reply', exact=True)).to_have_value(body)
    expect(card.get_by_role('button')).to_have_count(0)
    reveal(card.locator('.dev-approval-head'))
    shot(page, f'{width}-telegram-closing-awaiting')
    sender._process_message_queue()
    [frame] = sender.websocket_attempts
    assert frame['text'] == body and frame['chat_id'] == sender.telegram_user_id
    manager = SimpleNamespace(_mark_socket_heartbeat_seen=lambda: None)
    ack = {'type': 'message_sent', 'client_message_id': frame['client_message_id'], 'telegram_message_id': 44,
           'chat_id': sender.telegram_user_id, 'closing_reply_verified': True}
    TelegramWebSocketManager._on_message(manager, json.dumps({**ack, 'chat_id': 999}))
    assert api.get(path).json()['status'] == 'sending'
    with factory.begin() as db:
        row = db.query(OrchestratorEvent).filter_by(source='reply_delivery').one()
        payload = json.loads(row.payload)
        payload['telegram_attempt']['deadline'] = 0
        row.payload = json.dumps(payload)
    expect(card.locator('.dev-approval-status')).to_have_text('Send not confirmed', timeout=8000)
    reveal(card.locator('.dev-approval-head'))
    shot(page, f'{width}-telegram-closing-unconfirmed')
    requests = []
    def relay_lookup(request):
        requests.append(request)
        TelegramWebSocketManager._on_message(manager, json.dumps(ack))
        return True
    manager.is_connected = lambda: True
    manager._send_websocket_message = relay_lookup
    manager._recover_closing_reply_receipts = lambda: TelegramWebSocketManager._recover_closing_reply_receipts(manager)
    TelegramWebSocketManager._on_message(manager, json.dumps({'type': 'connection', 'status': 'connected',
        'closing_reply_receipts': True, 'closing_reply_receipt_recovery': True}))
    assert requests == [{'type': 'closing_reply_receipt_lookup', 'token': frame['client_message_id']}]
    expect(card.locator('.dev-approval-status')).to_have_text('Reply sent', timeout=8000)
    reveal(card.locator('.dev-approval-head'))
    shot(page, f'{width}-telegram-closing-sent')
    assert decide(review, request['request_id']).json()['status'] == 'sent'
    with factory.begin() as db:
        row = db.query(OrchestratorEvent).filter_by(source='reply_delivery').one()
        payload = json.loads(row.payload)
        payload['detail']['chat_id'] = 999
        row.payload = '{' if width in {390, 1280} else json.dumps(payload)
    refreshed = api.get(path).json()
    assert refreshed['status'] == 'unconfirmed'
    refreshed_event = {**event, 'metadata': {**event['metadata'], 'approval_request': refreshed}}
    damaged_card, _, _, _ = install(page, projected=refreshed_event)
    expect(damaged_card.locator('.dev-approval-status')).to_have_text('Send not confirmed')
    expect(damaged_card.get_by_role('button', name='Approve', exact=True)).to_have_count(0)
    reveal(damaged_card.locator('.dev-approval-head'))
    shot(page, f'{width}-telegram-closing-damaged')
    assert decide(review, request['request_id']).json()['status'] == 'unconfirmed'
    sender._process_message_queue()
    assert sender.websocket_attempts == [frame] and len(actions) == 1


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('channel', ['whatsapp', 'jira'])
def test_scope_change_during_provider_checks_is_shown_as_not_sent(page, width, review, monkeypatch, channel):
    from distr.core.db import Chat
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.integrations.whatsapp import relay_client
    from distr.core.kanban import jira_intake
    from distr.core.workflow import reply_delivery

    _, factory, details, _ = review
    monkeypatch.setattr(reply_delivery, 'execute_approved_workflow_reply', execute_approved_workflow_reply)
    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value={'success': True})))
    monkeypatch.setattr(relay_client.requests, 'post', post)
    monkeypatch.setattr(relay_client, 'whatsapp_send_dry_run_enabled', lambda: False)
    with factory.begin() as db:
        ticket = db.get(KanbanTicket, details['ticket_id'])
        if channel == 'jira':
            ticket.source_provider = 'jira'
            ticket.source_thread_id = None
            ticket.source_external_id = 'ABC-12'
            ticket.source_url = 'https://example.atlassian.net/browse/ABC-12'
        envelope = reply_delivery._delivery_envelope(db, ticket, reply_delivery.resolve_reply_target(ticket), '')
    def move_thread():
        with factory.begin() as db:
            assert db.query(OrchestratorEvent).filter_by(source='reply_delivery', status='sending').count() == 1
            db.get(Chat, details['workflow']['chat_id']).project_id = 10
        return {}
    if channel == 'jira':
        def account():
            move_thread()
            return {'email': 'team@example.test', 'api_token': 'fixture', 'domain': 'example.atlassian.net'}
        monkeypatch.setattr(jira_intake, 'load_jira_account', account)
    else:
        monkeypatch.setattr(relay_client, 'relay_request_headers', move_thread)
    request = request_review(review, {**details, 'channel': channel, 'delivery': envelope}).json()
    assert request['status'] == 'pending'
    event = {'event_id': 'review-event', 'event_type': 'tool_started', 'status': 'pending',
             'title': 'Closing reply review', 'metadata': {'tool_name': 'reply_delivery', 'approval_request': request}}
    page.set_viewport_size({'width': width, 'height': 900})
    card, body, _, actions = install(page, projected=event, decide_request=lambda rid, action: decide(review, rid, action))
    approve = card.get_by_role('button', name='Approve', exact=True)
    if channel == 'jira':
        expect(card.get_by_text('Closing reply · Jira', exact=True)).to_be_visible()
        expect(card.get_by_text(envelope['source_url'], exact=True)).to_be_visible()
        reveal(card.locator('.dev-approval-head') if width == 390 else card)
        shot(page, f'{width}-jira-provider-scope-pending')
        if width == 390:
            reveal(approve)
            shot(page, f'{width}-jira-provider-scope-pending-lower')
    reveal(approve)
    approve.click()
    expect(card.locator('.dev-approval-status')).to_have_text('Reply not sent')
    expect(card.get_by_label('Reply', exact=True)).to_have_value(body)
    reason = 'Nothing was sent. The Development thread project no longer matches this reply.'
    expect(card.get_by_role('status')).to_have_text(reason)
    expect(card.get_by_text(reason, exact=False)).to_have_count(1)
    expect(card.get_by_role('button')).to_have_count(0)
    reveal(card.locator('.dev-approval-head') if width == 390 else card)
    shot(page, f'{width}-{channel}-provider-scope-changed')
    assert len(actions) == 1
    assert decide(review, request['request_id']).json()['status'] == 'rejected'
    post.assert_not_called()


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('state', list(LABELS))
def test_workflow_review_is_readable_and_truthful(page, width, state):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    card, body, to, actions = install(page, state)
    expect(card.locator('.dev-approval-status')).to_have_text(LABELS[state])
    expect(card.get_by_label('Reply', exact=True)).to_have_value(body)
    assert card.get_by_label('Reply', exact=True).get_attribute('readonly') is not None
    for value in [to, 'reviewer@example.com', 'project-team@example.com', 'paul@example.com',
                  'Re: Original customer request', 'authoritative-thread-456', '703', '42']:
        expect(card).to_contain_text(value)
    assert 'WRONG-FALLBACK' not in card.inner_text() and 'Misleading label' not in card.inner_text()
    assert 'resolved_target' not in card.inner_text() and '[object Object]' not in card.inner_text()
    reveal(card.locator('.dev-approval-head'))
    shot(page, f'{width}-{state}')
    if width == 390:
        reveal(card.locator(':scope > :last-child'))
        shot(page, f'{width}-{state}-lower')
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    if state == 'pending':
        approve = card.get_by_role('button', name='Approve', exact=True)
        reveal(approve)
        assert approve.evaluate('(el) => { const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); }')
        approve.click()
        expect(card.locator('.dev-approval-status')).to_have_text('Reply sent')
        assert len(actions) == 1 and actions[0][0] == 'approve'
        assert actions[0][1]['x-decisions-user-action'] == 'click'
    else:
        expect(card.get_by_role('button')).to_have_count(0)
        if state in {'sent', 'unconfirmed'}:
            card.screenshot(path=SHOTS / f'{width}-{state}-card.png')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_earlier_receipt_does_not_show_this_review_as_sent(page, width, review, monkeypatch):
    from distr.core.db.kanban import KanbanTicket
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow import reply_delivery
    from distr.core.workflow.reply_channel import resolve_reply_target

    _, factory, details, _ = review
    monkeypatch.setattr(reply_delivery, 'execute_approved_workflow_reply', execute_approved_workflow_reply)
    send = Mock(return_value={'success': True, 'message_id': 'original-confirmation'})
    monkeypatch.setitem(reply_delivery.ADAPTERS, 'whatsapp', send)
    with factory() as db:
        ticket = db.get(KanbanTicket, details['ticket_id'])
        delivery = reply_delivery._delivery_envelope(db, ticket, resolve_reply_target(ticket), '')
    original = {**details, 'delivery': delivery}
    first = request_review(review, original).json()
    assert decide(review, first['request_id']).json()['status'] == 'sent'
    with factory.begin() as db:
        receipt = db.query(OrchestratorEvent).filter_by(source='reply_delivery').one()
        receipt.payload, receipt.evidence = '{broken', None
    revised = {**original, 'body': 'The requested changes are ready for review.\nDeployment still needs your approval.'}
    request = request_review(review, revised).json()
    assert request['status'] == 'pending'
    projected = {'event_id': 'review-event', 'event_type': 'tool_started', 'status': 'pending',
                 'title': 'Closing reply review', 'metadata': {'tool_name': 'reply_delivery', 'approval_request': request}}
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    reason = 'An earlier delivery record does not match this review. Check the original conversation before sending another reply.'
    card, body, _, actions = install(page, projected=projected, decide_request=lambda rid, action: decide(review, rid, action))
    approve = card.get_by_role('button', name='Approve', exact=True)
    reveal(approve)
    approve.click()
    expect(card.locator('.dev-approval-status')).to_have_text('Reply not sent')
    expect(card.get_by_label('Reply', exact=True)).to_have_value(body)
    expect(card.get_by_role('button')).to_have_count(0)
    note = card.get_by_text(reason, exact=True)
    expect(note).to_be_visible()
    expect(card.get_by_text(reason, exact=False)).to_have_count(1)
    reveal(card.locator('.dev-approval-head') if width == 390 else card)
    shot(page, f'{width}-earlier-receipt-conflict')
    if width == 390:
        reveal(note)
        shot(page, f'{width}-earlier-receipt-conflict-lower')
    assert len(actions) == 1 and actions[0][0] == 'approve'
    send.assert_called_once()
    assert review[0].get(f'/api/dev-approvals/{first["request_id"]}').json()['status'] == 'sent'
    with factory() as db:
        assert db.query(OrchestratorEvent).filter_by(source='reply_delivery').one().payload == '{broken'


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_long_review_and_addresses_keep_actions_reachable_without_body_mutation(page, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    card, body, to, actions = install(page, long=True)
    expect(card.get_by_label('Reply', exact=True)).to_have_value(body)
    reveal(card.locator('.dev-approval-head'))
    shot(page, f'{width}-long-review-top')
    card.get_by_label('Reply', exact=True).focus()
    page.keyboard.press('Control+End')
    assert card.locator('img').count() == 0
    deny = card.get_by_role('button', name='Deny', exact=True)
    reveal(deny)
    message = card.get_by_label('Reply', exact=True)
    message.hover()
    page.mouse.wheel(0, 10000)
    page.wait_for_function('(el) => el.scrollTop > 0 && el.scrollTop + el.clientHeight >= el.scrollHeight - 2', arg=message.element_handle())
    shot(page, f'{width}-long-review-actions')
    box = deny.bounding_box()
    assert box and box['x'] >= 0 and box['x'] + box['width'] <= width and box['y'] + box['height'] <= page.viewport_size['height']
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    deny.click()
    expect(card.locator('.dev-approval-status')).to_have_text('Denied')
    assert len(actions) == 1 and actions[0][0] == 'deny'
    expect(card.get_by_label('Reply', exact=True)).to_have_value(body)


def test_generic_tool_review_keeps_existing_argument_diff(page):
    card, _, _, _ = install(page, generic=True)
    expect(card.locator('.dev-approval-diff')).to_contain_text('/tmp/example.txt')
    expect(card.get_by_label('Reply', exact=True)).to_have_count(0)
    expect(card.get_by_role('button', name='Approve', exact=True)).to_be_visible()


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
def test_missing_authoritative_review_never_exposes_approval_controls(page, width):
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    card, _, _, actions = install(page, unavailable=True)
    expect(card.locator('.dev-approval-status')).to_have_text('Review unavailable')
    expect(card).to_contain_text('Complete reply review is unavailable. Refresh before approving.')
    expect(card.get_by_role('button')).to_have_count(0)
    expect(card.get_by_label('Reply', exact=True)).to_have_count(0)
    reveal(card)
    shot(page, f'{width}-review-unavailable')
    assert not actions and not page.evaluate('document.documentElement.scrollWidth > innerWidth')


@pytest.mark.parametrize('width', [390, 768, 1280, 1440])
@pytest.mark.parametrize('confirmed', [False, True])
def test_actual_persisted_event_projection_preserves_complete_review(page, width, review, monkeypatch, confirmed):
    from distr.core import chat_turns
    from distr.core.db import ChatTurnEvent, DevApprovalRequest
    from distr.core.db.orchestrator import OrchestratorEvent
    from distr.core.workflow import reply_delivery
    from distr.gui.web.routes import dev_approvals as routes
    monkeypatch.setattr(routes, '_post_card', real_post_card)
    monkeypatch.setattr(chat_turns, 'get_session', review[1])
    monkeypatch.setattr(chat_turns, '_broadcast', lambda payload: None)
    body = '\n'.join(f'Checked customer requirement {i}.' for i in range(60)) + '\n\nFINAL: Deployment still needs your approval.'
    if confirmed:
        body = 'Checked the original request. Deployment still needs your approval.'
    details = {**review[2], 'channel': 'email', 'body': body,
        'delivery': {**review[2]['delivery'], 'channel': 'email', 'board_id': 1, 'resolved_target': {
            'from': 'paul@example.com', 'to': ['actual-customer@example.com'],
            'cc': ['actual-reviewer@example.com'], 'subject': 'Re: Actual retained request',
            'thread_id': 'actual-original-thread'}}}
    response = request_review(review, details)
    assert response.status_code == 200
    rid = response.json()['request_id']
    with review[1]() as db:
        stored = json.loads(db.query(ChatTurnEvent).one().metadata_json)['approval_request']
        if not confirmed:
            assert len(stored['details']['body']) < len(body)  # Durable event remains redacted.
        if confirmed:
            row = db.query(DevApprovalRequest).filter_by(request_id=rid).one()
            row.status = 'sending'
            row.approval_id_hash = 'approved-in-this-process'
            row.signature = routes._sign(row, row.approval_id_hash)
            envelope = details['delivery']
            db.add(OrchestratorEvent(event_uid=reply_delivery._delivery_key(details['ticket_id'], body, envelope),
                source='reply_delivery', event_type=reply_delivery.REPLY_EVENT_TYPE, status='sent',
                ticket_id=details['ticket_id'], project_id=envelope['project_id'], board_id=envelope['board_id'],
                payload=json.dumps({'workflow': details['workflow'], 'envelope': envelope,
                                    'body': body, 'detail': {'id': 'confirmed'}})))
            db.commit()
    turns = chat_turns.get_turns(details['workflow']['chat_id'])
    projected = next(event for turn in turns['turns'] for event in turn['events']
        if event.get('metadata', {}).get('approval_request', {}).get('request_id') == rid)
    projected_request = projected['metadata']['approval_request']
    assert {k: v for k, v in projected_request['details'].items() if k != 'delivery_outcome'} == details
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 1000})
    card, actual, to, actions = install(page, projected=projected)
    expect(card.get_by_label('Reply', exact=True)).to_have_value(body)
    expect(card).to_contain_text('actual-customer@example.com')
    expect(card).to_contain_text('actual-reviewer@example.com')
    if confirmed:
        expect(card.locator('.dev-approval-status')).to_have_text('Reply sent')
        expect(card.get_by_role('button')).to_have_count(0)
        expect(card.get_by_text('The approved closing reply was sent.')).to_be_visible()
        reveal(card)
        SHOTS.mkdir(parents=True, exist_ok=True)
        card.screenshot(path=SHOTS / f'{width}-recovered-confirmation-card.png')
        if width == 390:
            reply = card.get_by_label('Reply', exact=True)
            reveal(reply)
            assert reply.evaluate("""node => {
                const r = node.getBoundingClientRect();
                return node.contains(document.elementFromPoint(r.left + r.width / 2, r.bottom - 4));
            }""")
            shot(page, f'{width}-recovered-confirmation-lower')
        assert not actions and not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        review[3].assert_not_called()
        return
    reveal(card.locator('.dev-approval-head'))
    shot(page, f'{width}-persisted-review-top')
    message = card.get_by_label('Reply', exact=True)
    reveal(message); message.hover(); page.mouse.wheel(0, 10000)
    page.wait_for_function('(el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 2', arg=message.element_handle())
    reveal(card.get_by_role('button', name='Approve', exact=True))
    shot(page, f'{width}-persisted-review-tail')
    expect(message).to_have_value(body)
    assert not actions and not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    review[3].assert_not_called()
